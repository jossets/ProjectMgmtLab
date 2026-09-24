# ProjectMgr

## Descriptions

Logiciel en mode serveur http tournant dans un navigateur, offrant toutes les fonctionnalités nécessaires pour donner un cours interactif sur la gestion de projet.

- Comptes enseignant
- Sessions de cours
- Compte élève
- Cours par thématique, rédigé au format markdown
- QCM d'évaluation par thématique
- Tableau blanc partagé de type https://miro.com light.
- Outil de création de Gantt.
- Outil de création de Kanban


## Cours

Les cours sont rangés dans le répertoire /cours.
Ils sont nommés /cours/XX_YYYYY.md
Un fichier par thématique au format markdown enrichi.

Les QCM sont rangés dans le répertoire /cours.
Ils sont nommés /cours/XX_YYYYY_qcm.md
Un fichier par thématique au format markdown enrichi.
Les + sont les bonnes réponses, les - les mauvaises.

Chaque titre `#`, `##` ou `###` d'un fichier de cours forme une diapositive (un `####` reste dans le contenu de la diapositive parente).
Depuis la page d'une session (`/sessions/{id}`), l'enseignant ouvre `/sessions/{id}/cours` : il choisit le cours à présenter et navigue dans l'arborescence à gauche, ce qui déplace en direct la diapositive affichée à droite chez tous les élèves connectés à cette session. Un élève peut revoir librement les diapositives déjà présentées, avec un bouton pour revenir sur la position de l'enseignant.

## Installation et lancement (V0)

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
copy .env.example .env      # puis éditer ADMIN_USERNAME / ADMIN_PASSWORD / SECRET_KEY
uvicorn app.main:app --reload
```

Le serveur écoute par défaut sur http://127.0.0.1:8000.

### Windows : script de lancement

`scripts/run.ps1` automatise les étapes ci-dessus (venv, dépendances, `.env` avec `SECRET_KEY` généré, démarrage) :

```powershell
.\scripts\run.ps1              # crée le venv si besoin, installe, lance sur :8000
.\scripts\run.ps1 -Dev         # installe aussi requirements-dev.txt (tests)
.\scripts\run.ps1 -Port 8080   # autre port
.\scripts\run.ps1 -SkipInstall # ne réinstalle pas les dépendances
```

- `/` : page d'accueil, bouton "Nouveau Gantt" (accessible sans connexion).
- `/login` : connexion admin (identifiants définis dans `.env`).
- `/gantt/{id}` : outil de Gantt, accessible à toute personne ayant l'url.

La base de données SQLite est créée automatiquement dans `data/app.db` au premier lancement.

Sur `/accounts`, l'administrateur dispose d'une sauvegarde/restauration de la base : téléchargement à la demande (copie cohérente via l'API de backup SQLite, même si le serveur écrit en même temps) et restauration par upload d'un fichier `.db` (contrôle d'intégrité + vérification que c'est bien une base ProjectMgr avant remplacement ; une copie de l'ancienne base est conservée sur le serveur sous `data/app.db.before-restore-<horodatage>`).

## Docker

```bash
docker build -t projectmgr .
docker run -d \
  -p 8000:8000 \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD=change-me \
  -e SECRET_KEY=$(openssl rand -hex 32) \
  -e SESSION_HTTPS_ONLY=true \
  -v projectmgr-data:/app/data \
  --name projectmgr \
  projectmgr
```

Le conteneur tourne en un seul worker uvicorn (voir le commentaire dans le `Dockerfile` — la synchronisation temps réel du tableau blanc/Kanban et le rate-limiting du login vivent en mémoire du process, pas en multi-worker). Le volume `/app/data` conserve `app.db` et les images uploadées entre redémarrages.

### Derrière Traefik

Traefik v2+ relaie les connexions WebSocket (`/ws/whiteboard/{id}`, `/ws/kanban/{id}`) de façon transparente dès qu'un routeur HTTP standard pointe vers ce service — pas de flag "activer les websockets" à chercher, contrairement à certains autres reverse proxies. Le seul point à surveiller : les timeouts d'inactivité de Traefik (`transport.respondingTimeouts.idleTimeout` dans la config statique) peuvent couper une connexion WS restée silencieuse trop longtemps ; le client du tableau blanc/Kanban se reconnecte déjà automatiquement dans ce cas, donc sans casser l'usage, mais une valeur trop courte peut provoquer des reconnexions visibles côté élève. Ne pas répliquer ce conteneur derrière Traefik sans affinité de session (sticky sessions) tant que l'état partagé (rooms WS, rate-limiting) reste en mémoire du process — voir plus haut.

`docker-compose.yml` route `projects.yolospacehacker.com` via des labels Traefik (TLS + certresolver `letencrypt`), avec les entrypoints (`http`/`https`) et le réseau externe (`traefik_lan`) déjà alignés sur le Traefik en place (`traefik.yml`) — pas de redirection HTTP→HTTPS ajoutée côté service, l'entrypoint `http` de ce Traefik la fait déjà globalement. Lancement :

```bash
copy .env.example .env   # puis éditer ADMIN_USERNAME / ADMIN_PASSWORD / SECRET_KEY
docker compose up -d --build
```

## Tests

Tests unitaires (fonctionnalités) et tests de sécurité (validation des entrées, injections) dans `/tests`, basés sur `pytest` et le `TestClient` de FastAPI. Ils utilisent une base SQLite temporaire dédiée, sans toucher à `data/app.db`.

```bash
pip install -r requirements-dev.txt
python -m pytest
```


