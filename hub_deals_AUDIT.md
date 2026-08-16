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

## ✅ Ajout d'Abidjan comme 2e ville de départ (Task 2, 2026-08-03)

### Recherche API et sélection de la méthode

Trois endpoints Travelpayouts ont été testés pour obtenir les prix de rabattement Abidjan → hubs :

| Endpoint | Requête | Résultat pour ABJ→CMN | Notes |
|---|---|---|---|
| `v1/prices/cheap` | Origine/destination direct, sans dates | ✅ 563€ | Performant, une requête par hub, précis sur les prix courants |
| `v2/prices/latest` | Derniers prix observés | ✅ 563€ | Redondant avec v1 |
| `v3/prices_for_dates` | Dates spécifiques | — (non testé pour CMN) | Repli utile pour NBO, seule route où v1/v2 n'ont renvoyé aucune donnée |

**Décision : `v1/prices/cheap` retenu comme méthode principale** (une requête par hub origin/destination), avec `v3/prices_for_dates` en repli pour NBO (qui présente parfois un délai d'indexation dans v1).

### Valeurs obtenues (2026-08-03, requête directe API)

```
ABJ → CMN : 563€ / 3h
ABJ → CDG : 511€ / 8h
ABJ → IST : 700€ / 9h
ABJ → NBO : 374€ / 8h
```

### Omissions et justifications

- **ADD (Addis-Abeba) : omis** — Aucune donnée API disponible sur les 3 endpoints testés. Contrairement à ABJ→CMN et autres, ABJ→ADD ne retourne que `{}` ou `null`. L'absence de donnée API (pas une erreur de code) empêche d'estimer un coût fiable pour cette route.
- **ABJ (Abidjan vers Abidjan) : pas d'entrée `RABATTEMENT["Abidjan"]["ABJ"]`** — Abidjan est l'une des villes de départ ET l'un des 6 hubs surveillés. Un rabattement "Abidjan → Abidjan" n'a aucun sens logistique (l'utilisateur part déjà d'Abidjan). Aucune entrée n'a été créée pour cette paire.

### ✅ Vérification en conditions réelles (2026-08-03, post-merge)

Branche fusionnée dans `master` (fast-forward, `d79e7d4..cb33e24`), poussée sur GitHub.
Vérification faite directement contre le vrai environnement :

- `python hub_deals_db.py` exécuté contre la **vraie** `flight_deals.db`
  (`C:\Users\Dell\hub_deals\flight_deals.db`) : log terminé par `=== Fin d'execution ===`,
  aucune ligne `ERREUR` (recherche exhaustive dans `flight_deals_log.txt`). Base passée de
  **606 à 670 lignes** (64 offres enregistrées sur ce relevé : 9 CMN, 9 CDG, 9 IST, 2 ADD,
  4 NBO, 0 ABJ — réparties en 33 lignes `Dakar` et 31 lignes `Abidjan`, cohérent avec les
  6 hubs actifs pour Dakar contre 4 pour Abidjan).
- `SELECT DISTINCT ville_depart FROM offres` sur la vraie base → **`[('Dakar',), ('Abidjan',)]`**
  — les deux villes bien présentes, comme attendu.
- `python detect_anomalies.py` exécuté sur la vraie base sans erreur (670 lignes, 21 relevés) :
  1 anomalie détectée (Namangan depuis Istanbul, au départ de **Dakar**, -12%) — aucune anomalie
  Abidjan sur ce relevé (attendu : `anomaly_detection.py` exige au moins 2 relevés historiques
  par `(ville, hub, destination)`, et c'est le tout premier relevé Abidjan).
- Tâche planifiée **"Traqueur de vols"** redéclenchée réellement (`Start-ScheduledTask`) :
  `LastTaskResult = 0`. Log confirme une exécution complète (17:47:13 → 17:47:46, encore
  64 offres, 33 Dakar / 31 Abidjan), base passée à **734 lignes**.

**Conclusion : l'ajout d'Abidjan fonctionne de bout en bout, en conditions réelles — vraie
base, vraie tâche planifiée Windows, vrai token API — sans coût API additionnel (la boucle
d'appel API reste indexée sur `HUBS`, indépendante de `RABATTEMENT`) et sans régression sur
le comportement Dakar existant. Rien en attente.**

## ✅ Recherche de billet à la demande (`recherche.py`, 2026-08-16)

Chantier ouvert le 2026-08-15 (spec + plan sur la branche `recherche-billet`), exécuté le
2026-08-16 en TDD, 6 tâches. Suite passée de **37 à 80 tests**.

### Ce que le module apporte

`hub_deals_db.py` fonctionne en éventail : il balaie une matrice hubs × destinations imposée et
signale ce qu'il juge anormalement bas. `recherche.py` fait l'inverse — l'utilisateur pose sa
propre question — avec deux différences de fond mesurées avant conception :

1. **Le vol direct est interrogé**, ce que le collecteur ne fait jamais (il passe toujours par un
   hub). Vérifié en conditions réelles le 2026-08-16 sur `Dakar → Brazzaville` : direct **932 €**
   contre **1001 € via Paris** — le collecteur seul rate donc la meilleure option.
2. **Les segments ville → hub sont demandés à l'API**, `RABATTEMENT` ne servant que de repli
   signalé entre parenthèses (ses valeurs estimées se sont révélées optimistes de 17 à 31 %).

Aucun coefficient correctif n'a été introduit : inventer un « +25 % » remplacerait une valeur
fausse par une autre. À total égal, une option entièrement mesurée passe devant une option
estimée, et un avertissement s'affiche si la meilleure option repose sur un prix estimé.

**`recherche.py` n'écrit jamais dans `flight_deals.db`** — une recherche manuelle ne doit pas
entrer dans l'historique qui nourrit la détection statistique. Vérifié : 8 219 lignes avant et
après cinq recherches consécutives.

### Destinations personnelles

`--surveiller <code>` ajoute une destination au relevé quotidien via `destinations_perso.json`
(local, gitignoré, 15 maximum, +9 appels par destination). La **lecture** vit dans
`hub_deals_db.py` (le collecteur en a besoin), l'**écriture** dans `recherche.py` : cet ordre
évite un import circulaire. Un JSON corrompu ou absent renvoie `{}` sans jamais faire échouer un
relevé — c'est un fichier de confort, pas une source de vérité.

### ✅ Vérification en conditions réelles (2026-08-16)

Vrai token, vraie base, vraie tâche planifiée.

| Vérification | Résultat |
|---|---|
| `recherche.py Dakar BZV` | direct 932 € en tête, devant 1001 € via Paris ✅ |
| `recherche.py Dakar ZZZ` | « Aucun itinéraire trouvé », pas de trace Python ✅ |
| `recherche.py Marseille BKK` | refus + liste des 5 villes ✅ |
| `recherche.py Dakar DKR` | refus (destination = ville de départ) ✅ |
| Écriture en base | 8 219 lignes avant **et** après 5 recherches ✅ |
| `--surveiller` / `--liste` / `--oublier` | aller-retour complet ✅ |
| Relevé complet avec `SIN` sous surveillance | log « 1 destination(s) personnelle(s) active(s) (+9 appels) », sortie 0 ✅ |
| Tâche planifiée « Traqueur de vols » | `LastTaskResult = 0`, prochain déclenchement 13h00 ✅ |

Relevé du 2026-08-16 09:13:51 : **210 routes** (contre 202 le matin même, soit +8 = les 8 hubs
ayant un prix vers `SIN` ; `ADD` n'en a pas) et **947 lignes** (contre 910, soit +37). Les 37
lignes `SIN` se répartissent sur les **5 villes de départ** — Dakar 8, Kinshasa 8, Abidjan 7,
Brazzaville 7, Lomé 7 — écarts correspondant exactement aux entrées `RABATTEMENT` absentes.
Aucune ligne parasite. Propriété confirmée : le prix hub → destination personnelle est interrogé
**une seule fois** par hub (906 € depuis CMN, 553 € depuis CDG) puis réutilisé pour les 5 villes.

### ⚠️ Fuite de secret trouvée et corrigée pendant la vérification

`recherche.py` affichait le **token Travelpayouts en clair** : `requests` place l'URL complète
dans ses exceptions, et un code IATA inexistant provoque un HTTP 400 par hub, donc 10 lignes
d'erreur portant le token. Le correctif du 2026-08-15 (`masquer_secrets()` appelée dans `log()`)
ne couvrait pas ce module, qui affiche avec `print()`. Masquage ajouté dans `_appeler()`, seul
endroit où une exception devient du texte dans ce module, avec un test qui reproduit la fuite.
Vérifié avec le **vrai** token, pas seulement la valeur factice des tests.

**Leçon transposable :** « le point de passage unique » n'est unique que pour un chemin de sortie
donné. `log()` couvrait le fichier journal ; un nouveau module écrivant sur `stdout` recrée le
trou. Tout code qui transforme une exception `requests` en texte doit masquer.

**Action en attente côté utilisateur :** ce token s'est affiché en clair dans une session d'outil
avant le correctif — le régénérer sur Travelpayouts puis refaire le `setx` reste prudent.

### Écarts assumés par rapport au plan

Trois tests du plan étaient faux et ont été corrigés :

1. `(1001-810)/810 = 23,58 %` s'affiche `+24 %` après arrondi — le plan attendait « 23 ».
2. Le test de fusion des destinations personnelles utilisait `BKK`, **déjà** présent dans
   `DESTINATIONS` : la fusion n'ajoutait aucune entrée et le test ne prouvait rien. Remplacé par
   `SIN`, avec un test supplémentaire vérifiant qu'un doublon garde son nom d'origine.
3. Le test de `--liste` réassignait `collecteur.CHEMIN_DESTINATIONS_PERSO`, sans effet : un
   argument par défaut est figé à la définition de la fonction. `main()` passe désormais le
   chemin explicitement.

Le même piège que le point 2 s'est reproduit pendant la vérification finale : `--surveiller BKK`
n'aurait rien prouvé (`nb_perso = 0`), d'où le choix de `SIN`.

## ✅ Rabattement mesuré au moment de l'alerte (2026-08-16)

Suite du chantier précédent, qui avait laissé ce problème hors scope. Suite passée de **80 à 97
tests**. Spec et plan dans `docs/superpowers/`.

### La mesure a reconfiguré le problème

La spec de la recherche de billet parlait d'un sous-dimensionnement de 17 à 31 %, sur deux
mesures ponctuelles. Les 40 segments (ville, hub) de `RABATTEMENT` ont été interrogés :

| Constat | Valeur |
|---|---|
| Segments avec un prix API | 23 / 40 |
| Segments sans aucun prix | 17 / 40 |
| Écart médian | +0 % |
| Sous-estimés | 12 |
| Sur-estimés | 3 |

**Ce n'est pas un sous-dimensionnement, c'est du vieillissement.** Kinshasa et Lomé, relevés par
API la veille, collent à +0 % sur 11 segments. Les dérives touchent les valeurs anciennes :
Brazzaville → Lagos **+171 %**, Abidjan → Nairobi +136 %, Abidjan → Le Caire +110 %,
Dakar → Abidjan +104 %, Brazzaville → Le Caire +59 %. Les « 17 à 31 % » cités étaient les deux
plus petits écarts du lot. Trois segments sont au contraire **sur**-estimés (Abidjan → Istanbul
−4 %) : une correction qui n'aurait su que rehausser les aurait empirés.

**`CDG` n'a de prix pour aucune des 5 villes.** Or Paris sort en tête de la plupart des meilleurs
totaux. L'indisponibilité de la mesure est donc le cas courant sur le hub le plus utile, pas un
cas limite.

### Conception retenue

But déclaré par l'utilisateur : **l'exactitude du chiffre affiché**, la détection n'étant pas en
cause. D'où une correction **d'affichage uniquement**, appliquée au moment de l'alerte :

1. `mesurer_rabattements()` — seule couche réseau, dédoublonne les couples, replie sur la table.
2. `corriger_anomalies()` — fonction pure : décale le prix du jour **et** la moyenne du même
   montant, puis re-trie.
3. `verifier_et_notifier_anomalies()` — enchaîne les deux et compose le message.

Le décalage est exact parce que le rabattement est une **constante additive** de tout
l'historique d'une route : l'écart absolu et l'écart-type sont préservés, donc le z-score. Seul
le pourcentage change. Hypothèse assumée et écrite dans le code : on substitue une constante à
une autre.

**Rejeté :** rafraîchir la table à chaque relevé (+40 appels) rendrait `rabattement` variable, sa
variance s'ajouterait à celle du vol principal et le z-score se déclencherait sur des baisses du
trajet vers le hub. **Rejeté :** rehausser la table ferait sauter `total_estime` d'un coup, la
moyenne historique resterait basse et plus aucune anomalie ne se déclencherait pendant plusieurs
jours.

### ✅ Vérification en conditions réelles (relevé du 2026-08-16 10:02:45)

203 routes, 915 lignes, sortie 0, `Rabattement mesure pour 2/3 anomalie(s).`

| Point | Résultat |
|---|---|
| `total_estime` stocké inchangé | **0 / 915** ligne incohérente avec `prix_vol_hub + rabattement` de la table |
| Décalage affiché = mesuré − table | **125 = 125** et **−28 = −28** |
| Écart absolu préservé | 42 € → 42 € sur les deux routes mesurées |
| Pourcentages décroissants | `[4.5, 3.9, 3.5]` |
| Token en clair dans le journal | 0 occurrence |
| Tâche planifiée | `LastTaskResult = 0` |

Effet concret : `Dakar → New York via Istanbul` était annoncé **769 €**, le vrai total est
**894 €**. Et `Abidjan → New York via Istanbul` **baisse** de 28 € (rabattement mesuré 672 €
contre 700 € en table) — la correction joue bien dans les deux sens.

Réserve : le décalage n'a pas *inversé* l'ordre sur ce relevé, le re-tri n'a donc pas été
sollicité en conditions réelles. Il reste couvert par un test unitaire construisant une
inversion.

### Deux défauts du plan, rattrapés à l'exécution

1. **Un test préexistant faisait un vrai appel API.** `TestNotificationMentionneLaVille` appelait
   `verifier_et_notifier_anomalies` sans neutraliser la mesure ; la greffe lui a donc ajouté un
   appel Travelpayouts et sa pause de 0,4 s. Le plan posait « aucun appel réseau dans les tests »
   en contrainte globale mais n'avait pas recensé les **appelants existants** de la fonction
   modifiée. Repéré parce que la suite est passée de 0,36 s à 2,56 s — la durée de la suite est
   un détecteur d'appel réseau involontaire.
2. **Les tests écrivaient dans `flight_deals_log.txt`**, y compris des lignes
   « Notification Telegram envoyee » qui constituent un faux témoignage dans un journal
   d'exploitation. 15 lignes retirées ; la suite n'écrit désormais plus rien (vérifié en
   comparant le nombre de lignes avant et après une exécution complète).

**Leçon :** modifier une fonction impose de recenser ses appelants dans les tests, pas seulement
d'écrire les nouveaux. Et toute fonction appelant `log()` doit voir `log()` neutralisé dans ses
tests, sans quoi le journal d'exploitation devient un mélange de réel et de simulé.
