import sqlite3
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db
import anomaly_detection


def _inserer_offre(conn, ville, hub, dest_code, dest_nom, total_estime, date_collecte):
    conn.execute("""
        INSERT INTO offres (
            date_collecte, ville_depart, hub_origine, destination_code,
            destination_nom, prix_vol_hub, rabattement, total_estime, date_depart, lien
        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, '2026-09-01T10:00:00+00:00', '/search/x')
    """, (date_collecte, ville, hub, dest_code, dest_nom, total_estime, total_estime))
    conn.commit()


class TestCalculerMoyennesHistoriques(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_ne_mixe_pas_les_moyennes_de_deux_villes(self):
        # Meme hub/destination, mais couts de rabattement (donc total_estime)
        # tres differents selon la ville de depart.
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Abidjan", "Casablanca", "SID", "Sal", 250, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Abidjan", "Casablanca", "SID", "Sal", 250, "2026-08-02 10:00:00")

        moyennes = anomaly_detection.calculer_moyennes_historiques(self.conn)

        self.assertEqual(moyennes[("Dakar", "Casablanca", "SID")]["moyenne"], 600)
        self.assertEqual(moyennes[("Abidjan", "Casablanca", "SID")]["moyenne"], 250)


class TestDetecterAnomalies(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_resultat_contient_la_ville_depart(self):
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 500, "2026-08-03 10:00:00")  # -16.7%

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-03 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["ville_depart"], "Dakar")

    def test_baisse_dans_une_ville_ne_declenche_pas_d_anomalie_dans_une_autre(self):
        # Abidjan baisse fortement, mais Dakar (meme hub/destination) reste stable :
        # seule l'anomalie Abidjan doit remonter.
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-03 10:00:00")
        _inserer_offre(self.conn, "Abidjan", "Casablanca", "SID", "Sal", 250, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Abidjan", "Casablanca", "SID", "Sal", 250, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Abidjan", "Casablanca", "SID", "Sal", 150, "2026-08-03 10:00:00")  # -40%

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-03 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["ville_depart"], "Abidjan")


if __name__ == "__main__":
    unittest.main()
