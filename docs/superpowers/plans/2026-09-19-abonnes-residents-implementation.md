# Abonnés résidents d'un hub — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** permettre à un abonné résidant dans une ville que nous relevons d'être alerté sur les vols directs au départ de chez lui, avec un plancher d'économie adapté au niveau de prix de son marché.

**Architecture:** un résident n'est pas un nouveau concept — c'est une ville dont le rabattement vers son propre hub vaut `0`. On expose donc le rabattement dans les anomalies détectées, on branche dessus un plancher d'économie relatif, on court-circuite la mesure d'un rabattement nul, on adapte les deux constructeurs de message, puis seulement on ouvre les villes. L'ordre est délibéré : la table de données est modifiée **en dernier**, quand tout le code qui la consomme est prêt.

**Tech Stack:** Python 3.12, SQLite, `unittest` exécuté par `pytest`, aucune dépendance nouvelle.

**Spec:** `docs/superpowers/specs/2026-09-19-abonnes-residents-design.md`

## Global Constraints

- **Aucune valeur existante de `RABATTEMENT` n'est modifiée.** On ajoute des clés, on n'en corrige aucune. Le piège documenté du projet (« ne jamais changer `RABATTEMENT` sans recalculer `total_estime` ») ne doit pas se déclencher.
- **Les 5 villes actuelles gardent exactement leur comportement sur leurs routes existantes** : plancher `ECONOMIE_MINIMALE = 80`, messages inchangés. Toute régression sur ces routes est un échec de la tâche.
- Plancher résident : `max(25 €, 12 % de la moyenne historique)`. Constantes nommées `PLANCHER_RESIDENT_EUROS = 25` et `PLANCHER_RESIDENT_PART = 0.12`.
- Un rabattement `NULL` en base (anciennes lignes, outil de diagnostic) n'est **pas** un résident : il retombe sur le plancher de 80 €. Jamais d'exception levée.
- Le dépôt écrit ses fichiers Python en ASCII pour les commentaires et les identifiants (accents autorisés uniquement dans les chaînes destinées à l'utilisateur, déjà le cas ailleurs). Suivre l'existant.
- `hub_deals_db.py` est en CRLF et mélange `€` littéral et `€` échappé : **vérifier l'octet exact avant toute édition** de ces régions, sinon le remplacement échoue silencieusement.
- Un commit par tâche, message en français sans accents dans le sujet, terminé par `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- La suite complète (`python -m pytest -q`) doit passer à la fin de chaque tâche. Elle compte 308 tests au départ.

## Structure des fichiers

| Fichier | Responsabilité | Tâches |
|---|---|---|
| `anomaly_detection.py` | expose le rabattement, porte `plancher_economie` | 1, 2 |
| `hub_deals_db.py` | court-circuit de mesure, message propriétaire, table des villes et hubs | 3, 4, 5 |
| `abonnes.py` | message abonné, noms affichés | 4, 5 |
| `reprise_residents.py` *(créé)* | migration ponctuelle de reprise d'historique | 6 |
| `tests/test_anomaly_detection.py` | seuil et exposition du rabattement | 1, 2 |
| `tests/test_hub_deals_db.py` | invariants structurels, court-circuit, message propriétaire | 3, 4, 5 |
| `tests/test_abonnes.py` | message abonné | 4 |
| `tests/test_reprise_residents.py` *(créé)* | idempotence et exactitude de la migration | 6 |

---

### Task 1 : exposer le rabattement dans les anomalies détectées

`detecter_anomalies` ne renvoie pas le rabattement de la route ; sans lui, aucune décision ne peut dépendre du fait qu'une route est directe. C'est la brique dont dépendent les tâches 2, 4 et 5.

**Files:**
- Modify: `anomaly_detection.py` (requête et dictionnaire de résultat dans `detecter_anomalies`, lignes ~168-224)
- Test: `tests/test_anomaly_detection.py` (helper `_inserer_offre`, ligne 16)

**Interfaces:**
- Consomme : rien.
- Produit : chaque dictionnaire d'anomalie porte désormais la clé `"rabattement"` (float, ou `None` si la colonne est vide). Les tâches 2, 4 et 5 en dépendent.

- [ ] **Step 1 : donner un rabattement non nul par défaut au helper de test**

Le helper actuel insère `rabattement = 0` en dur. Comme la tâche 2 fera de `0` le discriminant « résident », laisser ce défaut ferait basculer **tous** les tests de détection existants sur le plancher résident et changerait leur sens. On fixe donc un défaut non nul, représentatif d'une ville classique, avant toute autre chose.

Dans `tests/test_anomaly_detection.py`, remplacer le helper :

```python
def _inserer_offre(conn, ville, hub, dest_code, dest_nom, total_estime, date_collecte,
                   rabattement=500):
    """rabattement=500 par defaut : une ville classique. Les tests de
    residents passent explicitement rabattement=0, qui est le discriminant
    du plancher relatif."""
    conn.execute("""
        INSERT INTO offres (
            date_collecte, ville_depart, hub_origine, destination_code,
            destination_nom, prix_vol_hub, rabattement, total_estime, date_depart, lien
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-09-01T10:00:00+00:00', '/search/x')
    """, (date_collecte, ville, hub, dest_code, dest_nom, total_estime, rabattement,
          total_estime))
    conn.commit()
```

- [ ] **Step 2 : vérifier que la suite passe toujours**

Run: `python -m pytest tests/test_anomaly_detection.py -q`
Expected: PASS (le rabattement n'est encore lu nulle part, ce changement est neutre).

- [ ] **Step 3 : écrire le test qui échoue**

Ajouter dans `tests/test_anomaly_detection.py`, dans la classe qui teste `detecter_anomalies` :

```python
    def test_l_anomalie_porte_le_rabattement_de_sa_route(self):
        """Sans cette donnee, aucune decision ne peut dependre du fait que
        la route est directe."""
        for jour, prix in (("2026-08-01 10:00:00", 1000),
                           ("2026-08-02 10:00:00", 1000),
                           ("2026-08-03 10:00:00", 1000)):
            _inserer_offre(self.conn, "Dakar", "Casablanca", "BKK", "Bangkok",
                           prix, jour, rabattement=468)
        _inserer_offre(self.conn, "Dakar", "Casablanca", "BKK", "Bangkok",
                       700, "2026-08-04 10:00:00", rabattement=468)

        anomalies = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-04 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["rabattement"], 468)

    def test_un_rabattement_absent_vaut_None_et_ne_plante_pas(self):
        """detect_anomalies.py insere des lignes sans rabattement : la
        colonne peut etre NULL."""
        self.conn.execute("""
            INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                destination_code, destination_nom, prix_vol_hub, total_estime,
                date_depart, lien)
            VALUES ('2026-08-01 10:00:00', 'Dakar', 'Casablanca', 'SIN',
                    'Singapour', 900, 900, '', '')
        """)
        self.conn.commit()
        for jour in ("2026-08-02 10:00:00", "2026-08-03 10:00:00"):
            self.conn.execute("""
                INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                    destination_code, destination_nom, prix_vol_hub, total_estime,
                    date_depart, lien)
                VALUES (?, 'Dakar', 'Casablanca', 'SIN', 'Singapour', 900, 900, '', '')
            """, (jour,))
        self.conn.execute("""
            INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                destination_code, destination_nom, prix_vol_hub, total_estime,
                date_depart, lien)
            VALUES ('2026-08-04 10:00:00', 'Dakar', 'Casablanca', 'SIN',
                    'Singapour', 600, 600, '', '')
        """)
        self.conn.commit()

        anomalies = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-04 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertIsNone(anomalies[0]["rabattement"])
```

- [ ] **Step 4 : lancer les tests pour les voir échouer**

Run: `python -m pytest tests/test_anomaly_detection.py -k "rabattement_de_sa_route or rabattement_absent" -v`
Expected: FAIL avec `KeyError: 'rabattement'`.

- [ ] **Step 5 : implémenter**

Dans `anomaly_detection.py`, fonction `detecter_anomalies`, ajouter la colonne à la requête :

```python
    cur = conn.execute("""
        SELECT ville_depart, hub_origine, destination_code, destination_nom,
               total_estime, date_depart, lien, rabattement
        FROM offres
        WHERE date_collecte = ?
    """, (date_collecte,))
```

Adapter le dépaquetage de la boucle :

```python
    for (ville, hub, dest_code, dest_nom, total_estime, date_depart, lien,
         rabattement) in cur.fetchall():
```

Et ajouter la clé au dictionnaire construit (à côté de `"hub"` et `"ville_depart"`) :

```python
                # sert de discriminant au plancher d'economie : 0 = vol
                # direct depuis la ville de l'abonne (voir plancher_economie)
                "rabattement": rabattement,
```

- [ ] **Step 6 : lancer les tests**

Run: `python -m pytest tests/test_anomaly_detection.py -q`
Expected: PASS.

- [ ] **Step 7 : vérifier la non-régression complète**

Run: `python -m pytest -q`
Expected: PASS, 310 tests.

- [ ] **Step 8 : commit**

```bash
git add anomaly_detection.py tests/test_anomaly_detection.py
git commit -m "Anomalies : exposer le rabattement de la route"
```

---

### Task 2 : plancher d'économie relatif pour les routes directes

**Files:**
- Modify: `anomaly_detection.py` (constantes en tête, ~ligne 85 ; garde-fou commun dans `detecter_anomalies`, ~ligne 213)
- Test: `tests/test_anomaly_detection.py`

**Interfaces:**
- Consomme : la clé `"rabattement"` de la tâche 1.
- Produit : `plancher_economie(rabattement, moyenne) -> float`, importable depuis `anomaly_detection`.

- [ ] **Step 1 : écrire les tests qui échouent**

```python
class TestPlancherEconomie(unittest.TestCase):
    """Le plancher de 80 EUR est calibre pour des itineraires a 1127 EUR de
    mediane. Chez un resident dont le billet mediane vaut 270 a 400 EUR, il
    exige 20 a 30 % de baisse et eteint tout (mesure du 2026-09-19)."""

    def test_une_route_avec_rabattement_garde_le_plancher_absolu(self):
        self.assertEqual(anomaly_detection.plancher_economie(468, 1127), 80)

    def test_un_rabattement_nul_donne_le_plancher_relatif(self):
        # 12 % de 500 = 60, au-dessus du plancher plancher de 25
        self.assertEqual(anomaly_detection.plancher_economie(0, 500), 60)

    def test_le_plancher_relatif_ne_descend_jamais_sous_25_euros(self):
        # 12 % de 90 = 10,8 : sans garde-fou on alerterait pour 11 EUR
        self.assertEqual(anomaly_detection.plancher_economie(0, 90), 25)

    def test_un_rabattement_absent_n_est_pas_un_resident(self):
        """NULL en base n'est pas 0 : c'est une ligne dont on ignore le
        rabattement, pas un vol direct."""
        self.assertEqual(anomaly_detection.plancher_economie(None, 500), 80)

    def test_la_bascule_se_fait_exactement_a_25_euros(self):
        moyenne_pile = anomaly_detection.PLANCHER_RESIDENT_EUROS / \
            anomaly_detection.PLANCHER_RESIDENT_PART  # 208.33...
        self.assertAlmostEqual(
            anomaly_detection.plancher_economie(0, moyenne_pile), 25)
```

Et les deux tests de bout en bout, dans la classe de `detecter_anomalies` :

```python
    def test_un_resident_declenche_sur_une_economie_sous_80_euros(self):
        """58 EUR sur un billet a 138 EUR de moyenne : -42 %, refuse par le
        plancher absolu, retenu pour un vol direct."""
        for jour in ("2026-08-01 10:00:00", "2026-08-02 10:00:00",
                     "2026-08-03 10:00:00"):
            _inserer_offre(self.conn, "Istanbul", "Istanbul", "BCN", "Barcelone",
                           138, jour, rabattement=0)
        _inserer_offre(self.conn, "Istanbul", "Istanbul", "BCN", "Barcelone",
                       80, "2026-08-04 10:00:00", rabattement=0)

        anomalies = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-04 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["economie"], 58)

    def test_la_meme_baisse_ne_declenche_pas_pour_une_ville_classique(self):
        """Non-regression : le plancher de 80 EUR reste entier des que la
        route a un rabattement."""
        for jour in ("2026-08-01 10:00:00", "2026-08-02 10:00:00",
                     "2026-08-03 10:00:00"):
            _inserer_offre(self.conn, "Dakar", "Istanbul", "BCN", "Barcelone",
                           138, jour, rabattement=525)
        _inserer_offre(self.conn, "Dakar", "Istanbul", "BCN", "Barcelone",
                       80, "2026-08-04 10:00:00", rabattement=525)

        anomalies = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-04 10:00:00")

        self.assertEqual(anomalies, [])
```

- [ ] **Step 2 : lancer les tests pour les voir échouer**

Run: `python -m pytest tests/test_anomaly_detection.py -k "Plancher or resident or ville_classique" -v`
Expected: FAIL avec `AttributeError: module 'anomaly_detection' has no attribute 'plancher_economie'`.

- [ ] **Step 3 : implémenter les constantes et la fonction**

Dans `anomaly_detection.py`, sous `ECONOMIE_MINIMALE` :

```python
# Plancher des routes SANS rabattement (l'abonne reside dans la ville de
# depart). ECONOMIE_MINIMALE est calibre pour des itineraires a 1127 EUR de
# mediane, ou 80 EUR pesent 7 % du billet ; sur un marche de residence a
# 270-400 EUR de mediane, le meme seuil exige 20 a 30 % de baisse et
# n'a rien laisse passer sur 52 releves (mesure du 2026-09-19).
#
# Le double plancher est necessaire dans les deux sens : la part seule
# laisserait passer des alertes a 11 EUR sur les vols intra-europeens a
# 80 EUR, le montant seul reproduirait le biais qu'on corrige ici.
PLANCHER_RESIDENT_EUROS = 25
PLANCHER_RESIDENT_PART = 0.12


def plancher_economie(rabattement, moyenne: float) -> float:
    """Economie minimale, en euros, pour qu'une route declenche.

    Un rabattement nul veut dire que l'abonne part de chez lui : aucun
    rabattement reel ne vaut 0, un test structurel le garantit. NULL n'est
    PAS 0 -- c'est une ligne dont le rabattement est inconnu, qui retombe
    sur le plancher absolu.
    """
    if rabattement == 0:
        return max(PLANCHER_RESIDENT_EUROS, PLANCHER_RESIDENT_PART * moyenne)
    return ECONOMIE_MINIMALE
```

Note : `None == 0` vaut `False` en Python, donc le cas NULL est couvert sans test supplémentaire.

- [ ] **Step 4 : brancher la fonction sur le garde-fou commun**

Dans `detecter_anomalies`, remplacer :

```python
        economie = moyenne - total_estime
        declenche = declenche and economie >= ECONOMIE_MINIMALE
```

par :

```python
        economie = moyenne - total_estime
        declenche = declenche and economie >= plancher_economie(rabattement, moyenne)
```

- [ ] **Step 5 : lancer les tests**

Run: `python -m pytest tests/test_anomaly_detection.py -q`
Expected: PASS.

- [ ] **Step 6 : vérifier la non-régression complète**

Run: `python -m pytest -q`
Expected: PASS, 317 tests. Si un test d'un autre fichier échoue, c'est qu'il insérait un rabattement nul sans le vouloir — corriger le test en lui donnant un rabattement représentatif, **jamais** en assouplissant le seuil.

- [ ] **Step 7 : commit**

```bash
git add anomaly_detection.py tests/test_anomaly_detection.py
git commit -m "Detection : plancher d'economie relatif pour les vols directs"
```

---

### Task 3 : ne pas mesurer un rabattement nul

`mesurer_rabattements` interroge l'API pour le trajet ville → hub. Pour un résident, ce trajet est la ville vers elle-même : l'API répond 400, et sans court-circuit chaque relevé partirait chercher une valeur qui ne peut pas exister.

**Files:**
- Modify: `hub_deals_db.py:554` (juste après `cout = RABATTEMENT.get(ville, {}).get(hub_iata)`)
- Test: `tests/test_hub_deals_db.py`

**Interfaces:**
- Consomme : rien.
- Produit : pour un couple à rabattement nul, `mesurer_rabattements` renvoie `{"prix": 0, "table": 0, "mesure": False}` sans aucun appel réseau.

- [ ] **Step 1 : écrire le test qui échoue**

```python
class TestMesurerRabattementNul(unittest.TestCase):
    """Un resident part de chez lui : mesurer ce trajet reviendrait a
    interroger l'API sur une ville vers elle-meme, ce qui repond 400."""

    def setUp(self):
        self._original = hub_deals_db.RABATTEMENT
        hub_deals_db.RABATTEMENT = {
            "Paris": {"CDG": {"prix": 0, "duree_h": 0}},
            "Dakar": {"CDG": {"prix": 496, "duree_h": 6}},
        }

    def tearDown(self):
        hub_deals_db.RABATTEMENT = self._original

    def test_aucun_appel_api_pour_un_rabattement_nul(self):
        appels = []

        def faux_get_prix(origine, destination):
            appels.append((origine, destination))
            return {"price": 999}

        mesures = hub_deals_db.mesurer_rabattements(
            [("Paris", "Paris")], get_prix=faux_get_prix, pause=False)

        self.assertEqual(appels, [])
        self.assertEqual(mesures[("Paris", "Paris")],
                         {"prix": 0, "table": 0, "mesure": False})

    def test_une_ville_classique_est_toujours_mesuree(self):
        appels = []

        def faux_get_prix(origine, destination):
            appels.append((origine, destination))
            return {"price": 510}

        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Paris")], get_prix=faux_get_prix, pause=False)

        self.assertEqual(appels, [("DKR", "CDG")])
        self.assertTrue(mesures[("Dakar", "Paris")]["mesure"])
        self.assertEqual(mesures[("Dakar", "Paris")]["prix"], 510)
```

Ce test suppose `VILLE_IATA["Paris"] = "PAR"` et `VILLE_IATA["Dakar"] = "DKR"` ; le premier n'existe qu'à la tâche 5, mais il n'est jamais atteint puisque le court-circuit rend la main avant. Le second existe déjà.

- [ ] **Step 2 : lancer le test pour le voir échouer**

Run: `python -m pytest tests/test_hub_deals_db.py -k MesurerRabattementNul -v`
Expected: FAIL — `appels` vaut `[('PAR', 'CDG')]` au lieu de `[]` (ou `KeyError` sur `VILLE_IATA`).

- [ ] **Step 3 : implémenter le court-circuit**

Dans `hub_deals_db.py`, dans `mesurer_rabattements`, juste après le `continue` du rabattement inconnu :

```python
        cout = RABATTEMENT.get(ville, {}).get(hub_iata)
        if cout is None:
            continue  # pas de rabattement connu pour ce couple

        # rabattement nul = l'abonne reside dans la ville du hub. Mesurer
        # ce trajet interrogerait l'API sur une ville vers elle-meme, qui
        # repond 400 (voir EQUIVALENCES).
        if cout["prix"] == 0:
            mesures[(ville, hub_nom)] = {"prix": 0, "table": 0, "mesure": False}
            continue
```

- [ ] **Step 4 : lancer les tests**

Run: `python -m pytest tests/test_hub_deals_db.py -k MesurerRabattementNul -v`
Expected: PASS.

- [ ] **Step 5 : vérifier la non-régression complète**

Run: `python -m pytest -q`
Expected: PASS, 319 tests.

- [ ] **Step 6 : commit**

```bash
git add hub_deals_db.py tests/test_hub_deals_db.py
git commit -m "Rabattement : ne pas mesurer un trajet nul"
```

---

### Task 4 : les messages disent « vol direct »

Deux constructeurs de message produisent aujourd'hui des absurdités pour un résident : `construire_bloc` (message du propriétaire) afficherait « Paris (depuis Paris, au depart de Paris) » et « Rabattement estime, non mesure ce jour » ; `_bloc_abonne` (message de l'abonné) afficherait « Paris via Paris ».

La spec ne nommait que `_bloc_abonne` ; `construire_bloc` relève de la même exigence et est traité ici.

**Files:**
- Modify: `hub_deals_db.py:916-966` (`construire_bloc`)
- Modify: `abonnes.py:160-167` (`_bloc_abonne`)
- Test: `tests/test_hub_deals_db.py`, `tests/test_abonnes.py`

**Interfaces:**
- Consomme : la clé `"rabattement"` de la tâche 1.
- Produit : rien pour les tâches suivantes.

- [ ] **Step 1 : écrire les tests qui échouent (message abonné)**

Dans `tests/test_abonnes.py` :

```python
class TestBlocAbonneResident(unittest.TestCase):
    def _anomalie(self, rabattement):
        return {
            "destination": "Dubai", "destination_code": "DXB",
            "hub": "Paris", "ville_depart": "Paris",
            "prix_actuel": 320.0, "moyenne_historique": 420.0,
            "baisse_pct": 23.8, "economie": 100.0,
            "rabattement": rabattement, "rabattement_mesure": None,
            "lien": "/search/x", "date_depart": "2026-10-01",
        }

    def test_un_vol_direct_ne_dit_pas_via_sa_propre_ville(self):
        bloc = abonnes._bloc_abonne(self._anomalie(0), "Paris")
        self.assertIn("vol direct", bloc)
        self.assertNotIn("via Paris", bloc)

    def test_une_route_avec_rabattement_garde_le_via(self):
        bloc = abonnes._bloc_abonne(self._anomalie(496), "Dakar")
        self.assertIn("via Paris", bloc)
        self.assertNotIn("vol direct", bloc)
```

- [ ] **Step 2 : lancer pour voir échouer**

Run: `python -m pytest tests/test_abonnes.py -k BlocAbonneResident -v`
Expected: FAIL — « vol direct » absent du bloc.

- [ ] **Step 3 : implémenter dans `abonnes.py`**

```python
def _bloc_abonne(a: dict, ville: str) -> str:
    lien = hub_deals_db.url_aviasales(a["lien"], ville.lower())
    # rabattement nul = l'abonne part de chez lui ; « via Paris » pour un
    # Parisien n'aurait aucun sens
    trajet = "— vol direct" if a.get("rabattement") == 0 else f"via {a['hub']}"
    return (
        f"\n<b>{a['destination']}</b> {trajet}\n"
        f"{a['prix_actuel']:.0f}€ (-{a['baisse_pct']:.0f}%) — "
        f"{a['economie']:.0f}€ de moins que d'habitude\n"
        f"{lien}"
    )
```

- [ ] **Step 4 : lancer les tests du fichier**

Run: `python -m pytest tests/test_abonnes.py -q`
Expected: PASS. Le format des routes à rabattement doit rester **identique à l'octet près** — `tests/test_abonnes.py:150` vérifie la chaîne exacte `<b>Sao Paulo</b> via Abidjan`. Si ce test casse, c'est l'implémentation qu'il faut corriger, pas lui.

- [ ] **Step 5 : écrire les tests qui échouent (message propriétaire)**

Dans `tests/test_hub_deals_db.py` :

```python
class TestConstruireBlocResident(unittest.TestCase):
    def _anomalie(self, ville, rabattement, prix):
        return {
            "destination": "Dubai", "destination_code": "DXB",
            "hub": "Paris", "ville_depart": ville,
            "prix_actuel": prix, "moyenne_historique": prix + 150.0,
            "baisse_pct": 20.0, "economie": 150.0,
            "rabattement": rabattement, "rabattement_mesure": None,
            "lien": "/search/x", "date_depart": "2026-10-01",
        }

    def test_un_groupe_d_une_seule_ville_residente_dit_vol_direct(self):
        bloc = hub_deals_db.construire_bloc([self._anomalie("Paris", 0, 320.0)])
        self.assertIn("vol direct", bloc)
        self.assertNotIn("Rabattement estime", bloc)

    def test_un_groupe_mixte_distingue_les_deux_natures(self):
        """Un resident de Paris et un Dakarois peuvent partager la meme
        affaire CDG -> DXB : le groupe les affiche cote a cote."""
        bloc = hub_deals_db.construire_bloc([
            self._anomalie("Paris", 0, 320.0),
            self._anomalie("Dakar", 496, 816.0),
        ])
        self.assertIn("vol direct", bloc)
        self.assertIn("rabattement estime", bloc)

    def test_une_ville_classique_seule_est_inchangee(self):
        bloc = hub_deals_db.construire_bloc([self._anomalie("Dakar", 496, 816.0)])
        self.assertIn("Rabattement estime, non mesure ce jour", bloc)
        self.assertNotIn("vol direct", bloc)
```

- [ ] **Step 6 : lancer pour voir échouer**

Run: `python -m pytest tests/test_hub_deals_db.py -k ConstruireBlocResident -v`
Expected: FAIL sur les deux premiers tests.

- [ ] **Step 7 : implémenter dans `hub_deals_db.py`**

Dans `construire_bloc`, cas à une seule ville, remplacer le calcul de `note` :

```python
        if a.get("rabattement") == 0:
            note = "Vol direct, sans rabattement"
        elif a["rabattement_mesure"] is not None:
            note = f"Rabattement mesure ce jour : {a['rabattement_mesure']:.0f}€"
        else:
            note = "Rabattement estime, non mesure ce jour"
```

Cas à plusieurs villes, remplacer le calcul de `etat` :

```python
        if a.get("rabattement") == 0:
            etat = "vol direct"
        elif a["rabattement_mesure"] is not None:
            etat = f"rabattement mesure {a['rabattement_mesure']:.0f}€"
        else:
            etat = "rabattement estime"
```

Attention : ce fichier est en CRLF et utilise `€` échappé plutôt que `€` littéral dans cette région. Reproduire l'existant à l'octet près.

- [ ] **Step 8 : lancer les tests**

Run: `python -m pytest tests/test_hub_deals_db.py -q`
Expected: PASS.

- [ ] **Step 9 : vérifier la non-régression complète**

Run: `python -m pytest -q`
Expected: PASS, 324 tests.

- [ ] **Step 10 : commit**

```bash
git add hub_deals_db.py abonnes.py tests/test_hub_deals_db.py tests/test_abonnes.py
git commit -m "Messages : dire vol direct quand le rabattement est nul"
```

---

### Task 5 : ouvrir les villes résidentes

C'est l'interrupteur. Tout le code qui consomme ces données est prêt. Six tests structurels existants encodent l'hypothèse inverse (« aucune ville n'a de rabattement vers son propre hub ») : ils sont réécrits ici, pas supprimés.

**Files:**
- Modify: `hub_deals_db.py:62-72` (`HUBS`), `:136-142` (`VILLE_IATA`), `:196+` (`RABATTEMENT`)
- Modify: `abonnes.py:19-25` (`NOMS_AFFICHES`)
- Test: `tests/test_hub_deals_db.py:240-305` (classe `TestRabattement`)

**Interfaces:**
- Consomme : les tâches 1 à 4.
- Produit : 13 villes de départ et 13 hubs. La tâche 6 lit `RABATTEMENT` pour en déduire les villes résidentes.

- [ ] **Step 1 : écrire les tests structurels dans leur nouvelle forme**

Dans `tests/test_hub_deals_db.py`, classe `TestRabattement`, **remplacer** les tests suivants :

`test_les_villes_de_depart_attendues_sont_presentes` :

```python
    def test_les_villes_de_depart_attendues_sont_presentes(self):
        self.assertEqual(
            set(hub_deals_db.RABATTEMENT.keys()),
            {"Dakar", "Abidjan", "Brazzaville", "Lome", "Kinshasa",
             "Paris", "Istanbul", "Casablanca", "Le Caire", "Lagos",
             "Nairobi", "Addis-Abeba", "Johannesburg"},
        )
```

`test_chaque_entree_a_un_prix_et_une_duree_positifs` :

```python
    def test_chaque_entree_a_un_prix_et_une_duree_coherents(self):
        """Un rabattement vaut 0 exactement quand la ville est celle du hub
        (l'abonne part de chez lui) ; partout ailleurs il est positif."""
        for ville, couts in hub_deals_db.RABATTEMENT.items():
            for hub_iata, cout in couts.items():
                propre_hub = hub_deals_db.HUBS[hub_iata]["nom"] == ville
                if propre_hub:
                    self.assertEqual(
                        cout["prix"], 0,
                        msg=f"{ville}->{hub_iata} est son propre hub : prix attendu 0")
                    self.assertEqual(cout["duree_h"], 0)
                else:
                    self.assertGreater(
                        cout["prix"], 0, msg=f"prix invalide pour {ville}->{hub_iata}")
                    self.assertGreater(
                        cout["duree_h"], 0, msg=f"duree_h invalide pour {ville}->{hub_iata}")
```

`test_aucune_ville_n_a_de_rabattement_vers_son_propre_hub` — l'hypothèse s'inverse, le test devient :

```python
    def test_chaque_ville_a_un_rabattement_nul_vers_son_propre_hub(self):
        """L'inverse de l'invariant d'avant le 2026-09-19 : une ville qui est
        aussi un hub ne voyait jamais ses propres vols directs (0 ligne
        Abidjan -> via Abidjan en base sur 111 releves)."""
        noms_hubs = {info["nom"]: iata for iata, info in hub_deals_db.HUBS.items()}
        for ville, couts in hub_deals_db.RABATTEMENT.items():
            iata_propre = noms_hubs.get(ville)
            self.assertIsNotNone(
                iata_propre, msg=f"{ville} n'a pas de hub a son nom")
            self.assertIn(
                iata_propre, couts,
                msg=f"{ville} n'a pas de route directe depuis chez elle")
            self.assertEqual(couts[iata_propre]["prix"], 0)
```

`test_abidjan_contient_exactement_les_hubs_attendus` :

```python
    def test_abidjan_contient_exactement_les_hubs_attendus(self):
        self.assertEqual(
            set(hub_deals_db.RABATTEMENT["Abidjan"].keys()),
            {"CMN", "CDG", "IST", "NBO", "JNB", "CAI", "LOS", "ABJ"},
        )
```

`test_lome_omet_les_hubs_sans_donnee_reelle` :

```python
    def test_lome_omet_les_hubs_sans_donnee_reelle(self):
        """ADD et JNB n'ont de prix sur aucun des endpoints Travelpayouts
        au depart de Lome : on les omet plutot que d'inventer une valeur.
        LFW, lui, est le hub de Lome elle-meme : rabattement nul."""
        self.assertEqual(
            set(hub_deals_db.RABATTEMENT["Lome"].keys()),
            {"CMN", "CDG", "IST", "NBO", "ABJ", "CAI", "LOS", "LFW"},
        )
```

`test_kinshasa_couvre_tous_les_hubs` — Kinshasa couvrait les 9 hubs d'alors, pas les 13 d'aujourd'hui :

```python
    def test_kinshasa_couvre_tous_les_hubs_de_correspondance(self):
        """Les hubs ajoutes le 2026-09-19 (DKR, BZV, LFW) ne servent que
        leur propre ville : Kinshasa n'a pas a s'y rabattre."""
        self.assertEqual(
            set(hub_deals_db.RABATTEMENT["Kinshasa"].keys()),
            {"CMN", "CDG", "IST", "ADD", "NBO", "ABJ", "JNB", "CAI", "LOS", "FIH"},
        )
```

Ajouter deux tests neufs :

```python
    def test_une_ville_residente_n_a_que_son_propre_hub(self):
        """Les correspondances pour residents produisent des itineraires
        absurdes (Paris -> Abidjan -> Rome a 1048 EUR quand le direct est a
        88 EUR). Mesure du 2026-09-19, spec section (e)."""
        residentes = {"Paris", "Istanbul", "Casablanca", "Le Caire", "Lagos",
                      "Nairobi", "Addis-Abeba", "Johannesburg"}
        noms_hubs = {info["nom"]: iata for iata, info in hub_deals_db.HUBS.items()}
        for ville in residentes:
            self.assertEqual(
                set(hub_deals_db.RABATTEMENT[ville].keys()),
                {noms_hubs[ville]},
                msg=f"{ville} ne doit avoir que son propre hub")

    def test_les_quatre_hubs_de_residence_ne_servent_que_leur_ville(self):
        """DKR, FIH, BZV et LFW ont ete ajoutes pour que Dakar, Kinshasa,
        Brazzaville et Lome voient leurs vols directs. Aucune autre ville
        n'a de rabattement vers eux -- sans quoi il faudrait des valeurs
        qu'on n'a jamais mesurees."""
        for hub in ("DKR", "FIH", "BZV", "LFW"):
            villes = [v for v, couts in hub_deals_db.RABATTEMENT.items() if hub in couts]
            self.assertEqual(
                len(villes), 1,
                msg=f"{hub} devrait ne servir qu'une ville, il en sert {villes}")
            self.assertEqual(hub_deals_db.HUBS[hub]["nom"], villes[0])
```

- [ ] **Step 2 : lancer pour voir échouer**

Run: `python -m pytest tests/test_hub_deals_db.py -k TestRabattement -v`
Expected: FAIL sur les huit tests (les villes et hubs n'existent pas encore).

- [ ] **Step 3 : ajouter les quatre hubs**

Dans `hub_deals_db.py`, `HUBS` :

```python
HUBS = {
    "CMN": {"nom": "Casablanca"},
    "CDG": {"nom": "Paris"},
    "IST": {"nom": "Istanbul"},
    "ADD": {"nom": "Addis-Abeba"},
    "NBO": {"nom": "Nairobi"},
    "ABJ": {"nom": "Abidjan"},
    "JNB": {"nom": "Johannesburg"},
    "CAI": {"nom": "Le Caire"},
    "LOS": {"nom": "Lagos"},
    # Ajoutes le 2026-09-19 : ces villes ne sont pas des hubs de
    # correspondance, elles sont interrogees pour que leurs propres
    # habitants voient leurs vols directs. Aucune autre ville n'a de
    # rabattement vers eux, la boucle d'insertion les saute donc d'elle-meme.
    "DKR": {"nom": "Dakar"},
    "FIH": {"nom": "Kinshasa"},
    "BZV": {"nom": "Brazzaville"},
    "LFW": {"nom": "Lome"},
}
```

- [ ] **Step 4 : ajouter les codes IATA des villes nouvelles**

```python
VILLE_IATA = {
    "Dakar": "DKR",
    "Abidjan": "ABJ",
    "Brazzaville": "BZV",
    "Lome": "LFW",
    "Kinshasa": "FIH",
    # villes residentes ajoutees le 2026-09-19
    "Paris": "PAR",          # PAR est le code destination ; CDG, le code hub
    "Istanbul": "IST",
    "Casablanca": "CMN",
    "Le Caire": "CAI",
    "Lagos": "LOS",
    "Nairobi": "NBO",
    "Addis-Abeba": "ADD",
    "Johannesburg": "JNB",
}
```

- [ ] **Step 5 : ajouter les entrées de rabattement**

Dans chacune des 5 villes existantes de `RABATTEMENT`, ajouter la ligne de son propre hub :

- `"Dakar"` : `"DKR": {"prix": 0, "duree_h": 0},`
- `"Abidjan"` : `"ABJ": {"prix": 0, "duree_h": 0},`
- `"Brazzaville"` : `"BZV": {"prix": 0, "duree_h": 0},`
- `"Lome"` : `"LFW": {"prix": 0, "duree_h": 0},`
- `"Kinshasa"` : `"FIH": {"prix": 0, "duree_h": 0},`

Puis, à la fin du dictionnaire, les 8 villes résidentes :

```python
    # --- Villes residentes, ajoutees le 2026-09-19 ---------------------
    # Un resident n'est pas un concept nouveau : c'est une ville dont le
    # rabattement vers son propre hub vaut 0. Le 0 est la valeur sincere,
    # pas un code d'exception -- et c'est lui qui bascule le plancher
    # d'economie sur sa forme relative (voir plancher_economie).
    #
    # Une seule entree par ville, volontairement : les correspondances
    # pour residents produisent des itineraires absurdes (Paris -> Abidjan
    # -> Rome a 1048 EUR quand notre propre base a le direct a 88 EUR).
    "Paris": {"CDG": {"prix": 0, "duree_h": 0}},
    "Istanbul": {"IST": {"prix": 0, "duree_h": 0}},
    "Casablanca": {"CMN": {"prix": 0, "duree_h": 0}},
    "Le Caire": {"CAI": {"prix": 0, "duree_h": 0}},
    "Lagos": {"LOS": {"prix": 0, "duree_h": 0}},
    "Nairobi": {"NBO": {"prix": 0, "duree_h": 0}},
    "Addis-Abeba": {"ADD": {"prix": 0, "duree_h": 0}},
    "Johannesburg": {"JNB": {"prix": 0, "duree_h": 0}},
```

**Ne toucher à aucune valeur existante.** Un `git diff` ne doit montrer que des lignes ajoutées dans ce dictionnaire.

- [ ] **Step 6 : ajouter les noms affichés aux abonnés**

Dans `abonnes.py` :

```python
NOMS_AFFICHES = {
    "Dakar": "Dakar",
    "Abidjan": "Abidjan",
    "Lome": "Lomé",
    "Kinshasa": "Kinshasa",
    "Brazzaville": "Brazzaville",
    "Paris": "Paris",
    "Istanbul": "Istanbul",
    "Casablanca": "Casablanca",
    "Le Caire": "Le Caire",
    "Lagos": "Lagos",
    "Nairobi": "Nairobi",
    "Addis-Abeba": "Addis-Abeba",
    "Johannesburg": "Johannesburg",
}
```

- [ ] **Step 7 : lancer les tests**

Run: `python -m pytest tests/test_hub_deals_db.py tests/test_abonnes.py -q`
Expected: PASS.

- [ ] **Step 8 : vérifier la non-régression complète**

Run: `python -m pytest -q`
Expected: PASS, 326 tests.

- [ ] **Step 9 : vérifier le diff de la table à l'œil**

Run: `git diff hub_deals_db.py | grep "^-" | grep -v "^---"`
Expected: **aucune ligne supprimée** dans `RABATTEMENT` (seules `HUBS` et `VILLE_IATA` peuvent montrer des accolades déplacées). Si une valeur existante a bougé, revenir en arrière : le piège du recalcul de `total_estime` serait amorcé.

- [ ] **Step 10 : commit**

```bash
git add hub_deals_db.py abonnes.py tests/test_hub_deals_db.py
git commit -m "Villes : ouvrir les 9 hubs en residence et promouvoir 4 villes en hubs"
```

---

### Task 6 : reprise d'historique des villes résidentes

Pour les 9 villes adossées à un hub déjà relevé, l'historique existe déjà sous une autre clé : `total_estime = prix_vol_hub + 0`, et `prix_vol_hub` est stocké depuis le 2026-07-21. Le report est arithmétiquement exact. Sans lui, ces villes resteraient muettes 3 relevés.

**Files:**
- Create: `reprise_residents.py`
- Test: `tests/test_reprise_residents.py`

**Interfaces:**
- Consomme : `hub_deals_db.RABATTEMENT`, `hub_deals_db.HUBS`, `hub_deals_db.VILLE_IATA` (tâche 5).
- Produit : `villes_residentes() -> dict[str, str]` (ville → code IATA de son hub) et `reprendre(conn) -> int` (nombre de lignes insérées).

- [ ] **Step 1 : écrire les tests qui échouent**

Créer `tests/test_reprise_residents.py` :

```python
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db
hub_deals_db.TRAVELPAYOUTS_PROJET = None
import reprise_residents


class TestVillesResidentes(unittest.TestCase):
    def test_reconnait_une_ville_a_son_rabattement_nul(self):
        villes = reprise_residents.villes_residentes()
        self.assertEqual(villes["Paris"], "CDG")
        self.assertEqual(villes["Abidjan"], "ABJ")
        self.assertEqual(villes["Dakar"], "DKR")

    def test_les_treize_villes_sont_residentes(self):
        self.assertEqual(len(reprise_residents.villes_residentes()), 13)


class TestReprise(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def _offre(self, date, ville, hub, dest, prix_vol, rabattement):
        self.conn.execute("""
            INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                destination_code, destination_nom, prix_vol_hub, rabattement,
                total_estime, date_depart, lien)
            VALUES (?, ?, ?, ?, 'Test', ?, ?, ?, '2026-10-01', '/search/x')
        """, (date, ville, hub, dest, prix_vol, rabattement, prix_vol + rabattement))
        self.conn.commit()

    def test_reporte_le_prix_du_vol_sans_rabattement(self):
        self._offre("2026-08-01 10:00:00", "Dakar", "Paris", "DXB", 300, 496)

        reprise_residents.reprendre(self.conn)

        ligne = self.conn.execute("""
            SELECT prix_vol_hub, rabattement, total_estime FROM offres
            WHERE ville_depart = 'Paris'
        """).fetchone()
        self.assertEqual(ligne, (300, 0, 300))

    def test_ne_duplique_pas_les_lignes_d_une_source_multi_villes(self):
        """Cinq villes partagent le meme prix hub -> destination : le report
        ne doit produire qu'une ligne pour le resident."""
        for ville, rab in (("Dakar", 496), ("Abidjan", 486), ("Kinshasa", 708)):
            self._offre("2026-08-01 10:00:00", ville, "Paris", "DXB", 300, rab)

        reprise_residents.reprendre(self.conn)

        n = self.conn.execute(
            "SELECT COUNT(*) FROM offres WHERE ville_depart = 'Paris'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_est_idempotente(self):
        self._offre("2026-08-01 10:00:00", "Dakar", "Paris", "DXB", 300, 496)

        premier = reprise_residents.reprendre(self.conn)
        second = reprise_residents.reprendre(self.conn)

        self.assertEqual(premier, 1)
        self.assertEqual(second, 0)
        n = self.conn.execute(
            "SELECT COUNT(*) FROM offres WHERE ville_depart = 'Paris'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_n_ecrit_pas_de_route_ramenant_la_ville_chez_elle(self):
        self._offre("2026-08-01 10:00:00", "Dakar", "Paris", "PAR", 120, 496)

        reprise_residents.reprendre(self.conn)

        n = self.conn.execute(
            "SELECT COUNT(*) FROM offres WHERE ville_depart = 'Paris'").fetchone()[0]
        self.assertEqual(n, 0)

    def test_ne_touche_a_aucune_ligne_existante(self):
        self._offre("2026-08-01 10:00:00", "Dakar", "Paris", "DXB", 300, 496)
        avant = self.conn.execute("""
            SELECT ville_depart, prix_vol_hub, rabattement, total_estime
            FROM offres WHERE ville_depart = 'Dakar'
        """).fetchall()

        reprise_residents.reprendre(self.conn)

        apres = self.conn.execute("""
            SELECT ville_depart, prix_vol_hub, rabattement, total_estime
            FROM offres WHERE ville_depart = 'Dakar'
        """).fetchall()
        self.assertEqual(avant, apres)

    def test_ignore_un_hub_sans_historique(self):
        """Dakar, Kinshasa, Brazzaville et Lome viennent d'etre promus
        hubs : aucune ligne a reporter pour eux, et ce n'est pas une
        erreur."""
        self.assertEqual(reprise_residents.reprendre(self.conn), 0)
```

- [ ] **Step 2 : lancer pour voir échouer**

Run: `python -m pytest tests/test_reprise_residents.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'reprise_residents'`.

- [ ] **Step 3 : écrire le script**

Créer `reprise_residents.py` :

```python
"""Reprise d'historique des villes residentes (migration ponctuelle).

Une ville residente est une ville dont le rabattement vers son propre hub
vaut 0 : son total estime est exactement le prix du vol depuis ce hub, deja
stocke dans la colonne prix_vol_hub depuis le 2026-07-21. Le report est
donc arithmetiquement exact, et non une estimation.

Sans lui, chaque ville ouverte le 2026-09-19 resterait muette trois
releves, le temps que MIN_RELEVES_HISTORIQUE soit atteint.

Rejouable sans risque : une ligne deja presente n'est jamais reinseree.

Usage : python reprise_residents.py [chemin_base]
"""
import sqlite3
import sys

import hub_deals_db


def villes_residentes(rabattement=None, hubs=None) -> dict:
    """{nom de ville: code IATA de son hub} pour toute ville dont le
    rabattement vers un hub vaut 0."""
    rabattement = hub_deals_db.RABATTEMENT if rabattement is None else rabattement
    hubs = hub_deals_db.HUBS if hubs is None else hubs
    trouvees = {}
    for ville, couts in rabattement.items():
        for hub_iata, cout in couts.items():
            if cout["prix"] == 0:
                trouvees[ville] = hub_iata
    return trouvees


def reprendre(conn: sqlite3.Connection) -> int:
    """Insere l'historique des vols directs de chaque ville residente.

    Renvoie le nombre de lignes inserees.
    """
    total = 0
    for ville, hub_iata in villes_residentes().items():
        hub_nom = hub_deals_db.HUBS[hub_iata]["nom"]
        code_ville = hub_deals_db.VILLE_IATA[ville]
        cur = conn.execute("""
            INSERT INTO offres (
                date_collecte, ville_depart, hub_origine, destination_code,
                destination_nom, prix_vol_hub, rabattement, total_estime,
                date_depart, lien
            )
            SELECT DISTINCT o.date_collecte, ?, o.hub_origine, o.destination_code,
                   o.destination_nom, o.prix_vol_hub, 0, o.prix_vol_hub,
                   o.date_depart, o.lien
            FROM offres o
            WHERE o.hub_origine = ?
              AND o.ville_depart != ?
              AND o.destination_code != ?
              AND o.prix_vol_hub IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM offres deja
                  WHERE deja.ville_depart = ?
                    AND deja.date_collecte = o.date_collecte
                    AND deja.hub_origine = o.hub_origine
                    AND deja.destination_code = o.destination_code
              )
        """, (ville, hub_nom, ville, code_ville, ville))
        total += cur.rowcount
    conn.commit()
    return total


if __name__ == "__main__":
    chemin = sys.argv[1] if len(sys.argv) > 1 else hub_deals_db.DB_PATH
    connexion = sqlite3.connect(chemin)
    try:
        n = reprendre(connexion)
        print(f"{n} ligne(s) reportee(s) dans {chemin}")
    finally:
        connexion.close()
```

`hub_deals_db.DB_PATH` vaut `"flight_deals.db"` (vérifié le 2026-09-19).

- [ ] **Step 4 : lancer les tests**

Run: `python -m pytest tests/test_reprise_residents.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5 : vérifier la non-régression complète**

Run: `python -m pytest -q`
Expected: PASS, 334 tests.

- [ ] **Step 6 : commit**

```bash
git add reprise_residents.py tests/test_reprise_residents.py
git commit -m "Reprise : reporter l'historique des vols directs des villes residentes"
```

---

### Task 7 : vérification de bout en bout sur une copie de la base réelle

Les tests unitaires prouvent chaque pièce ; ils ne prouvent pas que l'ensemble produit les volumes annoncés dans la spec. Cette tâche le vérifie **sur une copie**, sans jamais écrire dans la base de production.

**Files:**
- Create: aucun fichier versionné — le script vit dans le répertoire temporaire de la session.

**Interfaces:**
- Consomme : les tâches 1 à 6.
- Produit : une décision go / no-go avant de laisser tourner le relevé quotidien.

- [ ] **Step 1 : copier la base et y appliquer la reprise**

```bash
cp flight_deals.db "$TEMP/verif_residents.db"
python reprise_residents.py "$TEMP/verif_residents.db"
```

Expected : un nombre de l'ordre de **16 000** lignes reportées (16 156 mesurées le 2026-09-19 ; l'écart correspond aux relevés faits depuis).

- [ ] **Step 2 : vérifier l'exactitude arithmétique du report**

```bash
python -c "
import sqlite3, os
c = sqlite3.connect(os.path.join(os.environ['TEMP'], 'verif_residents.db'))
faux = c.execute('''SELECT COUNT(*) FROM offres
    WHERE rabattement = 0 AND total_estime != prix_vol_hub''').fetchone()[0]
print('lignes incoherentes :', faux)
"
```

Expected : `lignes incoherentes : 0`.

- [ ] **Step 3 : compter les affaires par ville résidente sur le dernier mois**

```bash
python -c "
import sqlite3, os, statistics, sys
sys.path.insert(0, '.')
import anomaly_detection as ad
c = sqlite3.connect(os.path.join(os.environ['TEMP'], 'verif_residents.db'))
dates = [r[0] for r in c.execute(
    \"SELECT DISTINCT date_collecte FROM offres WHERE date_collecte >= '2026-08-20' ORDER BY date_collecte\")]
tot = {}
for d in dates:
    c.execute('BEGIN')
    c.execute('DELETE FROM offres WHERE date_collecte > ?', (d,))
    for a in ad.detecter_anomalies(c, date_collecte=d):
        tot.setdefault(a['ville_depart'], []).append(a['economie'])
    c.execute('ROLLBACK')
print(len(dates), 'releves')
for v in sorted(tot, key=lambda v: -len(tot[v])):
    print(f\"{v:<16}{len(tot[v]):>5} affaires  mediane {statistics.median(tot[v]):>6.0f} EUR\")
"
```

Expected : les 5 villes classiques retrouvent leurs ordres de grandeur connus (Lomé, Dakar et Kinshasa en tête, médiane autour de 143 €) — **c'est le cas témoin, il doit passer en premier**. Les villes résidentes sortent aux volumes de la spec : Istanbul la plus bavarde (~60 affaires), Paris la plus pauvre en montant (médiane ~22 € avant plancher, ~19 affaires retenues), Abidjan et Lagos les plus rentables (médiane 150-190 €).

Si les villes classiques ne retrouvent pas leurs chiffres, **arrêter** : une régression s'est glissée dans le détecteur, et aucun chiffre résident n'est interprétable.

- [ ] **Step 4 : vérifier le rendu d'un message résident**

```bash
python -c "
import sqlite3, os, sys
sys.path.insert(0, '.')
import hub_deals_db, anomaly_detection as ad
c = sqlite3.connect(os.path.join(os.environ['TEMP'], 'verif_residents.db'))
d = ad.get_dernier_releve(c)
anos = [dict(a, rabattement_mesure=None) for a in ad.detecter_anomalies(c, date_collecte=d)]
for g in hub_deals_db.grouper_anomalies(anos)[:3]:
    print(hub_deals_db.construire_bloc(g))
    print('---')
"
```

Expected : aucun bloc ne contient « depuis X, au depart de X » avec une mention de rabattement estimé ; les lignes de résident portent « vol direct ».

- [ ] **Step 5 : appliquer la reprise à la base réelle**

Uniquement si les étapes 1 à 4 sont concluantes. La sauvegarde quotidienne étant poussée sur la branche `sauvegardes`, un retour en arrière reste possible.

```bash
python sauvegarde.py
python reprise_residents.py
```

Expected : le même ordre de grandeur qu'à l'étape 1.

- [ ] **Step 6 : lancer un relevé complet et lire le journal**

```bash
python hub_deals_db.py
tail -40 flight_deals_log.txt
```

Expected : ~404 routes interrogées, aucune erreur `400` sur un segment ville → elle-même, le compte d'anomalies et d'affaires journalisé, et la mention `Abonnes : n/n envoye(s)`.

- [ ] **Step 7 : commit** (rien à commiter si tout est propre — cette tâche ne produit pas de code)

```bash
git status --short
```

Expected : arbre propre. `flight_deals.db` et `flight_deals_log.txt` sont ignorés par git.

---

### Task 8 : documentation

**Files:**
- Modify: `README.md` (principe, tableau des fichiers, section détection)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consomme : tout ce qui précède.
- Produit : rien.

- [ ] **Step 1 : mettre à jour le principe dans le README**

Remplacer la phrase d'ouverture (« Détecteur de bonnes affaires vol au départ de Dakar, Abidjan, Brazzaville, Lomé et Kinshasa, via 9 hubs… ») par une formulation à 13 villes et 13 hubs, et ajouter sous le bloc `total_estime` :

```markdown
Une ville peut aussi être **sa propre origine** : le rabattement vaut alors 0 et
`total_estime` se réduit au prix du vol direct. C'est le cas des treize villes vers leur
propre hub — un Parisien ne paie rien pour rejoindre Paris.
```

- [ ] **Step 2 : documenter le plancher relatif**

Dans la section « Détection d'anomalie », après le paragraphe sur `ECONOMIE_MINIMALE` :

```markdown
Ce plancher de 80 € est calibré pour des itinéraires à 1 127 € de médiane, où il pèse 7 %
du billet. Sur un vol direct au départ de la ville de l'abonné — rabattement nul, billet
médian de 270 à 400 € — il exigerait 20 à 30 % de baisse et n'a rien laissé passer sur
52 relevés. Ces routes utilisent donc un plancher relatif : `max(25 €, 12 % de la
moyenne)`. Le pourcentage est légitime ici et nulle part ailleurs, parce qu'il n'est
déformé que par la correction de rabattement — inexistante quand le rabattement est nul.
```

- [ ] **Step 3 : ajouter le script au tableau des fichiers**

```markdown
| `reprise_residents.py` | Migration ponctuelle : reporte l'historique des vols directs des villes résidentes. Rejouable sans risque. |
```

- [ ] **Step 4 : écrire l'entrée de CHANGELOG**

Suivre le format des entrées existantes. Couvrir : les 8 villes résidentes, les 4 hubs ajoutés, le plancher relatif et son motif, le court-circuit de mesure, les messages « vol direct », la reprise d'historique, et le fait qu'aucune valeur existante de `RABATTEMENT` n'a été touchée.

- [ ] **Step 5 : vérifier la suite une dernière fois**

Run: `python -m pytest -q`
Expected: PASS, 334 tests.

- [ ] **Step 6 : commit et push**

```bash
git add README.md CHANGELOG.md
git commit -m "Doc : abonnes residents et plancher d'economie relatif"
git push
```

Le push doit passer avec un délai d'attente (`timeout`) : un `git push` bloqué a déjà fait sauter jusqu'à 3 jours de relevés sans alerte (revue du 2026-08-22).

---

## Auto-revue

**Couverture de la spec**

| Section de la spec | Tâche |
|---|---|
| §1 `HUBS` — 4 entrées nouvelles | 5 |
| §2 `RABATTEMENT` — 8 villes, 5 entrées | 5 |
| §3 Seuil — `plancher_economie` | 1, 2 |
| §4 Court-circuit de `mesurer_rabattements` | 3 |
| §5 Message « vol direct » | 4 |
| §6 Reprise d'historique idempotente | 6 |
| Tests structurels | 5 |
| Tests de seuil | 2 |
| Tests de collecte | 5 (invariants) + 7 (relevé réel) |
| Tests de rabattement mesuré | 3 |
| Tests de message | 4 |
| Tests de migration | 6 |

**Écarts assumés par rapport à la spec**

- La spec ne nommait que `_bloc_abonne` pour le message ; `construire_bloc` (message du propriétaire) souffre du même défaut et est traité dans la même tâche.
- La spec ne mentionnait pas que `detecter_anomalies` ne renvoyait pas le rabattement. C'est la tâche 1, préalable à tout le reste.
- La spec ne mentionnait pas le helper de test `_inserer_offre`, qui insérait `rabattement = 0` en dur et aurait fait basculer toute la suite existante sur le plancher résident. Traité à la tâche 1, étape 1.
- Les tests « une ville résidente ne reçoit de lignes que pour son propre hub » et « un résident de Paris ne voit jamais PAR/CDG » sont couverts structurellement (tâche 5) plutôt que par un test de collecte : `enregistrer_prix` saute déjà les hubs sans entrée de rabattement, et `EQUIVALENCES` empêche la collecte de `CDG -> PAR` en amont. Un test de collecte ne ferait que re-tester du code inchangé.
