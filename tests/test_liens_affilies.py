"""Liens affiliés Travelpayouts et pied de message."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db


class TestUrlAviasales(unittest.TestCase):
    """Sans identifiant d'affilie, aucun clic ne rapporte ni ne se mesure
    (constat du 2026-09-15 : aucun lien envoye n'en portait)."""

    def setUp(self):
        self._marker = hub_deals_db.TRAVELPAYOUTS_MARKER

    def tearDown(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = self._marker

    def test_le_lien_porte_le_marker_et_l_etiquette(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = "123456"
        url = hub_deals_db.url_aviasales("/search/ABJ0302SAO1", "dakar")
        self.assertEqual(
            url, "https://www.aviasales.com/search/ABJ0302SAO1?marker=123456.dakar")

    def test_sans_marker_le_lien_reste_celui_d_aujourd_hui(self):
        """L'absence de marker ne doit jamais bloquer une alerte."""
        hub_deals_db.TRAVELPAYOUTS_MARKER = None
        url = hub_deals_db.url_aviasales("/search/ABJ0302SAO1", "dakar")
        self.assertEqual(url, "https://www.aviasales.com/search/ABJ0302SAO1")

    def test_le_bloc_du_proprietaire_porte_l_etiquette_proprietaire(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = "123456"
        a = {"destination": "Rome", "destination_code": "ROM", "hub": "Abidjan",
             "ville_depart": "Dakar", "prix_actuel": 975.0,
             "moyenne_historique": 1419.0, "baisse_pct": 31.3, "economie": 444.0,
             "rabattement_mesure": None, "lien": "/search/ABJ0511ROM1"}
        for groupe in ([a], [a, dict(a, ville_depart="Lome")]):
            bloc = hub_deals_db.construire_bloc(groupe)
            self.assertIn("?marker=123456.proprietaire", bloc)


class TestMarkerAbsentJournalise(unittest.TestCase):
    def setUp(self):
        self._marker = hub_deals_db.TRAVELPAYOUTS_MARKER
        self._log = hub_deals_db.log
        self._envoyer = hub_deals_db.envoyer_telegram
        self._mesurer = hub_deals_db.mesurer_rabattements
        self._detecter = hub_deals_db.detecter_anomalies
        self.lignes = []
        hub_deals_db.log = self.lignes.append
        hub_deals_db.envoyer_telegram = lambda msg: True
        hub_deals_db.mesurer_rabattements = lambda couples: {}
        hub_deals_db.detecter_anomalies = lambda conn, date_collecte=None: [{
            "destination": "Rome", "hub": "Abidjan", "ville_depart": "Dakar",
            "prix_actuel": 900.0, "moyenne_historique": 1000.0, "baisse_pct": 10.0,
            "economie": 100.0, "rabattement_mesure": None, "lien": "/search/X1"}]

    def tearDown(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = self._marker
        hub_deals_db.log = self._log
        hub_deals_db.envoyer_telegram = self._envoyer
        hub_deals_db.mesurer_rabattements = self._mesurer
        hub_deals_db.detecter_anomalies = self._detecter

    def test_un_marker_absent_est_signale_au_journal(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = None
        hub_deals_db.verifier_et_notifier_anomalies(None, "2026-09-15")
        self.assertIn("TRAVELPAYOUTS_MARKER", "\n".join(self.lignes))

    def test_un_marker_present_n_est_pas_signale(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = "123456"
        hub_deals_db.verifier_et_notifier_anomalies(None, "2026-09-15")
        self.assertNotIn("TRAVELPAYOUTS_MARKER", "\n".join(self.lignes))


if __name__ == "__main__":
    unittest.main()
