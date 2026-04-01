# SFMC Welcome URL Modifier - CLI

## C'est quoi ?

Script en ligne de commande qui remplace les URLs `/fr/` par `/fr-fr/` dans les emails des journeys **Welcome** sur Salesforce Marketing Cloud.

Fonctionnement identique au CLI Transactional (`sfmc-url-modifier/`), avec une seule différence : le filtre. Ce script ne remonte que les journeys dont le nom contient "welcome" (insensible à la casse), là où le CLI Transactional filtre sur le type `Transactional` de l'API SFMC.

Le reste (authentification, patterns de remplacement, cache, refresh) est le même code.

## Setup

### 1. Environnement Python

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration des credentials

```bash
cp .env.example .env
```

Ouvrir `.env` et renseigner les 4 variables :

```
SFMC_CLIENT_ID=votre_client_id
SFMC_CLIENT_SECRET=votre_client_secret
SFMC_SUBDOMAIN=mcXXXXXXXX
SFMC_MID=123456789
```

Si le CLI Transactional est déjà configuré, copier son `.env` :

```bash
cp ../sfmc-url-modifier/.env .env
```

### 3. Vérifier la connexion

```bash
python test_connection.py
```

Ce script teste l'authentification et affiche la liste des journeys Welcome détectées.

## Utilisation 

### Lister les journeys Welcome

```bash
python main.py -m list-journeys
```

Affiche uniquement les journeys dont le nom contient "welcome". Chaque entrée montre l'ID, le nom et le statut. Copier l'ID pour les étapes suivantes.

### Scanner les URLs

```bash
python main.py -m scan -j "JOURNEY_ID"
```

Affiche les URLs trouvées dans les emails de la journey, avec indication de celles qui matchent le pattern. Ne modifie rien.

### Analyser les changements (dry-run)

```bash
python main.py -m analyze -j "JOURNEY_ID"
```

Simule le remplacement et montre le avant/après. Aucune modification n'est envoyée à SFMC. **Toujours faire un dry-run avant d'exécuter.**

### Exécuter les remplacements

```bash
# Sur une journey
python main.py -m execute -j "JOURNEY_ID" --refresh

# Sur plusieurs journeys
python main.py -m execute -j "id1,id2,id3" --refresh

# Sur TOUTES les journeys Welcome
python main.py -m execute --all-welcome --refresh
```

L'option `--refresh` relance la journey pour que les modifications soient prises en compte.

### Exporter en JSON

```bash
python main.py -m analyze -j "JOURNEY_ID" -O resultats.json
```

### Changer le pattern

```bash
python main.py -m execute -j "JOURNEY_ID" -o "en" -n "en-gb" --refresh
```

## Toutes les options

| Option | Rôle | Défaut |
|--------|------|--------|
| `-m, --mode` | Action : `list-journeys`, `scan`, `analyze`, `execute` | obligatoire |
| `-j, --journey-id` | ID(s) de journey, séparés par virgule | — |
| `--all-welcome` | Traiter toutes les journeys Welcome | `false` |
| `-o, --old` | Pattern source à chercher | `fr` |
| `-n, --new` | Pattern de remplacement | `fr-fr` |
| `-r, --refresh` | Rafraîchir la journey après modification | `false` |
| `-O, --output` | Chemin du fichier JSON d'export | — |

## Fichiers du projet

```
main.py            Point d'entrée, parsing des arguments, orchestration
sfmc_api.py        Appels REST vers l'API SFMC, logique de remplacement
sfmc_auth.py       Authentification OAuth2, gestion du token
config.py          Chargement des variables depuis .env
test_connection.py Diagnostic connexion + listing des journeys Welcome
test_local.py      Test des patterns sur un fichier HTML local (sans connexion)
```

## Différence avec le CLI Transactional

| | CLI Transactional | CLI Welcome |
|-|-------------------|-------------|
| Filtre | Type API = `Transactional` | Nom contient "welcome" |
| Flag batch | `--all-transac` | `--all-welcome` |
| Tout le reste | Identique | Identique |