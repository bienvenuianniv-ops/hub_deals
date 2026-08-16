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
