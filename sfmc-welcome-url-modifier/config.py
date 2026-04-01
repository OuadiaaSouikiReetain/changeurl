import os
import re
from dotenv import load_dotenv

load_dotenv()

# Credentials SFMC
SFMC_CLIENT_ID = os.getenv('SFMC_CLIENT_ID')
SFMC_CLIENT_SECRET = os.getenv('SFMC_CLIENT_SECRET')
SFMC_SUBDOMAIN = os.getenv('SFMC_SUBDOMAIN')
SFMC_MID = os.getenv('SFMC_MID')

# Endpoints API
AUTH_URL = f"https://{SFMC_SUBDOMAIN}.auth.marketingcloudapis.com/v2/token"
REST_BASE_URL = f"https://{SFMC_SUBDOMAIN}.rest.marketingcloudapis.com"

# Mapping code pays -> (old_pattern, new_pattern)
COUNTRY_URL_MAPPINGS = {
    'FR': ('fr', 'fr-fr'),
    'ES': ('es', 'es-es'),
    'DE': ('de', 'de-de'),
    'IT': ('it', 'it-it'),
    'PT': ('pt', 'pt-pt'),
    'NL': ('nl', 'nl-nl'),
    'UK': ('uk', 'en-gb'),
    'US': ('us', 'en-us'),
    'EU': ('eu', 'en-eu'),
    'JP': ('jp', 'ja-jp'),
    'CA': ('ca', 'en-ca'),
    'AU': ('au', 'en-au'),
    'EN': ('en', 'en-gb'),
    'AT': ('at', 'de-at'),
    'FF': ('fr', 'fr-fr'),
    'FT': ('fr', 'fr-fr'),
    'SITE': ('fr', 'fr-fr'),
}


def extract_country_from_name(journey_name):
    """Extrait le code pays depuis le nom de la journey."""
    name = journey_name.upper()

    # Pattern transactional: JB-X-ET-{COUNTRY}-
    match = re.search(r'-ET-([A-Z]{2,4})-', name)
    if match:
        return match.group(1)

    # Pattern welcome: S-{COUNTRY}-Welcome ou X-CJ-xxx-{COUNTRY}-Welcome
    match = re.search(r'(?:^S-|-)([A-Z]{2})-WELCOME', name)
    if match:
        return match.group(1)

    # Pattern welcome CJ generalise: S-CJ-xxx-{COUNTRY}-... ou O-CJ-xxx-{COUNTRY}-...
    match = re.search(r'^[A-Z]-CJ-\d+-([A-Z]{2})-', name)
    if match:
        return match.group(1)

    # Pattern welcome v2: S-{COUNTRY}-xxx-V2
    match = re.search(r'^S-([A-Z]{2})-', name)
    if match:
        return match.group(1)

    # Pattern octobre: O-CJ-xxx-{COUNTRY}-
    match = re.search(r'O-CJ-\d+-([A-Z]{2})-', name)
    if match:
        return match.group(1)

    return None


def get_url_patterns_for_journey(journey_name):
    """Retourne (old_pattern, new_pattern) basé sur le nom de la journey."""
    country = extract_country_from_name(journey_name)
    if country and country in COUNTRY_URL_MAPPINGS:
        return COUNTRY_URL_MAPPINGS[country]
    return None
