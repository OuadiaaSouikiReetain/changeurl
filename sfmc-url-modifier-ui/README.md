# SFMC URL Modifier - Web UI

## C'est quoi ?

Interface web qui permet de corriger les URLs dans les emails des journeys SFMC sans passer par la ligne de commande. On se connecte, on sélectionne les journeys à traiter, et on lance les modifications en quelques clics.

C'est la version visuelle de l'outil CLI. Elle offre les mêmes fonctionnalités (scan, analyse, exécution) mais avec un retour visuel immédiat : modals de résultats, toasts de confirmation, indicateur de cache, etc.

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

Ouvrir `.env` et renseigner les 4 variables SFMC :

```
SFMC_CLIENT_ID=votre_client_id
SFMC_CLIENT_SECRET=votre_client_secret
SFMC_SUBDOMAIN=mcXXXXXXXX
SFMC_MID=123456789
```

Si le CLI est déjà configuré, on peut simplement copier son `.env` :

```bash
cp ../sfmc-url-modifier/.env .env
```

### 3. Lancer le serveur

```bash
python app.py
```

Ouvrir **http://localhost:5001** dans le navigateur.

## Utilisation 

### Étape 1 — Connexion

Au lancement, cliquer sur "Connexion" dans la sidebar. L'application utilise les credentials du `.env` pour s'authentifier via OAuth2 auprès de l'API SFMC. Un indicateur vert confirme que la connexion est active.

### Étape 2 — Filtrer les journeys

Trois filtres disponibles dans la sidebar :

- **Welcome** : uniquement les journeys dont le nom contient "welcome"
- **Transactional** : uniquement les journeys de type transactionnel
- **Toutes** : toutes les journeys actives

Le premier chargement prend environ 100 secondes (6000+ journeys à récupérer). Ensuite, le cache prend le relais et les changements de filtre sont instantanés.

### Étape 3 — Sélectionner les journeys

Cocher les journeys à traiter dans la liste. La pagination affiche 50 journeys à la fois ; cliquer "Charger plus" pour la suite. Le compteur en haut de la liste indique le nombre de journeys affichées sur le total.

### Étape 4 — Lancer une action

Trois boutons en bas de la liste :

| Bouton | Ce que ça fait | Modifie les emails ? |
|--------|---------------|---------------------|
| **Scanner** | Affiche toutes les URLs détectées dans les emails sélectionnés | Non |
| **Analyser** | Dry-run : montre le avant/après de chaque remplacement | Non |
| **Exécuter** | Applique les remplacements et rafraîchit les journeys | **Oui** |

**Toujours faire Scanner puis Analyser avant d'Exécuter.** Les résultats s'affichent dans une modal avec le détail par journey et par email.

### Configuration du pattern

Le pattern de remplacement par défaut est `/fr/` → `/fr-fr/`. Il est modifiable dans la sidebar sous "Configuration pattern" si besoin de traiter un autre cas (ex: `/en/` → `/en-gb/`).

## Cache et performances

L'API SFMC est lente (~100s pour charger toutes les journeys). Un cache en mémoire avec un TTL de 5 minutes évite de relancer cet appel à chaque interaction.

| Action | Sans cache | Avec cache |
|--------|-----------|------------|
| Premier chargement | ~100s | ~100s |
| Changement de filtre | ~100s | ~0.02s |
| Rechargement de page | ~100s | ~0.02s |

Le bouton "Actualiser" dans la sidebar force un rechargement complet en bypassant le cache.

## Cas Welcome Multistep

Les journeys `Welcome / Multistep` passent par le meme bouton `Rafraichir`, mais l'application utilise uniquement l'endpoint API SFMC cote backend.

Si SFMC refuse la republication (`403`), l'UI affiche un message explicite indiquant que la republication automatique a ete refusee et qu'une action manuelle cote SFMC reste necessaire.

## Endpoints API

Pour ceux qui veulent intégrer ou débugger, voici les routes exposées par le serveur Flask :

| Route | Méthode | Description |
|-------|---------|-------------|
| `/` | GET | Page HTML principale |
| `/api/connect` | POST | Authentification SFMC (utilise les credentials du .env) |
| `/api/journeys` | GET | Liste paginée des journeys (params: `type`, `page`) |
| `/api/scan` | POST | Extraction des URLs des journeys sélectionnées |
| `/api/analyze` | POST | Dry-run : prévisualisation des remplacements |
| `/api/execute` | POST | Application des modifications + refresh |

## Fichiers du projet

```
app.py               Serveur Flask, définition des 6 endpoints, logique métier
templates/index.html  Interface complète (HTML + CSS + JS dans un seul fichier)
requirements.txt      Dépendances Python (Flask, requests, python-dotenv)
```
