# Changelog

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/). Projet personnel sans versionnage sémantique — entrées datées.

## 2026-08-15

### Corrigé
- **Le seuil d'anomalie était mathématiquement inatteignable.** Le passage au z-score calculait la moyenne et l'écart-type sur *tout* l'historique, relevé du jour inclus. Un point inclus dans sa propre référence ne peut pas s'en écarter librement : avec un écart-type d'échantillon (N-1), son z-score est plafonné à `(n-1)/√n`, soit 0,71 sur 2 relevés, 1,16 sur 3 et 1,50 sur 4. Avec `SEUIL_ZSCORE = 1.5`, aucune route de moins de 4 relevés ne pouvait déclencher d'alerte, même en cas d'effondrement du prix — soit 1 014 des 1 341 routes de la base. Vérifié sur la base réelle : l'ancienne logique remontait **0 anomalie** sur le relevé du 2026-08-15, avec un z-score maximum de 1,155, exactement le plafond théorique pour n=3. `calculer_stats_historiques()` accepte désormais `exclure_date` et la détection construit sa référence **sans** le relevé jugé ; le même relevé remonte 6 anomalies.
- Suite de tests remise au vert : elle référençait encore `calculer_moyennes_historiques()` et `enregistrer_offres()`, renommées lors du refactor précédent sans mise à jour des tests (3 erreurs + 4 échecs).

### Ajouté

*Les quatre entrées suivantes documentent un chantier resté non commité dans l'arbre de travail, absent du changelog jusqu'ici.*

- **Matrice hubs × destinations imposée**, en remplacement de `get_special_offers`. Cet endpoint ne renvoyait que ce que contenait le cache Aviasales — majoritairement des routes CEI/Asie centrale, la base d'utilisateurs du service étant russophone. `v1/prices/cheap` impose origine **et** destination : la couverture est désormais choisie (`DESTINATIONS`, 32 villes sur 5 zones). Ajout de `construire_lien()`, l'endpoint ne renvoyant pas de lien direct, et de `EQUIVALENCES` (`CDG`/`PAR` désignent la même ville, l'API renvoie 400 si origine = destination).
- Hubs `JNB` (Johannesburg), `CAI` (Le Caire) et `LOS` (Lagos) : 6 → 9 hubs surveillés.
- `RABATTEMENT["Brazzaville"]` : 3e ville de départ (plusieurs valeurs estimées, signalées en commentaire, à confirmer).
- Détection par **z-score** en remplacement du seuil en pourcentage fixe : le seuil devient relatif à la volatilité propre de chaque route. Voir la section « Corrigé » — cette bascule était inopérante en l'état.

- `RABATTEMENT["Lome"]` et `RABATTEMENT["Kinshasa"]` : 4e et 5e villes de départ actives. Coûts obtenus par requête directe à l'API Travelpayouts le 2026-08-15 (`v1/prices/cheap`, complété par `v3/prices_for_dates`), même méthode que pour Abidjan. Kinshasa couvre les 9 hubs ; Lomé en couvre 7 — `ADD` et `JNB` omis, aucun prix renvoyé par aucun des deux endpoints. Ces routes avaient été jugées non couvertes le 2026-08-03, mais via `get_special_offers` uniquement ; les endpoints à origine/destination imposées les couvrent bien.
- Repli en pourcentage (`SEUIL_BAISSE`, 8 %) quand une route a moins de `MIN_RELEVES_ZSCORE` (4) relevés d'historique ou aucune dispersion — en dessous, l'écart-type n'est pas assez fiable pour arbitrer seul.
- Plancher `PLANCHER_BAISSE_ZSCORE` (3 %) en mode z-score : sur une route très stable, 1,5 écart-type peut ne représenter que quelques euros.
- Champ `methode` (`"z-score"` ou `"pourcentage"`) dans chaque résultat de `detecter_anomalies()`, pour savoir quelle règle a tranché.
- Tests : couverture des deux bugs ci-dessus (dont un test de régression sur l'atteignabilité du seuil), du repli en pourcentage, du tri, des hausses et des routes vues pour la première fois ; invariants structurels de `RABATTEMENT` valables pour toute ville présente ou future (hubs connus, prix et durées positifs, pas de rabattement vers soi-même). 13 → 28 tests.

### Modifié
- `MIN_RELEVES_HISTORIQUE` (2) : une route n'est plus jugée tant qu'elle n'a pas **deux relevés antérieurs**. La réécriture de la détection avait fait sauter le garde-fou `nb_releves >= 2` de l'ancien code — une route était jugée dès une seule observation passée, ce qui revient à signaler le bruit quotidien d'un prix de billet. Effet mesuré sur le relevé du 2026-08-15 13:00 : 11 anomalies → 8, les trois retirées étant exactement celles à `n=1` ; les 8 restantes ont 4 relevés d'historique et passent toutes par le z-score.
- Tâche planifiée « Traqueur de vols » : ajout d'un déclencheur **quotidien à 13h00** en plus du déclencheur d'ouverture de session, qui était jusqu'ici le seul — la collecte dépendait donc entièrement des connexions, et quelques jours sans allumer la machine créaient un trou dans l'historique dont la détection d'anomalie a besoin. `StartWhenAvailable` activé pour rattraper une exécution manquée. Restrictions batterie (`DisallowStartIfOnBatteries`, `StopIfGoingOnBatteries`) levées : sur ce portable, une session ouverte sur batterie empêchait le démarrage et un débranchement en cours de relevé tuait la tâche en laissant des données partielles, sans avertissement.
- Tri des anomalies par baisse décroissante plutôt que par z-score : critère lisible et commun aux deux méthodes de détection (le z-score est absent en mode pourcentage).
- `duree_h` documenté comme purement indicatif (il n'entre dans aucun calcul). Pour Lomé et Kinshasa, c'est la durée d'itinéraire renvoyée par l'API, escales comprises — d'où des valeurs plus élevées que les estimations « temps de vol » des premières villes.

## 2026-08-03

### Ajouté
- `anomaly_detection.py` : logique de détection d'anomalie mutualisée (moyenne historique, seuil `SEUIL_BAISSE`), utilisée par `hub_deals_db.py` et `detect_anomalies.py`
- `.gitignore`, dépôt git local puis distant (GitHub privé)
- `README.md`, `LICENSE` (MIT) et badge licence associé
- `requirements.txt` (dépendance `requests`)
- `.gitattributes` pour normaliser les fins de ligne en LF
- `hub_deals_AUDIT.md` : journal d'audit détaillé du projet
- Généralisation multi-villes de départ : `HUBS`/`RABATTEMENT` imbriqué (`RABATTEMENT[ville][hub]`), nouvelle colonne `ville_depart` dans `offres` (migration idempotente, backfill `'Dakar'` sur les lignes existantes), regroupement des moyennes historiques par (ville de départ, hub, destination) dans `anomaly_detection.py`, suite `tests/` (`unittest`, 13 tests) couvrant la migration et la non-contamination des moyennes entre villes
- `RABATTEMENT["Abidjan"]` : deuxième ville de départ active, 4 hubs (CMN, CDG, IST, NBO) — coûts obtenus par requête directe à l'API Travelpayouts (`v1/prices/cheap`, complété par `v3/prices_for_dates` pour NBO), contrairement à Dakar (estimation manuelle). `ADD` omis (aucune donnée API disponible pour cette route), pas d'entrée `ABJ` (Abidjan est déjà le hub)
- `tests/test_hub_deals_db.py` : test de garde-fou vérifiant les clés et les valeurs de `RABATTEMENT["Abidjan"]`

### Modifié
- Secrets (`TRAVELPAYOUTS_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) externalisés en variables d'environnement — plus aucune valeur en dur dans le code
- `detect_anomalies.py` réécrit en wrapper CLI fin autour de `anomaly_detection.py`
- Projet déplacé de `C:\Users\Dell\` (racine du profil) vers `C:\Users\Dell\hub_deals\` ; tâche planifiée "Traqueur de vols" mise à jour en conséquence
- Message de notification Telegram enrichi de la ville de départ (`depuis {hub}, au depart de {ville_depart}`)

### Supprimé
- `hub_deals.py` (script obsolète, remplacé par `hub_deals_db.py`)
- Hubs BZV (Brazzaville) et FIH (Kinshasa) retirés de `RABATTEMENT` — l'API Travelpayouts n'a aucune couverture "special offers" sur ces routes (confirmé par appel direct)

### Sécurité
- Token du bot Telegram régénéré via BotFather (l'ancien token, précédemment exposé en clair dans le code, a été révoqué)

## 2026-07-29

### Ajouté
- `detect_anomalies.py` : détection d'anomalie de prix par comparaison à la moyenne historique, avec mode diagnostic

## 2026-07-21

### Ajouté
- `hub_deals_db.py` : version avec stockage SQLite cumulatif (`flight_deals.db`), classement du jour et notification Telegram
- Tâche planifiée Windows "Traqueur de vols" pour l'exécution automatique quotidienne

## 2026-07-19

### Ajouté
- `test_travelpayouts.py` : script de test initial de l'API Travelpayouts
