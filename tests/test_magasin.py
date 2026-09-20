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


if __name__ == "__main__":
    unittest.main()
