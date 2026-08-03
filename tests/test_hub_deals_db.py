import sqlite3
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db


class TestInitDbMigration(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def _colonnes(self):
        return [row[1] for row in self.conn.execute("PRAGMA table_info(offres)")]

    def test_cree_la_table_avec_colonne_ville_depart(self):
        hub_deals_db.init_db(self.conn)
        self.assertIn("ville_depart", self._colonnes())

    def test_idempotent_sur_une_base_deja_a_jour(self):
        hub_deals_db.init_db(self.conn)
        hub_deals_db.init_db(self.conn)  # ne doit pas lever d'erreur
        self.assertIn("ville_depart", self._colonnes())

    def test_migre_une_table_existante_sans_ville_depart(self):
        # simule l'ancien schema (avant la colonne ville_depart)
        self.conn.execute("""
            CREATE TABLE offres (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date_collecte TEXT NOT NULL,
                hub_origine TEXT NOT NULL,
                destination_code TEXT,
                destination_nom TEXT,
                prix_vol_hub REAL,
                rabattement REAL,
                total_estime REAL,
                date_depart TEXT,
                lien TEXT
            )
        """)
        self.conn.execute("""
            INSERT INTO offres (date_collecte, hub_origine, destination_code, total_estime)
            VALUES ('2026-07-21 10:00:00', 'Casablanca', 'SID', 612)
        """)
        self.conn.commit()

        hub_deals_db.init_db(self.conn)

        self.assertIn("ville_depart", self._colonnes())
        ville = self.conn.execute("SELECT ville_depart FROM offres WHERE destination_code = 'SID'").fetchone()[0]
        self.assertEqual(ville, "Dakar")


if __name__ == "__main__":
    unittest.main()
