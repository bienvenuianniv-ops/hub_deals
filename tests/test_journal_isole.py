"""Aucun test n'ecrit dans les journaux d'exploitation (constate le 2026-09-26
et le 2026-10-27 : test_page_liens ajoutait « liens de la page ignores » a
flight_deals_log.txt, indiscernable d'un vrai incident de releve)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot_ecoute
import hub_deals_db

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestJournauxIsoles(unittest.TestCase):
    def test_le_journal_des_releves_n_est_pas_le_vrai(self):
        self.assertNotEqual(
            os.path.abspath(hub_deals_db.LOG_PATH),
            os.path.join(RACINE, "flight_deals_log.txt"))
        self.assertNotEqual(os.path.basename(hub_deals_db.LOG_PATH),
                            "flight_deals_log.txt")

    def test_le_journal_du_bot_n_est_pas_le_vrai(self):
        self.assertNotEqual(os.path.basename(bot_ecoute.LOG_PATH),
                            "bot_ecoute_log.txt")


if __name__ == "__main__":
    unittest.main()
