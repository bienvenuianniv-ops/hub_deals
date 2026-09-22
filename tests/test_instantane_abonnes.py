"""Instantane local des abonnes (incident du 2026-09-22).

Les abonnes n'existent qu'au seul endroit : la base Neon. Le dump public
les vide volontairement (fuite corrigee le 2026-09-20), donc ils ne sont
sauvegardes nulle part. Un DELETE malheureux -- ou un script lance sans y
penser, ce qui vient d'arriver -- effacerait un recrutement fait a la
main, la seule chose du projet qui ne se reconstruit pas toute seule.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import magasin


class TestInstantane(unittest.TestCase):

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.copies = os.path.join(self.dossier.name, "copies")
        self.conn = magasin.ouvrir(chemin=":memory:")
        quand = "2026-09-22T10:00:00+00:00"
        abonnes.inscrire(self.conn, 8889245697, "Sy Abou", quand)
        abonnes.choisir_ville(self.conn, 8889245697, "Abidjan", quand)
        abonnes.inscrire(self.conn, 8296006641, "Mariama", quand)
        abonnes.choisir_ville(self.conn, 8296006641, "Paris", quand)

    def tearDown(self):
        self.conn.close()
        self.dossier.cleanup()

    def _lire(self, chemin):
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)

    def test_ecrit_tous_les_abonnes_avec_leurs_colonnes(self):
        rapport = abonnes.ecrire_instantane(
            self.conn, self.copies, "2026-09-22T13:00:00+00:00")

        self.assertEqual(rapport["lignes"], 2)
        contenu = self._lire(rapport["chemin"])
        self.assertEqual(contenu["lignes"], 2)
        noms = sorted(a["prenom"] for a in contenu["abonnes"])
        self.assertEqual(noms, ["Mariama", "Sy Abou"])
        # toutes les colonnes, pas seulement celles qui servent a l'envoi :
        # une restauration doit pouvoir recreer la ligne telle quelle
        self.assertEqual(sorted(contenu["abonnes"][0]), sorted(abonnes._COLONNES))

    def test_un_fichier_date_par_jour_les_anciens_restent(self):
        """Un fichier unique reecrit chaque jour serait ecrase par la
        catastrophe elle-meme : le lendemain d'un DELETE, la sauvegarde
        ne contiendrait plus que la table vide."""
        a = abonnes.ecrire_instantane(self.conn, self.copies,
                                      "2026-09-21T13:00:00+00:00")
        b = abonnes.ecrire_instantane(self.conn, self.copies,
                                      "2026-09-22T13:00:00+00:00")

        self.assertNotEqual(a["chemin"], b["chemin"])
        self.assertTrue(os.path.exists(a["chemin"]))
        self.assertTrue(os.path.exists(b["chemin"]))

    def test_deux_fois_le_meme_jour_ne_fait_qu_un_fichier(self):
        a = abonnes.ecrire_instantane(self.conn, self.copies,
                                      "2026-09-22T09:00:00+00:00")
        b = abonnes.ecrire_instantane(self.conn, self.copies,
                                      "2026-09-22T13:00:00+00:00")

        self.assertEqual(a["chemin"], b["chemin"])
        self.assertEqual(len(os.listdir(self.copies)), 1)

    def test_ne_garde_que_les_copies_recentes(self):
        for jour in range(1, 40):
            abonnes.ecrire_instantane(
                self.conn, self.copies, f"2026-08-{jour:02d}T13:00:00+00:00"
                if jour <= 31 else f"2026-09-{jour - 31:02d}T13:00:00+00:00")

        restants = os.listdir(self.copies)
        self.assertEqual(len(restants), abonnes.COPIES_GARDEES)
        # ce sont bien les plus recentes qui restent
        self.assertIn("abonnes-20260908.json", restants)
        self.assertNotIn("abonnes-20260801.json", restants)

    def test_signale_une_chute_du_nombre_d_abonnes(self):
        """Une sauvegarde qu'on ne regarde jamais ne dit pas que la table
        a ete videe. C'est le silence ambigu, deja paye en aout."""
        abonnes.ecrire_instantane(self.conn, self.copies,
                                  "2026-09-21T13:00:00+00:00")
        self.conn.execute("DELETE FROM abonnes WHERE chat_id = 8296006641")
        self.conn.commit()

        rapport = abonnes.ecrire_instantane(self.conn, self.copies,
                                            "2026-09-22T13:00:00+00:00")

        self.assertEqual(rapport["lignes"], 1)
        self.assertEqual(rapport["precedent"], 2)
        self.assertTrue(rapport["chute"])

    def test_pas_de_chute_quand_le_nombre_monte(self):
        abonnes.ecrire_instantane(self.conn, self.copies,
                                  "2026-09-21T13:00:00+00:00")
        abonnes.inscrire(self.conn, 1072135850, "Godwin", "2026-09-22T08:00:00+00:00")

        rapport = abonnes.ecrire_instantane(self.conn, self.copies,
                                            "2026-09-22T13:00:00+00:00")

        self.assertFalse(rapport["chute"])

    def test_la_premiere_copie_n_est_jamais_une_chute(self):
        rapport = abonnes.ecrire_instantane(self.conn, self.copies,
                                            "2026-09-22T13:00:00+00:00")

        self.assertIsNone(rapport["precedent"])
        self.assertFalse(rapport["chute"])

    def test_une_copie_a_moitie_ecrite_ne_remplace_pas_la_bonne(self):
        """Ecriture atomique : si le processus meurt en plein milieu, la
        copie de la veille doit rester lisible."""
        bonne = abonnes.ecrire_instantane(self.conn, self.copies,
                                          "2026-09-22T09:00:00+00:00")
        vrai_replace = os.replace

        def replace_qui_echoue(src, dst):
            raise OSError("disque plein")

        os.replace = replace_qui_echoue
        try:
            with self.assertRaises(OSError):
                abonnes.ecrire_instantane(self.conn, self.copies,
                                          "2026-09-22T13:00:00+00:00")
        finally:
            os.replace = vrai_replace

        self.assertEqual(self._lire(bonne["chemin"])["lignes"], 2)
        # aucun fichier temporaire abandonne dans le dossier
        self.assertEqual(os.listdir(self.copies), ["abonnes-20260922.json"])


if __name__ == "__main__":
    unittest.main()
