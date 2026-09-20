"""Tests de la reprise des abonnes vers la base distante (2026-09-20)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import magasin
import migrer_abonnes


class TestMigration(unittest.TestCase):
    """Cible en SQLite : la migration ne doit rien connaitre du moteur.
    Le dialecte, lui, est couvert par le job Postgres."""

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.source = magasin.ouvrir(
            chemin=os.path.join(self.dossier.name, "source.db"))
        self.cible = magasin.ouvrir(
            chemin=os.path.join(self.dossier.name, "cible.db"))
        quand = abonnes.maintenant()
        abonnes.inscrire(self.source, 862000001, "Awa", quand)
        abonnes.choisir_ville(self.source, 862000001, "Paris", quand)
        abonnes.noter_ecoute(self.source, quand)

    def tearDown(self):
        # Windows verrouille un fichier SQLite ouvert : sans ces
        # fermetures, c'est le menage du dossier qui echoue.
        self.source.close()
        self.cible.close()
        self.dossier.cleanup()

    def test_reprend_les_abonnes_avec_leur_ville(self):
        compte = migrer_abonnes.migrer(self.source, self.cible)

        abonne = abonnes.trouver(self.cible, 862000001)
        self.assertEqual(compte["abonnes"], 1)
        self.assertEqual(abonne["ville_depart"], "Paris")
        self.assertTrue(abonne["actif"])

    def test_rejouee_ne_reprend_rien(self):
        """Une migration qu'on n'a pas rejouee n'est pas idempotente,
        c'est une supposition."""
        migrer_abonnes.migrer(self.source, self.cible)

        compte = migrer_abonnes.migrer(self.source, self.cible)

        self.assertEqual(compte["abonnes"], 0)
        self.assertEqual(abonnes.nb_actifs(self.cible), 1)

    def test_ne_touche_pas_a_un_abonne_deja_present(self):
        """La cible fait foi : si quelqu'un s'est inscrit par le webhook
        pendant la bascule, la migration ne doit pas l'ecraser avec une
        vieille ligne."""
        quand = abonnes.maintenant()
        abonnes.inscrire(self.cible, 862000001, "Awa", quand)
        abonnes.choisir_ville(self.cible, 862000001, "Dakar", quand)

        migrer_abonnes.migrer(self.source, self.cible)

        self.assertEqual(
            abonnes.trouver(self.cible, 862000001)["ville_depart"], "Dakar")

    def test_reprend_le_temoin_d_etat(self):
        compte = migrer_abonnes.migrer(self.source, self.cible)

        self.assertEqual(compte["etat_bot"], 1)

    def test_reprend_toutes_les_colonnes_d_abonnes(self):
        """migrer_abonnes reecrit la liste des colonnes. Si abonnes.py en
        gagne une sans qu'elle soit ajoutee ici, la migration l'oublierait
        en silence."""
        self.assertEqual(migrer_abonnes._COLONNES, abonnes._COLONNES)


@unittest.skipUnless(os.environ.get("HUB_DEALS_TEST_PG_URL"),
                     "HUB_DEALS_TEST_PG_URL absente")
class TestMigrationVersPostgres(unittest.TestCase):
    """La cible reelle est Postgres. Le compte des reprises repose sur
    rowcount apres ON CONFLICT DO NOTHING : meme syntaxe dans les deux
    moteurs, mais un rowcount qui vaudrait -1 ou 1 la ou SQLite rend 0
    ferait mentir « Reprises : 0 » -- la seule preuve d'idempotence dont
    on dispose le jour de la bascule."""

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.source = magasin.ouvrir(
            chemin=os.path.join(self.dossier.name, "source.db"))
        self.cible = magasin.ouvrir(url=os.environ["HUB_DEALS_TEST_PG_URL"])
        self.cible.execute("DELETE FROM abonnes")
        self.cible.execute("DELETE FROM etat_bot")
        self.cible.commit()
        quand = abonnes.maintenant()
        abonnes.inscrire(self.source, 862000001, "Awa", quand)
        abonnes.choisir_ville(self.source, 862000001, "Paris", quand)
        abonnes.noter_ecoute(self.source, quand)

    def tearDown(self):
        self.source.close()
        self.cible.close()
        self.dossier.cleanup()

    def test_reprend_puis_ne_reprend_plus_rien(self):
        premiere = migrer_abonnes.migrer(self.source, self.cible)
        seconde = migrer_abonnes.migrer(self.source, self.cible)

        self.assertEqual(premiere["abonnes"], 1)
        self.assertEqual(seconde["abonnes"], 0)
        self.assertEqual(abonnes.nb_actifs(self.cible), 1)

    def test_la_ville_arrive_bien_dans_la_base_distante(self):
        migrer_abonnes.migrer(self.source, self.cible)

        self.assertEqual(
            abonnes.trouver(self.cible, 862000001)["ville_depart"], "Paris")


if __name__ == "__main__":
    unittest.main()
