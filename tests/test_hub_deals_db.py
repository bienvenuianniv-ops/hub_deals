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


class TestEnregistrerOffresMultiVilles(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)

        # sauvegarde la config reelle, remplacee par une config de test
        self._hubs_original = hub_deals_db.HUBS
        self._rabattement_original = hub_deals_db.RABATTEMENT
        hub_deals_db.HUBS = {"CMN": {"nom": "Casablanca"}}
        hub_deals_db.RABATTEMENT = {
            "Dakar": {"CMN": {"prix": 400, "duree_h": 4}},
            "Abidjan": {"CMN": {"prix": 150, "duree_h": 2}},
        }

    def tearDown(self):
        hub_deals_db.HUBS = self._hubs_original
        hub_deals_db.RABATTEMENT = self._rabattement_original
        self.conn.close()

    def _offre_test(self, prix=100):
        return {
            "destination": "SID",
            "destination_name": "Sal",
            "price": prix,
            "departure_at": "2026-09-01T10:00:00+00:00",
            "link": "/search/CMN0109SID1",
        }

    def test_insere_une_ligne_par_ville_ayant_un_cout_pour_ce_hub(self):
        hub_deals_db.enregistrer_offres(self.conn, "CMN", [self._offre_test()], "2026-08-03 12:00:00")

        lignes = self.conn.execute(
            "SELECT ville_depart, total_estime FROM offres ORDER BY ville_depart"
        ).fetchall()

        self.assertEqual(len(lignes), 2)
        self.assertEqual(lignes, [("Abidjan", 250.0), ("Dakar", 500.0)])

    def test_ignore_une_ville_sans_cout_defini_pour_ce_hub(self):
        hub_deals_db.RABATTEMENT["Nairobi"] = {}  # aucune entree pour CMN

        hub_deals_db.enregistrer_offres(self.conn, "CMN", [self._offre_test()], "2026-08-03 12:00:00")

        villes = [row[0] for row in self.conn.execute("SELECT ville_depart FROM offres")]
        self.assertNotIn("Nairobi", villes)
        self.assertEqual(len(villes), 2)


if __name__ == "__main__":
    unittest.main()
