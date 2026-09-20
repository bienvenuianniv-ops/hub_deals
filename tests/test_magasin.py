"""Tests du magasin d'abonnes (bascule du 2026-09-20).

Spec : docs/superpowers/specs/2026-09-20-abonnes-hebergement-design.md
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import magasin


class TestTraduction(unittest.TestCase):
    """psycopg3 exige %s et ne connait pas ?. abonnes.py ecrit en ?, il
    n'est pas modifie : c'est la couche qui traduit."""

    def test_remplace_les_placeholders(self):
        self.assertEqual(
            magasin.traduire("SELECT x FROM t WHERE a = ? AND b = ?"),
            "SELECT x FROM t WHERE a = %s AND b = %s")

    def test_ne_touche_pas_a_un_point_d_interrogation_entre_quotes(self):
        """Aucune requete actuelle n'en contient -- ce qui rend le piege
        d'autant plus facile a introduire plus tard sans s'en apercevoir."""
        self.assertEqual(
            magasin.traduire("UPDATE t SET texte = 'ou ca ?' WHERE a = ?"),
            "UPDATE t SET texte = 'ou ca ?' WHERE a = %s")

    def test_ne_touche_pas_a_un_pourcent_existant(self):
        """Un % litteral doit etre double pour psycopg, sinon il le prend
        pour le debut d'un placeholder."""
        self.assertEqual(
            magasin.traduire("SELECT x FROM t WHERE nom LIKE 'a%' AND b = ?"),
            "SELECT x FROM t WHERE nom LIKE 'a%%' AND b = %s")


class TestOuvertureSQLite(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.chemin = os.path.join(self.dossier.name, "essai.db")
        self.ouvertes = []

    def tearDown(self):
        # Windows refuse de supprimer un fichier SQLite encore ouvert :
        # sans ces fermetures, c'est le menage du dossier temporaire qui
        # echoue, et l'erreur ne parle pas du tout du test concerne.
        for conn in self.ouvertes:
            conn.close()
        self.dossier.cleanup()

    def _ouvrir(self):
        conn = magasin.ouvrir(chemin=self.chemin)
        self.ouvertes.append(conn)
        return conn

    def test_cree_les_tables(self):
        conn = self._ouvrir()

        tables = {l[0] for l in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}

        self.assertIn("abonnes", tables)
        self.assertIn("etat_bot", tables)

    def test_est_idempotent(self):
        self._ouvrir()
        conn = self._ouvrir()

        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM abonnes").fetchone()[0], 0)

    def test_accepte_les_requetes_d_abonnes(self):
        """Le contrat qui compte : abonnes.py doit fonctionner tel quel."""
        import abonnes
        conn = self._ouvrir()

        abonnes.inscrire(conn, 862000001, "Awa", abonnes.maintenant())

        self.assertEqual(abonnes.nb_actifs(conn), 1)


class TestGardeCI(unittest.TestCase):
    """Le piege que cette classe existe pour fermer.

    Les tests Postgres se sautent faute de base -- commode en local. Mais
    si le job CI perdait sa variable de connexion, ils se sauteraient
    AUSSI, et la suite passerait au vert sans avoir rien verifie du
    dialecte qui tourne en production. Meme famille que la vigie qui
    rendait [] sur un dump illisible.
    """

    def test_en_ci_la_base_postgres_est_obligatoire(self):
        if not os.environ.get("CI"):
            self.skipTest("hors CI : la base Postgres est facultative")
        self.assertTrue(
            os.environ.get("HUB_DEALS_TEST_PG_URL"),
            "HUB_DEALS_TEST_PG_URL manque : les tests Postgres se seraient "
            "sautes en silence")


@unittest.skipUnless(os.environ.get("HUB_DEALS_TEST_PG_URL"),
                     "HUB_DEALS_TEST_PG_URL absente")
class TestOuverturePostgres(unittest.TestCase):
    """Les MEMES operations que TestOuvertureSQLite, contre le moteur qui
    tourne reellement en production."""

    def setUp(self):
        self.url = os.environ["HUB_DEALS_TEST_PG_URL"]
        self.conn = magasin.ouvrir(url=self.url)
        self.conn.execute("DELETE FROM abonnes")
        self.conn.execute("DELETE FROM etat_bot")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_cree_les_tables(self):
        nb = self.conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name IN ('abonnes', 'etat_bot')").fetchone()[0]

        self.assertEqual(nb, 2)

    def test_un_chat_id_au_dela_de_deux_milliards_passe(self):
        """INTEGER aurait leve ici, et seulement ici : le compte test du
        proprietaire (8,6e8) ne revele pas le defaut."""
        import abonnes
        grand = 7_000_000_000

        abonnes.inscrire(self.conn, grand, "Grand", abonnes.maintenant())

        self.assertIsNotNone(abonnes.trouver(self.conn, grand))

    def test_le_cycle_complet_d_un_abonne(self):
        import abonnes
        quand = abonnes.maintenant()

        abonnes.inscrire(self.conn, 862000001, "Awa", quand)
        abonnes.choisir_ville(self.conn, 862000001, "Paris", quand)
        servis = abonnes.abonnes_a_servir(self.conn)
        abonnes.desactiver(self.conn, 862000001, "stop", quand)

        self.assertEqual([a["ville_depart"] for a in servis], ["Paris"])
        self.assertEqual(abonnes.nb_actifs(self.conn), 0)

    def test_la_reinscription_conserve_la_ville(self):
        """ON CONFLICT DO UPDATE : meme syntaxe dans les deux moteurs, mais
        c'est ici qu'on le prouve."""
        import abonnes
        quand = abonnes.maintenant()
        abonnes.inscrire(self.conn, 862000001, "Awa", quand)
        abonnes.choisir_ville(self.conn, 862000001, "Paris", quand)
        abonnes.desactiver(self.conn, 862000001, "stop", quand)

        abonnes.inscrire(self.conn, 862000001, "Awa", quand)

        abonne = abonnes.trouver(self.conn, 862000001)
        self.assertEqual(abonne["ville_depart"], "Paris")
        self.assertTrue(abonne["actif"])

    def test_le_temoin_d_etat_s_ecrase(self):
        import abonnes
        abonnes.noter_ecoute(self.conn, "2026-09-20T10:00:00+00:00")
        abonnes.noter_ecoute(self.conn, "2026-09-20T11:00:00+00:00")

        valeur = self.conn.execute(
            "SELECT valeur FROM etat_bot WHERE cle = 'derniere_ecoute'").fetchone()[0]

        self.assertEqual(valeur, "2026-09-20T11:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
