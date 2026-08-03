# Abidjan comme 2ᵉ ville de départ — design

## Contexte

L'itération précédente ([[2026-08-03-multi-ville-depart-design]]) a généralisé `hub_deals` pour
supporter plusieurs villes de départ, chacune avec sa propre table de coûts de rabattement vers
les 6 hubs partagés (`RABATTEMENT[ville][hub]`). Seule `"Dakar"` était peuplée. Cette itération
ajoute `"Abidjan"` comme deuxième ville active — Abidjan étant déjà l'un des 6 hubs surveillés,
mais devenant ici elle-même un point de départ.

Aucun changement d'architecture n'est nécessaire : le code (`enregistrer_offres`, `init_db`,
`anomaly_detection.py`) traite déjà nativement une ville avec un sous-ensemble de hubs (un hub
absent de `RABATTEMENT[ville]` est simplement ignoré, cf. `couts_ville.get(hub_iata)` →
`continue`).

## Sourcing des données (décisions actées du brainstorming)

Contrairement à Dakar (valeurs rondes, estimées manuellement), les coûts Abidjan sont obtenus par
requête directe à l'API Travelpayouts (`v1/prices/cheap?origin=ABJ&destination=<hub>`, complété par
`v3/prices_for_dates` pour NBO faute de résultat sur `v1/prices/cheap`), le 2026-08-03 :

| Hub | Prix (€) | `duree_h` (arrondi depuis `duration_to` en minutes) |
|---|---|---|
| CMN (Casablanca) | 563 | 3 (205 min) |
| CDG (Paris) | 511 | 8 (480 min) |
| IST (Istanbul) | 700 | 9 (540 min) |
| NBO (Nairobi) | 374 | 8 (465 min) |
| ADD (Addis-Abeba) | — | **omis** : aucune donnée disponible sur `v1/prices/cheap`, `v2/prices/latest`, ni `v3/prices_for_dates` malgré plusieurs tentatives (route trop peu recherchée pour être en cache côté Travelpayouts) |
| ABJ | — | n/a : Abidjan est déjà le hub, pas de rabattement vers lui-même |

Valeurs gardées **exactes** (pas arrondies à la dizaine comme Dakar) — traçables à une requête API
datée, donc pas besoin d'arrondir pour masquer une estimation manuelle.

`ADD` reste absent de `RABATTEMENT["Abidjan"]` pour l'instant. Rien à coder pour ce cas : c'est le
même mécanisme qui permet déjà à n'importe quelle ville de n'avoir qu'un sous-ensemble de hubs.
Pourra être ajouté plus tard si l'API renvoie des données.

## Changement de code

Dans `hub_deals_db.py`, remplacer le commentaire placeholder par l'entrée réelle :

```python
RABATTEMENT = {
    "Dakar": {
        "CMN": {"prix": 400, "duree_h": 4},
        "CDG": {"prix": 300, "duree_h": 6},
        "IST": {"prix": 400, "duree_h": 7},
        "ADD": {"prix": 500, "duree_h": 6},
        "NBO": {"prix": 500, "duree_h": 8},
        "ABJ": {"prix": 200, "duree_h": 2},
    },
    "Abidjan": {
        "CMN": {"prix": 563, "duree_h": 3},
        "CDG": {"prix": 511, "duree_h": 8},
        "IST": {"prix": 700, "duree_h": 9},
        "NBO": {"prix": 374, "duree_h": 8},
    },
}
```

Le commentaire au-dessus est mis à jour pour documenter que les valeurs Dakar sont une estimation
manuelle (juillet 2026) et celles d'Abidjan viennent d'une requête API directe (2026-08-03).

## Test de garde-fou

Nouveau test dans `tests/test_hub_deals_db.py`, sur le **vrai** `hub_deals_db.RABATTEMENT` (pas de
monkeypatch, contrairement aux tests existants qui testent la logique générique avec des données
fictives) :

- `RABATTEMENT["Abidjan"]` contient exactement les clés `{"CMN", "CDG", "IST", "NBO"}` (donc pas
  `ADD` ni `ABJ` — vérifie que l'omission volontaire ne régresse pas silencieusement, et qu'on
  n'a pas ajouté par erreur une entrée `ABJ` vers soi-même).
- Chaque entrée a un `prix` et un `duree_h` numériques strictement positifs.

Objectif : attraper une faute de frappe ou un oubli futur (ex. ajout accidentel d'un hub, prix
négatif ou à zéro par erreur de copier-coller), pas re-tester la logique déjà couverte par les
tests existants.

## Documentation à mettre à jour

- `README.md` : mentionner Abidjan comme 2ᵉ ville active (actuellement affirme "seule Dakar est
  active").
- `CHANGELOG.md` : entrée pour l'ajout d'Abidjan.
- `hub_deals_AUDIT.md` : entrée documentant les requêtes API utilisées, les valeurs obtenues, et
  l'absence de données pour ADD.

## Vérification en conditions réelles

Même méthode que l'itération précédente : après merge, exécuter `python hub_deals_db.py` contre la
vraie base + déclencher la vraie tâche planifiée "Traqueur de vols", et vérifier :

- aucune `ERREUR` dans les logs
- `SELECT DISTINCT ville_depart FROM offres` inclut maintenant `'Abidjan'` en plus de `'Dakar'`
- le nombre de lignes insérées par relevé augmente cohéremment (offres CMN/CDG/IST/NBO dupliquées
  pour Abidjan, pas pour ADD/ABJ)

## Hors scope (YAGNI)

- Pas d'ajout de données pour `ADD` (aucune source fiable disponible actuellement).
- Pas de rabattement `ABJ → ABJ` (n'a pas de sens : Abidjan est déjà le hub).
- Pas de nouvelle ville de départ au-delà d'Abidjan dans cette itération.
- Pas de mécanisme de rafraîchissement automatique des coûts de rabattement via l'API — ce sont
  des valeurs figées, mises à jour manuellement comme pour Dakar.

## Fichiers impactés

- `hub_deals_db.py` — `RABATTEMENT["Abidjan"]` + commentaire
- `tests/test_hub_deals_db.py` — nouveau test de garde-fou
- `README.md`, `CHANGELOG.md`, `hub_deals_AUDIT.md` — documentation
