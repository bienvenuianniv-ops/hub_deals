# Rabattement mesuré au moment de l'alerte — design

## Contexte

Les alertes Telegram annoncent un total faux. `total_estime = prix_vol_hub + rabattement`, où
`rabattement` vient d'une table écrite en dur dans `hub_deals_db.py`. Cette table mélange des
estimations manuelles (Dakar, juillet 2026) et des relevés API (Kinshasa et Lomé, 2026-08-15).

Le chantier « recherche de billet à la demande » avait noté au passage que `RABATTEMENT`
sous-estimait de 17 à 31 %, sur deux mesures ponctuelles, et avait renvoyé le problème à une
itération dédiée. C'est cette itération.

## Mesure des 40 segments (2026-08-16)

Les 40 couples (ville de départ, hub) de `RABATTEMENT` ont été interrogés à l'API. Le résultat
contredit la prémisse d'un sous-dimensionnement uniforme :

| Constat | Valeur |
|---|---|
| Segments avec un prix API | **23 / 40** |
| Segments sans aucun prix | **17 / 40** |
| Écart médian table → API | **+0 %** |
| Segments sous-estimés | 12 |
| Segments sur-estimés | 3 |

Trois faits structurent la conception :

1. **Le problème est le vieillissement, pas le sous-dimensionnement.** Kinshasa et Lomé, relevés
   la veille par API, collent exactement (+0 % sur 11 segments). Les dérives concernent les
   villes aux valeurs les plus anciennes : Brazzaville → Lagos **+171 %**, Abidjan → Nairobi
   **+136 %**, Dakar → Abidjan **+104 %**, Abidjan → Le Caire +110 %, Brazzaville → Le Caire
   +59 %. Les « 17 à 31 % » cités dans la spec précédente étaient les deux plus petits écarts.
2. **Trois segments sont sur-estimés** (Abidjan → Istanbul −4 %, Lomé → Abidjan −2 %). Une
   correction appliquée « à la hausse » par principe les empirerait.
3. **`CDG` n'a de prix pour aucune des 5 villes** ce jour-là. Or Paris sort en tête de la
   plupart des meilleurs totaux. La couverture API n'est donc pas un détail d'implémentation :
   toute solution doit fonctionner correctement quand la mesure est indisponible, ce qui est le
   cas le plus fréquent sur le hub le plus utile.

## Décisions actées (brainstorming)

- **But : l'exactitude du chiffre affiché.** La détection fonctionne et n'est pas en cause.
- **Approche retenue : mesurer au moment de l'alerte**, pas à chaque relevé ni dans la table.
- **Présentation : échelle décalée** — le rabattement mesuré s'applique au prix du jour **et** à
  la moyenne historique.
- **Aucune écriture en base.** Le rabattement mesuré ne touche ni `total_estime`, ni l'historique,
  ni la détection.

### Pourquoi pas les autres approches

**Table auto-rafraîchie à chaque relevé** (+40 appels) : s'attaque à la cause, mais rend la
colonne `rabattement` variable dans le temps. Sa variance s'ajouterait alors à celle du vol
principal, et le z-score — qui surveille `total_estime` — se déclencherait sur des baisses du
trajet vers le hub plutôt que sur des bonnes affaires à destination. Rejeté au regard du but
déclaré.

**Re-relevé ponctuel de la table** : corrige 23 valeurs sur 40 et se re-désynchronise. Dakar l'a
prouvé en six semaines.

**Rehausser la table sans mesurer** : ferait sauter `total_estime` de plusieurs dizaines de
pourcents d'un coup. La moyenne historique resterait basse, donc plus aucune anomalie ne se
déclencherait pendant plusieurs jours, et les 9 166 lignes déjà en base deviendraient
incomparables aux nouvelles.

## Architecture

Une fonction isolée dans `hub_deals_db.py` :

```python
mesurer_rabattements(couples, get_prix=None, pause=True) -> dict
```

- `couples` : itérable de `(ville, hub_nom)` — le nom de hub, car c'est ce que porte la colonne
  `hub_origine` des anomalies, pas le code IATA.
- Retour : `{(ville, hub_nom): {"prix": float, "table": float, "mesure": bool}}`.
  `table` est la valeur de `RABATTEMENT`, rendue avec la mesure pour que le calcul du décalage
  (`prix − table`) reste une fonction pure de son entrée, sans relire les globales.
- `get_prix` est **injectable** : les tests passent une fausse fonction, aucun appel réseau.

**Piège d'identifiants à traiter explicitement.** Les anomalies portent le *nom* du hub
(`hub_origine` vaut « Paris »), tandis que `HUBS` et `RABATTEMENT` sont indexés par *code IATA*
(« CDG »). La fonction doit donc construire une correspondance nom → IATA à partir de `HUBS`, et
ignorer proprement un nom introuvable plutôt que lever une `KeyError` en pleine notification.
Les noms de `HUBS` sont uniques — un test structurel doit le garantir, faute de quoi
l'inversion écraserait silencieusement un hub.

Elle vit dans le collecteur et non dans `recherche.py` : ce dernier importe déjà `hub_deals_db`,
l'inverse créerait un cycle d'imports.

`anomaly_detection.py` **n'est pas modifié**. Il reste une logique pure, sans réseau — c'est ce
qui le rend testable et réutilisable par `detect_anomalies.py`.

## Algorithme

1. `detecter_anomalies()` renvoie ses résultats, inchangés.
2. Extraire les couples `(ville_depart, hub)` **distincts** des anomalies. 8 à 11 anomalies
   donnent typiquement 3 à 6 couples.
3. Pour chaque couple, interroger `VILLE_IATA[ville] → code IATA du hub`.
   - Prix trouvé → `{"prix": prix_api, "mesure": True}`
   - Aucun prix, ou erreur réseau → `{"prix": valeur_de_la_table, "mesure": False}`
4. Pour chaque anomalie dont le couple est **mesuré** :
   - `delta = prix_mesuré − rabattement_table`
   - `prix_actuel += delta` et `moyenne_historique += delta`
   - `baisse_pct` recalculée sur les valeurs décalées
5. Re-trier les anomalies par `baisse_pct` décroissante.

### Pourquoi le décalage est exact

Le rabattement est une **constante additive** pour une route donnée : la même valeur entre dans
chaque ligne de son historique. Ajouter `delta` au prix du jour et à la moyenne préserve donc
exactement l'écart absolu et l'écart-type, donc le z-score. Seul le pourcentage change, puisque
son dénominateur augmente.

**Hypothèse assumée et à énoncer dans le message** : on substitue une constante à une autre. Le
total affiché est « ce que vaudrait cette route si le rabattement mesuré aujourd'hui s'appliquait
à tout l'historique ». C'est faux au sens strict — le rabattement a bougé lui aussi — mais c'est
la seule transformation qui garde tous les chiffres du message cohérents entre eux.

### Pourquoi re-trier

Le décalage est plus fort sur les routes dont le rabattement est le plus sous-estimé, et il
réduit le pourcentage. Sans re-tri, l'ordre d'affichage ne correspondrait plus aux pourcentages
affichés — le lecteur verrait « −12 % » au-dessus de « −15 % ».

## Format du message

Route mesurée :

```
Nairobi (depuis Abidjan, au depart de Dakar)
1019 EUR (moyenne habituelle : 1109 EUR, -8%)
Rabattement mesure ce jour : 409 EUR
https://www.aviasales.com/search/...
```

Route non mesurée (l'API n'a pas répondu) :

```
Londres (depuis Paris, au depart de Dakar)
810 EUR (moyenne habituelle : 900 EUR, -10%)
Rabattement estime, non mesure ce jour
https://www.aviasales.com/search/...
```

La mention est **toujours présente**, dans un cas comme dans l'autre. Une correction silencieuse
serait pire que pas de correction : elle rendrait indiscernables un chiffre mesuré et un chiffre
estimé.

## Gestion des erreurs

- Erreur réseau sur un segment → repli sur la table, `mesure: False`, journalisé.
- Le message journalisé passe par `log()`, qui masque les secrets. Une exception `requests`
  contient l'URL complète, token compris — c'est la fuite corrigée le 2026-08-16 dans
  `recherche.py`.
- **Aucune erreur de mesure ne doit empêcher l'envoi de la notification.** Une alerte avec des
  totaux non corrigés vaut infiniment mieux qu'une alerte perdue.

## Tests

Aucun appel réseau : `get_prix` est injectée, comme dans `tests/test_recherche.py`.

- `mesurer_rabattements` renvoie le prix API quand il existe, avec `mesure: True`
- repli sur la valeur de la table quand l'API ne renvoie rien, avec `mesure: False`
- repli identique sur erreur réseau, sans propager l'exception
- les couples sont dédoublonnés : deux anomalies sur le même (ville, hub) = un seul appel
- le décalage s'applique au prix du jour **et** à la moyenne
- l'écart absolu est préservé par le décalage
- une anomalie non mesurée n'est pas décalée du tout
- les anomalies sont re-triées après correction
- une erreur de mesure n'empêche pas l'envoi de la notification
- un nom de hub introuvable dans `HUBS` est ignoré sans lever d'exception
- les noms de `HUBS` sont uniques (test structurel : sans quoi l'inversion nom → IATA
  perdrait un hub silencieusement)

## Vérification en conditions réelles

- Relevé complet réel : comparer le message Telegram reçu aux valeurs de la base.
- Vérifier qu'au moins une route affiche `Rabattement mesure ce jour` et au moins une autre
  `Rabattement estime, non mesure ce jour` — les segments `CDG` garantissent le second cas.
- Vérifier que `SELECT COUNT(*) FROM offres` et les `total_estime` stockés sont **identiques**
  à ce qu'ils auraient été sans ce changement : la correction est d'affichage uniquement.
- Vérifier dans le journal qu'aucun token n'apparaît en clair.

## Hors scope (YAGNI)

- Rafraîchir `RABATTEMENT` automatiquement.
- Corriger les valeurs de la table à la main.
- Corriger le classement stocké en base ou `classement_du_jour()`.
- Étendre la correction à `detect_anomalies.py` (outil de diagnostic, pas d'alerte).
- Toute modification de `anomaly_detection.py`.

## Fichiers impactés

| Fichier | Changement |
|---|---|
| `hub_deals_db.py` | `mesurer_rabattements()` + correction dans `verifier_et_notifier_anomalies()` |
| `tests/test_hub_deals_db.py` | Tests de la mesure et du décalage |
| `README.md`, `CHANGELOG.md` | Documentation |
| `hub_deals_AUDIT.md` | Mesures relevées et vérification |
