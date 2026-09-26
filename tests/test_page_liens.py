"""Les liens de la page ont leur propre etiquette (spec du 2026-09-22)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db
import page


class TestPairesDemandees(unittest.TestCase):
    def setUp(self):
        self.vraie = hub_deals_db.raccourcir_liens
        self.demandees = []
        hub_deals_db.raccourcir_liens = self._espion

    def tearDown(self):
        hub_deals_db.raccourcir_liens = self.vraie

    def _espion(self, paires):
        self.demandees = list(paires)
        return {}

    def test_la_page_a_ses_propres_etiquettes(self):
        """Sans etiquette distincte, le trafic de la page et celui du bot
        sont indiscernables dans Travelpayouts."""
        groupe = [{"ville_depart": "Dakar", "lien": "/vol/CMNMIL"}]

        hub_deals_db.preparer_liens_courts([groupe])

        self.assertIn(("/vol/CMNMIL", "dakar"), self.demandees)
        self.assertIn(("/vol/CMNMIL", page.etiquette_page("Dakar")),
                      self.demandees)
        self.assertIn(("/vol/CMNMIL", "proprietaire"), self.demandees)


class TestPageCasseeNeCoupePasLesAlertes(unittest.TestCase):
    """Relecture du 2026-09-26 : preparer_liens_courts promet de ne jamais
    lever. Une page.py qui ne se charge plus ne doit pas priver le
    proprietaire et les abonnes de leurs alertes du jour."""

    def setUp(self):
        self.vraie = hub_deals_db.raccourcir_liens
        self.demandees = []
        hub_deals_db.raccourcir_liens = lambda paires: (
            self.demandees.extend(paires) or {})
        self.page = sys.modules.get("page")
        sys.modules["page"] = None  # « import page » leve ImportError

    def tearDown(self):
        hub_deals_db.raccourcir_liens = self.vraie
        sys.modules["page"] = self.page

    def test_ne_leve_pas_et_garde_les_liens_du_bot(self):
        groupe = [{"ville_depart": "Dakar", "lien": "/vol/CMNMIL"}]

        hub_deals_db.preparer_liens_courts([groupe])

        self.assertIn(("/vol/CMNMIL", "proprietaire"), self.demandees)
        self.assertIn(("/vol/CMNMIL", "dakar"), self.demandees)
