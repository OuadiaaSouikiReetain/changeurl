"""
API SFMC - Journeys, Emails, Assets
"""

import requests
import re
import time
from config import REST_BASE_URL


class SFMCAPI:
    def __init__(self, auth):
        self.auth = auth
        self.base_url = REST_BASE_URL

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

        response = requests.get(url, headers=self.auth.get_headers(), params=params)
        response.raise_for_status()
        return response.json()

    def get_all_journeys(self):
        """Récupère TOUTES les journeys (pagination)"""
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
            response = requests.get(url, headers=self.auth.get_headers(), params=params)
            response.raise_for_status()
            data = response.json()

            items = data.get('items', [])
            all_items.extend(items)

            if len(items) < page_size:
                break
            page += 1

        return {'items': all_items, 'count': len(all_items)}

    def get_journey_by_id(self, journey_id):
        url = f"{self.base_url}/interaction/v1/interactions/{journey_id}"
        response = requests.get(url, headers=self.auth.get_headers())
        response.raise_for_status()
        return response.json()

    def get_journey_activities(self, journey_id):
        journey = self.get_journey_by_id(journey_id)
        email_activities = []

        for activity in journey.get('activities', []):
            if activity.get('type') in ['EMAILV2', 'EMAIL', 'EMAILSEND']:
                email_activities.append({
                    'activity_id': activity.get('id'),
                    'activity_key': activity.get('key'),
                    'name': activity.get('name'),
                    'config_args': activity.get('configurationArguments', {}),
                    'type': activity.get('type')
                })

        return email_activities, journey

    def update_journey(self, journey_id, journey_data):
        url = f"{self.base_url}/interaction/v1/interactions/{journey_id}"
        response = requests.put(url, headers=self.auth.get_headers(), json=journey_data)
        response.raise_for_status()
        return response.json()

    def create_journey_version(self, journey_id):
        url = f"{self.base_url}/interaction/v1/interactions/{journey_id}/newVersion"
        response = requests.post(url, headers=self.auth.get_headers())
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

    def wait_for_status(self, journey_id, target_statuses, timeout=90):
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(4)
            j = self.get_journey_by_id(journey_id)
            s = j.get('status', '')
            print(f"  [POLL] Status actuel: {s}")
            if s in target_statuses:
                return s
        raise Exception(f"Timeout: journey pas en {target_statuses} après {timeout}s")

    def refresh_journey(self, journey_id):
        journey = self.get_journey_by_id(journey_id)
        status = journey.get('status', '')
        version = journey.get('version', 1)
        logs = []
        print(f"  [REFRESH] Status: {status} | Version: {version}")
        logs.append(f"Status: {status}, Version: {version}")

        if status == 'Draft':
            result = self.update_journey(journey_id, journey)
            return {'method': 'save_draft', 'logs': logs, 'result': result}

        elif status in ['Running', 'Published', 'Scheduled']:
            try:
                logs.append(f"Arrêt version {version}...")
                self.stop_journey(journey_id, version)
                stopped_status = self.wait_for_status(journey_id, ['Stopped', 'Draft'], timeout=90)
                logs.append(f"Arrêtée (status: {stopped_status})")
                logs.append(f"Republication version {version}...")
                result = self.publish_journey(journey_id, version)
                logs.append("Republication lancée — email recompilé depuis Content Builder")
                return {'method': 'stop_republish', 'logs': logs, 'result': result}
            except Exception as e:
                logs.append(f"Stop/Republish échoué: {e}")
                try:
                    logs.append("Fallback: création nouvelle version...")
                    new_v = self.create_journey_version(journey_id)
                    new_num = new_v.get('version', version + 1)
                    new_id = new_v.get('id', journey_id)
                    result = self.publish_journey(new_id, new_num)
                    logs.append(f"Version {new_num} publiée")
                    return {'method': 'new_version', 'logs': logs, 'result': result}
                except Exception as e2:
                    logs.append(f"Fallback échoué: {e2}")
                    raise Exception(f"Refresh impossible. Logs: {' | '.join(logs)}")
        else:
            raise Exception(f"Status '{status}' non géré — faire Stop+Republish manuellement")

    # =====================
    # ASSETS / EMAILS
    # =====================

    def get_asset_by_id(self, asset_id, try_legacy=True):
        url = f"{self.base_url}/asset/v1/content/assets/{asset_id}"
        try:
            response = requests.get(url, headers=self.auth.get_headers())
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
        response = requests.patch(url, headers=self.auth.get_headers(), json=data)
        response.raise_for_status()
        return response.json()

    def get_email_by_legacy_id(self, email_id):
        url = f"{self.base_url}/asset/v1/content/assets"

        try:
            params = {"$filter": f"legacyData.legacyId eq {email_id}"}
            response = requests.get(url, headers=self.auth.get_headers(), params=params)
            response.raise_for_status()
            data = response.json()
            if data.get('count', 0) > 0:
                return data['items'][0]
        except:
            pass

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
                response = requests.post(f"{url}/query", headers=self.auth.get_headers(), json=query)
                response.raise_for_status()
                data = response.json()
                if data.get('count', 0) > 0:
                    return data['items'][0]
            except:
                continue
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
        response = requests.post(f"{url}/query", headers=self.auth.get_headers(), json=query)
        response.raise_for_status()
        return response.json()

    # =====================
    # URL REPLACEMENT
    # =====================

    def replace_urls_in_content(self, content, old_pattern, new_pattern, dry_run=True):
        """
        Remplace URLs dans HTML et AMPscript.
        Gère: /fr/, /fr", /fr', /fr<, /fr[space], /fr)
        Ne touche pas /fr-fr/
        """
        if not content:
            return content, []

        changes = []
        new_content = content

        patterns = [
            (rf'/{re.escape(old_pattern)}/(?!-)', f'/{new_pattern}/'),
            (rf'/{re.escape(old_pattern)}"', f'/{new_pattern}"'),
            (rf"/{re.escape(old_pattern)}'", f"/{new_pattern}'"),
            (rf'/{re.escape(old_pattern)}<', f'/{new_pattern}<'),
            (rf'/{re.escape(old_pattern)}(\s)', f'/{new_pattern}\\1'),
            (rf'/{re.escape(old_pattern)}\)', f'/{new_pattern})'),
        ]

        for pattern, replacement in patterns:
            for match in re.finditer(pattern, content):
                prefix = content[max(0, match.start()-3):match.start()]
                if f'-{old_pattern}' in prefix:
                    continue
                changes.append({
                    'original': match.group(),
                    'position': match.start(),
                    'context': content[max(0, match.start()-30):match.end()+30]
                })

            if not dry_run:
                new_content = re.sub(pattern, replacement, new_content)

        return new_content, changes

    def process_email_asset(self, asset_id, old_pattern, new_pattern, dry_run=True):
        result = {'asset_id': asset_id, 'name': None, 'changes': [], 'success': False, 'error': None}

        try:
            asset = self.get_asset_by_id(asset_id)
            result['name'] = asset.get('name')
            result['actual_asset_id'] = asset.get('id')

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

            new_content, changes = self.replace_urls_in_content(html_content, old_pattern, new_pattern, dry_run)
            result['changes'] = changes
            result['changes_count'] = len(changes)

            if not dry_run and changes:
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
