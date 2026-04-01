"""
Auth OAuth2 SFMC
"""

import requests
from config import SFMC_CLIENT_ID, SFMC_CLIENT_SECRET, AUTH_URL, SFMC_MID


class SFMCAuth:
    def __init__(self):
        self.access_token = None

    def get_token(self, force_refresh=False):
        # Toujours récupérer un nouveau token
        if self.access_token and not force_refresh:
            return self.access_token

        payload = {
            "grant_type": "client_credentials",
            "client_id": SFMC_CLIENT_ID,
            "client_secret": SFMC_CLIENT_SECRET
        }
        if SFMC_MID:
            payload["account_id"] = SFMC_MID

        response = requests.post(AUTH_URL, json=payload, timeout=30, verify=False)
        response.raise_for_status()

        data = response.json()
        self.access_token = data['access_token']

        print(f"[OK] Nouvelle session")
        return self.access_token

    def get_headers(self):
        return {
            "Authorization": f"Bearer {self.get_token()}",
            "Content-Type": "application/json"
        }

    def refresh(self):
        """Force nouveau token"""
        self.access_token = None
        return self.get_token(force_refresh=True)
