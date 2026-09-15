"""Abonnes au bot : donnees, filtrage, message, envoi, temoin d'ecoute."""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import hub_deals_db

T0 = "2026-09-15T12:00:00+00:00"
T1 = "2026-09-15T12:05:00+00:00"


def _base():
    conn = sqlite3.connect(":memory:")
    abonnes.init_abonnes(conn)
    return conn


class TestStructure(unittest.TestCase):
    def test_les_villes_proposees_sont_exactement_celles_de_rabattement(self):
        """Une ville proposee sans entree dans RABATTEMENT ne recevrait
        jamais rien ; une ville de RABATTEMENT absente serait inaccessible."""
        self.assertEqual(set(abonnes.NOMS_AFFICHES), set(hub_deals_db.RABATTEMENT))


class TestDonneesAbonnes(unittest.TestCase):
    def setUp(self):
        self.conn = _base()

    def tearDown(self):
        self.conn.close()

    def test_init_est_idempotent(self):
        abonnes.init_abonnes(self.conn)
        abonnes.init_abonnes(self.conn)

    def test_inscription_puis_lecture(self):
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        a = abonnes.trouver(self.conn, 111)
        self.assertEqual(a["prenom"], "Awa")
        self.assertIs(a["actif"], True)
        self.assertIsNone(a["ville_depart"])
        self.assertEqual(a["inscrit_le"], T0)

    def test_inconnu(self):
        self.assertIsNone(abonnes.trouver(self.conn, 999))

    def test_choix_de_ville(self):
        ville = sorted(abonnes.NOMS_AFFICHES)[0]
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        abonnes.choisir_ville(self.conn, 111, ville, T1)
        a = abonnes.trouver(self.conn, 111)
        self.assertEqual(a["ville_depart"], ville)
        self.assertEqual(a["modifie_le"], T1)

    def test_ville_inconnue_refusee(self):
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        with self.assertRaises(ValueError):
            abonnes.choisir_ville(self.conn, 111, "Atlantide", T1)

    def test_desactivation_conserve_la_ligne(self):
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        abonnes.desactiver(self.conn, 111, "stop", T1)
        a = abonnes.trouver(self.conn, 111)
        self.assertIs(a["actif"], False)
        self.assertEqual(a["motif_inactif"], "stop")

    def test_reinscription_reactive_et_garde_la_ville(self):
        ville = sorted(abonnes.NOMS_AFFICHES)[0]
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        abonnes.choisir_ville(self.conn, 111, ville, T0)
        abonnes.desactiver(self.conn, 111, "bloque", T0)
        abonnes.inscrire(self.conn, 111, "Awa", T1)
        a = abonnes.trouver(self.conn, 111)
        self.assertIs(a["actif"], True)
        self.assertIsNone(a["motif_inactif"])
        self.assertEqual(a["ville_depart"], ville)
        self.assertEqual(a["inscrit_le"], T0)

    def test_nb_actifs(self):
        abonnes.inscrire(self.conn, 1, "a", T0)
        abonnes.inscrire(self.conn, 2, "b", T0)
        abonnes.desactiver(self.conn, 2, "stop", T0)
        self.assertEqual(abonnes.nb_actifs(self.conn), 1)

    def test_abonnes_a_servir_ignore_inactifs_et_sans_ville(self):
        ville = sorted(abonnes.NOMS_AFFICHES)[0]
        for chat_id in (1, 2, 3):
            abonnes.inscrire(self.conn, chat_id, "x", T0)
        abonnes.choisir_ville(self.conn, 1, ville, T0)
        abonnes.choisir_ville(self.conn, 2, ville, T0)
        abonnes.desactiver(self.conn, 2, "stop", T0)
        # 3 n'a pas choisi de ville
        self.assertEqual([a["chat_id"] for a in abonnes.abonnes_a_servir(self.conn)], [1])

    def test_abonnes_a_servir_exclut_le_proprietaire(self):
        """Le proprietaire recoit deja le message complet : pas de doublon.
        TELEGRAM_CHAT_ID est une chaine, chat_id un entier."""
        ville = sorted(abonnes.NOMS_AFFICHES)[0]
        for chat_id in (1, 2):
            abonnes.inscrire(self.conn, chat_id, "x", T0)
            abonnes.choisir_ville(self.conn, chat_id, ville, T0)
        servis = abonnes.abonnes_a_servir(self.conn, exclure_chat_id="2")
        self.assertEqual([a["chat_id"] for a in servis], [1])


if __name__ == "__main__":
    unittest.main()
