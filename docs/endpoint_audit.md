# Audit sécurité des endpoints — ProjectMgr

**Date** : 2026-07-31
**Périmètre** : tous les endpoints HTTP et WebSocket exposés par l'application (`app/routers/auth.py`, `gantt.py`, `whiteboard.py`, `kanban.py`, `app/main.py`), ainsi que le rendu client correspondant (`static/js/*.js`, `templates/*.html`) pour la partie "les données utilisateur sont-elles échappées / jamais interprétées comme du code".

**Méthode** : lecture complète de chaque routeur et de son modèle Pydantic, vérification champ par champ (typage, bornes, format), recherche des patterns à risque (SQL brut, `innerHTML`/`eval`, traversée de chemin, comparaison non constante pour les secrets), et vérification croisée avec la suite de tests existante (`tests/`). Deux anomalies réelles ont été trouvées et corrigées directement (voir §2) ; le reste du document liste ce qui a été vérifié sans anomalie, et des recommandations opérationnelles hors du périmètre du code applicatif.

Suite à cet audit : **213 tests passent** (`python -m pytest`), y compris un nouveau test couvrant le correctif du §2.1.

---

## 1. Inventaire des endpoints

| Méthode | Route | Auth | Validation des entrées |
|---|---|---|---|
| GET | `/` | — | aucune entrée |
| GET/POST | `/login` | — | `username`/`password` bornés (`Form(..., max_length=...)`), comparaison en temps constant |
| POST | `/logout` | — | aucune entrée |
| POST | `/gantt/new` | — | aucune entrée |
| GET | `/gantt/{gantt_id}` | — | id = regex `^[0-9a-f]{32}$` |
| GET | `/api/gantt/{gantt_id}` | — | id validé, lecture seule |
| PUT | `/api/gantt/{gantt_id}/tasks` | — | `list[TaskIn]` typé/borné, taille de liste plafonnée |
| PATCH | `/api/gantt/{gantt_id}` | — | `RenameIn.name` bornée |
| POST | `/whiteboard/new` | — | aucune entrée |
| GET | `/whiteboard/{whiteboard_id}` | — | id validé |
| GET | `/api/whiteboard/{whiteboard_id}` | — | id validé, lecture seule |
| PATCH | `/api/whiteboard/{whiteboard_id}` | — | `RenameIn.name` bornée |
| POST | `/api/whiteboard/{whiteboard_id}/upload-image` | — | taille plafonnée, type vérifié par contenu (magic bytes), nom de fichier généré serveur |
| GET | `/uploads/whiteboard/{whiteboard_id}/{filename}` | — | `filename` = regex stricte (pas de traversée possible) |
| WS | `/ws/whiteboard/{whiteboard_id}` | — | id validé avant `accept()` ; chaque op (`create`/`update`/`delete`/`react`) validée par un modèle Pydantic dédié + vérification d'appartenance au board |
| POST | `/kanban/new` | — | aucune entrée |
| GET | `/kanban/{kanban_id}` | — | id validé |
| GET | `/api/kanban/{kanban_id}` | — | id validé, lecture seule |
| PATCH | `/api/kanban/{kanban_id}` | — | `RenameIn.name` bornée |
| WS | `/ws/kanban/{kanban_id}` | — | id validé avant `accept()` ; chaque op validée par un modèle dédié + vérification d'appartenance au board (colonne/carte) |

"Auth : —" est intentionnel : conformément aux specs, Gantt/tableau blanc/Kanban sont accessibles sans compte à quiconque a le lien (voir §4.3 pour la nuance sur la session admin).

---

## 2. Anomalies trouvées et corrigées

### 2.1 Confusion de type via l'opération WebSocket `update` du tableau blanc — **corrigé**

`app/routers/whiteboard.py`, handler `update` : le code appliquait tous les champs de `msg.element.model_dump()` (y compris `type`) sur l'élément existant en base, sans vérifier que le `type` envoyé correspondait au type réel de l'élément ciblé. Un client WebSocket pouvait donc envoyer une opération `update` avec `"type": "line"` sur l'`id` d'un élément texte existant : la validation Pydantic passait (les données étaient valides *pour une ligne*), et l'élément était silencieusement retypé, perdant son contenu texte/réactions au profit d'un tracé vide.

Ce n'est pas une escalade de privilège (l'attaquant devait déjà avoir le lien du tableau, qui donne de toute façon un accès complet en lecture/écriture à tous ses éléments), mais c'est un comportement non voulu et destructeur — un simple bug de logique exploitable pour saboter un tableau partagé (transformer discrètement tous les blocs texte d'une classe en traits vides, par exemple).

**Correctif** : rejet explicite si `msg.element.type != element.type`, avec un nouveau test de non-régression (`test_websocket_update_rejects_changing_element_type`, `tests/test_security_whiteboard.py`).

### 2.2 Branche de validation inatteignable dans `ElementIn.normalize_data` — **nettoyé**

Même fichier : la branche `elif len(json.dumps(self.data)) > MAX_DATA_JSON_LENGTH: raise ValueError(...)` ne pouvait jamais s'exécuter, puisque `check_type()` (validateur de champ, exécuté avant les validateurs de modèle) restreint déjà `type` aux 4 valeurs couvertes par les branches précédentes (`text`/`line`/`image`/`table`). Ce n'était pas exploitable — c'est du code mort — mais il laissait croire à tort qu'un type inconnu pouvait passer avec seulement une vérification de taille JSON, sans validation de structure. Supprimé avec la constante `MAX_DATA_JSON_LENGTH` devenue inutilisée, et un commentaire explicite ajouté pour que ça reste vrai si un type est ajouté plus tard.

---

## 3. Points vérifiés — sans anomalie

### 3.1 Typage et bornes des champs

Tous les champs acceptés en entrée (REST et WebSocket) sont typés et bornés via Pydantic, sans exception trouvée :
- Identifiants de ressources (`gantt_id`, `whiteboard_id`, `kanban_id`) : regex `^[0-9a-f]{32}$` appliquée par FastAPI (`Path(pattern=...)`) **avant** tout accès base de données ou disque — un id malformé renvoie 422 sans jamais atteindre le code métier.
- Chaînes libres : toutes bornées en longueur (`max_length`), ex. texte de bloc `2000`, cellule de tableau `500`, titre de colonne Kanban `100`, nom de tableau/gantt/kanban `200`.
- Nombres : toutes les coordonnées, tailles, index, compteurs ont des bornes `ge`/`le` explicites (ex. `z_index` ∈ [-1 000 000, 1 000 000], `font_size` ∈ [8, 96], `stroke_width` ∈ [1, 40]).
- Couleurs : format `#rrggbb` strict via regex (`HEX_COLOR_RE`), utilisé de façon cohérente dans les 3 routeurs.
- Collections : toutes plafonnées (paragraphes ≤ 200, segments/paragraphe ≤ 100, points/trait ≤ 2000, lignes de tableau ≤ 50, colonnes ≤ 20, colonnes Kanban ≤ 20, cartes/colonne ≤ 200, votes/réaction ≤ 1000).
- Enums informels (`type` d'élément, `reaction`, opération WebSocket `op`) : validés contre un ensemble fermé, jamais utilisés tels quels dans une requête ou un chemin de fichier.
- Identifiant de voteur anonyme (`voter_id`) : contraint au même format que les ids de ressources (`^[0-9a-f]{32}$}`), pas de chaîne libre.

Les modèles Pydantic utilisent le comportement par défaut (`extra="ignore"`) : des clés inconnues envoyées dans `data` sont silencieusement ignorées et jamais renvoyées (vérifié par `test_websocket_ignores_unexpected_extra_fields_in_text_data` côté whiteboard) — pas de risque de pollution de structure.

### 3.2 Injection SQL

Aucune requête SQL brute nulle part dans `app/` (recherché explicitement : aucun `.execute(text(...))`, aucune interpolation de chaîne dans une requête). Tous les accès passent par l'ORM SQLAlchemy (`db.get(...)`, `db.query(...).filter(...)`), qui paramètre systématiquement les valeurs. Risque nul.

### 3.3 XSS / injection HTML

- Les templates Jinja2 n'utilisent nulle part le filtre `| safe`, `Markup()` ni `autoescape false` — l'échappement automatique de Jinja2 reste actif partout où une donnée utilisateur (nom de tableau, de gantt, de kanban) est affichée côté serveur.
- Côté client, tout le contenu utilisateur (texte enrichi, cellules de tableau, cartes Kanban, titres de colonnes) est stocké sous forme de **structure** (paragraphes/segments, cellules typées), jamais de HTML brut — et inséré dans le DOM exclusivement via `textContent`/`createTextNode`, jamais `innerHTML` avec une valeur utilisateur. Vérifié explicitement : les seules occurrences d'`innerHTML` dans `whiteboard.js` sont des remises à vide (`= ''`) avant reconstruction via `createElement`/`textContent` ; `kanban.js` n'utilise `innerHTML` nulle part ; `gantt.js` ne l'utilise que pour un en-tête de tableau statique (aucune donnée utilisateur interpolée). Aucun `insertAdjacentHTML`, `document.write` ou `eval` dans tout `static/js/`.
- Les appels `document.execCommand(...)` (gras/italique/souligné/barré/liste/saut de ligne) utilisent tous des commandes fixes, jamais de commande `insertHTML` construite à partir d'une entrée utilisateur.
- Tests dédiés déjà en place et toujours verts : le texte contenant `<img src=x onerror=alert(1)>` est stocké tel quel (jamais interprété) et n'apparaît jamais dans le HTML de la page (`test_websocket_text_content_is_never_reflected_as_html`, `test_websocket_table_cell_content_is_never_reflected_as_html`, `test_websocket_card_text_is_never_reflected_as_html`, `test_websocket_column_title_is_never_reflected_as_html`).

### 3.4 Traversée de chemin (path traversal) et upload de fichiers

- Écriture : le nom de fichier stocké sur disque est **toujours généré côté serveur** (`uuid.uuid4().hex` + extension déduite du contenu réel), jamais dérivé du nom de fichier envoyé par le client.
- Lecture (`GET /uploads/whiteboard/{whiteboard_id}/{filename}`) : `filename` est contraint par une regex stricte (`^[0-9a-f]{32}\.(png|jpg|gif|webp)$`) qui exclut structurellement tout `..` ou `/` — testé explicitement contre `../../etc/passwd` et variantes (`test_get_uploaded_image_rejects_malformed_filename`).
- Le type de fichier n'est jamais déduit du `Content-Type` déclaré par le client ni de l'extension fournie : `sniff_image_ext()` vérifie les magic bytes réels (PNG/JPEG/GIF/WEBP) ; un exécutable renommé en `.png` est rejeté (`test_upload_rejects_executable_disguised_as_image`).
- `ImageData.src` (utilisé uniquement comme attribut `<img src>` côté client, jamais pour ouvrir un fichier côté serveur) est contraint à commencer par `/uploads/whiteboard/` — un `javascript:` ou une URL externe est rejeté (`test_image_element_rejects_src_outside_upload_endpoint`).

### 3.5 Authentification et secrets

- `POST /login` compare `username` et `password` avec `secrets.compare_digest` (temps constant) — y compris pour le nom d'utilisateur, ce qui évite une fuite par timing sur sa validité.
- Limitation de tentatives : 10 essais / 5 min par IP cliente, vérifiée avant la comparaison des identifiants.
- Isolation par élément/board : chaque opération WebSocket (`update`, `delete`, `react`, opérations Kanban sur colonne/carte) revérifie que la ressource ciblée par son id appartient bien au board de la connexion en cours — testé explicitement pour le cross-board sur whiteboard et kanban (élément/colonne/carte d'un autre board toujours rejeté, avec vérification que la ressource réelle n'est pas altérée).

### 3.6 Confusions de type / autres routeurs

Gantt et Kanban n'ont pas de champ `type` polymorphe comme les éléments du tableau blanc (une tâche Gantt et une carte Kanban ont chacune un seul schéma fixe) — la classe de bug du §2.1 ne peut pas s'y reproduire par construction.

---

## 4. Recommandations opérationnelles (hors correctif de code)

Ces points ne sont pas des failles dans le code applicatif lui-même, mais des réglages de déploiement/configuration à ne pas oublier — je ne les ai pas modifiés puisqu'ils dépendent de décisions hors du périmètre d'un audit de code (variables d'environnement de production, configuration du reverse proxy).

1. **Valeurs par défaut de `app/config.py`** : `ADMIN_PASSWORD` vaut `"changeme"` et `SECRET_KEY` vaut `"dev-secret-key-change-me"` si les variables d'environnement correspondantes ne sont pas définies. C'est pratique en développement mais **doit impérativement être écrasé en production** — sinon le mot de passe admin est un secret public (dans ce document !) et la clé de session est prévisible (permettant de forger des cookies de session). Recommandation : faire échouer le démarrage si `ADMIN_PASSWORD`/`SECRET_KEY` valent encore leur défaut et qu'une variable style `ENV=production` est positionnée.
2. **Taille de requête non plafonnée en amont** : les endpoints JSON (notamment `PUT /api/gantt/{id}/tasks`, qui accepte une liste) laissent Starlette/Pydantic parser tout le corps de la requête avant que la vérification `len(tasks) > MAX_TASKS_PER_GANTT` s'applique — un corps volumineux coûte du CPU/mémoire avant d'être rejeté. Pareil pour l'upload d'image tant que le corps n'est pas entièrement reçu par le serveur ASGI (la lecture bornée à `MAX_IMAGE_BYTES + 1` protège la mémoire de la *requête applicative*, pas la réception du corps HTTP lui-même). Les messages WebSocket, eux, sont déjà implicitement plafonnés par la taille de frame par défaut de la bibliothèque `websockets` utilisée par uvicorn. Recommandation : configurer une limite de taille de corps de requête au niveau du reverse proxy (Traefik, déjà prévu par les specs pour la prod).
3. **Rate limiting du login en mémoire process** : `_login_attempts` (dans `app/routers/auth.py`) est un dict Python en mémoire — il repart à zéro à chaque redémarrage et ne serait pas partagé entre plusieurs workers si l'app tournait un jour en multi-process. Sans impact tant qu'il n'y a qu'un seul worker (cas actuel), à surveiller si ça change.
4. **Fiabilité de l'IP cliente pour le rate limiting** : la clé de rate limiting est `request.client.host`. Derrière Traefik, ça ne reflète l'IP réelle du visiteur que si les en-têtes `X-Forwarded-For`/proxy sont correctement transmis et que uvicorn est configuré pour ne faire confiance qu'à l'IP du proxy Traefik (`--forwarded-allow-ips`) — une mauvaise configuration pourrait soit fusionner tous les visiteurs dans un seul compteur (un visiteur bloque tout le monde), soit permettre à un attaquant de contourner la limite en changeant d'IP annoncée.
5. **Le login admin ne protège aujourd'hui aucun endpoint** : `is_admin()`/la session `admin` ne servent qu'à afficher un badge sur la page d'accueil — aucune route ne vérifie l'authentification admin. C'est cohérent avec les specs actuelles ("aucun compte requis"), mais à garder en tête : le jour où des fonctionnalités enseignant/session seront ajoutées, il faudra explicitement gater ces nouvelles routes (`Depends(require_admin)` ou équivalent), l'existence de `/login` ne le fait pas automatiquement.
6. **En-têtes de sécurité HTTP** : aucun en-tête `X-Content-Type-Options: nosniff` / CSP n'est positionné globalement. Risque faible ici (les uploads sont déjà validés par contenu, pas par extension), mais une amélioration de défense en profondeur peu coûteuse à ajouter via un middleware si souhaité.

---

## 5. Conclusion

Deux anomalies réelles trouvées, toutes deux corrigées et couvertes par un test de non-régression : une confusion de type exploitable via l'opération `update` du tableau blanc (comportement destructeur, pas de fuite de données ni d'escalade de privilège), et du code de validation mort/trompeur. Le reste de la surface auditée — typage/bornes des champs, absence d'injection SQL, absence de XSS (stockage structuré + `textContent` partout, jamais de HTML brut), absence de traversée de chemin sur les uploads, vérification d'appartenance systématique des ressources à leur board — n'a révélé aucune anomalie. Les points restants sont des réglages de déploiement (secrets par défaut, limites de taille de requête, configuration du reverse proxy) à traiter au moment de la mise en production plutôt que dans le code applicatif.
