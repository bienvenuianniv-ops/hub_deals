"""Rendu de la page publique (spec du 2026-09-22)."""
import html
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import hub_deals_db
import page


def _affaire(ville="Dakar", destination="Milan", hub="CMN", prix=118.0,
             baisse=19.0, economie=28.0, lien="/vol/CMNMIL", rabattement=468):
    return {"ville_depart": ville, "destination": destination, "hub": hub,
            "prix_actuel": prix, "baisse_pct": baisse, "economie": economie,
            "lien": lien, "rabattement": rabattement}


class TestOrdreDesVilles(unittest.TestCase):
    def test_couvre_exactement_les_villes_connues(self):
        """Une ville absente de l'ordre ne serait jamais affichee ; une
        ville en trop planterait sur NOMS_AFFICHES."""
        self.assertEqual(set(page.ORDRE_VILLES), set(abonnes.NOMS_AFFICHES))
        self.assertEqual(len(page.ORDRE_VILLES), len(abonnes.NOMS_AFFICHES))


class TestEtiquette(unittest.TestCase):
    def test_prefixee_et_normalisee(self):
        """Distinguer le trafic de la page de celui du bot, et survivre
        aux noms composes."""
        self.assertEqual(page.etiquette_page("Dakar"), "page_dakar")
        self.assertEqual(page.etiquette_page("Le Caire"), "page_le_caire")

    def test_passe_par_etiquette_ville(self):
        for ville in abonnes.NOMS_AFFICHES:
            self.assertEqual(page.etiquette_page(ville),
                             "page_" + hub_deals_db.etiquette_ville(ville))


class TestBlocVille(unittest.TestCase):
    def test_affiche_prix_baisse_economie_et_destination(self):
        bloc = page.bloc_ville("Dakar", [[_affaire()]])

        self.assertIn("Milan", bloc)
        self.assertIn("118", bloc)
        self.assertIn("19", bloc)
        self.assertIn("28", bloc)

    def test_un_rabattement_nul_est_un_vol_direct(self):
        bloc = page.bloc_ville("Dakar", [[_affaire(rabattement=0)]])

        self.assertIn("direct", bloc.lower())
        self.assertNotIn("via CMN", bloc)

    def test_un_rabattement_non_nul_nomme_le_hub(self):
        bloc = page.bloc_ville("Dakar", [[_affaire(hub="CMN", rabattement=468)]])

        self.assertIn("CMN", bloc)

    def test_le_nom_de_destination_est_echappe(self):
        """destination_nom vient de l'API Travelpayouts : c'est du texte
        etranger, il n'entre jamais brut dans la page."""
        bloc = page.bloc_ville("Dakar", [[_affaire(destination='<script>x</script>')]])

        self.assertNotIn("<script>", bloc)
        self.assertIn(html.escape("<script>x</script>"), bloc)
