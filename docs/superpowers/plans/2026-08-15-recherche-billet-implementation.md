# Recherche de billet à la demande — plan d'implémentation

> **Pour les agents :** SOUS-COMPÉTENCE REQUISE — utiliser `superpowers:subagent-driven-development`
> (recommandé) ou `superpowers:executing-plans` pour exécuter ce plan tâche par tâche. Les étapes
> utilisent des cases à cocher (`- [x]`) pour le suivi.

**Objectif :** permettre à l'utilisateur d'interroger lui-même une route (une de ses 5 villes → une
destination quelconque) et de placer une destination sous surveillance du relevé quotidien.

**Architecture :** un module autonome `recherche.py` (CLI + logique), qui importe `hub_deals_db`
pour réutiliser `get_prix_route`, `construire_lien`, `HUBS`, `DESTINATIONS`, `RABATTEMENT` et
`VILLE_IATA`. La lecture des destinations personnelles vit dans `hub_deals_db.py` — c'est le
collecteur qui en a besoin — et l'écriture dans `recherche.py`, ce qui évite tout import circulaire.

**Pile technique :** Python 3, `requests`, `sqlite3`, `unittest` (bibliothèque standard). Aucune
nouvelle dépendance.

**Spec :** `docs/superpowers/specs/2026-08-15-recherche-billet-design.md`

## Contraintes globales

- **Aucun appel réseau dans les tests.** La fonction de prix est injectée en paramètre ; les tests
  passent une fausse fonction. Modèle existant : le remplacement de `envoyer_telegram` dans
  `tests/test_hub_deals_db.py`.
- **Aucune écriture dans `flight_deals.db` depuis `recherche.py`** — une recherche manuelle ne doit
  jamais entrer dans l'historique qui nourrit la détection statistique.
- **Commentaires et messages en français sans accents** dans le code Python, comme le reste du
  projet (`hub_deals_db.py`, `anomaly_detection.py`). Les accents sont admis dans la documentation
  Markdown.
- **`MAX_DESTINATIONS_PERSO = 15`** — chaque destination surveillée coûte 9 appels par relevé.
- **`PAUSE_ENTRE_APPELS`** (0.4 s) est respectée entre deux appels réels, jamais dans les tests.
- Le collecteur `hub_deals_db.py` ne doit subir qu'une greffe minimale : lecture du fichier
  personnel, fusion dans la boucle, une ligne de log. Rien d'autre.
- Suite verte à chaque commit : `python -m unittest discover -s tests`.
- **Imports** : les tâches ajoutent des imports au fil de l'eau (`time`, `requests`, `sqlite3`,
  `json`, `sys`). Les regrouper en tête de `recherche.py` dès qu'ils sont introduits, comme dans
  `hub_deals_db.py` — ne pas les laisser éparpillés au milieu du fichier.
- `hub_deals_db.py` importe déjà `json` : ne pas le réimporter en tâche 4.

---

### Tâche 1 : Résolution des entrées

Valider la ville et traduire l'argument de destination en code IATA. Aucune entrée/sortie, aucun
réseau — la brique la plus simple à tester.

**Fichiers :**
- Créer : `recherche.py`
- Créer : `tests/test_recherche.py`

**Interfaces :**
- Consomme : `hub_deals_db.RABATTEMENT`, `hub_deals_db.DESTINATIONS`, `hub_deals_db.VILLE_IATA`
- Produit :
  - `valider_ville(argument: str) -> str` — renvoie le nom canonique de la ville, lève `ValueError`
  - `resoudre_destination(argument: str) -> str` — renvoie un code IATA, lève `ValueError`

- [x] **Étape 1 : Écrire les tests qui échouent**

```python
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import recherche


class TestResolutionDesEntrees(unittest.TestCase):
    def test_accepte_une_ville_connue(self):
        self.assertEqual(recherche.valider_ville("Dakar"), "Dakar")

    def test_accepte_une_ville_sans_tenir_compte_de_la_casse(self):
        self.assertEqual(recherche.valider_ville("kinshasa"), "Kinshasa")

    def test_refuse_une_ville_inconnue_en_listant_les_villes_valides(self):
        with self.assertRaises(ValueError) as ctx:
            recherche.valider_ville("Marseille")
        message = str(ctx.exception)
        self.assertIn("Marseille", message)
        self.assertIn("Dakar", message)  # la liste des villes valides est proposee

    def test_un_code_de_trois_lettres_est_pris_tel_quel(self):
        self.assertEqual(recherche.resoudre_destination("bkk"), "BKK")

    def test_un_nom_connu_est_traduit_en_code_iata(self):
        self.assertEqual(recherche.resoudre_destination("Brazzaville"), "BZV")

    def test_un_nom_connu_est_traduit_sans_tenir_compte_de_la_casse(self):
        self.assertEqual(recherche.resoudre_destination("brazzaville"), "BZV")

    def test_refuse_un_nom_inconnu_en_demandant_le_code_iata(self):
        with self.assertRaises(ValueError) as ctx:
            recherche.resoudre_destination("Ouagadougou")
        self.assertIn("code IATA", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

Commande : `python -m unittest tests.test_recherche -v`
Attendu : `ModuleNotFoundError: No module named 'recherche'`

- [x] **Étape 3 : Écrire l'implémentation minimale**

Créer `recherche.py` :

```python
"""
Recherche de billet a la demande.

Le collecteur (hub_deals_db.py) fonctionne en eventail : il interroge une
matrice hubs x destinations imposee et signale ce qu'il juge anormalement
bas. Ce module fait l'inverse : l'utilisateur pose sa propre question.

Deux differences importantes avec le collecteur :
  - le VOL DIRECT ville -> destination est interroge, ce que le collecteur
    ne fait jamais (il passe toujours par un hub) ;
  - les segments ville -> hub sont demandes a l'API plutot que lus dans la
    table RABATTEMENT, dont les valeurs estimees se sont revelees
    optimistes de 17 a 31 % (voir la spec).

Aucune ecriture dans flight_deals.db : une recherche manuelle ne doit pas
entrer dans l'historique qui nourrit la detection statistique, sinon les
recherches de l'utilisateur fausseraient ses propres alertes.
"""

import hub_deals_db as collecteur


def valider_ville(argument: str) -> str:
    """Renvoie le nom canonique de la ville de depart, ou leve ValueError.

    Les villes disponibles sont celles de RABATTEMENT : ce sont les seules
    pour lesquelles on connait un cout de rabattement de repli.
    """
    for ville in collecteur.RABATTEMENT:
        if ville.lower() == argument.lower():
            return ville
    villes = ", ".join(sorted(collecteur.RABATTEMENT))
    raise ValueError(
        f"Ville de depart inconnue : {argument}. Villes disponibles : {villes}")


def resoudre_destination(argument: str) -> str:
    """Traduit l'argument en code IATA.

    Un argument de 3 lettres est pris pour un code IATA (permet de viser
    n'importe quelle ville du monde). Sinon on cherche parmi les noms des
    destinations connues. Aucun annuaire de villes n'est embarque : on ne
    devine pas un code a partir d'un nom inconnu.
    """
    if len(argument) == 3:
        return argument.upper()
    for code, nom in collecteur.DESTINATIONS.items():
        if nom.lower() == argument.lower():
            return code
    raise ValueError(
        f"Destination inconnue : {argument}. Donne son code IATA "
        f"(3 lettres, par exemple BKK pour Bangkok).")
```

- [x] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

Commande : `python -m unittest tests.test_recherche -v`
Attendu : 7 tests PASS

- [x] **Étape 5 : Vérifier que la suite complète reste verte**

Commande : `python -m unittest discover -s tests`
Attendu : OK (37 tests existants + 7 nouveaux = 44)

- [x] **Étape 6 : Commit**

```bash
git add recherche.py tests/test_recherche.py
git commit -m "feat(recherche): resolution de la ville et de la destination"
```

---

### Tâche 2 : Construction et classement des itinéraires

Le cœur. Interroge le vol direct et chaque hub, applique le repli sur `RABATTEMENT`, écarte les
options sans prix, trie.

**Fichiers :**
- Modifier : `recherche.py`
- Modifier : `tests/test_recherche.py`

**Interfaces :**
- Consomme : `valider_ville`, `resoudre_destination` (tâche 1),
  `hub_deals_db.get_prix_route`, `hub_deals_db.construire_lien`
- Produit :
  - `chercher_itineraires(ville: str, dest: str, get_prix=None, pause: bool = True) -> tuple[list[dict], list[str]]`
    — renvoie `(options, erreurs)`. Chaque option est un `dict` avec les clés :
    `libelle` (str), `hub` (str|None), `prix_aller` (float|None), `aller_estime` (bool),
    `prix_principal` (float), `total` (float), `estime` (bool), `date_depart` (str), `lien` (str).
    `erreurs` est une liste de messages lisibles.

- [x] **Étape 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_recherche.py` :

```python
class TestChercherItineraires(unittest.TestCase):
    """La fonction de prix est injectee : aucun appel reseau ici."""

    def setUp(self):
        self._rabattement = recherche.collecteur.RABATTEMENT
        self._hubs = recherche.collecteur.HUBS
        self._ville_iata = recherche.collecteur.VILLE_IATA
        recherche.collecteur.HUBS = {
            "CDG": {"nom": "Paris"},
            "IST": {"nom": "Istanbul"},
            "CMN": {"nom": "Casablanca"},
        }
        recherche.collecteur.RABATTEMENT = {
            "Testville": {
                "CDG": {"prix": 300, "duree_h": 6},
                "IST": {"prix": 400, "duree_h": 7},
                "CMN": {"prix": 350, "duree_h": 4},
            },
        }
        recherche.collecteur.VILLE_IATA = {"Testville": "TST"}

    def tearDown(self):
        recherche.collecteur.RABATTEMENT = self._rabattement
        recherche.collecteur.HUBS = self._hubs
        recherche.collecteur.VILLE_IATA = self._ville_iata

    def _prix(self, table):
        """Fabrique une fausse get_prix_route a partir d'un dict
        {(origine, destination): prix ou None}."""
        def get_prix(origine, destination):
            valeur = table.get((origine, destination))
            if valeur is None:
                return {}
            return {"price": valeur, "departure_at": "2026-09-05T10:00:00+00:00"}
        return get_prix

    def test_classe_les_options_du_moins_cher_au_plus_cher(self):
        options, _ = recherche.chercher_itineraires(
            "Testville", "BKK",
            get_prix=self._prix({
                ("TST", "BKK"): 1120,          # direct
                ("TST", "CDG"): 320, ("CDG", "BKK"): 540,   # 860
                ("TST", "IST"): 525, ("IST", "BKK"): 410,   # 935
                ("TST", "CMN"): 468, ("CMN", "BKK"): 690,   # 1158
            }), pause=False)

        self.assertEqual([o["total"] for o in options], [860, 935, 1120, 1158])

    def test_inclut_le_vol_direct_que_le_collecteur_n_interroge_jamais(self):
        options, _ = recherche.chercher_itineraires(
            "Testville", "BKK",
            get_prix=self._prix({("TST", "BKK"): 500}), pause=False)

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["libelle"], "direct")
        self.assertIsNone(options[0]["hub"])
        self.assertEqual(options[0]["total"], 500)

    def test_replie_sur_le_rabattement_quand_l_aller_n_a_pas_de_prix(self):
        options, _ = recherche.chercher_itineraires(
            "Testville", "BKK",
            get_prix=self._prix({("CDG", "BKK"): 540}), pause=False)

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["prix_aller"], 300)   # valeur de RABATTEMENT
        self.assertTrue(options[0]["aller_estime"])
        self.assertTrue(options[0]["estime"])
        self.assertEqual(options[0]["total"], 840)

    def test_ecarte_l_option_dont_le_segment_principal_n_a_pas_de_prix(self):
        """Sans prix pour hub -> destination, il n'existe aucune valeur de
        repli honnete : l'option disparait au lieu d'etre valorisee a 0."""
        options, _ = recherche.chercher_itineraires(
            "Testville", "BKK",
            get_prix=self._prix({("TST", "CDG"): 320}), pause=False)

        self.assertEqual(options, [])

    def test_a_total_egal_l_option_mesuree_passe_devant_l_estimee(self):
        options, _ = recherche.chercher_itineraires(
            "Testville", "BKK",
            get_prix=self._prix({
                ("TST", "CDG"): 500, ("CDG", "BKK"): 340,   # 840, mesure
                ("IST", "BKK"): 440,                        # 840, aller estime a 400
            }), pause=False)

        self.assertEqual([o["total"] for o in options], [840, 840])
        self.assertFalse(options[0]["estime"])
        self.assertTrue(options[1]["estime"])

    def test_saute_le_hub_egal_a_la_destination(self):
        """Aller a Paris via Paris n'est pas un itineraire : c'est le direct."""
        options, _ = recherche.chercher_itineraires(
            "Testville", "CDG",
            get_prix=self._prix({
                ("TST", "CDG"): 320,
                ("IST", "CDG"): 200,
            }), pause=False)

        libelles = [o["libelle"] for o in options]
        self.assertIn("direct", libelles)
        self.assertNotIn("via Paris", libelles)

    def test_refuse_une_destination_egale_a_la_ville_de_depart(self):
        with self.assertRaises(ValueError) as ctx:
            recherche.chercher_itineraires(
                "Testville", "TST", get_prix=self._prix({}), pause=False)
        self.assertIn("TST", str(ctx.exception))

    def test_une_erreur_reseau_n_interrompt_pas_la_recherche(self):
        import requests

        def get_prix(origine, destination):
            if origine == "TST" and destination == "CDG":
                raise requests.exceptions.RequestException("coupure")
            if (origine, destination) == ("CDG", "BKK"):
                return {"price": 540, "departure_at": "2026-09-05T10:00:00+00:00"}
            return {}

        options, erreurs = recherche.chercher_itineraires(
            "Testville", "BKK", get_prix=get_prix, pause=False)

        self.assertEqual(len(options), 1)          # repli sur RABATTEMENT
        self.assertTrue(options[0]["aller_estime"])
        self.assertEqual(len(erreurs), 1)
        self.assertIn("TST", erreurs[0])
```

- [x] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

Commande : `python -m unittest tests.test_recherche -v`
Attendu : `AttributeError: module 'recherche' has no attribute 'chercher_itineraires'`

- [x] **Étape 3 : Écrire l'implémentation minimale**

Ajouter à `recherche.py` (après `resoudre_destination`) :

```python
import time

import requests


def _appeler(get_prix, origine, destination, erreurs, pause):
    """Appelle la fonction de prix en absorbant les erreurs reseau.

    Renvoie l'offre (dict) ou {} si aucun prix. Une erreur reseau est
    consignee dans `erreurs` et traitee comme une absence de prix : une
    coupure sur un segment ne doit pas faire echouer toute la recherche.
    """
    try:
        offre = get_prix(origine, destination)
    except requests.exceptions.RequestException as e:
        erreurs.append(f"{origine}->{destination} : erreur reseau ({e})")
        return {}
    if pause:
        time.sleep(collecteur.PAUSE_ENTRE_APPELS)
    return offre or {}


def chercher_itineraires(ville, dest, get_prix=None, pause=True):
    """Construit et classe les itineraires de `ville` vers `dest`.

    Renvoie (options, erreurs). Le vol direct figure dans le meme
    classement que les trajets via hub.

    get_prix est injectable pour les tests ; par defaut on interroge
    reellement l'API via le collecteur.
    """
    if get_prix is None:
        get_prix = collecteur.get_prix_route

    origine = collecteur.VILLE_IATA[ville]
    if dest == origine:
        raise ValueError(
            f"Destination identique a la ville de depart ({dest}) : "
            f"cet itineraire n'a pas de sens.")

    erreurs = []
    options = []

    # 1. le vol direct -- le collecteur ne l'interroge jamais
    offre = _appeler(get_prix, origine, dest, erreurs, pause)
    if offre.get("price"):
        depart = offre.get("departure_at") or ""
        options.append({
            "libelle": "direct",
            "hub": None,
            "prix_aller": None,
            "aller_estime": False,
            "prix_principal": offre["price"],
            "total": offre["price"],
            "estime": False,
            "date_depart": depart,
            "lien": collecteur.construire_lien(origine, dest, depart),
        })

    # 2. un itineraire par hub disposant d'un rabattement pour cette ville
    for hub, cout in collecteur.RABATTEMENT[ville].items():
        if hub == dest:
            continue  # aller a X via X, c'est le vol direct deja traite

        offre_aller = _appeler(get_prix, origine, hub, erreurs, pause)
        if offre_aller.get("price"):
            prix_aller = offre_aller["price"]
            aller_estime = False
        else:
            prix_aller = cout["prix"]  # repli sur la valeur estimee
            aller_estime = True

        offre_principale = _appeler(get_prix, hub, dest, erreurs, pause)
        if not offre_principale.get("price"):
            continue  # aucun repli honnete possible sur ce segment

        depart = offre_principale.get("departure_at") or ""
        options.append({
            "libelle": f"via {collecteur.HUBS[hub]['nom']}",
            "hub": hub,
            "prix_aller": prix_aller,
            "aller_estime": aller_estime,
            "prix_principal": offre_principale["price"],
            "total": prix_aller + offre_principale["price"],
            "estime": aller_estime,
            "date_depart": depart,
            "lien": collecteur.construire_lien(hub, dest, depart),
        })

    # a total egal, l'option entierement mesuree passe devant l'estimee :
    # les estimations se sont revelees optimistes de 17 a 31 %
    options.sort(key=lambda o: (o["total"], o["estime"]))
    return options, erreurs
```

- [x] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

Commande : `python -m unittest tests.test_recherche -v`
Attendu : 15 tests PASS

- [x] **Étape 5 : Vérifier que la suite complète reste verte**

Commande : `python -m unittest discover -s tests`
Attendu : OK

- [x] **Étape 6 : Commit**

```bash
git add recherche.py tests/test_recherche.py
git commit -m "feat(recherche): construction et classement des itineraires"
```

---

### Tâche 3 : Contexte historique et affichage

Lire la base en lecture seule pour situer le prix, et produire la sortie texte.

**Fichiers :**
- Modifier : `recherche.py`
- Modifier : `tests/test_recherche.py`

**Interfaces :**
- Consomme : `chercher_itineraires` (tâche 2), `hub_deals_db.DB_PATH`
- Produit :
  - `contexte_historique(conn, ville: str, dest: str) -> dict | None` — `None` si la route est
    inconnue de la base ; sinon `{"nb_releves": int, "minimum": float, "date_minimum": str}`
  - `formater(ville: str, dest: str, options: list, erreurs: list, contexte: dict | None) -> str`

- [x] **Étape 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_recherche.py` :

```python
import sqlite3


class TestContexteHistorique(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        recherche.collecteur.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def _inserer(self, date, ville, dest, total):
        self.conn.execute("""
            INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                destination_code, destination_nom, prix_vol_hub, rabattement,
                total_estime, date_depart, lien)
            VALUES (?, ?, 'Paris', ?, 'Test', 0, 0, ?, '', '')
        """, (date, ville, dest, total))
        self.conn.commit()

    def test_renvoie_none_pour_une_route_inconnue(self):
        self.assertIsNone(
            recherche.contexte_historique(self.conn, "Dakar", "BKK"))

    def test_renvoie_le_minimum_et_sa_date(self):
        self._inserer("2026-08-10 10:00:00", "Dakar", "BZV", 810)
        self._inserer("2026-08-14 10:00:00", "Dakar", "BZV", 1001)
        self._inserer("2026-08-15 10:00:00", "Dakar", "BZV", 1001)

        contexte = recherche.contexte_historique(self.conn, "Dakar", "BZV")

        self.assertEqual(contexte["nb_releves"], 3)
        self.assertEqual(contexte["minimum"], 810)
        self.assertEqual(contexte["date_minimum"], "2026-08-10 10:00:00")

    def test_ne_melange_pas_les_villes_de_depart(self):
        self._inserer("2026-08-10 10:00:00", "Dakar", "BZV", 810)
        self._inserer("2026-08-10 10:00:00", "Kinshasa", "BZV", 200)

        contexte = recherche.contexte_historique(self.conn, "Dakar", "BZV")

        self.assertEqual(contexte["nb_releves"], 1)
        self.assertEqual(contexte["minimum"], 810)


class TestFormatage(unittest.TestCase):
    def _option(self, libelle, total, estime=False, hub="CDG"):
        return {
            "libelle": libelle, "hub": hub, "prix_aller": 300,
            "aller_estime": estime, "prix_principal": total - 300,
            "total": total, "estime": estime,
            "date_depart": "2026-09-05T10:00:00+00:00", "lien": "/search/x",
        }

    def test_affiche_la_meilleure_option_en_tete(self):
        sortie = recherche.formater(
            "Dakar", "BKK", [self._option("via Paris", 840),
                             self._option("via Istanbul", 935)], [], None)

        self.assertIn("Meilleure option", sortie)
        self.assertIn("via Paris", sortie)

    def test_signale_un_prix_estime_par_des_parentheses(self):
        sortie = recherche.formater(
            "Dakar", "BKK", [self._option("via Paris", 840, estime=True)], [], None)

        self.assertIn("(300)", sortie)

    def test_avertit_quand_la_meilleure_option_repose_sur_un_estime(self):
        sortie = recherche.formater(
            "Dakar", "BKK", [self._option("via Paris", 840, estime=True)], [], None)

        self.assertIn("estime", sortie.lower())
        self.assertIn("plus eleve", sortie.lower())

    def test_propose_la_surveillance_quand_la_route_est_inconnue(self):
        sortie = recherche.formater(
            "Dakar", "BKK", [self._option("via Paris", 840)], [], None)

        self.assertIn("--surveiller BKK", sortie)

    def test_situe_le_prix_par_rapport_au_minimum_historique(self):
        sortie = recherche.formater(
            "Dakar", "BZV", [self._option("via Paris", 1001)], [],
            {"nb_releves": 46, "minimum": 810, "date_minimum": "2026-08-10 10:00:00"})

        self.assertIn("810", sortie)
        self.assertIn("46", sortie)
        self.assertIn("23", sortie)  # +23 % au-dessus du minimum

    def test_message_honnete_quand_aucun_itineraire_n_est_trouve(self):
        sortie = recherche.formater("Dakar", "XXX", [], [], None)

        self.assertIn("aucun", sortie.lower())
        self.assertNotIn("Meilleure option", sortie)

    def test_mentionne_les_erreurs_reseau_rencontrees(self):
        sortie = recherche.formater(
            "Dakar", "BKK", [self._option("via Paris", 840)],
            ["DKR->IST : erreur reseau (coupure)"], None)

        self.assertIn("erreur reseau", sortie)
```

- [x] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

Commande : `python -m unittest tests.test_recherche -v`
Attendu : `AttributeError: module 'recherche' has no attribute 'contexte_historique'`

- [x] **Étape 3 : Écrire l'implémentation minimale**

Ajouter à `recherche.py` :

```python
import sqlite3


def contexte_historique(conn, ville, dest):
    """Ce que la base sait deja de cette route, ou None si elle l'ignore.

    Lecture seule : ce module n'ecrit jamais dans flight_deals.db.
    """
    ligne = conn.execute("""
        SELECT COUNT(DISTINCT date_collecte), MIN(total_estime)
        FROM offres
        WHERE ville_depart = ? AND destination_code = ?
    """, (ville, dest)).fetchone()

    if not ligne or not ligne[0]:
        return None

    nb_releves, minimum = ligne
    date_minimum = conn.execute("""
        SELECT date_collecte FROM offres
        WHERE ville_depart = ? AND destination_code = ? AND total_estime = ?
        ORDER BY date_collecte LIMIT 1
    """, (ville, dest, minimum)).fetchone()[0]

    return {
        "nb_releves": nb_releves,
        "minimum": minimum,
        "date_minimum": date_minimum,
    }


def formater(ville, dest, options, erreurs, contexte):
    """Produit la sortie texte de la recherche."""
    nom_dest = collecteur.DESTINATIONS.get(dest, dest)
    lignes = [f"\n{ville} -> {nom_dest} ({dest})\n"]

    if not options:
        lignes.append("  Aucun itineraire trouve : le cache de l'API ne connait")
        lignes.append("  aucun prix pour cette route. Ce n'est pas une panne.")
    else:
        for o in options:
            if o["hub"] is None:
                detail = " " * 18
            elif o["aller_estime"]:
                detail = f"({o['prix_aller']:.0f})+{o['prix_principal']:5.0f} ="
            else:
                detail = f" {o['prix_aller']:4.0f} +{o['prix_principal']:5.0f} ="
            provenance = "[aller estime]" if o["aller_estime"] else "[API]"
            lignes.append(
                f"  {o['libelle']:<18}{detail}{o['total']:6.0f} EUR   "
                f"depart {o['date_depart'][:10]}   {provenance}")

        meilleure = options[0]
        lignes.append(f"\n  Meilleure option : {meilleure['libelle']}, "
                      f"{meilleure['total']:.0f} EUR")
        if meilleure["lien"]:
            lignes.append(f"  https://www.aviasales.com{meilleure['lien']}")
        if meilleure["estime"]:
            lignes.append("  Attention : son prix d'aller est estime, pas mesure -- "
                          "le total reel peut etre plus eleve.")

    lignes.append("")
    if contexte is None:
        lignes.append("  Historique : route inconnue de ta base.")
        lignes.append(f"  -> python recherche.py --surveiller {dest}"
                      f"  (+{len(collecteur.HUBS)} appels par releve)")
    else:
        lignes.append(
            f"  Historique : {contexte['nb_releves']} releves, meilleur prix vu "
            f"{contexte['minimum']:.0f} EUR le {contexte['date_minimum'][:10]}.")
        if options:
            ecart = (options[0]["total"] - contexte["minimum"]) / contexte["minimum"] * 100
            lignes.append(f"  Prix actuel : {ecart:+.0f} % par rapport a ce minimum.")

    if erreurs:
        lignes.append("")
        lignes.append("  Segments non interroges :")
        for e in erreurs:
            lignes.append(f"    - {e}")

    return "\n".join(lignes)
```

- [x] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

Commande : `python -m unittest tests.test_recherche -v`
Attendu : 25 tests PASS

- [x] **Étape 5 : Vérifier que la suite complète reste verte**

Commande : `python -m unittest discover -s tests`
Attendu : OK

- [x] **Étape 6 : Commit**

```bash
git add recherche.py tests/test_recherche.py
git commit -m "feat(recherche): contexte historique et affichage des resultats"
```

---

### Tâche 4 : Destinations personnelles — lecture et écriture

La lecture vit dans `hub_deals_db.py` (le collecteur en a besoin), l'écriture dans `recherche.py`.
Cet ordre évite un import circulaire.

**Fichiers :**
- Modifier : `hub_deals_db.py`
- Modifier : `recherche.py`
- Modifier : `tests/test_recherche.py`
- Modifier : `.gitignore`

**Interfaces :**
- Produit dans `hub_deals_db.py` :
  - `CHEMIN_DESTINATIONS_PERSO = "destinations_perso.json"`
  - `charger_destinations_perso(chemin=CHEMIN_DESTINATIONS_PERSO) -> dict`
  - `destinations_actives(chemin=CHEMIN_DESTINATIONS_PERSO) -> dict`
- Produit dans `recherche.py` :
  - `MAX_DESTINATIONS_PERSO = 15`
  - `ajouter_destination(code: str, chemin=None) -> None` — lève `ValueError` au-delà du plafond
  - `retirer_destination(code: str, chemin=None) -> bool`

- [x] **Étape 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_recherche.py` :

```python
import json
import tempfile


class TestDestinationsPersonnelles(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.chemin = os.path.join(self.dossier.name, "destinations_perso.json")

    def tearDown(self):
        self.dossier.cleanup()

    def test_fichier_absent_renvoie_un_dictionnaire_vide(self):
        self.assertEqual(
            recherche.collecteur.charger_destinations_perso(self.chemin), {})

    def test_fichier_illisible_est_ignore_sans_faire_echouer(self):
        """Un JSON corrompu ne doit jamais faire echouer un releve : c'est
        un fichier de confort, pas une source de verite."""
        with open(self.chemin, "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du json")

        self.assertEqual(
            recherche.collecteur.charger_destinations_perso(self.chemin), {})

    def test_ajoute_une_destination(self):
        recherche.ajouter_destination("BKK", chemin=self.chemin)

        with open(self.chemin, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"BKK": "Bangkok"})

    def test_reprend_le_nom_de_DESTINATIONS_quand_il_existe(self):
        recherche.ajouter_destination("BZV", chemin=self.chemin)

        contenu = recherche.collecteur.charger_destinations_perso(self.chemin)
        self.assertEqual(contenu["BZV"], "Brazzaville")

    def test_utilise_le_code_comme_nom_pour_une_destination_inconnue(self):
        recherche.ajouter_destination("XYZ", chemin=self.chemin)

        contenu = recherche.collecteur.charger_destinations_perso(self.chemin)
        self.assertEqual(contenu["XYZ"], "XYZ")

    def test_retire_une_destination(self):
        recherche.ajouter_destination("BKK", chemin=self.chemin)

        self.assertTrue(recherche.retirer_destination("BKK", chemin=self.chemin))
        self.assertEqual(
            recherche.collecteur.charger_destinations_perso(self.chemin), {})

    def test_retirer_une_destination_absente_renvoie_faux(self):
        self.assertFalse(recherche.retirer_destination("BKK", chemin=self.chemin))

    def test_refuse_au_dela_du_plafond(self):
        for i in range(recherche.MAX_DESTINATIONS_PERSO):
            recherche.ajouter_destination(f"Z{i:02d}", chemin=self.chemin)

        with self.assertRaises(ValueError) as ctx:
            recherche.ajouter_destination("BKK", chemin=self.chemin)
        self.assertIn(str(recherche.MAX_DESTINATIONS_PERSO), str(ctx.exception))

    def test_destinations_actives_fusionne_sans_ecraser_les_originales(self):
        recherche.ajouter_destination("BKK", chemin=self.chemin)

        actives = recherche.collecteur.destinations_actives(self.chemin)

        self.assertIn("BKK", actives)                    # la personnelle
        self.assertIn("DKR", actives)                    # les originales
        self.assertEqual(actives["DKR"], "Dakar")
        self.assertEqual(len(actives), len(recherche.collecteur.DESTINATIONS) + 1)
```

- [x] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

Commande : `python -m unittest tests.test_recherche -v`
Attendu : `AttributeError: module 'hub_deals_db' has no attribute 'charger_destinations_perso'`

- [x] **Étape 3 : Écrire l'implémentation minimale**

Ajouter à `hub_deals_db.py`, juste après la définition de `EQUIVALENCES` :

```python
# Destinations ajoutees par l'utilisateur via recherche.py --surveiller.
# Fichier de confort, local et non versionne : un releve ne doit jamais
# echouer parce qu'il est absent ou mal forme.
CHEMIN_DESTINATIONS_PERSO = "destinations_perso.json"


def charger_destinations_perso(chemin=CHEMIN_DESTINATIONS_PERSO):
    """Lit les destinations personnelles. Renvoie {} si le fichier est
    absent, illisible ou mal forme -- jamais d'exception."""
    try:
        with open(chemin, encoding="utf-8") as f:
            contenu = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(contenu, dict):
        return {}
    return {str(k): str(v) for k, v in contenu.items()}


def destinations_actives(chemin=CHEMIN_DESTINATIONS_PERSO):
    """Destinations imposees + destinations personnelles.

    Les originales ne sont jamais ecrasees : en cas de doublon, c'est le
    nom d'origine qui prime."""
    return {**charger_destinations_perso(chemin), **DESTINATIONS}
```

Ajouter à `recherche.py` :

```python
import json

MAX_DESTINATIONS_PERSO = 15  # chaque destination coute 9 appels par releve


def ajouter_destination(code, chemin=None):
    """Place une destination sous surveillance du releve quotidien."""
    chemin = chemin or collecteur.CHEMIN_DESTINATIONS_PERSO
    perso = collecteur.charger_destinations_perso(chemin)

    if code not in perso and len(perso) >= MAX_DESTINATIONS_PERSO:
        raise ValueError(
            f"Plafond atteint : {MAX_DESTINATIONS_PERSO} destinations "
            f"personnelles au maximum (chacune coute "
            f"{len(collecteur.HUBS)} appels par releve). "
            f"Retires-en une avec --oublier.")

    perso[code] = collecteur.DESTINATIONS.get(code, code)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(perso, f, ensure_ascii=False, indent=2, sort_keys=True)


def retirer_destination(code, chemin=None):
    """Retire une destination de la surveillance. Renvoie True si elle y
    etait, False sinon."""
    chemin = chemin or collecteur.CHEMIN_DESTINATIONS_PERSO
    perso = collecteur.charger_destinations_perso(chemin)
    if code not in perso:
        return False
    del perso[code]
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(perso, f, ensure_ascii=False, indent=2, sort_keys=True)
    return True
```

- [x] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

Commande : `python -m unittest tests.test_recherche -v`
Attendu : 34 tests PASS

- [x] **Étape 5 : Ajouter le fichier au `.gitignore`**

Ajouter après la ligne `flight_deals_log.txt` :

```
# destinations mises sous surveillance par recherche.py : preference locale
destinations_perso.json
```

Vérifier : `git check-ignore destinations_perso.json` doit renvoyer le nom du fichier.

- [x] **Étape 6 : Vérifier que la suite complète reste verte**

Commande : `python -m unittest discover -s tests`
Attendu : OK

- [x] **Étape 7 : Commit**

```bash
git add hub_deals_db.py recherche.py tests/test_recherche.py .gitignore
git commit -m "feat(recherche): destinations personnelles sous surveillance"
```

---

### Tâche 5 : Greffe sur le collecteur

Le relevé quotidien doit balayer les destinations personnelles en plus des 32 imposées.

**Fichiers :**
- Modifier : `hub_deals_db.py` (bloc `if __name__ == "__main__"`)
- Modifier : `tests/test_hub_deals_db.py`

**Interfaces :**
- Consomme : `destinations_actives()` (tâche 4)
- Produit : aucune nouvelle interface publique

- [x] **Étape 1 : Écrire le test qui échoue**

Ajouter à `tests/test_hub_deals_db.py`, dans la classe `TestRabattement` ou une nouvelle classe :

```python
class TestDestinationsActives(unittest.TestCase):
    """La boucle de collecte doit balayer les destinations personnelles en
    plus des destinations imposees."""

    def test_destinations_actives_contient_les_originales_sans_fichier(self):
        actives = hub_deals_db.destinations_actives("fichier-qui-nexiste-pas.json")
        self.assertEqual(actives, hub_deals_db.DESTINATIONS)

    def test_la_boucle_principale_utilise_destinations_actives(self):
        """Garde-fou de non-regression : si quelqu'un remet DESTINATIONS en
        dur dans la boucle, les destinations personnelles cessent
        silencieusement d'etre collectees."""
        import inspect
        source = inspect.getsource(hub_deals_db)
        bloc_principal = source.split('if __name__ == "__main__":')[1]
        self.assertIn("destinations_actives", bloc_principal)
        self.assertNotIn("for dest_iata in DESTINATIONS", bloc_principal)

    def test_le_nom_d_une_destination_personnelle_arrive_en_base(self):
        """Sans cela, une destination personnelle serait enregistree avec son
        code IATA en guise de nom (BKK au lieu de Bangkok)."""
        conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(conn)
        offre = {"price": 100, "departure_at": "2026-09-01T10:00:00+00:00"}

        hub_deals_db.enregistrer_prix(
            conn, "CMN", "BKK", offre, "2026-08-15 12:00:00", dest_nom="Bangkok")

        noms = [r[0] for r in conn.execute(
            "SELECT DISTINCT destination_nom FROM offres")]
        conn.close()
        self.assertEqual(noms, ["Bangkok"])

    def test_le_nom_reste_celui_de_DESTINATIONS_si_non_precise(self):
        conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(conn)
        offre = {"price": 100, "departure_at": "2026-09-01T10:00:00+00:00"}

        hub_deals_db.enregistrer_prix(
            conn, "CMN", "DKR", offre, "2026-08-15 12:00:00")

        noms = [r[0] for r in conn.execute(
            "SELECT DISTINCT destination_nom FROM offres")]
        conn.close()
        self.assertEqual(noms, ["Dakar"])
```

- [x] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

Commande : `python -m unittest tests.test_hub_deals_db -v`
Attendu : le second test échoue — `'destinations_actives' not found in bloc_principal`

- [x] **Étape 3 : Écrire l'implémentation minimale**

Dans `hub_deals_db.py`, bloc `if __name__ == "__main__"`, remplacer :

```python
    for hub_iata, hub_info in HUBS.items():
        log(f"Interrogation des destinations depuis {hub_info['nom']} ({hub_iata})...")
        routes_hub = 0

        for dest_iata in DESTINATIONS:
```

par :

```python
    destinations = destinations_actives()
    nb_perso = len(destinations) - len(DESTINATIONS)
    if nb_perso:
        log(f"{nb_perso} destination(s) personnelle(s) active(s) "
            f"(+{nb_perso * len(HUBS)} appels)")

    for hub_iata, hub_info in HUBS.items():
        log(f"Interrogation des destinations depuis {hub_info['nom']} ({hub_iata})...")
        routes_hub = 0

        for dest_iata in destinations:
```

Il faut aussi que le **nom** d'une destination personnelle arrive jusqu'à la base. Ne PAS appeler
`charger_destinations_perso()` depuis `enregistrer_prix` : cette fonction est appelée une fois par
route trouvée (~200 fois par relevé), ce qui relirait le fichier autant de fois. Passer le nom en
paramètre optionnel — la signature reste compatible avec les tests existants.

Dans `enregistrer_prix`, remplacer la signature et la ligne du nom :

```python
def enregistrer_prix(conn: sqlite3.Connection, hub_iata: str, dest_iata: str,
                     offre: dict, date_collecte: str, dest_nom: str = None) -> int:
```

```python
    dest_nom = dest_nom or DESTINATIONS.get(dest_iata, dest_iata)
```

Et dans le bloc principal, passer le nom déjà chargé :

```python
            total_lignes_inserees += enregistrer_prix(
                conn, hub_iata, dest_iata, offre, date_collecte,
                dest_nom=destinations[dest_iata]
            )
```

- [x] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

Commande : `python -m unittest discover -s tests`
Attendu : OK — 37 tests d'origine + 34 des tâches 1 à 4 + 4 ici = **75 tests**

- [x] **Étape 5 : Commit**

```bash
git add hub_deals_db.py tests/test_hub_deals_db.py
git commit -m "feat(collecteur): balaye aussi les destinations personnelles"
```

---

### Tâche 6 : Interface en ligne de commande et documentation

Le point d'entrée, plus la documentation. Rien de nouveau côté logique.

**Fichiers :**
- Modifier : `recherche.py`
- Modifier : `README.md`
- Modifier : `CHANGELOG.md`

**Interfaces :**
- Consomme : toutes les fonctions des tâches 1 à 4
- Produit : `main(argv: list[str]) -> int` — code de sortie 0 si succès, 1 si erreur d'usage

- [x] **Étape 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_recherche.py` :

```python
import io
import contextlib


class TestInterfaceLigneDeCommande(unittest.TestCase):
    def _lancer(self, argv):
        """Lance main() en capturant la sortie standard."""
        tampon = io.StringIO()
        with contextlib.redirect_stdout(tampon):
            code = recherche.main(argv)
        return code, tampon.getvalue()

    def test_sans_argument_affiche_l_usage_et_echoue(self):
        code, sortie = self._lancer([])
        self.assertEqual(code, 1)
        self.assertIn("Usage", sortie)

    def test_ville_inconnue_affiche_un_message_clair(self):
        code, sortie = self._lancer(["Marseille", "BKK"])
        self.assertEqual(code, 1)
        self.assertIn("Marseille", sortie)
        self.assertIn("Dakar", sortie)   # liste des villes valides

    def test_liste_affiche_les_destinations_surveillees(self):
        dossier = tempfile.TemporaryDirectory()
        chemin = os.path.join(dossier.name, "perso.json")
        recherche.ajouter_destination("BKK", chemin=chemin)
        original = recherche.collecteur.CHEMIN_DESTINATIONS_PERSO
        recherche.collecteur.CHEMIN_DESTINATIONS_PERSO = chemin
        try:
            code, sortie = self._lancer(["--liste"])
        finally:
            recherche.collecteur.CHEMIN_DESTINATIONS_PERSO = original
            dossier.cleanup()

        self.assertEqual(code, 0)
        self.assertIn("BKK", sortie)
        self.assertIn("appels", sortie)   # le cout est rappele
```

- [x] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

Commande : `python -m unittest tests.test_recherche -v`
Attendu : `AttributeError: module 'recherche' has no attribute 'main'`

- [x] **Étape 3 : Écrire l'implémentation minimale**

Ajouter à la fin de `recherche.py` :

```python
import sys

USAGE = """Usage :
  python recherche.py <ville> <destination>   cherche un itineraire
  python recherche.py --surveiller <dest>     ajoute au releve quotidien
  python recherche.py --oublier <dest>        retire du releve quotidien
  python recherche.py --liste                 destinations surveillees

Villes disponibles : {villes}
La destination est un code IATA (BKK) ou le nom d'une destination connue."""


def main(argv):
    villes = ", ".join(sorted(collecteur.RABATTEMENT))

    if not argv:
        print(USAGE.format(villes=villes))
        return 1

    try:
        if argv[0] == "--liste":
            perso = collecteur.charger_destinations_perso()
            if not perso:
                print("Aucune destination personnelle sous surveillance.")
            else:
                cout = len(perso) * len(collecteur.HUBS)
                print(f"{len(perso)} destination(s) surveillee(s) "
                      f"(+{cout} appels par releve) :")
                for code, nom in sorted(perso.items()):
                    print(f"  {code}  {nom}")
            return 0

        if argv[0] == "--surveiller":
            code = resoudre_destination(argv[1])
            ajouter_destination(code)
            print(f"{code} ajoute au releve quotidien "
                  f"(+{len(collecteur.HUBS)} appels par releve).")
            return 0

        if argv[0] == "--oublier":
            code = resoudre_destination(argv[1])
            if retirer_destination(code):
                print(f"{code} retire du releve quotidien.")
            else:
                print(f"{code} n'etait pas sous surveillance.")
            return 0

        if len(argv) < 2:
            print(USAGE.format(villes=villes))
            return 1

        ville = valider_ville(argv[0])
        dest = resoudre_destination(argv[1])

    except ValueError as e:
        print(f"Erreur : {e}")
        return 1
    except IndexError:
        print(USAGE.format(villes=villes))
        return 1

    if not collecteur.TOKEN:
        print("Erreur : TRAVELPAYOUTS_TOKEN absent de l'environnement.")
        return 1

    print(f"Recherche en cours ({1 + 2 * len(collecteur.RABATTEMENT[ville])} "
          f"appels API, une dizaine de secondes)...")
    try:
        options, erreurs = chercher_itineraires(ville, dest)
    except ValueError as e:
        print(f"Erreur : {e}")
        return 1

    conn = sqlite3.connect(collecteur.DB_PATH)
    try:
        contexte = contexte_historique(conn, ville, dest)
    finally:
        conn.close()

    print(formater(ville, dest, options, erreurs, contexte))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [x] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

Commande : `python -m unittest tests.test_recherche -v`
Attendu : 37 tests PASS

- [x] **Étape 5 : Documenter dans `README.md`**

Ajouter une section après la description du relevé automatique :

```markdown
## Recherche à la demande

Le relevé quotidien propose ce qu'il juge intéressant. Pour poser sa propre question :

```bash
python recherche.py Dakar BZV          # par code IATA
python recherche.py Dakar Brazzaville  # par nom, pour les destinations connues
python recherche.py Kinshasa BKK
```

La recherche interroge le **vol direct** (que le relevé automatique n'interroge jamais) et chaque
hub disposant d'un rabattement pour cette ville, puis classe les itinéraires du moins cher au plus
cher. Les prix d'aller sont demandés à l'API ; quand elle ne répond pas, la valeur estimée de
`RABATTEMENT` sert de repli et est signalée entre parenthèses.

Pour suivre une destination dans le temps et recevoir les alertes Telegram dessus :

```bash
python recherche.py --surveiller BKK   # +9 appels par relevé
python recherche.py --liste
python recherche.py --oublier BKK
```

Les destinations surveillées sont stockées dans `destinations_perso.json` (local, non versionné),
15 au maximum. Une recherche n'écrit jamais dans la base.
```

- [x] **Étape 6 : Documenter dans `CHANGELOG.md`**

Ajouter en tête, sous une entrée `## 2026-08-15`, section `### Ajouté` :

```markdown
- **Recherche de billet à la demande** (`recherche.py`) : interroger soi-même une route depuis
  l'une des 5 villes vers n'importe quel code IATA, au lieu de subir les propositions du relevé.
  Inclut le **vol direct**, que le collecteur n'interroge jamais — sur `Dakar → Brazzaville`, le
  direct à 932 € bat les 1001 € via Paris annoncés par le relevé. Les segments ville → hub sont
  demandés à l'API plutôt que lus dans `RABATTEMENT`, dont les valeurs estimées se sont révélées
  optimistes de 17 à 31 % ; le repli sur la table est signalé entre parenthèses et une option
  entièrement mesurée passe devant une option estimée à total égal. Coût : 19 appels pour Dakar.
- **Mise sous surveillance** (`--surveiller`) : ajoute une destination au relevé quotidien via
  `destinations_perso.json` (local, non versionné, 15 maximum, +9 appels par destination). Elle
  bénéficie alors de la détection d'anomalie et des notifications Telegram existantes.
```

- [x] **Étape 7 : Vérifier que la suite complète reste verte**

Commande : `python -m unittest discover -s tests`
Attendu : OK

- [x] **Étape 8 : Commit**

```bash
git add recherche.py README.md CHANGELOG.md tests/test_recherche.py
git commit -m "feat(recherche): interface en ligne de commande et documentation"
```

---

## Vérification en conditions réelles (après la tâche 6)

À faire manuellement, avec le vrai token et la vraie base — conformément à l'usage du projet.

- [x] `python recherche.py Dakar BZV` — le vol direct (~932 €) apparaît et bat le « 1001 € via
      Paris » du relevé. Ouvrir le lien Aviasales proposé et vérifier qu'il pointe la bonne route.
- [x] `python recherche.py Dakar ZZZ` — message honnête « aucun itinéraire trouve », pas de trace
      d'erreur Python.
- [x] `python recherche.py Marseille BKK` — refus avec la liste des 5 villes.
- [x] `python recherche.py Dakar DKR` — refus (destination = ville de départ).
- [x] Relever `SELECT COUNT(*) FROM offres` **avant et après** plusieurs recherches : le nombre doit
      être identique (aucune écriture en base).
- [x] `python recherche.py --surveiller BKK`, puis `python hub_deals_db.py` (vrai relevé) : vérifier
      dans le log la ligne « 1 destination(s) personnelle(s) active(s) (+9 appels) », puis en base
      que `BKK` produit des lignes pour les 5 villes de départ.
- [x] `python recherche.py --oublier BKK` puis `--liste` : retour à l'état antérieur.
- [x] Confirmer que la tâche planifiée « Traqueur de vols » tourne toujours (prochain déclenchement
      quotidien à 13h00, `LastTaskResult = 0`).

## Après la vérification

- [ ] Fusionner dans `master` en `--no-ff` (usage du projet), pousser sur `origin`.
- [x] Mettre à jour `hub_deals_AUDIT.md` avec les mesures relevées.
