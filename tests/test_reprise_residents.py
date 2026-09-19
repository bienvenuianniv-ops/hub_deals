import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db
hub_deals_db.TRAVELPAYOUTS_PROJET = None
import reprise_residents


class TestVillesResidentes(unittest.TestCase):
    def test_reconnait_une_ville_a_son_rabattement_nul(self):
        villes = reprise_residents.villes_residentes()
        self.assertEqual(villes["Paris"], "CDG")
        self.assertEqual(villes["Abidjan"], "ABJ")
        self.assertEqual(villes["Dakar"], "DKR")

    def test_les_treize_villes_sont_residentes(self):
        self.assertEqual(len(reprise_residents.villes_residentes()), 13)

    def test_leve_si_un_rabattement_nul_vise_un_hub_etranger(self):
        """Controle negatif : un rabattement a 0 vers le hub d'une AUTRE
        ville ne doit jamais etre pris pour un resident -- la migration
        ecrirait sinon total_estime = prix_vol_hub pour un trajet qui
        coute en realite un rabattement."""
        original = hub_deals_db.RABATTEMENT
        hub_deals_db.RABATTEMENT = {
            "Dakar": {"DKR": {"prix": 0, "duree_h": 0}},
            "Abidjan": {"DKR": {"prix": 0, "duree_h": 0}},  # hub d'une autre ville
        }
        try:
            with self.assertRaises(ValueError):
                reprise_residents.villes_residentes()
        finally:
            hub_deals_db.RABATTEMENT = original


class TestReprise(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def _offre(self, date, ville, hub, dest, prix_vol, rabattement):
        self.conn.execute("""
            INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                destination_code, destination_nom, prix_vol_hub, rabattement,
                total_estime, date_depart, lien)
            VALUES (?, ?, ?, ?, 'Test', ?, ?, ?, '2026-10-01', '/search/x')
        """, (date, ville, hub, dest, prix_vol, rabattement, prix_vol + rabattement))
        self.conn.commit()

    def _index(self):
        """Noms des index presents sur la base."""
        return {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}

    def test_reporte_le_prix_du_vol_sans_rabattement(self):
        self._offre("2026-08-01 10:00:00", "Dakar", "Paris", "DXB", 300, 496)

        reprise_residents.reprendre(self.conn)

        ligne = self.conn.execute("""
            SELECT prix_vol_hub, rabattement, total_estime FROM offres
            WHERE ville_depart = 'Paris'
        """).fetchone()
        self.assertEqual(ligne, (300, 0, 300))

    def test_ne_duplique_pas_les_lignes_d_une_source_multi_villes(self):
        """Cinq villes partagent le meme prix hub -> destination : le report
        ne doit produire qu'une ligne pour le resident."""
        for ville, rab in (("Dakar", 496), ("Abidjan", 486), ("Kinshasa", 708)):
            self._offre("2026-08-01 10:00:00", ville, "Paris", "DXB", 300, rab)

        reprise_residents.reprendre(self.conn)

        n = self.conn.execute(
            "SELECT COUNT(*) FROM offres WHERE ville_depart = 'Paris'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_est_idempotente(self):
        self._offre("2026-08-01 10:00:00", "Dakar", "Paris", "DXB", 300, 496)

        premier = reprise_residents.reprendre(self.conn)
        second = reprise_residents.reprendre(self.conn)

        self.assertEqual(premier, 1)
        self.assertEqual(second, 0)
        n = self.conn.execute(
            "SELECT COUNT(*) FROM offres WHERE ville_depart = 'Paris'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_n_ecrit_pas_de_route_ramenant_la_ville_chez_elle(self):
        self._offre("2026-08-01 10:00:00", "Dakar", "Paris", "PAR", 120, 496)

        reprise_residents.reprendre(self.conn)

        n = self.conn.execute(
            "SELECT COUNT(*) FROM offres WHERE ville_depart = 'Paris'").fetchone()[0]
        self.assertEqual(n, 0)

    def test_n_ecrit_pas_de_route_vers_un_code_equivalent_a_la_ville(self):
        """CDG est un aeroport de Paris (EQUIVALENCES['PAR'] = {'CDG'}) :
        une destination CDG ramene Paris chez elle tout autant qu'une
        destination PAR, et ne doit donc pas etre reportee non plus."""
        self._offre("2026-08-01 10:00:00", "Dakar", "Paris", "CDG", 120, 496)

        reprise_residents.reprendre(self.conn)

        n = self.conn.execute(
            "SELECT COUNT(*) FROM offres WHERE ville_depart = 'Paris'").fetchone()[0]
        self.assertEqual(n, 0)

    def test_ne_touche_a_aucune_ligne_existante(self):
        self._offre("2026-08-01 10:00:00", "Dakar", "Paris", "DXB", 300, 496)
        avant = self.conn.execute("""
            SELECT ville_depart, prix_vol_hub, rabattement, total_estime
            FROM offres WHERE ville_depart = 'Dakar'
        """).fetchall()

        reprise_residents.reprendre(self.conn)

        apres = self.conn.execute("""
            SELECT ville_depart, prix_vol_hub, rabattement, total_estime
            FROM offres WHERE ville_depart = 'Dakar'
        """).fetchall()
        self.assertEqual(avant, apres)

    def test_ignore_un_hub_sans_historique(self):
        """Dakar, Kinshasa, Brazzaville et Lome viennent d'etre promus
        hubs : aucune ligne a reporter pour eux, et ce n'est pas une
        erreur."""
        self.assertEqual(reprise_residents.reprendre(self.conn), 0)

    def test_ne_laisse_pas_d_index_derriere_elle(self):
        """Une migration ponctuelle ne doit pas modifier durablement le
        schema : l'index n'existe que le temps de la reprise."""
        self._offre("2026-08-01 10:00:00", "Dakar", "Paris", "DXB", 300, 496)

        avant = self._index()
        reprise_residents.reprendre(self.conn)
        self.assertEqual(self._index(), avant)

    def test_l_index_existe_pendant_la_reprise(self):
        """Controle positif : sans lui, le test precedent passerait aussi
        si l'index n'etait jamais cree du tout.

        sqlite3.Connection n'accepte pas qu'on remplace l'attribut
        'execute' sur une instance (AttributeError: read-only dans cet
        environnement) : on sous-classe donc la connexion pour intercepter
        les appels a execute(), ce qui reste une preuve positive de l'etat
        du schema au moment precis de chaque insertion."""
        vus = []

        class ConnexionEspion(sqlite3.Connection):
            def execute(self, sql, *args):
                # Seule la requete de reprise combine INSERT et SELECT
                # DISTINCT ; la ligne de seed inseree juste avant, elle,
                # passe aussi par execute() mais ne doit pas etre comptee.
                if "INSERT INTO offres" in sql and "SELECT DISTINCT" in sql:
                    vus.append({r[0] for r in sqlite3.Connection.execute(
                        self, "SELECT name FROM sqlite_master WHERE type = 'index'"
                    ).fetchall()})
                return sqlite3.Connection.execute(self, sql, *args)

        conn = sqlite3.connect(":memory:", factory=ConnexionEspion)
        try:
            hub_deals_db.init_db(conn)
            conn.execute("""
                INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                    destination_code, destination_nom, prix_vol_hub, rabattement,
                    total_estime, date_depart, lien)
                VALUES (?, ?, ?, ?, 'Test', ?, ?, ?, '2026-10-01', '/search/x')
            """, ("2026-08-01 10:00:00", "Dakar", "Paris", "DXB", 300, 496, 796))
            conn.commit()

            reprise_residents.reprendre(conn)
        finally:
            conn.close()

        self.assertTrue(vus, "aucune insertion observee")
        self.assertIn("idx_reprise_residents", vus[0])


if __name__ == "__main__":
    unittest.main()
