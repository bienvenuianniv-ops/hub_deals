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


class TestCalculerStatsHistoriques(unittest.TestCase):
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

        stats = anomaly_detection.calculer_stats_historiques(self.conn)

        self.assertEqual(stats[("Dakar", "Casablanca", "SID")]["moyenne"], 600)
        self.assertEqual(stats[("Abidjan", "Casablanca", "SID")]["moyenne"], 250)

    def test_exclure_date_retire_le_releve_de_la_reference(self):
        """La reference doit pouvoir ignorer le releve qu'on s'apprete a
        juger, sinon ce releve deforme sa propre moyenne."""
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 300, "2026-08-03 10:00:00")

        cle = ("Dakar", "Casablanca", "SID")

        avec_tout = anomaly_detection.calculer_stats_historiques(self.conn)
        self.assertEqual(avec_tout[cle]["nb_releves"], 3)
        self.assertEqual(avec_tout[cle]["moyenne"], 500)  # (600+600+300)/3

        sans_le_jour = anomaly_detection.calculer_stats_historiques(
            self.conn, exclure_date="2026-08-03 10:00:00")
        self.assertEqual(sans_le_jour[cle]["nb_releves"], 2)
        self.assertEqual(sans_le_jour[cle]["moyenne"], 600)

    def test_ecart_type_absent_avec_un_seul_releve(self):
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")

        stats = anomaly_detection.calculer_stats_historiques(self.conn)

        self.assertIsNone(stats[("Dakar", "Casablanca", "SID")]["ecart_type"])


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

    def test_repli_en_pourcentage_quand_l_historique_est_court(self):
        """Avec 2 releves d'historique, l'ecart-type n'est pas fiable :
        la detection doit passer par le seuil en pourcentage."""
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 620, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 500, "2026-08-03 10:00:00")

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-03 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["methode"], "pourcentage")
        self.assertIsNone(anomalies[0]["z_score"])

    def test_z_score_utilise_quand_l_historique_est_assez_fourni(self):
        """A partir de MIN_RELEVES_ZSCORE releves d'historique et avec une
        vraie dispersion, c'est le z-score qui tranche."""
        for i, prix in enumerate([600, 610, 590, 605, 595], start=1):
            _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal",
                           prix, f"2026-08-0{i} 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 500, "2026-08-06 10:00:00")

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-06 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["methode"], "z-score")
        self.assertGreaterEqual(anomalies[0]["z_score"], anomaly_detection.SEUIL_ZSCORE)
        self.assertEqual(anomalies[0]["nb_releves_historique"], 5)

    def test_le_seuil_z_score_est_atteignable_sur_un_historique_court(self):
        """Regression : tant que le releve juge etait inclus dans sa propre
        reference, le z-score etait plafonne a (n-1)/racine(n) -- donc
        SEUIL_ZSCORE=1.5 etait inatteignable en dessous de 4 releves, quelle
        que soit l'ampleur de la baisse. Ici un effondrement de prix doit
        remonter, meme avec un historique court."""
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 610, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 60, "2026-08-03 10:00:00")  # -90%

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-03 10:00:00")

        self.assertEqual(len(anomalies), 1)

    def test_ignore_une_route_vue_pour_la_premiere_fois(self):
        """Sans historique anterieur, aucune comparaison n'est possible."""
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-01 10:00:00")

        self.assertEqual(anomalies, [])

    def test_une_hausse_n_est_pas_une_anomalie(self):
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 900, "2026-08-03 10:00:00")

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-03 10:00:00")

        self.assertEqual(anomalies, [])

    def test_mode_diagnostic_renvoie_aussi_les_non_anomalies(self):
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 599, "2026-08-03 10:00:00")

        self.assertEqual(
            anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-03 10:00:00"),
            [],
        )
        diagnostic = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-03 10:00:00", mode_diagnostic=True)
        self.assertEqual(len(diagnostic), 1)

    def test_tri_par_baisse_decroissante(self):
        for dest, prix_final in [("SID", 550), ("LIS", 300)]:
            _inserer_offre(self.conn, "Dakar", "Casablanca", dest, dest, 600, "2026-08-01 10:00:00")
            _inserer_offre(self.conn, "Dakar", "Casablanca", dest, dest, 600, "2026-08-02 10:00:00")
            _inserer_offre(self.conn, "Dakar", "Casablanca", dest, dest, prix_final, "2026-08-03 10:00:00")

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-03 10:00:00")

        self.assertEqual([a["destination_code"] for a in anomalies], ["LIS", "SID"])


if __name__ == "__main__":
    unittest.main()
