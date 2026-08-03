# hub_deals

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Détecteur de bonnes affaires vol au départ de Dakar, via des hubs de correspondance (Casablanca, Paris, Istanbul, Addis-Abeba, Nairobi, Abidjan). Interroge l'API Travelpayouts, stocke l'historique en SQLite, détecte les anomalies de prix par rapport à la moyenne historique, et notifie les bonnes affaires par Telegram.

## Principe

Un rabattement Dakar → hub a un coût forfaitaire connu (voir `RABATTEMENT` dans `hub_deals_db.py`). Le total estimé d'un trajet est donc :

```
total_estime = prix_vol_depuis_le_hub + cout_rabattement_dakar_hub
```

Chaque exécution enregistre les offres du jour dans `flight_deals.db`. Avec l'historique cumulé, `anomaly_detection.py` compare le prix du jour à la moyenne historique de chaque destination : une baisse ≥ 8 % déclenche une notification Telegram.

## Fichiers

| Fichier | Rôle |
|---|---|
| `hub_deals_db.py` | Script principal : récupère les offres, les enregistre en base, notifie les anomalies. C'est lui qu'exécute la tâche planifiée. |
| `anomaly_detection.py` | Logique partagée de détection d'anomalie (moyenne historique, seuil), utilisée par les deux scripts ci-dessous. |
| `detect_anomalies.py` | Outil CLI d'analyse/diagnostic — relit la base et affiche les comparaisons, sans notifier. |
| `test_travelpayouts.py` | Script de test brut de l'API Travelpayouts. |
| `hub_deals_AUDIT.md` | Journal d'audit détaillé du projet (historique des décisions et correctifs). |
| `flight_deals.db`, `flight_deals_log.txt` | Générés à l'exécution — ignorés par git. |

## Configuration

Variables d'environnement requises (aucun secret en dur dans le code) :

```
TRAVELPAYOUTS_TOKEN=...   # requis
TELEGRAM_BOT_TOKEN=...    # optionnel — sans lui, pas de notification
TELEGRAM_CHAT_ID=...      # optionnel — idem
```

## Usage

```
python hub_deals_db.py       # collecte + notification
python detect_anomalies.py   # analyse/diagnostic sans notifier
```

## Automatisation

Tourne via la tâche planifiée Windows **"Traqueur de vols"** (~1x/jour), configurée avec `C:\Users\Dell\hub_deals` comme répertoire de travail.

## Licence

[MIT](LICENSE)
