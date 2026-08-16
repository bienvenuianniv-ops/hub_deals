import os
import sys
import unittest

import requests

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


if __name__ == "__main__":
    unittest.main()
