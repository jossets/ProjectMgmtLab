# ProjectMgr

## Descriptions

Logiciel en mode serveur http tournant dans un navigateur, offrant toutes les fonctionnalités nécessaires pour sonnreun cours interactif sur la gestion de projet:

- Comptes enseignant
    - un compte admin tagué enseignant avec password dans fichier de conf
    - un compte enseignant peut créer des comptes admin ou eleves
    - un compte enseignant peut lister les éleves et les affecter à une sesison de cours
- Sessions de cours. 
    - Les sessions sont créées par un enseignant. 
    - Une session pêut être bloquée ou active. Active les élèves y ont accès. Bloqué, ils ont le status bloqué
    - Un lien d'invitation de session permet de créer un compte élève lié à une session
    - Un tableau blanc est affecté à chaque session. Des tableaux supplémentaires peuvent être ajoutés par un enseignant.
- Compte élève
    - un élève se connectant a sa page de profil qui liste les sessions: bloquées ou actives.
    - Il peut consuklter les cours des sessions actives
    - Il peut effectuer les QCM actifs, des sessions actives
- Cours par thématique, rédigé au format markdown
    - un enseigant peut lister les cours thématiques disponibles, et en affecter à une session    
- QCM d'évaluation par thématique
    - dans une session, un enseignant peut activer un QCM sur une durée limité
    - les élèves peuvent passer un qcm lié aux thématiques de la session, une seule fois
    - Les résultats des QCM sont résumés sur une page lisible par les enseignants
- Tableau blanc partagé de type https://miro.com light.
    - créer des blocs de texte (taille des caractères, couleur du texte, gras, italique, couleur du fond, couleur du bord...) déplaçable
    - dessiner des traits (taille, couleur)
    - uploader et coller des images, les redimentionner
    - remplir des tableaux de texte (5 lignes, 2 colonnes, ajouter/effacer ligne, ajouter/effacer colonne).    
- Outil de création de Gantt. Chacun eleve ou enseignant pour créer des Gantt, et les référencer par leur url.
    - Un gantt peut être affecté à une session (il est réservé aux membres du de la session) ou à rien (il est accessible à toute personne ayant l'id du gantt)
    - Partie gauche: Nom de la tache, début, fin, progress en %. Indentable pour afficher comme une sous-tache de la ligne au dessus.
    - Partie droite: représentation visuelle, scrolable
- Outil de création de Kanban
    - Un Kanban peut être affecté à une session (il est réservé aux membres de la session) ou à rien (il est accessible à toute personne ayant l'id du Kanban)
    - Colonnes avec titre
    - Post-it qui peut se décaller



## Organisation 

Les cours sont rangés dans le répertoire /cours, un fichier par thématique au format markdown enrichi.
Le serveur est up, et sert sur http. En prod il sera exposé par traefik en https dansun docker.
L'admin se connecte et créé un ou plusieurs comptes enseignants.
L'enseigant se connecte (login/password). Il créé une session (nom de session). Il affecte des thématiques de cours à la session via la page de session.
Il peut préparer son cours sur le tableau blanc de la session.

Le cours a lieu en présentiel ou via une plateforme de type Discord.
L'enseignant communique l'url du serveur avec un code de session. (https://projectmgr.yoloctf.org/join/3D6GH75)
L'élève est invité à se connecter s'il a déjà un compte ou créer un compte.
L'élève est ajouté à la session.
L'enseignant peut bloquer la création de compte ou l'authoriser. Il peut bloquer des comptes déjà créés.
Les élèves peuvent interajir sur le tableau blanc, et les outils de type gantt.


## Specs 

- Serveur en python
- Sauvegarde des données en base de donnée sqllite.
