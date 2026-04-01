#!/usr/bin/env python3
"""
SFMC Welcome URL Modifier
Modification URLs dans les journeys Welcome
"""

import argparse
import json
import sys

try:
    from colorama import init, Fore, Style
    init()
except ImportError:
    class Fore:
        GREEN = RED = YELLOW = CYAN = MAGENTA = RESET = ''
    class Style:
        BRIGHT = RESET_ALL = ''

from sfmc_auth import SFMCAuth
from sfmc_api import SFMCAPI


def header(text):
    print(f"\n{Fore.CYAN}{'='*60}\n {text}\n{'='*60}{Style.RESET_ALL}\n")

def ok(text):
    print(f"{Fore.GREEN}[OK]{Style.RESET_ALL} {text}")

def warn(text):
    print(f"{Fore.YELLOW}[!]{Style.RESET_ALL} {text}")

def err(text):
    print(f"{Fore.RED}[ERREUR]{Style.RESET_ALL} {text}")

def info(text):
    print(f"{Fore.CYAN}[i]{Style.RESET_ALL} {text}")


def is_welcome_journey(journey):
    """Check si le nom contient 'welcome' (case insensitive)"""
    name = journey.get('name', '').lower()
    return 'welcome' in name


def list_emails(api, name_filter=None):
    header("Emails SFMC")
    all_emails = []

    for t in ['htmlemail', 'templatebasedemail']:
        try:
            r = api.search_assets(name_filter=name_filter, asset_type=t)
            all_emails.extend(r.get('items', []))
        except:
            pass

    print(f"Total: {len(all_emails)}\n")
    for e in all_emails[:20]:
        print(f"  {Fore.MAGENTA}{e.get('id')}{Style.RESET_ALL} - {e.get('name')}")
    return all_emails


def list_journeys(api, status_filter=None, welcome_only=True):
    header("Journeys Welcome" if welcome_only else "Toutes les Journeys")

    data = api.get_all_journeys()
    all_j = data.get('items', [])

    if welcome_only:
        journeys = [j for j in all_j if is_welcome_journey(j)]
    else:
        journeys = all_j

    print(f"Total: {len(journeys)}\n")
    for j in journeys:
        status = j.get('status', '?')
        color = Fore.GREEN if status == 'Running' else Fore.YELLOW
        print(f"  {Fore.MAGENTA}{j.get('id')}{Style.RESET_ALL}")
        print(f"  {j.get('name')} | {color}{status}{Style.RESET_ALL} | {j.get('definitionType')}")
        print()
    return journeys


def analyze_journey(api, journey_id, old, new):
    header(f"Analyse: {journey_id}")
    activities, journey = api.get_journey_activities(journey_id)
    print(f"{journey.get('name')} | {journey.get('status')}")
    print(f"Email activities: {len(activities)}\n")

    results = []
    for act in activities:
        print(f"{Fore.CYAN}--- {act['name']} ---{Style.RESET_ALL}")
        asset_id = extract_asset_id(act)
        if not asset_id:
            warn("Asset ID non trouvé")
            continue

        r = api.process_email_asset(asset_id, old, new, dry_run=True)
        results.append(r)
        if r['changes']:
            print(f"  {Fore.YELLOW}{len(r['changes'])} changements{Style.RESET_ALL}")
            for c in r['changes'][:3]:
                print(f"    ...{c['context'][:60]}...")
    return results


def analyze_asset(api, asset_id, old, new):
    header(f"Analyse asset: {asset_id}")
    r = api.process_email_asset(asset_id, old, new, dry_run=True)
    print(f"Asset: {r.get('name')}")
    if r['changes']:
        print(f"\n{Fore.YELLOW}{len(r['changes'])} changements:{Style.RESET_ALL}")
        for c in r['changes']:
            print(f"  ...{c['context'][:60]}...")
    else:
        info("Aucun changement")
    return r


def execute_journey(api, journey_id, old, new, refresh=False):
    header(f"Execute: {journey_id}")
    warn("Modifications appliquées!")

    if input("\nConfirmer? (oui/non): ").lower() not in ['oui', 'o', 'yes', 'y']:
        print("Annulé.")
        return

    activities, _ = api.get_journey_activities(journey_id)
    results = []

    for act in activities:
        print(f"\n--- {act['name']} ---")
        asset_id = extract_asset_id(act)
        if not asset_id:
            continue
        r = api.process_email_asset(asset_id, old, new, dry_run=False)
        results.append(r)

    total = sum(len(r.get('changes', [])) for r in results)
    header("Résumé")
    print(f"Modifs: {total}")

    if refresh and total > 0:
        header("Refresh Journey")
        api.refresh_journey(journey_id)
    elif total > 0:
        warn("Utiliser --refresh pour rafraîchir auto")

    return results


def execute_asset(api, asset_id, old, new):
    header(f"Execute asset: {asset_id}")
    warn("Modifications appliquées!")

    if input("\nConfirmer? (oui/non): ").lower() not in ['oui', 'o', 'yes', 'y']:
        print("Annulé.")
        return

    r = api.process_email_asset(asset_id, old, new, dry_run=False)
    if r['success']:
        ok(f"{len(r['changes'])} modifs")
    else:
        err(r.get('error'))
    return r


def scan_journey(api, journey_id, old, new):
    """Scan une journey avec la même logique que analyze"""
    import re
    header(f"Scan: {journey_id}")
    activities, journey = api.get_journey_activities(journey_id)
    print(f"{journey.get('name')} | {journey.get('status')}")
    print(f"Email activities: {len(activities)}\n")

    all_urls = []

    for act in activities:
        print(f"{Fore.CYAN}--- {act['name']} ---{Style.RESET_ALL}")
        asset_id = extract_asset_id(act)
        if not asset_id:
            warn("Asset ID non trouvé")
            continue

        # Utiliser la même logique que analyze (dry_run)
        r = api.process_email_asset(asset_id, old, new, dry_run=True)

        if r['changes']:
            # Extraire les URLs uniques des changements
            found_urls = set()
            for change in r['changes']:
                context = change.get('context', '')
                # Extraire l'URL du contexte
                urls = re.findall(r'https?://[^\s"\'<>]+', context)
                for url in urls:
                    # Nettoyer l'URL
                    clean = re.split(r'\?sez_client_id=|\?campaign=|&sez_', url)[0]
                    clean = clean.rstrip('?&')
                    if f'/{old}' in clean and f'/{old}-' not in clean:
                        found_urls.add(clean)

            unique_urls = sorted(list(found_urls))
            print(f"  {Fore.YELLOW}{len(unique_urls)} URLs uniques (/{old}/):{Style.RESET_ALL}")
            for url in unique_urls:
                print(f"    {url[:100]}")
                all_urls.append({'journey': journey.get('name'), 'activity': act['name'], 'url': url})
        else:
            info("Aucune URL à modifier")

    return all_urls


def extract_asset_id(activity):
    cfg = activity.get('config_args', {})

    if 'triggeredSend' in cfg:
        ts = cfg['triggeredSend']
        return ts.get('emailId') or ts.get('legacyEmailId') or ts.get('assetId')

    for k in ['emailId', 'assetId', 'legacyEmailId', 'contentBuilderAssetId']:
        if cfg.get(k):
            return cfg[k]
    return None


def get_journey_ids(api, journey_arg, all_welcome):
    if all_welcome:
        info("Récupération journeys Welcome...")
        data = api.get_all_journeys()
        welcome = [j for j in data.get('items', []) if is_welcome_journey(j)]
        ids = [j.get('id') for j in welcome]
        print(f"  {len(ids)} trouvées")
        return ids
    elif journey_arg:
        return [j.strip() for j in journey_arg.split(',')]
    return []


def main():
    p = argparse.ArgumentParser(description='SFMC Welcome URL Modifier')
    p.add_argument('--mode', '-m', required=True,
                   choices=['list-journeys', 'list-all-journeys', 'list-emails', 'analyze', 'execute', 'scan'])
    p.add_argument('--journey-id', '-j', help='Journey ID (ou plusieurs: id1,id2)')
    p.add_argument('--asset-id', '-a', help='Asset ID')
    p.add_argument('--all-welcome', action='store_true', help='Toutes les journeys Welcome')
    p.add_argument('--status', '-s', help='Filtrer par status')
    p.add_argument('--name', help='Filtrer emails par nom')
    p.add_argument('--old', '-o', default='fr', help='Pattern source (défaut: fr)')
    p.add_argument('--new', '-n', default='fr-fr', help='Pattern cible (défaut: fr-fr)')
    p.add_argument('--refresh', '-r', action='store_true', help='Refresh journey après modif')
    p.add_argument('--output', '-O', help='Export JSON')

    args = p.parse_args()

    if args.mode in ['analyze', 'execute', 'scan']:
        if not args.journey_id and not args.asset_id and not args.all_welcome:
            err("--journey-id, --asset-id ou --all-welcome requis")
            sys.exit(1)

    header("SFMC Welcome URL Modifier")
    print(f"Mode: {args.mode}")
    print(f"Pattern: /{args.old}/ -> /{args.new}/")

    try:
        info("Connexion...")
        auth = SFMCAuth()
        auth.refresh()
        api = SFMCAPI(auth)

        results = None

        if args.mode == 'list-journeys':
            results = list_journeys(api, args.status, welcome_only=True)

        elif args.mode == 'list-all-journeys':
            results = list_journeys(api, args.status, welcome_only=False)

        elif args.mode == 'list-emails':
            results = list_emails(api, args.name)

        elif args.mode == 'analyze':
            if args.asset_id:
                results = analyze_asset(api, args.asset_id, args.old, args.new)
            else:
                ids = get_journey_ids(api, args.journey_id, args.all_welcome)
                results = []
                for jid in ids:
                    results.extend(analyze_journey(api, jid, args.old, args.new) or [])

        elif args.mode == 'execute':
            if args.asset_id:
                results = execute_asset(api, args.asset_id, args.old, args.new)
            else:
                ids = get_journey_ids(api, args.journey_id, args.all_welcome)
                results = []
                for jid in ids:
                    results.extend(execute_journey(api, jid, args.old, args.new, args.refresh) or [])

        elif args.mode == 'scan':
            ids = get_journey_ids(api, args.journey_id, args.all_welcome)
            results = []
            for jid in ids:
                results.extend(scan_journey(api, jid, args.old, args.new) or [])

            header("Résumé Scan")
            print(f"Total URLs avec /{args.old}/: {len(results)}")

        if args.output and results:
            with open(args.output, 'w') as f:
                json.dump(results, f, indent=2, default=str)
            ok(f"Export: {args.output}")

    except Exception as e:
        err(str(e))
        sys.exit(1)


if __name__ == '__main__':
    main()
