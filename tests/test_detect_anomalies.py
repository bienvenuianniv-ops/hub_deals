import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import detect_anomalies


class TestMainAppliqueLaMigration(unittest.TestCase):
    """Verifie que detect_anomalies.main() applique la migration de schema
    (ajout de la colonne ville_depart) avant toute requete, exactement
    comme hub_deals_db.py le fait deja dans son propre __main__.

    Avant le fix, main() ouvrait la connexion et interrogeait directement
    la table `offres` sans jamais appeler init_db() : sur une base encore
    sur l'ancien schema (sans la colonne ville_depart ajoutee par cette
    branche), cela levait sqlite3.OperationalError."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        # simule l'ancien schema (avant la colonne ville_depart), avec une
        # ligne existante -- meme scenario que
        # TestInitDbMigration.test_migre_une_table_existante_sans_ville_depart
        # dans tests/test_hub_deals_db.py
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
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
        conn.execute("""
            INSERT INTO offres (date_collecte, hub_origine, destination_code, total_estime)
            VALUES ('2026-07-21 10:00:00', 'Casablanca', 'SID', 612)
        """)
        conn.commit()
        conn.close()

        self._db_path_original = detect_anomalies.DB_PATH
        detect_anomalies.DB_PATH = self.db_path

    def tearDown(self):
        detect_anomalies.DB_PATH = self._db_path_original
        os.remove(self.db_path)

    def test_ne_plante_pas_sur_une_base_non_migree(self):
        # Avant le fix : sqlite3.OperationalError: no such column: ville_depart
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                detect_anomalies.main()
            except sqlite3.OperationalError as e:
                self.fail(f"main() a plante sur une base non migree : {e}")

    def test_la_colonne_ville_depart_existe_apres_lexecution(self):
        with contextlib.redirect_stdout(io.StringIO()):
            detect_anomalies.main()

        conn = sqlite3.connect(self.db_path)
        colonnes = [row[1] for row in conn.execute("PRAGMA table_info(offres)")]
        ville = conn.execute(
            "SELECT ville_depart FROM offres WHERE destination_code = 'SID'"
        ).fetchone()[0]
        conn.close()

        self.assertIn("ville_depart", colonnes)
        self.assertEqual(ville, "Dakar")


if __name__ == "__main__":
    unittest.main()
