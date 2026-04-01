# SFMC URL Modifier - CLI Transactional

## C'est quoi ?

Script en ligne de commande qui remplace automatiquement les URLs `/fr/` par `/fr-fr/` dans les emails des journeys **transactionnelles** sur Salesforce Marketing Cloud.

Il se connecte à l'API SFMC, récupère le contenu HTML de chaque email dans les journeys sélectionnées, applique les remplacements, puis met à jour les emails via l'API. L'option `--refresh` relance la journey pour que les changements soient pris en compte immédiatement.

Ce CLI ne traite que les journeys de type "Transactional". Pour les journeys Welcome, voir le projet `sfmc-welcome-url-modifier/`.

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



### 3. Vérifier la connexion

```bash
python test_connection.py
```

Ce script tente une authentification OAuth2 et affiche le nombre de journeys récupérées. Si ça échoue, vérifier les credentials dans `.env`.

## Utilisation 

### Lister les journeys disponibles

```bash
# Uniquement les transactionnelles
python main.py -m list-journeys

# Toutes les journeys (transac + welcome + autres)
python main.py -m list-all-journeys
```

Chaque journey est affichée avec son ID, son nom et son statut. Copier l'ID de la journey à traiter pour les étapes suivantes.

### Scanner les URLs d'une journey

```bash
python main.py -m scan -j "JOURNEY_ID"
```

Affiche toutes les URLs trouvées dans les emails de la journey, avec indication de celles qui matchent le pattern `/fr/`. Ne modifie rien.

### Analyser les changements (dry-run)

```bash
python main.py -m analyze -j "JOURNEY_ID"
```

Simule le remplacement et affiche le avant/après pour chaque URL. Aucune modification n'est envoyée à SFMC. **Toujours faire un dry-run avant d'exécuter.**

### Exécuter les remplacements

```bash
# Sur une journey
python main.py -m execute -j "JOURNEY_ID" --refresh

# Sur plusieurs journeys (IDs séparés par des virgules, sans espaces)
python main.py -m execute -j "id1,id2,id3" --refresh

# Sur TOUTES les journeys transactionnelles
python main.py -m execute --all-transac --refresh
```

L'option `--refresh` relance automatiquement la journey après modification pour que les nouveaux emails utilisent les URLs corrigées.

### Exporter les résultats en JSON

```bash
python main.py -m analyze -j "JOURNEY_ID" -O resultats.json
```

Utile pour garder une trace de ce qui a été modifié ou pour du reporting.

### Changer le pattern de remplacement

Par défaut, l'outil remplace `fr` par `fr-fr`. Pour un autre pattern :

```bash
python main.py -m execute -j "JOURNEY_ID" -o "en" -n "en-gb" --refresh
```

## Toutes les options

| Option | Rôle | Défaut |
|--------|------|--------|
| `-m, --mode` | Action : `list-journeys`, `list-all-journeys`, `scan`, `analyze`, `execute` | obligatoire |
| `-j, --journey-id` | ID(s) de journey, séparés par virgule | — |
| `-a, --asset-id` | Cibler un email directement par son asset ID | — |
| `--all-transac` | Traiter toutes les journeys transactionnelles | `false` |
| `-o, --old` | Pattern source à chercher | `fr` |
| `-n, --new` | Pattern de remplacement | `fr-fr` |
| `-r, --refresh` | Rafraîchir la journey après modification | `false` |
| `-O, --output` | Chemin du fichier JSON d'export | — |

## Fichiers du projet

```
main.py            Point d'entrée, parsing des arguments, orchestration
sfmc_api.py        Appels REST vers l'API SFMC, cache des journeys, logique de remplacement
sfmc_auth.py       Authentification OAuth2, gestion et renouvellement du token
config.py          Chargement des variables d'environnement depuis .env
test_connection.py Diagnostic rapide : teste l'auth et affiche les journeys
test_local.py      Test des patterns de remplacement sur un fichier HTML local (sans connexion SFMC)
```