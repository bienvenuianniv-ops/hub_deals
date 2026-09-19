import sqlite3
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db

# jamais d'appel reel a l'API des liens courts depuis les tests, meme
# quand TRAVELPAYOUTS_PROJET est pose sur la machine
hub_deals_db.TRAVELPAYOUTS_PROJET = None
import anomaly_detection


def _inserer_offre(conn, ville, hub, dest_code, dest_nom, total_estime, date_collecte,
                   rabattement=500):
    """rabattement=500 par defaut : une ville classique. Les tests de
    residents passent explicitement rabattement=0, qui est le discriminant
    du plancher relatif."""
    conn.execute("""
        INSERT INTO offres (
            date_collecte, ville_depart, hub_origine, destination_code,
            destination_nom, prix_vol_hub, rabattement, total_estime, date_depart, lien
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-09-01T10:00:00+00:00', '/search/x')
    """, (date_collecte, ville, hub, dest_code, dest_nom, total_estime, rabattement,
          total_estime))
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

    def test_ignore_une_route_n_ayant_qu_un_seul_releve_anterieur(self):
        """Un point de reference unique ne fait pas un historique : le prix
        d'un billet bouge trop d'un jour a l'autre pour qu'une comparaison
        a une seule observation passee veuille dire quoi que ce soit. Meme
        une chute de 50% ne doit pas remonter."""
        _inserer_offre(self.conn, "Lome", "Le Caire", "DKR", "Dakar", 1200, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Lome", "Le Caire", "DKR", "Dakar", 600, "2026-08-02 10:00:00")

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-02 10:00:00")

        self.assertEqual(anomalies, [])

    def test_juge_la_route_des_deux_releves_anterieurs(self):
        """Au releve suivant, la route a deux points de reference : elle
        devient jugeable."""
        _inserer_offre(self.conn, "Lome", "Le Caire", "DKR", "Dakar", 1200, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Lome", "Le Caire", "DKR", "Dakar", 1200, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Lome", "Le Caire", "DKR", "Dakar", 600, "2026-08-03 10:00:00")

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-03 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["nb_releves_historique"], 2)

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
        # les deux baisses doivent rester au-dessus des seuils de detection
        # (>= 6% ET >= ECONOMIE_MINIMALE euros) : ce test porte sur l'ordre,
        # pas sur le declenchement
        for dest, prix_final in [("SID", 500), ("LIS", 300)]:
            _inserer_offre(self.conn, "Dakar", "Casablanca", dest, dest, 600, "2026-08-01 10:00:00")
            _inserer_offre(self.conn, "Dakar", "Casablanca", dest, dest, 600, "2026-08-02 10:00:00")
            _inserer_offre(self.conn, "Dakar", "Casablanca", dest, dest, prix_final, "2026-08-03 10:00:00")

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-03 10:00:00")

        self.assertEqual([a["destination_code"] for a in anomalies], ["LIS", "SID"])

    def test_l_anomalie_porte_le_rabattement_de_sa_route(self):
        """Sans cette donnee, aucune decision ne peut dependre du fait que
        la route est directe."""
        for jour, prix in (("2026-08-01 10:00:00", 1000),
                           ("2026-08-02 10:00:00", 1000),
                           ("2026-08-03 10:00:00", 1000)):
            _inserer_offre(self.conn, "Dakar", "Casablanca", "BKK", "Bangkok",
                           prix, jour, rabattement=468)
        _inserer_offre(self.conn, "Dakar", "Casablanca", "BKK", "Bangkok",
                       700, "2026-08-04 10:00:00", rabattement=468)

        anomalies = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-04 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["rabattement"], 468)

    def test_un_rabattement_absent_vaut_None_et_ne_plante_pas(self):
        """detect_anomalies.py insere des lignes sans rabattement : la
        colonne peut etre NULL."""
        self.conn.execute("""
            INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                destination_code, destination_nom, prix_vol_hub, total_estime,
                date_depart, lien)
            VALUES ('2026-08-01 10:00:00', 'Dakar', 'Casablanca', 'SIN',
                    'Singapour', 900, 900, '', '')
        """)
        self.conn.commit()
        for jour in ("2026-08-02 10:00:00", "2026-08-03 10:00:00"):
            self.conn.execute("""
                INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                    destination_code, destination_nom, prix_vol_hub, total_estime,
                    date_depart, lien)
                VALUES (?, 'Dakar', 'Casablanca', 'SIN', 'Singapour', 900, 900, '', '')
            """, (jour,))
        self.conn.execute("""
            INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                destination_code, destination_nom, prix_vol_hub, total_estime,
                date_depart, lien)
            VALUES ('2026-08-04 10:00:00', 'Dakar', 'Casablanca', 'SIN',
                    'Singapour', 600, 600, '', '')
        """)
        self.conn.commit()

        anomalies = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-04 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertIsNone(anomalies[0]["rabattement"])


class TestSeuilsStricts(unittest.TestCase):
    """Recalibrage du 2026-09-12.

    Le detecteur remontait 79 alertes par releve en mediane (jusqu'a 112),
    d'une economie mediane de 64 EUR : du bruit quotidien habille en bonne
    affaire. La cause : les routes suivies sont tres stables (coefficient
    de variation median 2,8%), donc 1,5 ecart-type ne pesait qu'environ
    4% de baisse -- le plancher a 3% ne filtrait plus rien.

    Trois criteres cumulatifs remplacent ce reglage : z >= 2, baisse >= 6%,
    et une economie d'au moins ECONOMIE_MINIMALE euros. Chaque test
    ci-dessous isole un seul critere, les deux autres etant satisfaits.
    """

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_une_baisse_sous_le_plancher_ne_declenche_pas(self):
        """Route tres stable : le z-score s'envole (21) et l'economie est
        large (150 EUR), mais la baisse de 5% reste sous le plancher."""
        for i, prix in enumerate([3000, 3000, 3010, 2990, 3000], start=1):
            _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal",
                           prix, f"2026-08-0{i} 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 2850,
                       "2026-08-06 10:00:00")  # -5.0%, 150 EUR

        anomalies = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-06 10:00:00")

        self.assertEqual(anomalies, [])

    def test_un_z_score_entre_1_5_et_2_ne_declenche_plus(self):
        """Route volatile : baisse de 9% et 90 EUR d'economie, mais le prix
        n'est qu'a 1,56 ecart-type sous la moyenne -- sous le nouveau seuil."""
        for i, prix in enumerate([950, 1050, 950, 1050], start=1):
            _inserer_offre(self.conn, "Lome", "Le Caire", "DKR", "Dakar",
                           prix, f"2026-08-0{i} 10:00:00")
        _inserer_offre(self.conn, "Lome", "Le Caire", "DKR", "Dakar", 910,
                       "2026-08-05 10:00:00")  # -9%, 90 EUR, z = 1.56

        anomalies = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-05 10:00:00")

        self.assertEqual(anomalies, [])

    def test_une_economie_trop_faible_ne_declenche_pas(self):
        """Route bon marche : -12% et z de 14, mais 60 EUR gagnes ne valent
        pas une notification."""
        for i, prix in enumerate([500, 500, 505, 495], start=1):
            _inserer_offre(self.conn, "Abidjan", "Istanbul", "CAI", "Le Caire",
                           prix, f"2026-08-0{i} 10:00:00")
        _inserer_offre(self.conn, "Abidjan", "Istanbul", "CAI", "Le Caire", 440,
                       "2026-08-05 10:00:00")  # -12%, 60 EUR

        anomalies = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-05 10:00:00")

        self.assertEqual(anomalies, [])

    def test_le_mode_pourcentage_applique_aussi_l_economie_minimale(self):
        """Le garde-fou en euros ne doit pas dependre de la methode : sur un
        historique court, une baisse de 12% a 60 EUR ne passe pas non plus."""
        _inserer_offre(self.conn, "Brazzaville", "Nairobi", "LIS", "Lisbonne",
                       500, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Brazzaville", "Nairobi", "LIS", "Lisbonne",
                       500, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Brazzaville", "Nairobi", "LIS", "Lisbonne",
                       440, "2026-08-03 10:00:00")  # -12%, 60 EUR

        anomalies = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-03 10:00:00")

        self.assertEqual(anomalies, [])

    def test_le_resultat_porte_l_economie_en_euros(self):
        """L'economie en euros est desormais un critere de declenchement :
        elle doit voyager avec l'anomalie, sinon le message ne peut pas
        justifier l'alerte autrement que par un pourcentage."""
        for i, prix in enumerate([1000, 1000, 1010, 990], start=1):
            _inserer_offre(self.conn, "Kinshasa", "Casablanca", "BRU", "Bruxelles",
                           prix, f"2026-08-0{i} 10:00:00")
        _inserer_offre(self.conn, "Kinshasa", "Casablanca", "BRU", "Bruxelles", 880,
                       "2026-08-05 10:00:00")

        [a] = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-05 10:00:00")

        self.assertEqual(a["economie"], 120.0)

    def test_une_vraie_bonne_affaire_passe_les_trois_criteres(self):
        """Garde-fou inverse : un reglage trop strict ne doit pas etouffer
        les alertes qui valent le deplacement."""
        for i, prix in enumerate([1000, 1000, 1010, 990], start=1):
            _inserer_offre(self.conn, "Kinshasa", "Casablanca", "BRU", "Bruxelles",
                           prix, f"2026-08-0{i} 10:00:00")
        _inserer_offre(self.conn, "Kinshasa", "Casablanca", "BRU", "Bruxelles", 880,
                       "2026-08-05 10:00:00")  # -12%, 120 EUR, z = 14.7

        anomalies = anomaly_detection.detecter_anomalies(
            self.conn, date_collecte="2026-08-05 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["baisse_pct"], 12.0)
        self.assertEqual(anomalies[0]["destination_code"], "BRU")


if __name__ == "__main__":
    unittest.main()
