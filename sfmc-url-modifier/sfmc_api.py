# -*- coding: utf-8-sig -*-
"""
SFMC API - Gestion journeys et emails
"""

import sys
import io
# Force UTF-8-SIG pour eviter les erreurs d'encodage Windows (cp1252)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf-8-sig'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8-sig', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8-sig', errors='replace')

import requests
import re
import time
import uuid
import xml.etree.ElementTree as ET
from config import REST_BASE_URL, SOAP_BASE_URL, extract_country_from_name, get_url_patterns_for_journey, COUNTRY_URL_MAPPINGS


class JourneyCache:
    """Cache mémoire avec TTL"""
    def __init__(self, ttl_seconds=300):
        self.ttl = ttl_seconds
        self._cache = {}
        self._timestamps = {}

    def get(self, key):
        if key in self._cache:
            if time.time() - self._timestamps[key] < self.ttl:
                return self._cache[key]
            else:
                # Expired
                del self._cache[key]
                del self._timestamps[key]
        return None

    def set(self, key, value):
        self._cache[key] = value
        self._timestamps[key] = time.time()

    def clear(self):
        self._cache.clear()
        self._timestamps.clear()

    def is_valid(self, key):
        return key in self._cache and (time.time() - self._timestamps[key] < self.ttl)


# Global cache instance (4 heures)
_journey_cache = JourneyCache(ttl_seconds=14400)


class SFMCAPI:
    def __init__(self, auth):
        self.auth = auth
        self.base_url = REST_BASE_URL
        self.soap_url = SOAP_BASE_URL
        self.cache = _journey_cache

    def _get_transactional_default_attributes(self, definition_key):
        """Derive minimal required attrs like site/locale from transactional definition key."""
        patterns = get_url_patterns_for_journey(definition_key or '')
        if not patterns:
            return {}
        site, locale = patterns
        defaults = {}
        if site:
            defaults['site'] = site
        if locale:
            defaults['locale'] = locale
        return defaults

    def _soap_envelope(self, body_xml):
        token = self.auth.get_token()
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:ns="http://exacttarget.com/wsdl/partnerAPI" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<soapenv:Header><ns:fueloauth>{token}</ns:fueloauth></soapenv:Header>'
            f'<soapenv:Body>{body_xml}</soapenv:Body>'
            '</soapenv:Envelope>'
        )

    def _soap_post(self, action, body_xml):
        print(f"\n[SOAP] Action={action}")
        print(f"[SOAP] URL={self.soap_url}")
        print(f"[SOAP] Body=\n{body_xml}")
        response = requests.post(
            self.soap_url,
            data=self._soap_envelope(body_xml).encode('utf-8'),
            headers={
                'Content-Type': 'text/xml; charset=utf-8',
                'SOAPAction': action,
            },
            timeout=60,
            verify=False
        )
        print(f"[SOAP] HTTP Status={response.status_code}")
        print(f"[SOAP] Response=\n{response.text}")
        response.raise_for_status()
        return response.text

    def _request_with_auth_retry(self, method, url, **kwargs):
        response = requests.request(method, url, **kwargs)
        if response.status_code == 401:
            self.auth.refresh()
            headers = dict(kwargs.get('headers') or {})
            headers.update(self.auth.get_headers())
            kwargs['headers'] = headers
            response = requests.request(method, url, **kwargs)
        return response

    def _parse_soap_update_result(self, xml_text):
        namespaces = {
            'soap': 'http://schemas.xmlsoap.org/soap/envelope/',
            'ns': 'http://exacttarget.com/wsdl/partnerAPI',
        }
        root = ET.fromstring(xml_text)
        overall = root.findtext('.//ns:OverallStatus', default='', namespaces=namespaces)
        status_code = root.findtext('.//ns:Results/ns:StatusCode', default='', namespaces=namespaces)
        status_message = root.findtext('.//ns:Results/ns:StatusMessage', default='', namespaces=namespaces)
        error_code = root.findtext('.//ns:Results/ns:ErrorCode', default='', namespaces=namespaces)
        print(f"[SOAP PARSE] OverallStatus={overall!r} | StatusCode={status_code!r} | StatusMessage={status_message!r} | ErrorCode={error_code!r}")
        if overall != 'OK' or status_code not in ('', 'OK'):
            raise Exception(f"SOAP {status_code or overall}: {status_message or 'unknown error'} ({error_code or 'n/a'})")
        return {
            'overall_status': overall or 'OK',
            'status_code': status_code or 'OK',
            'status_message': status_message or 'OK',
            'error_code': error_code,
        }

    def fire_triggered_send_soap(self, customer_key, contact_email, contact_key, attributes=None):
        """
        Envoie un email directement via SOAP TriggeredSend.
        Utilise pour les welcome journeys (EmailAudience trigger) qui ne supportent pas fire_api_event.
        """
        print(f"\n[FIRE_TRIGGERED_SOAP] customer_key={customer_key!r} email={contact_email!r} key={contact_key!r}")
        attrs_xml = ''
        for name, value in (attributes or {}).items():
            if value:
                attrs_xml += (
                    '<ns:Attribute>'
                    f'<ns:Name>{name}</ns:Name>'
                    f'<ns:Value>{value}</ns:Value>'
                    '</ns:Attribute>'
                )
        body = (
            '<ns:CreateRequest>'
            '<ns:Objects xsi:type="ns:TriggeredSend">'
            '<ns:TriggeredSendDefinition>'
            f'<ns:CustomerKey>{customer_key}</ns:CustomerKey>'
            '</ns:TriggeredSendDefinition>'
            '<ns:Subscribers>'
            '<ns:Subscriber>'
            f'<ns:EmailAddress>{contact_email}</ns:EmailAddress>'
            f'<ns:SubscriberKey>{contact_key}</ns:SubscriberKey>'
            '<ns:Status>Active</ns:Status>'
            + (f'<ns:Attributes>{attrs_xml}</ns:Attributes>' if attrs_xml else '') +
            '</ns:Subscriber>'
            '</ns:Subscribers>'
            '</ns:Objects>'
            '</ns:CreateRequest>'
        )
        xml_response = self._soap_post('Create', body)
        # Parse result
        root = ET.fromstring(xml_response)
        ns = {'ns': 'http://exacttarget.com/wsdl/partnerAPI'}
        overall = root.findtext('.//ns:OverallStatus', default='', namespaces=ns)
        status_code = root.findtext('.//ns:Results/ns:StatusCode', default='', namespaces=ns)
        status_msg = root.findtext('.//ns:Results/ns:StatusMessage', default='', namespaces=ns)
        print(f"[FIRE_TRIGGERED_SOAP] OverallStatus={overall!r} StatusCode={status_code!r} Msg={status_msg!r}")
        if 'Permission Failed' in (status_msg or ''):
            raise Exception(
                "Permission SFMC manquante pour l'envoi test welcome. "
                "Allez dans Setup > Apps > Installed Packages et ajoutez: "
                "Email > Subscribers: Read + Write"
            )
        if 'no valid subscribers' in (status_msg or '').lower():
            raise Exception(
                "Le subscriber n'existe pas dans SFMC All Subscribers et le package n'a pas "
                "les droits pour le creer (Subscribers: Write manquant). "
                "Ajoutez la permission dans Setup > Apps > Installed Packages."
            )
        if overall not in ('OK', 'HasErrors') and status_code not in ('OK', ''):
            raise Exception(f"SOAP TriggeredSend echoue: {overall} / {status_msg}")
        return {'overall_status': overall, 'status_code': status_code, 'status_message': status_msg}

    def get_triggered_send_customer_key(self, numeric_id):
        """
        Resout un CustomerKey SOAP depuis l'ID numerique d'un TriggeredSendDefinition.
        Les journey activities stockent un triggeredSendKey numerique (ex: 313169),
        mais SOAP TriggeredSend Create a besoin du CustomerKey string (ex: 'S-ET-FR-Welcome').
        """
        print(f"\n[GET_TSD_KEY] Recherche CustomerKey pour ID numerique={numeric_id!r}")
        body = (
            '<ns:RetrieveRequestMsg>'
            '<ns:RetrieveRequest>'
            '<ns:ObjectType>TriggeredSendDefinition</ns:ObjectType>'
            '<ns:Properties>ID</ns:Properties>'
            '<ns:Properties>CustomerKey</ns:Properties>'
            '<ns:Properties>Name</ns:Properties>'
            '<ns:Properties>TriggeredSendStatus</ns:Properties>'
            '<ns:Filter xsi:type="ns:SimpleFilterPart">'
            '<ns:Property>ID</ns:Property>'
            '<ns:SimpleOperator>equals</ns:SimpleOperator>'
            f'<ns:Value>{numeric_id}</ns:Value>'
            '</ns:Filter>'
            '</ns:RetrieveRequest>'
            '</ns:RetrieveRequestMsg>'
        )
        xml_response = self._soap_post('Retrieve', body)
        root = ET.fromstring(xml_response)
        ns = {'ns': 'http://exacttarget.com/wsdl/partnerAPI'}
        customer_key = root.findtext('.//ns:Results/ns:CustomerKey', default='', namespaces=ns)
        name = root.findtext('.//ns:Results/ns:Name', default='', namespaces=ns)
        status = root.findtext('.//ns:Results/ns:TriggeredSendStatus', default='', namespaces=ns)
        print(f"[GET_TSD_KEY] id={numeric_id} -> CustomerKey={customer_key!r} Name={name!r} Status={status!r}")
        return customer_key or None

    def update_triggered_send_status(self, customer_key, status):
        print(f"\n[TRIGGERED_SEND] update_triggered_send_status: key={customer_key!r} -> status={status!r}")
        body = (
            '<ns:UpdateRequest>'
            '<ns:Objects xsi:type="ns:TriggeredSendDefinition">'
            f'<ns:CustomerKey>{customer_key}</ns:CustomerKey>'
            f'<ns:TriggeredSendStatus>{status}</ns:TriggeredSendStatus>'
            '</ns:Objects>'
            '</ns:UpdateRequest>'
        )
        result = self._parse_soap_update_result(self._soap_post('Update', body))
        print(f"[TRIGGERED_SEND] status={status!r} -> OK: {result}")
        return result

    def refresh_triggered_send_content(self, customer_key):
        print(f"\n[TRIGGERED_SEND] refresh_triggered_send_content: key={customer_key!r}")
        body = (
            '<ns:UpdateRequest>'
            '<ns:Objects xsi:type="ns:TriggeredSendDefinition">'
            f'<ns:CustomerKey>{customer_key}</ns:CustomerKey>'
            '<ns:RefreshContent>true</ns:RefreshContent>'
            '</ns:Objects>'
            '</ns:UpdateRequest>'
        )
        result = self._parse_soap_update_result(self._soap_post('Update', body))
        print(f"[TRIGGERED_SEND] RefreshContent -> OK: {result}")
        return result

    def refresh_multistep_triggered_send(self, customer_key):
        logs = []
        print(f"\n{'='*60}")
        print(f"[REFRESH_MULTISTEP] START -- customer_key={customer_key!r}")
        print(f"{'='*60}")

        # ETAPE 1 : Inactive
        print(f"\n[REFRESH_MULTISTEP] ETAPE 1/3 -- Passage en Inactive")
        logs.append(f"TriggeredSendDefinition {customer_key}: passage en Inactive")
        inactive = self.update_triggered_send_status(customer_key, 'Inactive')
        logs.append(f"TriggeredSendDefinition inactive: {inactive}")
        print(f"[REFRESH_MULTISTEP] ETAPE 1 OK -- inactive={inactive}")

        refresh_error = None
        refreshed = None
        try:
            # ETAPE 2 : RefreshContent
            print(f"\n[REFRESH_MULTISTEP] ETAPE 2/3 -- RefreshContent=true")
            logs.append(f"TriggeredSendDefinition {customer_key}: RefreshContent=true")
            refreshed = self.refresh_triggered_send_content(customer_key)
            logs.append(f"TriggeredSendDefinition content rafraichi: {refreshed}")
            print(f"[REFRESH_MULTISTEP] ETAPE 2 OK -- refreshed={refreshed}")
        except Exception as e:
            refresh_error = str(e)
            logs.append(f"RefreshContent echoue: {e}")
            print(f"[REFRESH_MULTISTEP] ETAPE 2 ECHEC -- {e}")
        finally:
            try:
                # ETAPE 3 : Active (toujours)
                print(f"\n[REFRESH_MULTISTEP] ETAPE 3/3 -- Retour en Active (finally)")
                logs.append(f"TriggeredSendDefinition {customer_key}: retour en Active")
                active = self.update_triggered_send_status(customer_key, 'Active')
                logs.append(f"TriggeredSendDefinition active: {active}")
                print(f"[REFRESH_MULTISTEP] ETAPE 3 OK -- active={active}")
            except Exception as e2:
                logs.append(f"CRITIQUE: impossible de remettre en Active: {e2}")
                print(f"[REFRESH_MULTISTEP] ETAPE 3 CRITIQUE ECHEC -- {e2}")

        print(f"\n[REFRESH_MULTISTEP] RESULTAT FINAL")
        print(f"  refresh_error : {refresh_error}")
        print(f"  logs          : {logs}")
        print(f"{'='*60}\n")

        if refresh_error:
            raise Exception(
                f"RefreshContent echoue pour {customer_key} -- "
                f"l'email contient des erreurs de validation SFMC. "
                f"Corrigez l'email dans Content Builder puis relancez. "
                f"Detail: {refresh_error}"
            )

        return {
            'customer_key': customer_key,
            'steps': logs,
            'inactive': inactive,
            'refresh': refreshed,
            'active': active,
        }

    def _iter_activity_nodes(self, node):
        """Traverse recursively a journey tree and yield activity-like dicts."""
        if isinstance(node, list):
            for item in node:
                yield from self._iter_activity_nodes(item)
            return

        if not isinstance(node, dict):
            return

        if 'type' in node or 'configurationArguments' in node or 'arguments' in node:
            yield node

        nested_keys = [
            'activities',
            'children',
            'branches',
            'outcomes',
            'results',
            'nodes',
            'steps',
            'workflowApiObjects',
        ]
        for key in nested_keys:
            if key in node:
                yield from self._iter_activity_nodes(node.get(key))

        for key in ['configurationArguments', 'arguments', 'metaData']:
            nested = node.get(key)
            if isinstance(nested, (dict, list)):
                yield from self._iter_activity_nodes(nested)

    def _extract_activity_asset_id(self, activity):
        """Support multiple journey payload shapes, including welcome multi-step."""
        candidates = []

        def collect(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    lk = str(k).lower()
                    if lk in {
                        'emailid',
                        'assetid',
                        'legacyemailid',
                        'legacyid',
                        'contentbuilderassetid',
                        'usercontentbuilderassetid',
                    } and v not in (None, '', 0):
                        candidates.append(v)
                    elif isinstance(v, (dict, list)):
                        collect(v)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(activity.get('configurationArguments', {}))
        collect(activity.get('arguments', {}))
        collect(activity.get('metaData', {}))
        collect(activity)

        ordered_candidates = []
        seen = set()
        for candidate in candidates:
            if candidate in (None, '', 0):
                continue
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            ordered_candidates.append(candidate)

        for candidate in ordered_candidates:
            try:
                asset = self.get_asset_by_id(candidate)
            except Exception:
                continue

            if (
                ('views' in asset and 'html' in asset.get('views', {})) or
                asset.get('data', {}).get('email', {}).get('htmlBody') or
                asset.get('assetType', {}).get('name') in ('htmlemail', 'templatebasedemail', 'textonlyemail')
            ):
                return candidate

        for candidate in ordered_candidates:
            return candidate
        return None

    def _looks_like_email_asset(self, asset):
        if not isinstance(asset, dict):
            return False
        return bool(
            ('views' in asset and 'html' in asset.get('views', {})) or
            asset.get('data', {}).get('email', {}).get('htmlBody') or
            asset.get('assetType', {}).get('name') in ('htmlemail', 'templatebasedemail', 'textonlyemail')
        )

    # =====================
    # JOURNEYS
    # =====================

    def get_journeys(self, page_size=200, status_filter=None):
        url = f"{self.base_url}/interaction/v1/interactions"
        params = {
            "$pageSize": page_size,
            "$orderBy": "modifiedDate desc"
        }
        if status_filter:
            params["status"] = status_filter

        response = requests.get(url, headers=self.auth.get_headers(), params=params, timeout=30, verify=False)
        response.raise_for_status()
        return response.json()

    def get_all_journeys(self, exclude_stopped=True, use_cache=True):
        """Récupère toutes les journeys avec pagination"""
        cache_key = f"all_journeys_exclude_{exclude_stopped}"

        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                print(f"  [CACHE HIT] Returning {len(cached)} cached journeys")
                return {'items': cached, 'count': len(cached), 'from_cache': True}

        all_items = []
        page = 1
        page_size = 200

        while True:
            url = f"{self.base_url}/interaction/v1/interactions"
            params = {
                "$pageSize": page_size,
                "$page": page,
                "$orderBy": "modifiedDate desc"
            }
            response = requests.get(url, headers=self.auth.get_headers(), params=params, timeout=30, verify=False)
            response.raise_for_status()
            data = response.json()

            items = data.get('items', [])
            if exclude_stopped:
                items = [j for j in items if j.get('status') != 'Stopped']
            all_items.extend(items)

            if len(data.get('items', [])) < page_size:
                break
            page += 1

        self.cache.set(cache_key, all_items)

        return {'items': all_items, 'count': len(all_items), 'from_cache': False}

    def get_journeys_paginated(self, page=1, page_size=50, journey_type='all', exclude_stopped=True):
        """Pagination pour l'UI"""
        data = self.get_all_journeys(exclude_stopped=exclude_stopped, use_cache=True)
        all_journeys = data.get('items', [])

        if journey_type == 'transactional':
            filtered = [j for j in all_journeys if j.get('definitionType') == 'Transactional']
        elif journey_type == 'welcome':
            filtered = [j for j in all_journeys if 'welcome' in j.get('name', '').lower()]
        else:
            filtered = all_journeys

        total = len(filtered)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_items = filtered[start_idx:end_idx]

        has_more = end_idx < total

        return {
            'items': page_items,
            'total': total,
            'page': page,
            'page_size': page_size,
            'has_more': has_more,
            'from_cache': data.get('from_cache', False)
        }

    def invalidate_cache(self):
        self.cache.clear()
        print("  [CACHE] Cleared")

    def build_event_test_data(self, contact_key, contact_email, extra_data=None, schema=None):
        """Build a permissive event payload for welcome journeys with required entry fields."""
        data = dict(extra_data or {})

        local_part = (contact_email or '').split('@', 1)[0].strip() or 'Test'
        first_name = (
            data.get('firstname') or
            data.get('firstName') or
            data.get('FirstName') or
            local_part
        )

        # Common aliases seen in SFMC event schemas.
        defaults = {
            'EmailAddress': contact_email,
            'emailAddress': contact_email,
            'emailaddress': contact_email,
            'ContactKey': contact_key,
            'contactKey': contact_key,
            'contactkey': contact_key,
            'FirstName': first_name,
            'firstName': first_name,
            'firstname': first_name,
        }

        for key, value in defaults.items():
            if value not in (None, '') and key not in data:
                data[key] = value

        # If the schema exposes other required names, fill obvious equivalents when missing.
        for field in schema or []:
            if isinstance(field, dict):
                name = field.get('name') or field.get('key')
            else:
                name = str(field)
            if not name or name in data:
                continue

            lowered = name.lower()
            if 'email' in lowered:
                data[name] = contact_email
            elif lowered in {'contactkey', 'contact_key'}:
                data[name] = contact_key
            elif lowered in {'firstname', 'first_name'}:
                data[name] = first_name

        return data

    def get_journey_event_key(self, journey_id):
        """
        Récupère la clé d'envoi de la journey.
        Supporte: API Event (eventDefinitionKey) et Transactional API (definitionKey).
        Retourne un dict avec 'send_type': 'api_event' ou 'transactional'.
        """
        journey = self.get_journey_by_id(journey_id)
        definition_type = journey.get('definitionType', '')
        triggers = journey.get('triggers', [])
        for trigger in triggers:
            cfg = trigger.get('configurationArguments', {})
            meta = trigger.get('metaData', {})
            args = trigger.get('arguments', {})
            trigger_type = trigger.get('type', '')

            # Pour les journeys Transactional, on cherche directement definitionKey
            # (ignorer eventDefinitionKey qui causerait fire_api_event avec un UUID inexistant)
            if definition_type != 'Transactional':
                # Cherche eventDefinitionKey partout (tous types de triggers)
                event_key = (
                    cfg.get('eventDefinitionKey') or cfg.get('EventDefinitionKey') or
                    meta.get('eventDefinitionKey') or meta.get('EventDefinitionKey') or
                    args.get('eventDefinitionKey') or args.get('EventDefinitionKey') or
                    trigger.get('eventDefinitionKey') or trigger.get('EventDefinitionKey')
                )
                if event_key:
                    return {
                        'event_definition_key': event_key,
                        'send_type': 'api_event',
                        'trigger_type': trigger_type,
                        'journey_name': journey.get('name'),
                        'journey_key': journey.get('key'),
                        'version': journey.get('version'),
                        'status': journey.get('status'),
                        'schema': cfg.get('schema', [])
                    }

            definition_key = (
                cfg.get('definitionKey') or cfg.get('DefinitionKey') or
                meta.get('definitionKey') or meta.get('DefinitionKey') or
                args.get('definitionKey') or args.get('DefinitionKey') or
                trigger.get('definitionKey') or trigger.get('DefinitionKey')
            )
            if definition_key:
                return {
                    'event_definition_key': definition_key,
                    'send_type': 'transactional',
                    'trigger_type': trigger_type,
                    'journey_name': journey.get('name'),
                    'journey_key': journey.get('key'),
                    'version': journey.get('version'),
                    'status': journey.get('status'),
                    'schema': cfg.get('schema', [])
                }

        send_definitions = self.get_journey_send_definitions(journey)
        if send_definitions:
            # Pour Transactional: prendre la première clé non-numérique (string definitionKey)
            # pour éviter d'utiliser un triggeredSendKey numérique invalide pour l'API REST
            if definition_type == 'Transactional':
                first = next(
                    (s for s in send_definitions if not str(s['definition_key']).isdigit()),
                    send_definitions[0]
                )
            else:
                first = send_definitions[0]
            return {
                'event_definition_key': first['definition_key'],
                'send_type': 'transactional',
                'trigger_type': 'transactional-api',
                'journey_name': journey.get('name'),
                'journey_key': journey.get('key'),
                'version': journey.get('version'),
                'status': journey.get('status'),
                'schema': first.get('schema', []),
                'activity_name': first.get('activity_name'),
                'activity_id': first.get('activity_id')
            }

        return None

    def get_journey_send_definitions(self, journey_or_id, activity_ids=None):
        """Find triggered send / send definition keys in activities, including nested multistep branches."""
        journey = journey_or_id if isinstance(journey_or_id, dict) else self.get_journey_by_id(journey_or_id)
        definitions = []
        seen = set()
        selected_activity_ids = {str(x) for x in (activity_ids or []) if x not in (None, '')}

        for activity in self._iter_activity_nodes(journey.get('activities', [])):
            activity_id = activity.get('id')
            if selected_activity_ids and str(activity_id) not in selected_activity_ids:
                continue
            cfg = activity.get('configurationArguments', {})
            triggered_send = cfg.get('triggeredSend', {})
            candidates = [
                cfg.get('triggeredSendKey'),
                cfg.get('definitionKey'),
                triggered_send.get('triggeredSendKey'),
                triggered_send.get('definitionKey'),
            ]

            for candidate in candidates:
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                definitions.append({
                    'definition_key': candidate,
                    'activity_name': activity.get('name'),
                    'activity_id': activity_id,
                    'schema': cfg.get('schema', []),
                })

        return definitions

    def fire_transactional_email(self, definition_key, contact_key, contact_email, extra_data=None):
        """
        Envoie un email via l'API Transactional Messaging SFMC.
        Utilisé pour les journeys de type 'transactional-api'.
        """
        print(f"\n{'='*60}")
        print(f"[FIRE_TRANS] START")
        print(f"[FIRE_TRANS] definition_key={definition_key!r}")
        print(f"[FIRE_TRANS] contact_key={contact_key!r}")
        print(f"[FIRE_TRANS] contact_email={contact_email!r}")
        print(f"[FIRE_TRANS] extra_data={extra_data}")

        # Vérifie que la send definition existe et est Active, et pré-inscrit le contact dans la DE d'abonnement
        try:
            defn_check = requests.get(
                f"{self.base_url}/messaging/v1/email/definitions/{definition_key}",
                headers=self.auth.get_headers(), verify=False, timeout=30
            )
            if defn_check.ok:
                defn_json = defn_check.json()
                defn_status = defn_json.get('status', 'Unknown')
                print(f"[FIRE_TRANS] definition status={defn_status!r}")
                if defn_status.lower() != 'active':
                    raise Exception(
                        f"Send definition '{definition_key}' est en status '{defn_status}' (pas Active) — "
                        f"activez-la dans SFMC avant d'envoyer."
                    )
                # Pré-inscrit le contact dans AllSubscribers via SOAP pour éviter MissingRequiredFields
                # (autoAddSubscriber échoue si la DE d'abonnement a des champs requis comme insert_date)
                try:
                    sub_body = (
                        '<ns:UpdateRequestMsg><ns:Options><ns:SaveOptions><ns:SaveOption>'
                        '<ns:PropertyName>*</ns:PropertyName><ns:SaveAction>UpdateAdd</ns:SaveAction>'
                        '</ns:SaveOption></ns:SaveOptions></ns:Options>'
                        '<ns:Objects xsi:type="ns:Subscriber">'
                        f'<ns:EmailAddress>{contact_email}</ns:EmailAddress>'
                        f'<ns:SubscriberKey>{contact_key}</ns:SubscriberKey>'
                        '<ns:Status>Active</ns:Status>'
                        '</ns:Objects>'
                        '</ns:UpdateRequestMsg>'
                    )
                    sub_resp = self._soap_post('Update', sub_body)
                    print(f"[FIRE_TRANS] Subscriber pre-add OK")
                    time.sleep(2)  # Laisse SFMC propager le subscriber avant l'envoi
                except Exception as sub_err:
                    print(f"[FIRE_TRANS] WARNING subscriber pre-add: {sub_err}")
            else:
                print(f"[FIRE_TRANS] WARNING: impossible de vérifier la definition ({defn_check.status_code})")
        except Exception as defn_err:
            if 'pas Active' in str(defn_err):
                raise
            print(f"[FIRE_TRANS] WARNING check definition: {defn_err}")

        # Garantit que EmailAddress et SubscriberKey sont toujours dans les attributes
        attrs = dict(extra_data or {})
        attrs.setdefault('EmailAddress', contact_email)
        attrs.setdefault('SubscriberKey', contact_key)
        for k, v in self._get_transactional_default_attributes(definition_key).items():
            attrs.setdefault(k, v)
        print(f"[FIRE_TRANS] attrs final={attrs}")

        bulk_url = f"{self.base_url}/messaging/v1/email/messages/"
        bulk_payload = {
            "definitionKey": definition_key,
            "recipients": [{
                "contactKey": contact_key,
                "to": contact_email,
                "attributes": attrs
            }]
        }
        print(f"[FIRE_TRANS] BULK POST {bulk_url}")
        print(f"[FIRE_TRANS] BULK payload={bulk_payload}")
        response = self._request_with_auth_retry(
            'POST',
            bulk_url,
            headers=self.auth.get_headers(),
            json=bulk_payload,
            timeout=30,
            verify=False
        )
        print(f"[FIRE_TRANS] BULK HTTP={response.status_code}")
        print(f"[FIRE_TRANS] BULK response={response.text}")
        if response.ok:
            resp_json = response.json()
            # SFMC returns 202 even when individual send fails — check responses[0].status
            responses = resp_json.get('responses', [])
            message_key = None
            if responses:
                first = responses[0]
                message_key = first.get('messageKey')
                status = first.get('status', '')
                print(f"[FIRE_TRANS] BULK responses[0].status={status!r}")
                if status and status.lower() not in ('ok', 'accepted', 'success', 'queued'):
                    err_code = first.get('errorCode') or first.get('messageKey', '')
                    err_msg = first.get('message') or first.get('errorMessage') or status
                    raise Exception(
                        f"SFMC transactional send refusé (HTTP 202 mais status={status!r}): "
                        f"errorCode={err_code}, message={err_msg}"
                    )
            if message_key:
                disposition = self.wait_for_transactional_message_disposition(message_key, timeout=20)
                resp_json['messageKey'] = message_key
                resp_json['disposition'] = disposition
            else:
                print(f"[FIRE_TRANS] WARNING: aucun messageKey retourne, statut final non verifiable")
            print(f"[FIRE_TRANS] BULK OK")
            print(f"{'='*60}\n")
            return resp_json

        print(f"[FIRE_TRANS] BULK échoué -> tentative SINGLE")
        message_key = f"{contact_key}-{uuid.uuid4().hex[:12]}"
        single_url = f"{self.base_url}/messaging/v1/email/messages/{message_key}"
        single_payload = {
            "definitionKey": definition_key,
            "recipient": {
                "contactKey": contact_key,
                "to": contact_email,
                "attributes": extra_data or {}
            }
        }
        print(f"[FIRE_TRANS] SINGLE POST {single_url}")
        print(f"[FIRE_TRANS] SINGLE payload={single_payload}")
        retry = self._request_with_auth_retry(
            'POST',
            single_url,
            headers=self.auth.get_headers(),
            json=single_payload,
            timeout=30,
            verify=False
        )
        print(f"[FIRE_TRANS] SINGLE HTTP={retry.status_code}")
        print(f"[FIRE_TRANS] SINGLE response={retry.text}")
        if retry.ok:
            print(f"[FIRE_TRANS] SINGLE OK")
            print(f"{'='*60}\n")
            return retry.json()

        print(f"[FIRE_TRANS] ECHEC TOTAL")
        print(f"{'='*60}\n")
        raise Exception(
            "Transactional send failed. "
            f"bulk_status={response.status_code} bulk_body={response.text} | "
            f"single_status={retry.status_code} single_body={retry.text}"
        )

    def get_transactional_message_status(self, message_key):
        """
        Lit le statut d'un message transactionnel email via son messageKey.
        """
        url = f"{self.base_url}/messaging/v1/email/messages/{message_key}"
        print(f"[TRANS_STATUS] GET {url}")
        response = self._request_with_auth_retry(
            'GET',
            url,
            headers=self.auth.get_headers(),
            timeout=30,
            verify=False
        )
        print(f"[TRANS_STATUS] HTTP={response.status_code}")
        print(f"[TRANS_STATUS] response={response.text}")
        response.raise_for_status()
        return response.json()

    def wait_for_transactional_message_disposition(self, message_key, timeout=20, poll_interval=2):
        """
        Distingue un message accepte d'un message reellement envoye ou non envoye.
        """
        deadline = time.time() + timeout
        last_status = None

        while time.time() < deadline:
            status_json = self.get_transactional_message_status(message_key)
            event_type = (
                status_json.get('eventCategoryType') or
                status_json.get('eventType') or
                status_json.get('status')
            )
            normalized = (event_type or '').lower()
            if event_type:
                last_status = event_type

            if normalized.endswith('emailnotsent') or normalized == 'emailnotsent':
                reason = (
                    status_json.get('statusMessage') or
                    status_json.get('message') or
                    status_json.get('info')
                )
                raise Exception(
                    f"SFMC a accepte la requete mais n'a pas envoye l'email. "
                    f"messageKey={message_key}, status={event_type}, reason={reason or 'non precise'}"
                )

            if normalized.endswith('emailsent') or normalized == 'emailsent':
                print(f"[TRANS_STATUS] message envoye: messageKey={message_key}")
                return status_json

            time.sleep(poll_interval)

        print(f"[TRANS_STATUS] timeout attente disposition finale, dernier statut={last_status!r}")
        return {
            'messageKey': message_key,
            'status': last_status or 'Pending',
            'pending': True
        }

    def _normalize_de_row(self, raw_values, contact_email, contact_key):
        """
        Normalise les clés d'une ligne DE pour l'API Transactional Messaging.
        - Remplace emailaddress -> EmailAddress (casing requis par SFMC)
        - Remplace subscriberkey -> SubscriberKey
        - Injecte les valeurs du contact de test
        - Retire les valeurs nulles
        """
        normalized = {}
        for k, v in raw_values.items():
            lk = k.lower()
            if 'email' in lk:
                normalized['EmailAddress'] = contact_email
            elif lk in ('subscriberkey', 'subscriber_key', 'contactkey', 'contact_key'):
                normalized['SubscriberKey'] = contact_key
            elif v not in (None, ''):
                normalized[k] = v
        # Garantit que EmailAddress et SubscriberKey sont toujours présents
        normalized['EmailAddress'] = contact_email
        normalized['SubscriberKey'] = contact_key
        return {k: v for k, v in normalized.items() if v not in (None, '')}

    def get_send_def_attributes(self, definition_key, contact_email, contact_key):
        """
        Récupère les attributs requis depuis la DE de la Send Definition.
        1. Cherche une ligne existante avec cet email dans la DE
        2. Si non trouvée, prend n'importe quelle ligne comme template et remplace email/key
        Retourne un dict d'attributs prêt à passer dans fire_transactional_email.
        """
        try:
            defn_raw = requests.get(
                f"{self.base_url}/messaging/v1/email/definitions/{definition_key}",
                headers=self.auth.get_headers(), verify=False, timeout=30
            )
            defn_raw.raise_for_status()
            defn = defn_raw.json()
            de_key = defn.get('subscriptions', {}).get('dataExtension')
            if not de_key:
                print(f"[SEND_DEF_ATTRS] Pas de dataExtension dans la send def")
                return {}

            print(f"[SEND_DEF_ATTRS] DE key={de_key!r} recherche ligne pour {contact_email!r}")

            # Cherche ligne existante pour cet email
            r = requests.get(
                f"{self.base_url}/data/v1/customobjectdata/key/{de_key}/rowset",
                headers=self.auth.get_headers(), verify=False, timeout=30,
                params={'$filter': f"emailaddress eq '{contact_email}'"}
            )
            if r.ok:
                items = r.json().get('items', [])
                if items:
                    vals = self._normalize_de_row(items[0].get('values', {}), contact_email, contact_key)
                    print(f"[SEND_DEF_ATTRS] Ligne trouvee pour l'email: {vals}")
                    return vals

            # Pas de ligne pour cet email -> template depuis premiere ligne
            print(f"[SEND_DEF_ATTRS] Pas de ligne pour {contact_email!r} -> template premiere ligne")
            r2 = requests.get(
                f"{self.base_url}/data/v1/customobjectdata/key/{de_key}/rowset",
                headers=self.auth.get_headers(), verify=False, timeout=30,
                params={'pageSize': 1}
            )
            if r2.ok:
                items = r2.json().get('items', [])
                if items:
                    vals = self._normalize_de_row(items[0].get('values', {}), contact_email, contact_key)
                    print(f"[SEND_DEF_ATTRS] Template construit: {vals}")
                    return vals
        except Exception as e:
            print(f"[SEND_DEF_ATTRS] Erreur: {e}")
        return {}

    def get_email_send_definition(self, definition_key):
        url = f"{self.base_url}/messaging/v1/email/definitions/{definition_key}"
        print(f"\n[SEND_DEF] GET {url}")
        response = requests.get(url, headers=self.auth.get_headers(), timeout=30, verify=False)
        if not response.ok:
            raise Exception(
                f"SFMC GET definition failed [{response.status_code}]: {response.text}"
            )
        return response.json()

    def refresh_transactional_send_definition(self, definition_key):
        """
        GET + PATCH la send definition transactionnelle avec les mêmes données.
        Le PATCH (même sans modification) déclenche l'invalidation du cache SFMC :
        au prochain envoi, SFMC relit l'asset depuis Content Builder et recompile.
        La journey reste Running pendant toute l'opération.
        """
        print(f"\n[REFRESH_TRANS_DEF] START -- definition_key={definition_key!r}")

        # Étape 1 : GET
        definition = self.get_email_send_definition(definition_key)
        print(f"[REFRESH_TRANS_DEF] GET OK -- name={definition.get('name')!r} status={definition.get('status')!r}")

        # Étape 2 : PATCH avec les mêmes données (force invalidation du cache SFMC)
        # Attention : SFMC rejette certains champs du GET dans le PATCH (ex: isSendLogging, isReconcilable)
        raw_options = definition.get("options", {})
        safe_options = {k: v for k, v in raw_options.items() if k in ("trackLinks", "cc", "bcc")}
        payload = {
            "name": definition.get("name"),
            "description": definition.get("description", ""),
            "status": definition.get("status"),
            "content": definition.get("content", {}),
            "subscriptions": definition.get("subscriptions", {}),
            "options": safe_options
        }
        url = f"{self.base_url}/messaging/v1/email/definitions/{definition_key}"
        print(f"[REFRESH_TRANS_DEF] PATCH {url}")
        print(f"[REFRESH_TRANS_DEF] PATCH payload={payload}")
        response = self._request_with_auth_retry(
            'PATCH',
            url,
            headers=self.auth.get_headers(),
            json=payload,
            timeout=30,
            verify=False
        )
        print(f"[REFRESH_TRANS_DEF] PATCH HTTP={response.status_code}")
        print(f"[REFRESH_TRANS_DEF] PATCH response={response.text}")
        response.raise_for_status()
        result = response.json()
        print(f"[REFRESH_TRANS_DEF] OK -- cache invalide, Content Builder sera relu au prochain envoi")
        return result

    def fire_api_event(self, event_definition_key, contact_key, contact_email, extra_data=None):
        """
        Déclenche une entrée dans une journey via API Event.
        Utilisé pour tester que la journey envoie bien le nouvel email.
        """
        print(f"\n{'='*60}")
        print(f"[FIRE_API_EVENT] START")
        print(f"[FIRE_API_EVENT] event_definition_key={event_definition_key!r}")
        print(f"[FIRE_API_EVENT] contact_key={contact_key!r}")
        print(f"[FIRE_API_EVENT] contact_email={contact_email!r}")
        print(f"[FIRE_API_EVENT] extra_data={extra_data}")

        url = f"{self.base_url}/interaction/v1/events"
        data_payload = self.build_event_test_data(contact_key, contact_email, extra_data)
        payload = {
            "ContactKey": contact_key,
            "EventDefinitionKey": event_definition_key,
            "Data": data_payload
        }
        print(f"[FIRE_API_EVENT] POST {url}")
        print(f"[FIRE_API_EVENT] payload={payload}")

        response = self._request_with_auth_retry(
            'POST',
            url,
            headers=self.auth.get_headers(),
            json=payload,
            timeout=30,
            verify=False
        )
        print(f"[FIRE_API_EVENT] HTTP={response.status_code}")
        print(f"[FIRE_API_EVENT] response={response.text}")

        if not response.ok:
            print(f"[FIRE_API_EVENT] ECHEC")
            print(f"{'='*60}\n")
            raise Exception(f"SFMC {response.status_code}: {response.text}")

        result = response.json()
        print(f"[FIRE_API_EVENT] OK -- {result}")
        print(f"{'='*60}\n")
        return result

    def get_journey_by_id(self, journey_id):
        url = f"{self.base_url}/interaction/v1/interactions/{journey_id}"
        response = self._request_with_auth_retry(
            'GET',
            url,
            headers=self.auth.get_headers(),
            timeout=30,
            verify=False
        )
        response.raise_for_status()
        return response.json()

    def get_journey_activities(self, journey_id):
        journey = self.get_journey_by_id(journey_id)
        email_activities = []
        seen = set()
        transactional_assets = {}

        if journey.get('definitionType') == 'Transactional':
            for send_def in self.get_journey_send_definitions(journey):
                definition_key = send_def.get('definition_key')
                activity_id = send_def.get('activity_id')
                if not definition_key or not activity_id or str(definition_key).isdigit():
                    continue
                try:
                    definition = self.get_email_send_definition(definition_key)
                    customer_key = definition.get('content', {}).get('customerKey')
                    if not customer_key:
                        continue
                    for asset_type in ('htmlemail', 'templatebasedemail', 'textonlyemail'):
                        asset = self.get_asset_by_customer_key(customer_key, asset_type=asset_type)
                        if asset:
                            transactional_assets[str(activity_id)] = asset.get('id')
                            break
                except Exception:
                    continue

        for activity in self._iter_activity_nodes(journey.get('activities', [])):
            activity_type = str(activity.get('type', '')).upper()
            if activity_type not in ['EMAILV2', 'EMAIL', 'EMAILSEND']:
                continue

            activity_id = activity.get('id')
            activity_key = activity.get('key')
            dedupe_key = activity_id or activity_key or repr(activity)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            asset_id = transactional_assets.get(str(activity_id)) or self._extract_activity_asset_id(activity)

            # Some welcome journeys expose image asset IDs in activity payloads.
            # When that happens, fall back to the Content Builder email with the
            # same name as the activity if it exists.
            if not transactional_assets.get(str(activity_id)):
                try:
                    asset = self.get_asset_by_id(asset_id) if asset_id else None
                except Exception:
                    asset = None

                if not self._looks_like_email_asset(asset):
                    activity_name = activity.get('name')
                    if activity_name:
                        for asset_type in ('htmlemail', 'templatebasedemail', 'textonlyemail'):
                            try:
                                matches = self.search_assets(name_filter=activity_name, asset_type=asset_type).get('items', [])
                            except Exception:
                                continue
                            exact = next((x for x in matches if x.get('name') == activity_name), None)
                            if exact:
                                asset_id = exact.get('id')
                                break

            email_activities.append({
                'activity_id': activity_id,
                'activity_key': activity_key,
                'name': activity.get('name'),
                'config_args': activity.get('configurationArguments', {}),
                'type': activity.get('type'),
                'asset_id': asset_id
            })

        return email_activities, journey

    def update_journey(self, journey_id, journey_data):
        url = f"{self.base_url}/interaction/v1/interactions/{journey_id}"
        response = requests.put(url, headers=self.auth.get_headers(), json=journey_data, timeout=30, verify=False)
        response.raise_for_status()
        return response.json()

    def create_journey_version(self, journey_id):
        url = f"{self.base_url}/interaction/v1/interactions/{journey_id}/newVersion"
        response = requests.post(url, headers=self.auth.get_headers(), timeout=30, verify=False)
        response.raise_for_status()
        return response.json()

    def stop_journey(self, journey_id, version_number):
        url = f"{self.base_url}/interaction/v1/interactions/stop/{journey_id}?versionNumber={version_number}"
        response = requests.post(url, headers=self.auth.get_headers(), timeout=60, verify=False)
        response.raise_for_status()
        return response.json()

    def publish_journey(self, journey_id, version_number=1):
        url = f"{self.base_url}/interaction/v1/interactions/publishAsync/{journey_id}?versionNumber={version_number}"
        response = requests.post(url, headers=self.auth.get_headers(), timeout=60, verify=False)
        response.raise_for_status()
        return response.json()

    def _raise_refresh_permission_error(self, operation, journey, logs, err):
        status_code = getattr(getattr(err, 'response', None), 'status_code', None)
        journey_name = journey.get('name', journey.get('id', 'journey'))
        definition_type = journey.get('definitionType', 'unknown')

        if status_code == 403:
            logs.append(f"{operation} refuse: permissions insuffisantes (HTTP 403)")
            error = Exception(
                f"Refresh API refuse pour la journey '{journey_name}' ({definition_type}). "
                "SFMC bloque l'action de publication/arret pour ce package ou cet utilisateur. "
                "Il faut republier manuellement dans Journey Builder, ou corriger les permissions Journey Builder cote SFMC. "
                f"Logs: {' | '.join(logs)}"
            )
            setattr(error, 'manual_required', True)
            setattr(error, 'journey_name', journey_name)
            setattr(error, 'journey_type', definition_type)
            setattr(error, 'logs', logs)
            raise error

        raise Exception(f"Refresh impossible. Logs: {' | '.join(logs)}")

    def wait_for_status(self, journey_id, target_statuses, timeout=90):
        """Poll jusqu'à ce que la journey atteigne un statut cible."""
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(4)
            j = self.get_journey_by_id(journey_id)
            s = j.get('status', '')
            print(f"  [POLL] Status actuel: {s}")
            if s in target_statuses:
                return s
        raise Exception(f"Timeout: journey pas en {target_statuses} après {timeout}s")

    def refresh_journey(self, journey_id, activity_ids=None):
        """
        Recharge le contenu email compilé dans SFMC.
        SFMC compile l'email au moment de la publication -- il faut Stop + Republish
        pour que les modifications Content Builder soient prises en compte par la journey.
        """
        print(f"\n{'#'*70}")
        print(f"[REFRESH_JOURNEY] START -- journey_id={journey_id!r}")
        print(f"{'#'*70}")

        journey = self.get_journey_by_id(journey_id)
        status = journey.get('status', '')
        version = journey.get('version', 1)
        journey_key = journey.get('key', journey_id)
        definition_type = journey.get('definitionType', '')
        logs = []
        event_info = self.get_journey_event_key(journey_id)

        print(f"[REFRESH_JOURNEY] name={journey.get('name')!r}")
        print(f"[REFRESH_JOURNEY] status={status!r} | version={version!r} | definitionType={definition_type!r}")
        print(f"[REFRESH_JOURNEY] journey_key={journey_key!r}")
        print(f"[REFRESH_JOURNEY] event_info={event_info}")
        if activity_ids:
            print(f"[REFRESH_JOURNEY] activity_ids cibles={activity_ids}")

        logs.append(f"Status: {status}, Version: {version}")
        logs.append(f"DefinitionType: {definition_type or 'unknown'}")
        if activity_ids:
            logs.append(f"Filtre activity_ids: {activity_ids}")

        send_definitions = self.get_journey_send_definitions(journey, activity_ids=activity_ids)
        print(f"[REFRESH_JOURNEY] send_definitions trouvees ({len(send_definitions)}) : {send_definitions}")
        logs.append(f"Send definitions trouvees: {len(send_definitions)} -- {[s['definition_key'] for s in send_definitions]}")

        if send_definitions:
            refreshed = []
            if definition_type == 'Transactional':
                print(f"[REFRESH_JOURNEY] -> Méthode: TRANSACTIONAL PATCH REST")
                # Filtrer les clés numériques (triggeredSendKey) invalides pour l'API REST
                trans_defs = [s for s in send_definitions if not str(s['definition_key']).isdigit()]
                if not trans_defs:
                    trans_defs = send_definitions
                for send_def in trans_defs:
                    definition_key = send_def['definition_key']
                    print(f"[REFRESH_JOURNEY] Traitement send_def: key={definition_key!r} activity={send_def.get('activity_name')!r}")
                    logs.append(f"Send definition detectee: {definition_key}")
                    result = self.refresh_transactional_send_definition(definition_key)
                    refreshed.append({
                        'definition_key': definition_key,
                        'activity_name': send_def.get('activity_name'),
                        'result': result
                    })
                logs.append(f"{len(refreshed)} send definition(s) rafraichie(s) via Transactional Messaging API")
                print(f"[REFRESH_JOURNEY] TRANSACTIONAL OK -- {len(refreshed)} definitions patchees")
                return {
                    'method': 'refresh_definition',
                    'logs': logs,
                    'result': refreshed,
                    'definition_keys': [x['definition_key'] for x in refreshed]
                }

            print(f"[REFRESH_JOURNEY] -> Méthode: MULTISTEP SOAP")
            for send_def in send_definitions:
                definition_key = send_def['definition_key']
                print(f"[REFRESH_JOURNEY] Traitement triggered_send: key={definition_key!r} activity={send_def.get('activity_name')!r}")
                logs.append(f"Triggered send detecte: {definition_key}")
                result = self.refresh_multistep_triggered_send(definition_key)
                logs.extend(result['steps'])
                refreshed.append({
                    'definition_key': definition_key,
                    'activity_name': send_def.get('activity_name'),
                    'result': result
                })
            logs.append(f"{len(refreshed)} triggered send(s) rafraichi(s) via SOAP TriggeredSendDefinition")
            print(f"[REFRESH_JOURNEY] MULTISTEP OK -- {len(refreshed)} triggered sends rafraichis")
            return {
                'method': 'refresh_triggered_send_definition',
                'logs': logs,
                'result': refreshed,
                'definition_keys': [x['definition_key'] for x in refreshed]
            }

        print(f"[REFRESH_JOURNEY] Aucune send_definition trouvee -- fallback sur event_info / status")
        if activity_ids:
            raise Exception(
                "Impossible de cibler uniquement les emails selectionnes: aucune send definition associee aux activity_ids fournis."
            )

        if event_info and event_info.get('send_type') == 'transactional':
            definition_key = event_info['event_definition_key']
            print(f"[REFRESH_JOURNEY] -> event_info transactionnel détecté: key={definition_key!r}")
            logs.append(f"Transactional definition: {definition_key}")
            self.refresh_transactional_send_definition(definition_key)
            logs.append("Send definition verifiee - contenu live depuis Content Builder")
            # Fall through to Stop+Republish to reload the journey

        print(f"[REFRESH_JOURNEY] -> Pas d'event transactionnel. status={status!r} definitionType={definition_type!r}")

        if status == 'Draft':
            print(f"[REFRESH_JOURNEY] -> Méthode: SAVE DRAFT")
            result = self.update_journey(journey_id, journey)
            logs.append("Draft sauvegarde")
            return {'method': 'save_draft', 'logs': logs, 'result': result}

        elif status in ['Running', 'Published', 'Scheduled']:
            if definition_type != 'Transactional':
                print(f"[REFRESH_JOURNEY] -> Méthode: REPUBLICATION DIRECTE")
                try:
                    logs.append(f"Republication directe version {version}...")
                    result = self.publish_journey(journey_id, version)
                    logs.append("Republication lancee - email recompile depuis Content Builder")
                    return {'method': 'republish_current', 'logs': logs, 'result': result}
                except Exception as e:
                    logs.append(f"Republication directe echouee: {str(e).encode('ascii', errors='replace').decode()}")
                    self._raise_refresh_permission_error("Republication directe", journey, logs, e)
            # ── Méthode 1 : Stop + Republish (même version) ──────────────────────
            print(f"[REFRESH_JOURNEY] -> Méthode: STOP + REPUBLISH")
            try:
                logs.append(f"Arret version {version}...")
                self.stop_journey(journey_id, version)
                stopped_status = self.wait_for_status(journey_id, ['Stopped', 'Draft'], timeout=90)
                logs.append(f"Journey arretee (status: {stopped_status})")
                logs.append(f"Republication version {version}...")
                result = self.publish_journey(journey_id, version)
                logs.append("Republication lancee - email recompile depuis Content Builder")
                return {'method': 'stop_republish', 'logs': logs, 'result': result}

            except Exception as e:
                logs.append(f"Stop/Republish echoue: {str(e).encode('ascii', errors='replace').decode()}")

                try:
                    logs.append("Fallback: creation nouvelle version...")
                    new_v = self.create_journey_version(journey_id)
                    new_num = new_v.get('version', version + 1)
                    new_id = new_v.get('id', journey_id)
                    logs.append(f"Nouvelle version creee: v{new_num} (id: {new_id})")
                    result = self.publish_journey(new_id, new_num)
                    logs.append(f"Version {new_num} publiee - email recompile")
                    return {'method': 'new_version', 'logs': logs, 'result': result}

                except Exception as e2:
                    logs.append(f"Fallback echoue: {str(e2).encode('ascii', errors='replace').decode()}")
                    self._raise_refresh_permission_error("Stop/Republish", journey, logs, e2)

        else:
            msg = f"Status '{status}' non géré -- faire Stop+Republish manuellement dans Journey Builder"
            logs.append(msg)
            raise Exception(msg)

    # =====================
    # ASSETS / EMAILS
    # =====================

    def get_asset_by_id(self, asset_id, try_legacy=True):
        url = f"{self.base_url}/asset/v1/content/assets/{asset_id}"
        try:
            response = requests.get(url, headers=self.auth.get_headers(), timeout=30, verify=False)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404 and try_legacy:
                print(f"  [INFO] Recherche par legacy ID...")
                legacy = self.get_email_by_legacy_id(asset_id)
                if legacy:
                    print(f"  [OK] Trouvé: {legacy.get('name')}")
                    return legacy
            raise

    def update_asset(self, asset_id, data):
        url = f"{self.base_url}/asset/v1/content/assets/{asset_id}"
        response = requests.patch(url, headers=self.auth.get_headers(), json=data, timeout=30, verify=False)
        response.raise_for_status()
        return response.json()

    def get_email_by_legacy_id(self, email_id):
        url = f"{self.base_url}/asset/v1/content/assets"

        # Filtre simple
        try:
            params = {"$filter": f"legacyData.legacyId eq {email_id}"}
            response = requests.get(url, headers=self.auth.get_headers(), params=params, timeout=30, verify=False)
            response.raise_for_status()
            data = response.json()
            if data.get('count', 0) > 0:
                return data['items'][0]
        except:
            pass

        # Query sur types email
        for email_type in ['htmlemail', 'templatebasedemail', 'textonlyemail']:
            try:
                query = {
                    "page": {"page": 1, "pageSize": 50},
                    "query": {
                        "leftOperand": {"property": "assetType.name", "simpleOperator": "equal", "value": email_type},
                        "logicalOperator": "AND",
                        "rightOperand": {"property": "data.email.legacy.legacyId", "simpleOperator": "equal", "value": str(email_id)}
                    }
                }
                response = requests.post(f"{url}/query", headers=self.auth.get_headers(), json=query, timeout=30, verify=False)
                response.raise_for_status()
                data = response.json()
                if data.get('count', 0) > 0:
                    return data['items'][0]
            except:
                continue
        return None

    def get_asset_by_customer_key(self, customer_key, asset_type="htmlemail"):
        url = f"{self.base_url}/asset/v1/content/assets/query"
        query = {
            "page": {"page": 1, "pageSize": 10},
            "query": {
                "leftOperand": {"property": "assetType.name", "simpleOperator": "equal", "value": asset_type},
                "logicalOperator": "AND",
                "rightOperand": {"property": "customerKey", "simpleOperator": "equal", "value": str(customer_key)}
            }
        }
        response = requests.post(url, headers=self.auth.get_headers(), json=query, timeout=30, verify=False)
        response.raise_for_status()
        data = response.json()
        if data.get('count', 0) > 0:
            return data['items'][0]
        return None

    def search_assets(self, name_filter=None, asset_type="htmlemail"):
        url = f"{self.base_url}/asset/v1/content/assets"
        query = {
            "page": {"page": 1, "pageSize": 50},
            "query": {
                "leftOperand": {"property": "assetType.name", "simpleOperator": "equal", "value": asset_type},
                "logicalOperator": "AND",
                "rightOperand": {"property": "name", "simpleOperator": "like", "value": f"%{name_filter}%" if name_filter else "%"}
            }
        }
        response = requests.post(f"{url}/query", headers=self.auth.get_headers(), json=query, timeout=30, verify=False)
        response.raise_for_status()
        return response.json()

    # =====================
    # URL REPLACEMENT
    # =====================

    def replace_urls_in_content(self, content, old_pattern, new_pattern, dry_run=True, url_replacements=None):
        """
        Remplace URLs dans HTML et AMPscript.
        Gère: /fr/, /fr", /fr', /fr<, /fr[space], /fr)
        Ne touche pas /fr-fr/

        url_replacements: liste de dict {'old': 'url_complete', 'new': 'nouvelle_url'}
        """
        if not content:
            return content, []

        changes = []
        new_content = content

        # 1. Remplacements d'URL complets (prioritaire)
        if url_replacements:
            for repl in url_replacements:
                old_url = repl.get('old', '')
                new_url = repl.get('new', '')
                if old_url and new_url and old_url in content:
                    for match in re.finditer(re.escape(old_url), content):
                        changes.append({
                            'original': match.group(),
                            'replacement': new_url,
                            'position': match.start(),
                            'context': content[max(0, match.start()-20):match.end()+20],
                            'type': 'full_url'
                        })
                    if not dry_run:
                        new_content = new_content.replace(old_url, new_url)

        # 2. Patterns de remplacement standard
        if old_pattern:
            patterns = [
                (rf'/{re.escape(old_pattern)}/(?!-)', f'/{new_pattern}/'),
                (rf'/{re.escape(old_pattern)}"', f'/{new_pattern}"'),
                (rf"/{re.escape(old_pattern)}'", f"/{new_pattern}'"),
                (rf'/{re.escape(old_pattern)}<', f'/{new_pattern}<'),
                (rf'/{re.escape(old_pattern)}(\s)', f'/{new_pattern}\\1'),
                (rf'/{re.escape(old_pattern)}\)', f'/{new_pattern})'),
            ]

            for pattern, replacement in patterns:
                for match in re.finditer(pattern, new_content if not dry_run else content):
                    # Skip si déjà fr-fr
                    prefix = (new_content if not dry_run else content)[max(0, match.start()-3):match.start()]
                    if f'-{old_pattern}' in prefix:
                        continue
                    changes.append({
                        'original': match.group(),
                        'position': match.start(),
                        'context': (new_content if not dry_run else content)[max(0, match.start()-30):match.end()+30],
                        'type': 'pattern'
                    })

                if not dry_run:
                    new_content = re.sub(pattern, replacement, new_content)

        return new_content, changes

    def process_email_asset(self, asset_id, old_pattern, new_pattern, dry_run=True, url_replacements=None):
        """Traite un email: récupère, modifie, sauvegarde"""
        result = {'asset_id': asset_id, 'name': None, 'changes': [], 'success': False, 'error': None}

        try:
            asset = self.get_asset_by_id(asset_id)
            result['name'] = asset.get('name')
            result['actual_asset_id'] = asset.get('id')

            # Trouver le contenu HTML
            html_content = None
            location = None

            if 'views' in asset and 'html' in asset['views']:
                html_content = asset['views']['html'].get('content', '')
                location = 'views.html.content'

            if not html_content and 'content' in asset:
                html_content = asset.get('content', '')
                location = 'content'

            if not html_content and 'data' in asset:
                html_content = asset.get('data', {}).get('email', {}).get('htmlBody', '')
                if html_content:
                    location = 'data.email.htmlBody'

            if not html_content:
                result['error'] = "HTML non trouvé"
                return result

            # Remplacer
            new_content, changes = self.replace_urls_in_content(html_content, old_pattern, new_pattern, dry_run, url_replacements)
            result['changes'] = changes
            result['changes_count'] = len(changes)

            if not dry_run and changes:
                # Sauvegarder
                if location == 'views.html.content':
                    update = {'views': {'html': {'content': new_content}}}
                elif location == 'data.email.htmlBody':
                    update = {'data': {'email': {'htmlBody': new_content}}}
                else:
                    update = {'content': new_content}

                self.update_asset(result['actual_asset_id'], update)
                result['success'] = True
                print(f"  [OK] {result['actual_asset_id']} - {len(changes)} modifs")
            else:
                result['success'] = True
                if dry_run:
                    print(f"  [DRY-RUN] {asset_id} - {len(changes)} modifs")

            return result

        except Exception as e:
            result['error'] = str(e)
            print(f"  [ERREUR] {asset_id}: {e}")
            return result

    def process_journey_auto(self, journey_id, dry_run=True):
        """
        Traite une journey avec détection automatique du pays depuis son nom.
        Retourne les patterns utilisés et les résultats.
        """
        journey = self.get_journey_by_id(journey_id)
        journey_name = journey.get('name', '')

        # Détecter le pays et les patterns
        country = extract_country_from_name(journey_name)
        patterns = get_url_patterns_for_journey(journey_name)

        result = {
            'journey_id': journey_id,
            'journey_name': journey_name,
            'country_detected': country,
            'patterns': patterns,
            'results': [],
            'total_changes': 0,
            'success': False,
            'error': None
        }

        if not patterns:
            result['error'] = f"Pays non détecté ou non supporté dans: {journey_name}"
            print(f"  [SKIP] {journey_name} - pays non détecté")
            return result

        old_pattern, new_pattern = patterns
        result['old_pattern'] = old_pattern
        result['new_pattern'] = new_pattern

        print(f"  [AUTO] {journey_name} : /{old_pattern}/ -> /{new_pattern}/")

        # Traiter les activités email
        activities, _ = self.get_journey_activities(journey_id)

        for act in activities:
            asset_id = act.get('asset_id') or self._extract_activity_asset_id(act)

            if asset_id:
                r = self.process_email_asset(asset_id, old_pattern, new_pattern, dry_run)
                r['activity_name'] = act.get('name')
                result['results'].append(r)
                result['total_changes'] += len(r.get('changes', []))

        result['success'] = True
        return result
