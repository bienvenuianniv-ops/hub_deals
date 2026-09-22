"""Tests du magasin d'abonnes (bascule du 2026-09-20).

Spec : docs/superpowers/specs/2026-09-20-abonnes-hebergement-design.md
"""
import os
import sys
import sqlite3
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import magasin

# conftest remplace magasin.ouvrir pour toute la suite ; cette classe-ci
# teste le contrat de la vraie fonction, capturee ici a l'import.
_VRAI_OUVRIR = magasin.ouvrir


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

    def test_quand_postgres_est_exige_la_base_doit_etre_la(self):
        """La garde se declenche sur HUB_DEALS_EXIGE_PG, pose par le seul
        job qui pretend tester Postgres -- et NON sur CI, qui vaut 1 sur
        tous les runners GitHub : le job Windows n'a pas de Postgres, et
        la garde le faisait echouer a tort (constate au premier run)."""
        if not os.environ.get("HUB_DEALS_EXIGE_PG"):
            self.skipTest("Postgres non exige ici")
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
        # ce setUp EFFACE la table : jamais ailleurs que sur un
        # conteneur jetable (voir TestBaseJetable)
        magasin.exiger_base_jetable(self.url)
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

    def test_un_role_sans_droit_de_creation_peut_ouvrir(self):
        """En production, le role du bot n'a que SELECT/INSERT/UPDATE : il
        ne peut PAS creer de table. Or ouvrir() lance un CREATE TABLE IF
        NOT EXISTS a chaque connexion -- constate le 2026-09-20 sur la
        vraie base Neon, ou ce role se voit refuser CREATE.

        Les tables existent deja : ouvrir() ne doit donc rien tenter."""
        mdp = "essai_role_restreint_2026"
        proprietaire = magasin.ouvrir(url=self.url)
        proprietaire.execute("DROP ROLE IF EXISTS essai_restreint")
        proprietaire.execute(f"CREATE ROLE essai_restreint LOGIN PASSWORD '{mdp}'")
        proprietaire.execute(
            "GRANT SELECT, INSERT, UPDATE ON abonnes, etat_bot TO essai_restreint")
        proprietaire.commit()
        restreinte = self.url.split("://", 1)[1].split("@", 1)[1]

        try:
            conn = magasin.ouvrir(url=f"postgresql://essai_restreint:{mdp}@{restreinte}")
            nb = conn.execute("SELECT COUNT(*) FROM abonnes").fetchone()[0]
            conn.close()
        finally:
            proprietaire.execute(
                "REVOKE ALL ON abonnes, etat_bot FROM essai_restreint")
            proprietaire.execute("DROP ROLE IF EXISTS essai_restreint")
            proprietaire.commit()
            proprietaire.close()

        self.assertEqual(nb, 0)

    def test_le_temoin_d_etat_s_ecrase(self):
        import abonnes
        abonnes.noter_ecoute(self.conn, "2026-09-20T10:00:00+00:00")
        abonnes.noter_ecoute(self.conn, "2026-09-20T11:00:00+00:00")

        valeur = self.conn.execute(
            "SELECT valeur FROM etat_bot WHERE cle = 'derniere_ecoute'").fetchone()[0]

        self.assertEqual(valeur, "2026-09-20T11:00:00+00:00")


if __name__ == "__main__":
    unittest.main()


class TestCheminExpliciteContreEnvironnement(unittest.TestCase):
    """Un chemin explicite est une intention explicite.

    Incident du 2026-09-22 : HUB_DEALS_ABONNES_URL est posee sur le
    portable pour le releve. Toute la suite locale ouvrait donc la base
    des abonnes de PRODUCTION au lieu du fichier temporaire demande, et y
    a insere 58 faux abonnes. Le garde-fou de conftest ne detournait que
    le chemin SQLite, jamais l'URL.
    """

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.chemin = os.path.join(self.dossier.name, "essai.db")
        self.ancienne = os.environ.get(magasin.URL_ENV)
        # une URL volontairement injoignable : si ouvrir() la consulte,
        # le test echoue par la connexion, pas par une assertion floue
        os.environ[magasin.URL_ENV] = "postgresql://personne@127.0.0.1:1/vide?connect_timeout=1"

    def tearDown(self):
        if self.ancienne is None:
            os.environ.pop(magasin.URL_ENV, None)
        else:
            os.environ[magasin.URL_ENV] = self.ancienne
        self.dossier.cleanup()

    def test_un_chemin_donne_ignore_l_url_de_l_environnement(self):
        conn = _VRAI_OUVRIR(chemin=self.chemin)
        try:
            self.assertIsInstance(conn, sqlite3.Connection)
            self.assertTrue(os.path.exists(self.chemin))
        finally:
            conn.close()

    def test_sans_chemin_ni_url_l_environnement_reste_le_defaut(self):
        """La production, elle, ne passe aucun argument : le repli par
        l'environnement doit survivre a la correction."""
        with self.assertRaises(Exception):
            _VRAI_OUVRIR()


class TestBaseJetable(unittest.TestCase):
    """TestOuverturePostgres.setUp fait « DELETE FROM abonnes ».

    Le jour ou HUB_DEALS_TEST_PG_URL pointerait la base de production,
    lancer la suite effacerait les abonnes -- un recrutement fait a la
    main, la seule chose du projet qui ne se reconstruit pas toute
    seule. Meme famille que l'incident du 2026-09-22, ou un chemin
    explicite etait supplante par l'environnement.

    La base de test est TOUJOURS un conteneur jetable sur localhost
    (voir .github/workflows/tests.yml). La production est chez Neon.
    """

    def test_accepte_une_base_locale(self):
        for url in ("postgresql://postgres:essai@localhost:5432/hub_deals_test",
                    "postgresql://postgres:essai@127.0.0.1:5432/hub_deals_test"):
            magasin.exiger_base_jetable(url)  # ne doit pas lever

    def test_refuse_une_base_distante(self):
        neon = ("postgresql://bot:secret@ep-square-lake-b1mtso4n-pooler"
                ".c-5.eu-central-1.aws.neon.tech/hub_deals")

        with self.assertRaises(RuntimeError) as e:
            magasin.exiger_base_jetable(neon)

        self.assertIn("localhost", str(e.exception))
        # le message ne doit pas recracher l'URL : il finit dans un
        # journal, et l'URL porte le mot de passe
        self.assertNotIn("secret", str(e.exception))

    def test_refuse_une_url_sans_hote(self):
        """Un doute se tranche du cote sur : on refuse."""
        with self.assertRaises(RuntimeError):
            magasin.exiger_base_jetable("pas une url")
