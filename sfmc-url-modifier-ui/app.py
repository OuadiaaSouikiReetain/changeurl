# -*- coding: utf-8-sig -*-
#!/usr/bin/env python3
"""
SFMC URL Modifier - Web UI
"""

import os
import sys
import re

# Force UTF-8-SIG stdout/stderr pour eviter les erreurs d'encodage Windows (cp1252)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf-8-sig'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8-sig', errors='replace', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8-sig', errors='replace', line_buffering=True)
else:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
import requests as _requests
import urllib3
urllib3.disable_warnings()

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify

# Patch global : tous les appels requests utilisent verify=False et timeout=30
_orig_request = _requests.Session.request
def _patched_request(self, *args, **kwargs):
    kwargs.setdefault('verify', False)
    kwargs.setdefault('timeout', 30)
    return _orig_request(self, *args, **kwargs)
_requests.Session.request = _patched_request

# .env
_base = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(_base, '.env')
if not os.path.exists(env_path):
    env_path = os.path.join(_base, '..', 'sfmc-url-modifier', '.env')
load_dotenv(env_path, override=True)

# Modules SFMC
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sfmc-welcome-url-modifier'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sfmc-url-modifier'))

from sfmc_auth import SFMCAuth
from sfmc_api import SFMCAPI
from config import extract_country_from_name, get_url_patterns_for_journey, COUNTRY_URL_MAPPINGS

app = Flask(__name__)

# Global API instance
api = None
auth = None


def build_test_event_data(api, contact_key, contact_email, first_name=None, extra_data=None, schema=None):
    payload = dict(extra_data or {})
    if first_name and not any(k in payload for k in ['firstname', 'firstName', 'FirstName']):
        payload['firstname'] = first_name
    return api.build_event_test_data(contact_key, contact_email, payload, schema=schema)


def get_api():
    global api, auth
    if api is None:
        auth = SFMCAuth()
        auth.refresh()
        api = SFMCAPI(auth)
    return api


def refresh_connection():
    global api, auth
    auth = SFMCAuth()
    auth.refresh()
    api = SFMCAPI(auth)
    return api


def get_asset_html_content(asset):
    """Retourne le HTML d'un asset email quel que soit son stockage."""
    if 'views' in asset and 'html' in asset['views']:
        html = asset['views']['html'].get('content', '')
        if html:
            return html

    if asset.get('content'):
        return asset.get('content', '')

    if 'data' in asset:
        return asset.get('data', {}).get('email', {}).get('htmlBody', '')

    return ''


def extract_full_url_from_content(content, position):
    """Reconstruit l'URL complète autour d'une position /xx/ dans le HTML."""
    if not content or position is None:
        return None

    http_start = content.rfind('http://', 0, position + 1)
    https_start = content.rfind('https://', 0, position + 1)
    start = max(http_start, https_start)
    if start < 0:
        return None

    left_chunk = content[start:position]
    if any(sep in left_chunk for sep in ['"', "'", '<', '>', '\n', '\r']):
        return None

    end = position
    while end < len(content) and content[end] not in '"\'<>) \t\r\n':
        end += 1

    return content[start:end].rstrip('?&')


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/debug-attrs', methods=['POST'])
def debug_attrs():
    """Debug: retourne les attributs DE pour un test send."""
    try:
        a = get_api()
        data = request.json
        definition_key = data.get('definition_key', 'S-ET-FR-TestApp')
        contact_email = data.get('contact_email', 'wadiasouiki@gmail.com')
        contact_key = data.get('contact_key', 'debug-key')
        has_method = hasattr(a, 'get_send_def_attributes')
        has_normalize = hasattr(a, '_normalize_de_row')
        attrs = a.get_send_def_attributes(definition_key, contact_email, contact_key) if has_method else {}
        return jsonify({
            'has_get_send_def_attributes': has_method,
            'has_normalize_de_row': has_normalize,
            'api_module': type(a).__module__,
            'base_url': a.base_url,
            'attrs': attrs
        })
        message = (
            f'Email accepte par SFMC pour {contact_email}, statut final encore en attente'
            if pending else
            f'Email de test dÃ©clenchÃ© pour {contact_email}'
        )
        return jsonify({
            'has_get_send_def_attributes': has_method,
            'has_normalize_de_row': has_normalize,
            'api_module': type(a).__module__,
            'base_url': a.base_url,
            'attrs': attrs
        })
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/connect', methods=['POST'])
def connect():
    try:
        refresh_connection()
        return jsonify({'success': True, 'message': 'Connexion réussie'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/journeys', methods=['GET'])
def get_journeys():
    """GET /api/journeys?type=all&page=1&page_size=50&exclude_stopped=true&no_cache=false"""
    try:
        api = get_api()
        journey_type = request.args.get('type', 'all')
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 50))
        exclude_stopped = request.args.get('exclude_stopped', 'true').lower() == 'true'
        no_cache = request.args.get('no_cache', 'false').lower() == 'true'

        if no_cache:
            api.invalidate_cache()

        data = api.get_journeys_paginated(
            page=page,
            page_size=page_size,
            journey_type=journey_type,
            exclude_stopped=exclude_stopped
        )

        result = []
        for j in data['items']:
            name = j.get('name', '')
            country = extract_country_from_name(name)
            patterns = get_url_patterns_for_journey(name)
            result.append({
                'id': j.get('id'),
                'name': name,
                'status': j.get('status'),
                'type': j.get('definitionType'),
                'modifiedDate': j.get('modifiedDate'),
                'country': country,
                'patterns': patterns
            })

        return jsonify({
            'success': True,
            'journeys': result,
            'count': len(result),
            'total': data['total'],
            'page': data['page'],
            'page_size': data['page_size'],
            'has_more': data['has_more'],
            'from_cache': data['from_cache']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/analyze', methods=['POST'])
def analyze():
    try:
        api = get_api()
        data = request.json
        journey_id = data.get('journey_id')
        asset_id = data.get('asset_id')
        auto_detect = data.get('auto_detect', True)
        old_pattern = data.get('old', 'fr')
        new_pattern = data.get('new', 'fr-fr')
        url_replacements = data.get('url_replacements', [])
        skip_pattern = data.get('skip_pattern', False)
        if skip_pattern:
            old_pattern = None
            new_pattern = None
            auto_detect = False

        results = []

        if asset_id:
            r = api.process_email_asset(asset_id, old_pattern, new_pattern, dry_run=True, url_replacements=url_replacements)
            results.append(r)
        elif journey_id:
            activities, journey = api.get_journey_activities(journey_id)
            journey_name = journey.get('name', '')

            # Auto-détection du pays
            if auto_detect:
                patterns = get_url_patterns_for_journey(journey_name)
                if patterns:
                    old_pattern, new_pattern = patterns
                    country = extract_country_from_name(journey_name)
                else:
                    country = None
            else:
                country = None

            journey_info = {
                'name': journey_name,
                'status': journey.get('status'),
                'activities_count': len(activities),
                'country_detected': country,
                'old_pattern': old_pattern,
                'new_pattern': new_pattern
            }

            for act in activities:
                aid = extract_asset_id(act)
                if aid:
                    r = api.process_email_asset(aid, old_pattern, new_pattern, dry_run=True, url_replacements=url_replacements)
                    r['activity_name'] = act.get('name')
                    results.append(r)

            return jsonify({
                'success': True,
                'journey': journey_info,
                'results': results,
                'total_changes': sum(len(r.get('changes', [])) for r in results)
            })

        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/execute', methods=['POST'])
def execute():
    try:
        api = get_api()
        data = request.json
        journey_id = data.get('journey_id')
        asset_id = data.get('asset_id')
        auto_detect = data.get('auto_detect', True)
        old_pattern = data.get('old', 'fr')
        new_pattern = data.get('new', 'fr-fr')
        refresh = data.get('refresh', False)
        url_replacements = data.get('url_replacements', [])
        skip_pattern = data.get('skip_pattern', False)
        if skip_pattern:
            old_pattern = None
            new_pattern = None
            auto_detect = False

        results = []

        if asset_id:
            r = api.process_email_asset(asset_id, old_pattern, new_pattern, dry_run=False, url_replacements=url_replacements)
            results.append(r)
        elif journey_id:
            activities, journey = api.get_journey_activities(journey_id)
            journey_name = journey.get('name', '')

            # Auto-détection du pays
            if auto_detect:
                patterns = get_url_patterns_for_journey(journey_name)
                if patterns:
                    old_pattern, new_pattern = patterns
                    country = extract_country_from_name(journey_name)
                else:
                    country = None
            else:
                country = None

            for act in activities:
                aid = extract_asset_id(act)
                if aid:
                    r = api.process_email_asset(aid, old_pattern, new_pattern, dry_run=False, url_replacements=url_replacements)
                    r['activity_name'] = act.get('name')
                    results.append(r)

            total_changes = sum(len(r.get('changes', [])) for r in results)
            refresh_result = None
            if refresh and total_changes > 0:
                try:
                    refresh_result = api.refresh_journey(journey_id)
                except Exception as e:
                    refresh_result = {'error': str(e)}

            return jsonify({
                'success': True,
                'journey_name': journey_name,
                'country_detected': country,
                'old_pattern': old_pattern,
                'new_pattern': new_pattern,
                'results': results,
                'total_changes': total_changes,
                'refresh_result': refresh_result
            })

        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/scan', methods=['POST'])
def scan():
    try:
        api = get_api()
        data = request.json
        journey_id = data.get('journey_id')
        auto_detect = data.get('auto_detect', True)
        old_pattern = data.get('old', 'fr')
        new_pattern = data.get('new', 'fr-fr')
        url_replacements = data.get('url_replacements', [])
        skip_pattern = data.get('skip_pattern', False)
        if skip_pattern:
            old_pattern = None
            new_pattern = None
            auto_detect = False

        activities, journey = api.get_journey_activities(journey_id)
        journey_name = journey.get('name', '')

        # Auto-détection du pays
        if auto_detect:
            patterns = get_url_patterns_for_journey(journey_name)
            if patterns:
                old_pattern, new_pattern = patterns
                country = extract_country_from_name(journey_name)
            else:
                country = None
        else:
            country = None

        journey_info = {
            'name': journey_name,
            'status': journey.get('status'),
            'activities_count': len(activities),
            'country_detected': country,
            'old_pattern': old_pattern,
            'new_pattern': new_pattern
        }

        all_urls = []
        activity_results = []

        for act in activities:
            asset_id = extract_asset_id(act)
            if not asset_id:
                continue

            r = api.process_email_asset(asset_id, old_pattern, new_pattern, dry_run=True, url_replacements=url_replacements)
            html_content = ''
            if r.get('changes'):
                try:
                    asset = api.get_asset_by_id(r.get('actual_asset_id') or asset_id)
                    html_content = get_asset_html_content(asset)
                except Exception:
                    html_content = ''

            urls = set()
            if r.get('changes'):
                for change in r['changes']:
                    if change.get('type') == 'full_url':
                        original = change.get('original', '')
                        if original:
                            urls.add(original.rstrip('?&'))
                        continue

                    url = extract_full_url_from_content(html_content, change.get('position'))
                    if not url:
                        context = change.get('context', '')
                        found = re.findall(r'https?://[^\s"\'<>]+', context)
                        url = found[0] if found else None

                    if not url:
                        continue

                    clean = re.split(r'\?sez_client_id=|\?campaign=|&sez_', url)[0].rstrip('?&')
                    if old_pattern and f'/{old_pattern}' in clean and f'/{old_pattern}-' not in clean:
                        urls.add(clean)

            activity_results.append({
                'activity_name': act.get('name'),
                'asset_name': r.get('name'),
                'changes_count': len(r.get('changes', [])),
                'urls': sorted(list(urls))
            })
            all_urls.extend(urls)

        return jsonify({
            'success': True,
            'journey': journey_info,
            'activities': activity_results,
            'unique_urls': sorted(list(set(all_urls))),
            'total_urls': len(set(all_urls))
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/countries', methods=['GET'])
def get_countries():
    """Retourne les mappings pays supportés"""
    return jsonify({
        'success': True,
        'mappings': COUNTRY_URL_MAPPINGS
    })


@app.route('/api/refresh', methods=['POST'])
def refresh_journey():
    """Rafraîchit/Republie une journey"""
    try:
        api = get_api()
        data = request.json
        journey_id = data.get('journey_id')
        activity_ids = data.get('activity_ids') or []

        if not journey_id:
            return jsonify({'success': False, 'error': 'journey_id requis'})

        journey = api.get_journey_by_id(journey_id)
        journey_name = journey.get('name', '')
        status = journey.get('status', '')

        result = api.refresh_journey(journey_id, activity_ids=activity_ids)
        method = result.get('method')
        refresh_status = 'OK - email recompile depuis Content Builder'
        if method == 'stop_republish':
            refresh_status = 'OK - Stop + Republish effectue, email recompile'
        elif method == 'republish_current':
            refresh_status = 'OK - Republication directe effectuee'
        elif method == 'new_version':
            refresh_status = 'OK - Nouvelle version publiee'
        elif method == 'refresh_definition':
            refresh_status = 'OK - send definition transactionnelle rafraichie'

        return jsonify({
            'success': True,
            'journey_id': journey_id,
            'journey_name': journey_name,
            'journey_status': status,
            'method': method,
            'logs': result.get('logs', []),
            'refresh_status': refresh_status
        })
    except Exception as e:
        sfmc_detail = None
        sfmc_status = None
        if hasattr(e, 'response') and e.response is not None:
            sfmc_status = e.response.status_code
            sfmc_detail = e.response.text
        return jsonify({
            'success': False,
            'error': str(e),
            'sfmc_status': sfmc_status,
            'sfmc_detail': sfmc_detail,
            'manual_required': getattr(e, 'manual_required', False),
            'journey_type': getattr(e, 'journey_type', None),
            'journey_name': getattr(e, 'journey_name', None),
            'logs': getattr(e, 'logs', [])
        })


@app.route('/api/journey-emails', methods=['POST'])
def get_journey_emails():
    """Récupère la liste des emails d'une journey"""
    try:
        api = get_api()
        data = request.json
        journey_id = data.get('journey_id')

        if not journey_id:
            return jsonify({'success': False, 'error': 'journey_id requis'})

        activities, journey = api.get_journey_activities(journey_id)

        emails = []
        for act in activities:
            asset_id = extract_asset_id(act)
            if asset_id:
                try:
                    asset = api.get_asset_by_id(asset_id)
                    emails.append({
                        'asset_id': asset_id,
                        'activity_id': act.get('activity_id'),
                        'activity_name': act.get('name'),
                        'name': asset.get('name'),
                        'type': act.get('type')
                    })
                except Exception:
                    emails.append({
                        'asset_id': asset_id,
                        'activity_id': act.get('activity_id'),
                        'activity_name': act.get('name'),
                        'name': f'Asset {asset_id}',
                        'type': act.get('type')
                    })

        return jsonify({
            'success': True,
            'journey_id': journey_id,
            'journey_name': journey.get('name', ''),
            'emails': emails,
            'count': len(emails)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/journey-event-key', methods=['POST'])
def get_journey_event_key():
    """Récupère l'EventDefinitionKey de l'entry source API Event d'une journey."""
    try:
        api = get_api()
        data = request.json
        journey_id = data.get('journey_id')
        if not journey_id:
            return jsonify({'success': False, 'error': 'journey_id requis'})

        journey_raw = api.get_journey_by_id(journey_id)
        triggers_raw = journey_raw.get('triggers', [])

        result = api.get_journey_event_key(journey_id)
        if not result:
            return jsonify({
                'success': False,
                'error': 'Aucun API Event trouvé sur cette journey.',
                'debug_triggers': triggers_raw
            })

        return jsonify({'success': True, **result, 'debug_triggers': triggers_raw})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/test-send', methods=['POST'])
def test_send():
    """
    Déclenche un envoi de test via API Event pour vérifier que la journey
    utilise bien le nouvel email après modification.
    """
    try:
        api = get_api()
        data = request.json
        journey_id = data.get('journey_id')
        contact_key = data.get('contact_key')
        contact_email = data.get('contact_email')
        first_name = data.get('first_name')
        event_definition_key = data.get('event_definition_key')
        extra_data = data.get('extra_data', {})

        print(f"\n{'*'*70}")
        print(f"[TEST_SEND] REQUEST recu")
        print(f"[TEST_SEND] journey_id={journey_id!r}")
        print(f"[TEST_SEND] contact_key={contact_key!r}")
        print(f"[TEST_SEND] contact_email={contact_email!r}")
        print(f"[TEST_SEND] first_name={first_name!r}")
        print(f"[TEST_SEND] event_definition_key (fourni)={event_definition_key!r}")
        print(f"[TEST_SEND] extra_data={extra_data}")

        if not all([journey_id, contact_key, contact_email]):
            return jsonify({'success': False, 'error': 'journey_id, contact_key et contact_email requis'})

        # Auto-récupère la clé d'envoi si non fournie
        send_type = data.get('send_type')
        event_schema = []
        event_info = None
        if not event_definition_key:
            print(f"[TEST_SEND] Pas d'event_definition_key fourni -> auto-detection...")
            journey_raw = api.get_journey_by_id(journey_id)
            triggers_raw = journey_raw.get('triggers', [])
            print(f"[TEST_SEND] triggers_raw={triggers_raw}")

            event_info = api.get_journey_event_key(journey_id)
            print(f"[TEST_SEND] event_info detecte={event_info}")
            if not event_info:
                trigger_types = [t.get('type', 'inconnu') for t in triggers_raw]
                print(f"[TEST_SEND] ECHEC: aucun event_info. trigger_types={trigger_types}")
                return jsonify({
                    'success': False,
                    'error': f'Clé d\'envoi introuvable. Types trouvés: {trigger_types}',
                    'debug_triggers': triggers_raw
                })
            event_definition_key = event_info['event_definition_key']
            send_type = event_info.get('send_type', 'api_event')
            event_schema = event_info.get('schema', [])
            print(f"[TEST_SEND] event_definition_key auto={event_definition_key!r}")
            print(f"[TEST_SEND] send_type auto={send_type!r}")
            print(f"[TEST_SEND] event_schema={event_schema}")

        print(f"[TEST_SEND] send_type final={send_type!r}")

        # Determine early si on va utiliser SOAP (welcome/EmailAudience)
        # pour eviter de generer un UUID inutile dans ce cas
        _trigger_type_early = (event_info.get('trigger_type', '') if event_info else data.get('trigger_type', ''))
        _welcome_triggers = ('EmailAudience', 'ContactAudience', 'Audience', 'DataExtension')
        _will_use_soap = send_type != 'transactional' and (
            _trigger_type_early in _welcome_triggers or
            (event_definition_key or '').startswith('EmailAudience-') or
            (event_definition_key or '').startswith('ContactAudience-')
        )

        # Pour api_event (pas SOAP): contact_key unique pour eviter la restriction de re-entree SFMC
        import uuid as _uuid
        if send_type != 'transactional' and not _will_use_soap:
            contact_key = f"test-{_uuid.uuid4().hex[:12]}"
            print(f"[TEST_SEND] contact_key unique genere (api_event): {contact_key!r}")

        if send_type == 'transactional':
            # Pour transactional : récupère les attributs requis depuis la DE de la send definition
            de_attrs = api.get_send_def_attributes(event_definition_key, contact_email, contact_key)
            # Merge : de_attrs en base, extra_data fourni par l'utilisateur prend la priorité
            merged = {**de_attrs, **extra_data}
            # Ajoute firstname si présent
            if first_name and not any(k in merged for k in ['firstname', 'firstName', 'FirstName']):
                merged['firstname'] = first_name
            extra_data = merged
            print(f"[TEST_SEND] extra_data transactional (DE+user)={extra_data}")
        else:
            extra_data = build_test_event_data(
                api,
                contact_key,
                contact_email,
                first_name=first_name,
                extra_data=extra_data,
                schema=event_schema
            )
            print(f"[TEST_SEND] extra_data api_event={extra_data}")

        # Choisit la méthode d'envoi selon le type de journey
        trigger_type = (event_info.get('trigger_type', '') if event_info else data.get('trigger_type', ''))
        welcome_trigger_types = ('EmailAudience', 'ContactAudience', 'Audience', 'DataExtension')
        use_soap = send_type != 'transactional' and (
            trigger_type in welcome_trigger_types or
            event_definition_key.startswith('EmailAudience-') or
            event_definition_key.startswith('ContactAudience-')
        )
        print(f"[TEST_SEND] trigger_type={trigger_type!r} use_soap={use_soap}")

        if send_type == 'transactional':
            print(f"[TEST_SEND] -> Methode: fire_transactional_email")
            result = api.fire_transactional_email(event_definition_key, contact_key, contact_email, extra_data)
        elif use_soap:
            # Welcome/EmailAudience: SOAP TriggeredSend avec CustomerKey resolu depuis ID numerique
            # fire_api_event(EmailAudience-xxx) retourne 200 mais n'envoie rien (endpoint APIEvent seulement)
            send_defs = api.get_journey_send_definitions(journey_id)
            soap_customer_key = None
            if send_defs:
                numeric_key = send_defs[0]['definition_key']
                print(f"[TEST_SEND] welcome: triggeredSendKey numerique={numeric_key!r} -> resolution CustomerKey...")
                try:
                    soap_customer_key = api.get_triggered_send_customer_key(numeric_key)
                except Exception as e_resolve:
                    print(f"[TEST_SEND] welcome: echec resolution CustomerKey: {e_resolve}")
            print(f"[TEST_SEND] welcome: soap_customer_key resolu={soap_customer_key!r}")
            if soap_customer_key:
                print(f"[TEST_SEND] -> Methode: fire_triggered_send_soap (CustomerKey={soap_customer_key!r})")
                result = api.fire_triggered_send_soap(soap_customer_key, contact_email, contact_key, extra_data)
            else:
                # Fallback si resolution impossible
                import uuid as _uuid2
                contact_key = f"test-{_uuid2.uuid4().hex[:12]}"
                print(f"[TEST_SEND] -> Methode: fire_api_event (fallback no CustomerKey, contact_key={contact_key!r})")
                result = api.fire_api_event(event_definition_key, contact_key, contact_email, extra_data)
        else:
            print(f"[TEST_SEND] -> Methode: fire_api_event")
            result = api.fire_api_event(event_definition_key, contact_key, contact_email, extra_data)

        print(f"[TEST_SEND] SUCCES -- result={result}")
        print(f"{'*'*70}\n")

        disposition = (result or {}).get('disposition', {}) if isinstance(result, dict) else {}
        pending = disposition.get('pending', False)
        message = (
            f'Email accepte par SFMC pour {contact_email}, statut final encore en attente'
            if pending else
            f'Email de test déclenché pour {contact_email}'
        )

        return jsonify({
            'success': True,
            'message': message,
            'contact_key': contact_key,
            'event_definition_key': event_definition_key,
            'send_type': send_type,
            'result': result
        })
    except Exception as e:
        print(f"[TEST_SEND] EXCEPTION: {e}")
        print(f"{'*'*70}\n")
        return jsonify({'success': False, 'error': str(e)})


def extract_asset_id(activity):
    if activity.get('asset_id'):
        return activity.get('asset_id')

    cfg = activity.get('config_args', {})

    if 'triggeredSend' in cfg:
        ts = cfg['triggeredSend']
        return ts.get('emailId') or ts.get('legacyEmailId') or ts.get('assetId')

    for k in ['emailId', 'assetId', 'legacyEmailId', 'contentBuilderAssetId']:
        if cfg.get(k):
            return cfg[k]

    for key in ['arguments', 'metaData']:
        nested = activity.get(key, {})
        if isinstance(nested, dict):
            for k in ['emailId', 'assetId', 'legacyEmailId', 'contentBuilderAssetId']:
                if nested.get(k):
                    return nested[k]
    return None


@app.route('/api/debug-definition', methods=['POST'])
def debug_definition():
    """Debug: appel direct SFMC pour une definition key"""
    try:
        api = get_api()
        data = request.json
        definition_key = data.get('definition_key', '')
        url = f"{api.base_url}/messaging/v1/email/definitions/{definition_key}"
        import requests as req
        response = req.get(url, headers=api.auth.get_headers(), timeout=30, verify=False)
        return jsonify({
            'url': url,
            'status_code': response.status_code,
            'response_body': response.text,
            'ok': response.ok
        })
    except Exception as e:
        return jsonify({'error': str(e)})


@app.errorhandler(400)
def bad_request(e):
    return jsonify({'success': False, 'error': str(e)}), 400


@app.errorhandler(404)
def not_found(e):
    return jsonify({'success': False, 'error': f'Route non trouvée: {request.path}'}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({'success': False, 'error': str(e)}), 405


@app.errorhandler(500)
def internal_error(e):
    return jsonify({'success': False, 'error': f'Erreur interne: {str(e)}'}), 500


@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5001)
    args = parser.parse_args()
    # Initialise la connexion SFMC au démarrage
    try:
        refresh_connection()
        print('[OK] Connexion SFMC initialisee au demarrage')
    except Exception as e:
        print(f'[WARN] Connexion SFMC au demarrage echouee: {e}')
    app.run(debug=False, port=args.port, use_reloader=False)
