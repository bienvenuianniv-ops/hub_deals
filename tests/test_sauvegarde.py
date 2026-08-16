import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sauvegarde


def _base_temoin(chemin):
    """Cree une petite base au meme schema que la vraie."""
    conn = sqlite3.connect(chemin)
    conn.execute("""
        CREATE TABLE offres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_collecte TEXT NOT NULL, ville_depart TEXT,
            hub_origine TEXT NOT NULL, destination_code TEXT,
            destination_nom TEXT, prix_vol_hub REAL, rabattement REAL,
            total_estime REAL, date_depart TEXT, lien TEXT)
    """)
    for i in range(5):
        conn.execute("""INSERT INTO offres (date_collecte, ville_depart,
            hub_origine, destination_code, destination_nom, prix_vol_hub,
            rabattement, total_estime, date_depart, lien)
            VALUES (?, 'Dakar', 'Paris', 'LON', 'Londres', ?, 300, ?, '', '/x')""",
            (f"2026-08-{10+i} 10:00:00", 100 + i, 400 + i))
    conn.commit()
    return conn


class TestGenerationDuDump(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dossier.name, "test.db")
        self.conn = _base_temoin(self.db)

    def tearDown(self):
        self.conn.close()
        self.dossier.cleanup()

    def test_le_dump_contient_le_schema_et_les_donnees(self):
        dump = sauvegarde.generer_dump(self.conn)

        self.assertIn("CREATE TABLE", dump)
        self.assertIn("INSERT INTO", dump)
        self.assertIn("Londres", dump)

    def test_le_dump_est_restaurable_a_l_identique(self):
        """Le test qui compte vraiment : une sauvegarde qu'on n'a jamais
        restauree n'est pas une sauvegarde, c'est une supposition."""
        dump = sauvegarde.generer_dump(self.conn)
        avant = self.conn.execute(
            "SELECT COUNT(*), SUM(total_estime) FROM offres").fetchone()

        restauree = sqlite3.connect(":memory:")
        restauree.executescript(dump)
        apres = restauree.execute(
            "SELECT COUNT(*), SUM(total_estime) FROM offres").fetchone()
        restauree.close()

        self.assertEqual(avant, apres)


class TestRestauration(unittest.TestCase):
    """sqlite3 n'existe PAS en ligne de commande sur la machine de
    l'utilisateur : la restauration doit etre outillee, pas laissee a
    une commande de README qu'on decouvre inexecutable le jour ou l'on
    en a besoin."""

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dossier.name, "test.db")
        self.conn = _base_temoin(self.db)
        self.dump = os.path.join(self.dossier.name, "dump.sql")
        with open(self.dump, "w", encoding="utf-8") as f:
            f.write(sauvegarde.generer_dump(self.conn))

    def tearDown(self):
        self.conn.close()
        self.dossier.cleanup()

    def test_restaure_une_base_identique(self):
        cible = os.path.join(self.dossier.name, "restauree.db")

        sauvegarde.restaurer(self.dump, cible)

        conn = sqlite3.connect(cible)
        apres = conn.execute(
            "SELECT COUNT(*), SUM(total_estime) FROM offres").fetchone()
        conn.close()
        avant = self.conn.execute(
            "SELECT COUNT(*), SUM(total_estime) FROM offres").fetchone()
        self.assertEqual(avant, apres)

    def test_refuse_d_ecraser_une_base_existante(self):
        """Restaurer par-dessus la base en service detruirait les donnees
        qu'on cherche justement a proteger."""
        cible = os.path.join(self.dossier.name, "deja_la.db")
        with open(cible, "w") as f:
            f.write("x")

        with self.assertRaises(FileExistsError):
            sauvegarde.restaurer(self.dump, cible)


class TestSauvegardeLocale(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dossier.name, "test.db")
        _base_temoin(self.db).close()

    def tearDown(self):
        self.dossier.cleanup()

    def _copies(self):
        return [f for f in os.listdir(self.dossier.name)
                if f.startswith("test.db.sauvegarde-")]

    def test_cree_une_copie_horodatee(self):
        chemin = sauvegarde.sauvegarder_local(self.db)

        self.assertTrue(os.path.exists(chemin))
        self.assertIn("sauvegarde-", os.path.basename(chemin))
        self.assertEqual(len(self._copies()), 1)

    def test_la_copie_est_une_base_lisible(self):
        chemin = sauvegarde.sauvegarder_local(self.db)

        conn = sqlite3.connect(chemin)
        n = conn.execute("SELECT COUNT(*) FROM offres").fetchone()[0]
        conn.close()
        self.assertEqual(n, 5)

    def test_ne_garde_que_les_n_plus_recentes(self):
        for i in range(7):
            sauvegarde.sauvegarder_local(self.db, garder=3,
                                         horodatage=f"20260816-1200{i:02d}")

        copies = sorted(self._copies())
        self.assertEqual(len(copies), 3)
        # les trois dernieres, pas les trois premieres
        self.assertIn("120006", copies[-1])
        self.assertIn("120004", copies[0])

    def test_la_purge_ne_touche_pas_les_autres_fichiers(self):
        temoin = os.path.join(self.dossier.name, "ne-pas-supprimer.txt")
        with open(temoin, "w") as f:
            f.write("garde-moi")

        for i in range(5):
            sauvegarde.sauvegarder_local(self.db, garder=2,
                                         horodatage=f"20260816-1300{i:02d}")

        self.assertTrue(os.path.exists(temoin))
        self.assertTrue(os.path.exists(self.db))


class TestSauvegardeDistante(unittest.TestCase):
    """Les commandes git sont injectees : aucun appel reseau ni ecriture
    dans le depot pendant les tests."""

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dossier.name, "test.db")
        self.conn = _base_temoin(self.db)

    def tearDown(self):
        self.conn.close()
        self.dossier.cleanup()

    @staticmethod
    def _code_diff(args):
        """`git diff --quiet` rend 1 quand il Y A des differences et 0
        quand il n'y en a pas -- convention inverse de l'intuition."""
        return 1 if ("diff" in args and "--quiet" in args) else 0

    def test_ecrit_le_dump_et_pousse(self):
        commandes = []

        def executer(args, cwd=None):
            commandes.append(args)
            return self._code_diff(args), ""

        ok = sauvegarde.sauvegarder_distant(
            self.conn, dossier=self.dossier.name, executer=executer)

        self.assertTrue(ok)
        aplati = " | ".join(" ".join(c) for c in commandes)
        self.assertIn("add", aplati)
        self.assertIn("commit", aplati)
        self.assertIn("push", aplati)
        chemin = os.path.join(self.dossier.name, sauvegarde.NOM_DUMP)
        self.assertTrue(os.path.exists(chemin))

    def test_une_erreur_git_n_interrompt_rien(self):
        """Une panne de sauvegarde ne doit jamais faire echouer un releve :
        meme regle que pour Telegram."""
        def executer(args, cwd=None):
            if "push" in args:
                return 1, "fatal: unable to access"
            return self._code_diff(args), ""

        ok = sauvegarde.sauvegarder_distant(
            self.conn, dossier=self.dossier.name, executer=executer)

        self.assertFalse(ok)   # signale l'echec sans lever

    def test_une_exception_inattendue_est_absorbee(self):
        def executer(args, cwd=None):
            raise OSError("git introuvable")

        ok = sauvegarde.sauvegarder_distant(
            self.conn, dossier=self.dossier.name, executer=executer)

        self.assertFalse(ok)

    def test_ne_commite_pas_quand_rien_n_a_change(self):
        """Deux sauvegardes identiques ne doivent pas produire deux commits
        vides : git commit echoue alors, ce n'est pas une erreur."""
        etats = []

        def executer(args, cwd=None):
            if "diff" in args and "--quiet" in args:
                etats.append("diff")
                return 0, ""      # 0 = aucun changement
            etats.append(args[1] if len(args) > 1 else args[0])
            return 0, ""

        ok = sauvegarde.sauvegarder_distant(
            self.conn, dossier=self.dossier.name, executer=executer)

        self.assertTrue(ok)
        self.assertNotIn("commit", etats)
        self.assertNotIn("push", etats)


if __name__ == "__main__":
    unittest.main()
