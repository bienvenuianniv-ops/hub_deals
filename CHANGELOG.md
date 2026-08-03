# Changelog

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/). Projet personnel sans versionnage sémantique — entrées datées.

## 2026-08-03

### Ajouté
- `anomaly_detection.py` : logique de détection d'anomalie mutualisée (moyenne historique, seuil `SEUIL_BAISSE`), utilisée par `hub_deals_db.py` et `detect_anomalies.py`
- `.gitignore`, dépôt git local puis distant (GitHub privé)
- `README.md`, `LICENSE` (MIT) et badge licence associé
- `requirements.txt` (dépendance `requests`)
- `.gitattributes` pour normaliser les fins de ligne en LF
- `hub_deals_AUDIT.md` : journal d'audit détaillé du projet
- Généralisation multi-villes de départ : `HUBS`/`RABATTEMENT` imbriqué (`RABATTEMENT[ville][hub]`), nouvelle colonne `ville_depart` dans `offres` (migration idempotente, backfill `'Dakar'` sur les lignes existantes), regroupement des moyennes historiques par (ville de départ, hub, destination) dans `anomaly_detection.py`, suite `tests/` (`unittest`, 9 tests) couvrant la migration et la non-contamination des moyennes entre villes

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
