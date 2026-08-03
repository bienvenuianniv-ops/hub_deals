# Support multi-villes de départ — design

## Contexte

`hub_deals` détecte les bonnes affaires vol accessibles depuis Dakar en passant par un hub (Casablanca, Paris, Istanbul, Addis-Abeba, Nairobi, Abidjan) : coût de rabattement Dakar → hub (forfaitaire, codé en dur) + prix du vol hub → destination (via l'API Travelpayouts `get_special_offers`).

Le projet doit être généralisé pour supporter **plusieurs villes de départ**, chacune avec sa propre table de coûts de rabattement vers les mêmes hubs — pas seulement Dakar. Pour cette première itération, seule Dakar reste une ville active (avec ses valeurs actuelles) ; les autres villes seront ajoutées plus tard par l'utilisateur, la structure doit juste être prête à les accueillir.

## Décisions actées (issues du brainstorming)

- Une "ville de départ" a sa propre table de coûts de rabattement vers les hubs — pas un modèle "deals directs sans hub".
- Portée de cette itération : préparer la structure généralisée, ne pas ajouter de vraies nouvelles villes/coûts.
- Tous les hubs surveillés restent partagés entre toutes les villes de départ (pas de liste de hubs personnalisable par ville).

## Architecture — configuration

`RABATTEMENT` (dict plat `hub → coût`) devient une structure à deux niveaux dans `hub_deals_db.py`, séparant les hubs (partagés, avec leur nom d'affichage) des coûts de rabattement par ville de départ :

```python
HUBS = {
    "CMN": {"nom": "Casablanca"},
    "CDG": {"nom": "Paris"},
    "IST": {"nom": "Istanbul"},
    "ADD": {"nom": "Addis-Abeba"},
    "NBO": {"nom": "Nairobi"},
    "ABJ": {"nom": "Abidjan"},
}

RABATTEMENT = {
    "Dakar": {
        "CMN": {"prix": 400, "duree_h": 4},
        "CDG": {"prix": 300, "duree_h": 6},
        "IST": {"prix": 400, "duree_h": 7},
        "ADD": {"prix": 500, "duree_h": 6},
        "NBO": {"prix": 500, "duree_h": 8},
        "ABJ": {"prix": 200, "duree_h": 2},
    },
    # "Abidjan": { ... },  # a ajouter plus tard, meme structure
}
```

Seule `"Dakar"` est peuplée, avec les valeurs actuelles (inchangées). Ajouter une ville plus tard = ajouter une entrée à `RABATTEMENT`, sans toucher au reste du code.

## Flux d'exécution — pas de coût API supplémentaire

Constat clé : l'appel `get_special_offers(hub)` ne dépend pas de la ville de départ — il renvoie les bonnes affaires *depuis ce hub*, indépendamment de la manière dont on y arrive. Le coût de rabattement n'intervient qu'au moment du calcul de `total_estime`.

Nouvelle boucle dans `hub_deals_db.py` :

1. Pour chaque hub dans `HUBS` → **un seul appel API** `get_special_offers(hub)` (inchangé, 6 appels)
2. Pour chaque ville de `RABATTEMENT` ayant une entrée pour ce hub → calcule `total_estime = prix_offre + RABATTEMENT[ville][hub]["prix"]` et insère une ligne par (ville, offre)

Ajouter des villes de départ n'augmente donc **pas** le nombre d'appels API — seulement le nombre de lignes insérées en base par relevé.

## Schéma DB + migration

- Nouvelle colonne `ville_depart TEXT NOT NULL` sur la table `offres`.
- Migration idempotente dans `init_db()` : vérifie via `PRAGMA table_info(offres)` si la colonne existe déjà avant de l'ajouter (`ALTER TABLE offres ADD COLUMN ville_depart TEXT NOT NULL DEFAULT 'Dakar'`), pour être sûre à ré-exécuter à chaque lancement du script.
- Les ~500 lignes existantes sont rétro-taguées `'Dakar'` via la clause `DEFAULT` de l'`ALTER TABLE` — aucune perte d'historique, cohérent avec le comportement actuel (seule ville active jusqu'ici).

## Détection d'anomalie (`anomaly_detection.py`)

- `calculer_moyennes_historiques()` : la clé de regroupement passe de `(hub_origine, destination_code)` à `(ville_depart, hub_origine, destination_code)`. Sans ça, des lignes de villes différentes (donc des `total_estime` basés sur des coûts de rabattement différents) seraient moyennées ensemble, faussant la détection.
- `detecter_anomalies()` : la requête `SELECT` inclut `ville_depart`, et chaque résultat renvoyé porte ce champ.
- `get_dernier_releve()` : inchangé (le plus récent `date_collecte`, tous départs confondus).

## Notification Telegram (`hub_deals_db.py`)

Le message d'anomalie mentionne désormais la ville de départ, par exemple :

```
Casablanca (depuis Dakar)
212€ (moyenne habituelle : 250€, -15%)
https://www.aviasales.com/...
```

## Hors scope (volontairement, YAGNI)

- Pas de fichier de config externe (JSON/YAML) — `HUBS` et `RABATTEMENT` restent des dicts Python en dur dans `hub_deals_db.py`, cohérent avec le style actuel du script.
- Pas de liste de hubs personnalisable par ville de départ.
- Pas d'ajout réel de nouvelles villes avec leurs coûts de rabattement — seule la structure est généralisée ; Dakar reste l'unique ville active à l'issue de cette itération.
- Pas de changement au nombre ou à la liste des hubs surveillés (toujours CMN, CDG, IST, ADD, NBO, ABJ).

## Fichiers impactés

- `hub_deals_db.py` — config `HUBS`/`RABATTEMENT`, boucle principale, `init_db()` (migration), `enregistrer_offres()`, `verifier_et_notifier_anomalies()` (message Telegram)
- `anomaly_detection.py` — `calculer_moyennes_historiques()`, `detecter_anomalies()`
- `detect_anomalies.py` — aucun changement de logique attendu (délègue déjà tout à `anomaly_detection.py`), mais à vérifier que l'affichage JSON reste cohérent avec le nouveau champ `ville_depart`
- `hub_deals_AUDIT.md` / `README.md` / `CHANGELOG.md` — à mettre à jour une fois l'implémentation terminée
