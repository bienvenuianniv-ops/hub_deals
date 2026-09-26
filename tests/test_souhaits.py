"""Liste d'attente des villes non couvertes (spec du 2026-09-22)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import magasin
import sauvegarde
import souhaits

T0 = "2026-09-22T10:00:00+00:00"


class TestSouhaits(unittest.TestCase):
    def setUp(self):
        self.conn = magasin.ouvrir(chemin=":memory:")

    def tearDown(self):
        self.conn.close()

    def test_note_un_souhait(self):
        souhaits.noter(self.conn, 111, "Nairobi", T0)

        self.assertEqual(souhaits.compter(self.conn), {"Nairobi": 1})

    def test_le_meme_souhait_deux_fois_ne_compte_qu_une_fois(self):
        """Quelqu'un qui reclique le lien ne doit pas gonfler la demande."""
        souhaits.noter(self.conn, 111, "Nairobi", T0)
        souhaits.noter(self.conn, 111, "Nairobi", T0)

        self.assertEqual(souhaits.compter(self.conn), {"Nairobi": 1})

    def test_deux_personnes_comptent_deux_fois(self):
        souhaits.noter(self.conn, 111, "Nairobi", T0)
        souhaits.noter(self.conn, 222, "Nairobi", T0)

        self.assertEqual(souhaits.compter(self.conn), {"Nairobi": 2})


class TestConfidentialite(unittest.TestCase):
    def test_la_table_ne_sort_jamais_sur_le_depot_public(self):
        """Elle porte des chat_id Telegram. C'est exactement la fuite du
        2026-09-20, ou les abonnes partaient sur un depot public."""
        self.assertIn("villes_souhaitees", sauvegarde.TABLES_PRIVEES)


if __name__ == "__main__":
    unittest.main()
