# hub_deals — résumé dense

Détecteur de bonnes affaires vol, basé sur l'API Travelpayouts, avec stockage SQLite,
détection d'anomalie de prix vs historique, et notification Telegram. Tourne en tâche
planifiée Windows quotidienne.

**✅ Vérifié en conditions réelles le 2026-08-03 10:47** — tâche "Traqueur de vols" déclenchée manuellement après tous les correctifs ci-dessous : `LastTaskResult=0`, 6 hubs traités (BZV/FIH absents), token API et token Telegram lus depuis l'environnement et fonctionnels, `anomaly_detection.py` importé sans erreur, notification Telegram envoyée avec succès (2 anomalies), base passée de 437 à 471 lignes. Aucune erreur dans `flight_deals_log.txt`.

## Fichiers (`C:\Users\Dell\hub_deals\`)

| Fichier | Rôle |
|---|---|
| `hub_deals_db.py` | **Script principal**, exécuté par la tâche planifiée. Fetch → SQLite → classement → notif Telegram (délègue la détection d'anomalie à `anomaly_detection.py`). |
| `anomaly_detection.py` | **Ajouté le 2026-08-03.** Source unique de la logique de détection d'anomalie (moyenne historique, seuil `SEUIL_BAISSE`), importée par les deux scripts ci-dessous. |
| ~~`hub_deals.py`~~ | Supprimé le 2026-08-03 (obsolète, plus utilisé). |
| `detect_anomalies.py` | Outil CLI d'analyse/diagnostic (relit `flight_deals.db`), mode diagnostic inclus. Réécrit le 2026-08-03 en wrapper fin autour de `anomaly_detection.py`. |
| `test_travelpayouts.py` | Script de test API brut (offres DKR + prix les moins chers 48h). Dev/debug uniquement. |
| `flight_deals.db` | SQLite, table unique `offres`. 437 lignes, 15 relevés, du 2026-07-21 au 2026-08-03. |
| `flight_deals_log.txt` | Log texte append-only (12.6 Ko), horodaté UTC. |

## Tâche planifiée

- Nom : **"Traqueur de vols"** — État : Ready — Action : exécute `hub_deals_db.py`
- Fréquence observée dans les logs : ~quotidienne (ex. 2026-08-02 15:14, 2026-08-03 08:05)
- Le script attend 30s au démarrage (`time.sleep(30)`) pour laisser le réseau se stabiliser après ouverture de session — sans quoi l'appel API plante.

## Flux d'exécution (`hub_deals_db.py`)

1. `init_db()` — crée `offres` si absente
2. Pour chaque hub dans `RABATTEMENT` (8 hubs) → `get_special_offers()` (API Travelpayouts `/aviasales/v3/get_special_offers`) → `enregistrer_offres()`
3. `classement_du_jour()` — tri par `total_estime` ASC
4. `verifier_et_notifier_anomalies()` — moyenne historique par (hub, destination), si baisse ≥ `SEUIL_NOTIFICATION` (8%) et ≥2 relevés historiques → notif Telegram HTML
5. Log de chaque étape dans `flight_deals_log.txt` + console

## Schéma DB (`offres`)

```
id, date_collecte, hub_origine, destination_code, destination_nom,
prix_vol_hub, rabattement, total_estime, date_depart, lien
```
`total_estime = prix_vol_hub + rabattement` (coût de rabattement Dakar → hub, forfaitaire, pas dynamique).

## Hubs configurés (RABATTEMENT, coûts juillet 2026)

| IATA | Ville | Coût rabattement | Durée |
|---|---|---|---|
| CMN | Casablanca | 400€ | 4h |
| CDG | Paris | 300€ | 6h |
| IST | Istanbul | 400€ | 7h |
| ADD | Addis-Abeba | 500€ | 6h |
| NBO | Nairobi | 500€ | 8h |
| ABJ | Abidjan | 200€ | 2h |

~~BZV (Brazzaville, 470€/6h) et FIH (Kinshasa, 570€/7h)~~ — **retirés le 2026-08-03**, voir décision ci-dessous.

## Stats actuelles de la base

- 437 lignes / 15 relevés (2026-07-21 → 2026-08-03)
- Répartition : Paris 135, Istanbul 135, Casablanca 134, Nairobi 19, Addis-Abeba 13, Abidjan 1
- BZV et FIH : 0 offre sur 15 relevés historiques (avant retrait)

## ✅ Décision BZV/FIH (2026-08-03)

Appel direct de l'API Travelpayouts (`get_special_offers`) pour BZV et FIH : réponse valide `{"data":[],"success":true}`, contre 9 résultats pour CMN en contrôle. Confirmé : ce n'est pas un bug du script, Aviasales n'a simplement aucune donnée "special offers" sur les routes Brazzaville et Kinshasa (couverture insuffisante, pas un problème de code IATA).

**Décision : retrait de BZV et FIH de `RABATTEMENT` dans `hub_deals_db.py`.** Ils consommaient 2 appels API par exécution pour 0 résultat exploitable. Le dict passe de 8 à 6 hubs (CMN, CDG, IST, ADD, NBO, ABJ).

## ⚠️ Points de sécurité / dette technique

1. ~~**Secrets en dur dans le code**~~ — **Corrigé le 2026-08-03.** `hub_deals_db.py` lit désormais `TRAVELPAYOUTS_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` uniquement depuis `os.environ`, sans fallback. Valeurs persistées en variables d'environnement **utilisateur** Windows (`[Environment]::SetEnvironmentVariable(..., "User")`, visibles dans `HKCU:\Environment`) pour que la tâche planifiée "Traqueur de vols" continue de fonctionner. Le script s'arrête (`SystemExit`) si `TRAVELPAYOUTS_TOKEN` est absent.
   - ✅ Token du bot Telegram régénéré via BotFather le 2026-08-03 (l'ancien token, exposé en clair dans le code, est révoqué). Nouvelle valeur en place dans `HKCU:\Environment\TELEGRAM_BOT_TOKEN`.
   - Note technique : une session shell déjà ouverte avant ce changement ne verra pas les nouvelles variables (son bloc d'environnement est figé à son démarrage) — normal, pas un bug. La tâche planifiée, elle, relit l'environnement à chaque déclenchement et n'a pas besoin de reboot.
2. ~~**Duplication de logique**~~ — **Corrigé le 2026-08-03.** Extraction dans `anomaly_detection.py` (`get_dernier_releve`, `calculer_moyennes_historiques`, `detecter_anomalies`, `SEUIL_BAISSE`). `hub_deals_db.py` et `detect_anomalies.py` importent désormais ce module au lieu de recalculer chacun leur moyenne historique. Vérifié fonctionnel de bout en bout (`detect_anomalies.py` exécuté contre la base réelle, 437 lignes).
3. ~~**Pas de `.gitignore` / pas de dépôt git détecté**~~ — **Corrigé le 2026-08-03**, voir section Migration ci-dessous.
4. **`total_estime` statique** — le coût de rabattement Dakar→hub est un forfait fixe par hub (juillet 2026), pas recalculé dynamiquement ; peut dériver dans le temps.

## ✅ Vérification secrets — données (2026-08-03)

Scan complet de `flight_deals.db` et `flight_deals_log.txt` pour toute fuite de secret :

- **`flight_deals.db`** — table `offres` (+ `sqlite_sequence` technique). Colonnes : uniquement données de vol (prix, dates, destinations, liens Aviasales). Le champ `lien` contient des tokens de *recherche* Aviasales (session côté client, publics par nature dans un lien partageable), pas nos clés API. Recherche exhaustive des chaînes de token connues (ancien/nouveau bot Telegram, Travelpayouts) sur tout le contenu texte de la table : **0 correspondance**.
- **`flight_deals_log.txt`** — aucune ligne `ERREUR`, aucune URL contenant `token=`, aucune trace des tokens (ancien ou nouveau) sur tout l'historique du fichier.
- ⚠️ Risque latent identifié (pas un incident) : `requests.exceptions.RequestException` peut inclure l'URL complète (donc le token) dans son message si un appel API échoue un jour ; ces erreurs sont écrites dans `flight_deals_log.txt` via `log()`. Aucune occurrence à ce jour, mais à garder en tête si des erreurs réseau apparaissent.

**Conclusion : `hub_deals` est intégralement propre côté secrets — code (4 scripts), base SQLite et fichier de log.**

## ✅ Migration vers sous-dossier dédié + git local (2026-08-03)

Le projet vivait à plat dans `C:\Users\Dell` (le home directory), mélangé avec des fichiers personnels sans rapport — pas de `.gitignore`, pas de dépôt versionné.

**Actions effectuées :**
1. Création de `C:\Users\Dell\hub_deals\` et déplacement des 7 fichiers du projet (les 4 scripts, `flight_deals.db`, `flight_deals_log.txt`, `hub_deals_AUDIT.md`)
2. `.gitignore` ajouté : exclut `flight_deals.db`, `flight_deals_log.txt` (données générées à l'exécution), `__pycache__/`, `*.pyc`, `.env`
3. `git init` local dans `hub_deals\` + commit initial (6 fichiers versionnés, DB/log correctement ignorés) — **dépôt gardé local, pas de remote**
4. Tâche planifiée **"Traqueur de vols"** mise à jour : `WorkingDirectory` passé de `C:\Users\Dell` à `C:\Users\Dell\hub_deals` (modifié manuellement via l'UI Planificateur de tâches, `Set-ScheduledTask`/`Register-ScheduledTask` en PowerShell ayant échoué avec "Accès refusé" en session non-élevée)
5. **Vérifié en conditions réelles** : tâche déclenchée manuellement après migration → `LastTaskResult=0`, log écrit dans `C:\Users\Dell\hub_deals\flight_deals_log.txt` (216→234 lignes), base mise à jour au bon endroit (471→505 lignes), notification Telegram envoyée avec succès

## Prochaines actions suggérées

- [x] Régénérer le token du bot Telegram (fait le 2026-08-03)
- [x] Décider du sort de BZV/FIH → retirés le 2026-08-03 (confirmé : 0 couverture Aviasales, pas un bug)
- [x] Fusionner la logique de détection d'anomalie → extraite dans `anomaly_detection.py` (2026-08-03)
- [x] Vérifier absence de secrets dans le code, la DB et les logs → confirmé, rien trouvé (2026-08-03)
- [x] Ajouter `.gitignore` et versionner le projet en git → sous-dossier `C:\Users\Dell\hub_deals\`, dépôt local créé, tâche planifiée mise à jour et vérifiée (2026-08-03)

## ✅ Généralisation multi-villes de départ (2026-08-03)

Le script ne supportait qu'une seule ville de départ implicite (Dakar), avec un coût de
rabattement forfaitaire par hub codé en dur. Objectif : permettre d'ajouter d'autres villes
de départ à l'avenir (ex. Abidjan) sans changement de code, juste en enrichissant une
config.

**Changements (branche `worktree-multi-ville-depart`, hors du checkout principal
`C:\Users\Dell\hub_deals` tant qu'elle n'est pas mergée) :**

1. `RABATTEMENT` restructuré en dictionnaire imbriqué `RABATTEMENT[ville][hub]` (au lieu de
   `RABATTEMENT[hub]`). Une seule ville active pour l'instant : `"Dakar"`, avec les 6 mêmes
   hubs et coûts qu'avant (CMN, CDG, IST, ADD, NBO, ABJ) — comportement identique, juste
   restructuré.
2. Nouvelle colonne `ville_depart TEXT NOT NULL DEFAULT 'Dakar'` sur la table `offres`,
   ajoutée par une migration idempotente dans `init_db()` (`ALTER TABLE ... ADD COLUMN`
   exécuté uniquement si la colonne est absente ; sans danger à ré-exécuter à chaque
   lancement). Les lignes existantes (créées avant la migration) sont backfillées
   automatiquement à `'Dakar'` par la clause `DEFAULT`, sans script de migration séparé à
   lancer manuellement.
3. `enregistrer_offres()` boucle désormais sur chaque ville présente dans `RABATTEMENT` et
   insère une ligne par (ville, offre) — pour l'instant une seule ville, donc même volume
   de lignes qu'avant.
4. `anomaly_detection.py` : les moyennes historiques sont regroupées par
   `(ville_depart, hub_origine, destination_code)` au lieu de `(hub_origine,
   destination_code)` seul, pour ne jamais mélanger les moyennes de deux villes de départ
   différentes une fois qu'une deuxième ville sera ajoutée. Chaque anomalie détectée porte
   désormais la clé `ville_depart`.
5. Message Telegram enrichi : mentionne désormais la ville de départ (`depuis {hub}, au
   depart de {ville_depart}`).
6. **Aucun coût API supplémentaire** : le nombre d'appels à `get_special_offers()` reste
   égal au nombre de hubs (6), peu importe le nombre de villes de départ configurées —
   c'est l'insertion en base qui se multiplie par ville, pas l'appel réseau. Ajouter une
   ville ne coûte donc rien côté quota API.
7. Suite `tests/` ajoutée (`unittest`, 9 tests, exécutable via
   `python -m unittest discover -s tests -v`) : migration idempotente, backfill
   `'Dakar'` sur une table pré-existante, non-contamination des moyennes entre deux villes
   simulées (Dakar/Abidjan), présence de `ville_depart` dans le résultat de
   `detecter_anomalies()`, mention de la ville dans le message Telegram.

**✅ Vérification end-to-end (2026-08-03, dans le worktree, PAS contre la base/tâche planifiée réelles)**

Cette branche n'est pas encore mergée dans `master` ; la tâche planifiée Windows "Traqueur
de vols" pointe toujours vers `C:\Users\Dell\hub_deals` (code pré-fusion). Décision : valider
ce Task 5 sur une **copie jetable** de la vraie base dans le worktree, et laisser la
vérification en conditions réelles (vraie base + vraie tâche planifiée) à après le merge.

- Copie de `flight_deals.db` placée dans le worktree (`.gitignore`, jetable) : état de
  départ **505 lignes, pas de colonne `ville_depart`** (schéma pré-migration, confirmé par
  `PRAGMA table_info(offres)`).
- `python hub_deals_db.py` exécuté contre cette copie : log terminé par
  `=== Fin d'execution ===`, **aucune ligne `ERREUR`** (recherche exhaustive dans
  `flight_deals_log.txt`, 0 correspondance). 35 offres collectées sur ce relevé (9 CMN, 9
  CDG, 9 IST, 1 ADD, 7 NBO, 0 ABJ), base passée de 505 à **540 lignes**.
- Après exécution : `PRAGMA table_info(offres)` inclut bien `ville_depart` ; requête
  `SELECT DISTINCT ville_depart FROM offres` → **`[('Dakar',)]`** (une seule ville, comme
  attendu, `RABATTEMENT` n'en contenant qu'une pour l'instant). Les 505 lignes historiques
  ont bien été backfillées à `'Dakar'` (pas d'autre valeur ni de `NULL`).
- `python detect_anomalies.py` exécuté sans erreur : 540 lignes / 18 relevés, aucune
  anomalie au-delà du seuil de -8 % sur ce relevé précis, mode diagnostic affiché (32
  comparaisons) — chaque entrée JSON porte bien `"ville_depart": "Dakar"`.
- Suite `tests/` : 9/9 tests passent (`python -m unittest discover -s tests -v`).
- Note technique rencontrée pendant la vérification, sans rapport avec le code : la session
  shell utilisée pour ce test avait été ouverte avant que `TRAVELPAYOUTS_TOKEN` etc. ne
  soient (re)confirmés en variables d'environnement **utilisateur** Windows — comportement
  déjà documenté plus haut dans cet audit (le bloc d'environnement d'un process est figé à
  son démarrage). Contournement pour cette vérification ponctuelle : lecture directe des
  valeurs via `[Environment]::GetEnvironmentVariable(..., "User")` et injection dans
  l'environnement du process avant de lancer le script. Les valeurs elles-mêmes étaient
  bien présentes dans `HKCU:\Environment` ; aucune modification de secret effectuée.
- **Correctif final (2026-08-03, avant merge)** : la revue finale de branche a trouvé 2
  problèmes importants — `detect_anomalies.py` plantait sur une base non migrée (aucun appel
  à `init_db()`), et un test polluait le vrai `flight_deals_log.txt` (`log()` non stubbé dans
  `TestNotificationMentionneLaVille`). Les deux corrigés en un seul commit (`4dc220a`),
  re-vérifiés par une re-revue ciblée : les deux corrections confirmées, aucune régression.
  Suite passée à 11/11 tests.

**✅ Vérification en conditions réelles, post-merge (2026-08-03 14:20-14:22)**

Branche fusionnée dans `master` (fast-forward, `4326135..4dc220a`), poussée sur GitHub.
Vérification faite directement contre le vrai environnement :

- `python hub_deals_db.py` exécuté contre la **vraie** `flight_deals.db`
  (`C:\Users\Dell\hub_deals\flight_deals.db`) : log terminé par `=== Fin d'execution ===`,
  aucune `ERREUR`. Base passée de **505 à 539 lignes** (34 offres, mêmes 6 hubs : 9 CMN, 9
  CDG, 9 IST, 1 ADD, 6 NBO, 0 ABJ).
- `SELECT DISTINCT ville_depart FROM offres` sur la vraie base → **`[('Dakar',)]`** — les 505
  lignes historiques réelles ont bien été backfillées.
- `python detect_anomalies.py` exécuté sur la vraie base sans erreur (539 lignes, 18 relevés).
- Tâche planifiée **"Traqueur de vols"** redéclenchée réellement (`Start-ScheduledTask`) :
  `LastTaskResult = 0`. Log confirme une exécution complète (14:21:57 → 14:22:30), base passée
  à **573 lignes**.

**Conclusion : la généralisation multi-villes fonctionne de bout en bout, en conditions
réelles — vraie base, vraie tâche planifiée Windows, vrai token API — sans coût API
additionnel et sans régression sur le comportement Dakar existant. Rien en attente.**
