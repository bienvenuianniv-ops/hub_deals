# Changelog

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/). Projet personnel sans versionnage sémantique — entrées datées.

## 2026-08-16

### Modifié
- **Les 5 rabattements vers Paris étaient les valeurs les plus fausses de la table, sur le hub le
  plus utilisé.** `v1/prices/cheap` ne renvoie rien pour `ville → CDG`, mais `v3/prices_for_dates`
  si. Mesure du 2026-08-16 : Lomé → Paris **279 € → 862 € (+209 %)**, Brazzaville → Paris
  600 € → 1 306 € (+118 %), Kinshasa → Paris 377 € → 708 € (+88 %), Dakar → Paris 300 € → 496 €
  (+65 %) ; Abidjan → Paris était au contraire sur-estimé (511 € → 486 €). Conséquence mesurée
  avant correction : **les 20 meilleurs prix d'un relevé passaient tous par Paris**, non par
  réalité du marché mais parce que Paris portait le rabattement le plus bas de la table. Après
  correction, Paris ne représente plus que 6 des 20 meilleurs prix et Le Caire en prend 11 ; le
  meilleur trajet au départ de Lomé passe de « Paris → Londres 335 € » à « Abidjan → Accra
  649 € ». 1 783 lignes recalculées rétroactivement, selon la même procédure que la mise à jour
  précédente.
- **Piège aller simple / aller-retour documenté dans le code.** `v2/prices/latest` et
  `v3/prices_for_dates` prennent un paramètre `one_way` qui, laissé à `true`, renvoie des prix
  environ 43 % plus bas que les aller-retour de `v1/prices/cheap`. Une première sonde a ainsi
  « récupéré » 15 segments avec des valeurs incomparables ; seul un contrôle sur un segment couvert
  par les trois endpoints (`DKR→CMN` : v1=468, v2=467, v3=468 en aller-retour) l'a révélé.
- 12 segments restent sans prix sur aucun des trois endpoints et gardent leur ancienne valeur,
  marquée `[NM]` dans la table.

## 2026-08-16

### Modifié
- **`RABATTEMENT` remis à jour et historique recalculé rétroactivement.** Les 23 segments pour
  lesquels l'API renvoie un prix ont été réécrits d'après une mesure du 2026-08-16 ; 15 valeurs
  changent, dont Brazzaville → Lagos (400 € → **1 083 €**), Abidjan → Nairobi (374 € → 883 €) et
  Dakar → Abidjan (200 € → 409 €). Deux valeurs étaient **sur**-estimées (Abidjan → Istanbul
  700 € → 672 €). Les 17 segments sans prix API sont conservés tels quels et marqués `[NM]` dans
  la table, avec l'âge de la valeur : aucune n'est inventée pour combler un trou. `CDG` est `[NM]`
  pour les cinq villes, ce qui reste la limite principale de cette table.
- **Le recalcul rétroactif était indispensable, pas cosmétique.** Changer la table sans toucher
  l'historique aurait fait bondir `total_estime` sur **4 383 lignes (43 % de la base)** tandis que
  les moyennes historiques seraient restées basses : sur ces routes, le prix du jour serait passé
  systématiquement au-dessus de sa propre moyenne, et **plus aucune anomalie n'aurait été détectée
  pendant environ 49 relevés, soit sept semaines**. `prix_vol_hub` et `rabattement` étant stockés
  séparément, `total_estime` a pu être recalculé sur toute la base (sauvegarde prise avant). Le
  décalage étant additif et appliqué à l'ensemble de l'historique d'une route, les écarts relatifs
  et les z-scores sont préservés.
- Vérification après migration : 10 081 lignes intactes, 0 violation de
  `total_estime = prix_vol_hub + rabattement`, 0 ligne portant un rabattement différent de la
  table, et la détection retrouve **les mêmes anomalies aux mêmes totaux** que ceux calculés par
  la correction d'affichage — les deux mécanismes convergent.

### Corrigé
- **Les alertes Telegram annonçaient des totaux faux.** `total_estime` additionne le prix du vol
  hub → destination et une valeur de `RABATTEMENT` écrite en dur. Mesure des 40 segments le
  2026-08-16 : le problème n'est pas un sous-dimensionnement uniforme mais du **vieillissement** —
  Kinshasa et Lomé, relevés la veille par API, collent à +0 %, tandis que Brazzaville → Lagos
  dérive de **+171 %**, Abidjan → Nairobi de +136 % et Dakar → Abidjan de +104 %. Trois segments
  sont au contraire **sur**-estimés. Le coût réel du trajet ville → hub est désormais mesuré au
  moment de l'alerte et appliqué au prix du jour **et** à la moyenne historique : le rabattement
  étant une constante additive de tout l'historique d'une route, ce décalage préserve l'écart
  absolu et le z-score, et seul le pourcentage change. Chaque ligne indique si le rabattement a
  été mesuré ou vient de la table — 17 des 40 segments n'ont aucun prix API, dont `CDG` pour les
  cinq villes. Correction d'affichage uniquement : la base et la détection sont inchangées, donc
  l'historique reste comparable. 80 → 97 tests.

### Ajouté
- **Recherche de billet à la demande** (`recherche.py`) : interroger soi-même une route depuis
  l'une des 5 villes vers n'importe quel code IATA, au lieu de subir les propositions du relevé.
  Inclut le **vol direct**, que le collecteur n'interroge jamais — mesuré le 2026-08-15 sur
  `Dakar → Brazzaville`, le direct à 932 € bat les 1001 € via Paris annoncés par le relevé. Les
  segments ville → hub sont demandés à l'API plutôt que lus dans `RABATTEMENT`, dont les valeurs
  estimées se sont révélées optimistes de 17 à 31 % ; le repli sur la table est signalé entre
  parenthèses et une option entièrement mesurée passe devant une option estimée à total égal.
  Coût : 19 appels pour Dakar.
- **Mise sous surveillance** (`--surveiller`) : ajoute une destination au relevé quotidien via
  `destinations_perso.json` (local, non versionné, 15 maximum, +9 appels par destination). Elle
  bénéficie alors de la détection d'anomalie et des notifications Telegram existantes.
- 37 → 79 tests.

## 2026-08-15

### Corrigé
- **Des routes ramenaient une ville de départ chez elle.** Quatre villes de `RABATTEMENT` figurent aussi dans `DESTINATIONS` (`DKR`, `ABJ`, `BZV`, `FIH`). Le code excluait bien `destination == hub`, mais jamais `destination == ville de départ` : la base contenait donc des lignes « Dakar → via Paris → Dakar » (381 € de vol + 300 € de rabattement = 681 € pour revenir chez soi). L'exclusion ne pouvait pas se faire dans la boucle d'appels API — la route `CDG→DKR` reste valable pour les quatre autres villes de départ — elle se fait donc à l'insertion, ville par ville, dans `enregistrer_prix()`, via une table `VILLE_IATA`. Mesuré sur la base réelle : 105 lignes parasites sur 7 414 (1,4 %), réparties sur 46 relevés, soit ~16 par relevé. Lomé n'en produisait aucune, mais par accident seulement : `LFW` n'est pas dans `DESTINATIONS`. Un test structurel vérifie désormais que toute ville de `RABATTEMENT` a son code IATA, pour qu'ajouter une ville sans le sien échoue au lieu de réintroduire le bug silencieusement. Les 105 lignes historiques ont été purgées (sauvegarde de la base prise avant suppression) ; vérifié après coup que les routes légitimes vers ces mêmes villes sont intactes — `DKR` conserve ses lignes au départ d'Abidjan, Brazzaville, Kinshasa et Lomé. 30 → 32 tests.
- **Le seuil d'anomalie était mathématiquement inatteignable.** Le passage au z-score calculait la moyenne et l'écart-type sur *tout* l'historique, relevé du jour inclus. Un point inclus dans sa propre référence ne peut pas s'en écarter librement : avec un écart-type d'échantillon (N-1), son z-score est plafonné à `(n-1)/√n`, soit 0,71 sur 2 relevés, 1,16 sur 3 et 1,50 sur 4. Avec `SEUIL_ZSCORE = 1.5`, aucune route de moins de 4 relevés ne pouvait déclencher d'alerte, même en cas d'effondrement du prix — soit 1 014 des 1 341 routes de la base. Vérifié sur la base réelle : l'ancienne logique remontait **0 anomalie** sur le relevé du 2026-08-15, avec un z-score maximum de 1,155, exactement le plafond théorique pour n=3. `calculer_stats_historiques()` accepte désormais `exclure_date` et la détection construit sa référence **sans** le relevé jugé ; le même relevé remonte 6 anomalies.
- Suite de tests remise au vert : elle référençait encore `calculer_moyennes_historiques()` et `enregistrer_offres()`, renommées lors du refactor précédent sans mise à jour des tests (3 erreurs + 4 échecs).

### Sécurité
- **Le token d'API s'écrivait en clair dans le journal.** Sur erreur réseau, `requests` place l'URL complète dans son exception — query string comprise, donc `token=…`. Le message partait tel quel dans `flight_deals_log.txt` (2 occurrences constatées). Ajout de `masquer_secrets()`, appliqué **dans `log()`** et non chez les appelants : c'est le point de passage unique de tout ce qui est journalisé, donc le seul endroit où l'oubli est impossible. Couvre aussi le token du bot Telegram, que l'URL de l'API porte dans son chemin (`/bot<token>/sendMessage`). Le `chat_id` n'est volontairement pas masqué : il ne circule que dans le corps du POST, donc n'apparaît jamais dans une exception, et c'est souvent un nombre court — le remplacer aveuglément mutilerait des messages légitimes contenant la même suite de chiffres. Les occurrences déjà écrites ont été retirées du fichier existant (sans copie de sauvegarde, volontairement : elle conserverait le secret qu'on cherche à effacer). Portée réelle du problème : le journal est dans `.gitignore` et n'a jamais été commité — le token n'a donc jamais atteint GitHub, il n'était exposé que localement. 32 → 37 tests.

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
