"""Payload « attente_ » du lien de liste d'attente (spec du 2026-09-22)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import bot_ecoute
import hub_deals_db
import magasin
import souhaits

CODE = "invitation_test_2026"
T0 = "2026-09-22T10:00:00+00:00"


def _start(payload, chat_id=111):
    return {"update_id": 1, "message": {
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": chat_id, "first_name": "Awa"},
        "text": f"/start {payload}"}}


class TestListeDAttente(unittest.TestCase):
    def setUp(self):
        self.conn = magasin.ouvrir(chemin=":memory:")

    def tearDown(self):
        self.conn.close()

    def test_enregistre_le_souhait_sans_creer_d_abonne(self):
        bot_ecoute.traiter_update(self.conn, _start("attente_nairobi"),
                                  CODE, T0)

        self.assertEqual(souhaits.compter(self.conn), {"Nairobi": 1})
        self.assertIsNone(abonnes.trouver(self.conn, 111))

    def test_ne_consomme_pas_le_plafond(self):
        bot_ecoute.traiter_update(self.conn, _start("attente_nairobi"),
                                  CODE, T0)

        self.assertEqual(abonnes.nb_actifs(self.conn), 0)

    def test_survit_aux_noms_composes(self):
        bot_ecoute.traiter_update(self.conn, _start("attente_le_caire"),
                                  CODE, T0)

        self.assertEqual(souhaits.compter(self.conn), {"Le Caire": 1})

    def test_une_ville_inconnue_est_refusee(self):
        actions, _ = bot_ecoute.traiter_update(
            self.conn, _start("attente_atlantide"), CODE, T0)

        self.assertEqual(souhaits.compter(self.conn), {})
        self.assertIn(bot_ecoute.MSG_INVITATION,
                      [a.get("text") for a in actions])

    def test_le_chemin_inverse_couvre_les_treize_villes(self):
        """L'etiquette ecrite par la page et celle attendue par le bot
        viennent du meme etiquette_ville() -- defaut du 2026-09-16."""
        for ville in abonnes.NOMS_AFFICHES:
            etiquette = hub_deals_db.etiquette_ville(ville)
            self.assertEqual(bot_ecoute.ville_depuis_etiquette(etiquette),
                             ville)


if __name__ == "__main__":
    unittest.main()


class TestDemandeDInvitation(unittest.TestCase):
    """La page ne porte plus le code : pour une ville servie, son bouton
    est une demande d'invitation (relecture du 2026-09-26)."""

    def setUp(self):
        self.conn = magasin.ouvrir(chemin=":memory:")

    def tearDown(self):
        self.conn.close()

    def test_une_ville_proposee_est_notee_sans_abonner(self):
        actions, _ = bot_ecoute.traiter_update(
            self.conn, _start("attente_dakar"), CODE, T0)

        self.assertEqual(souhaits.compter(self.conn), {"Dakar": 1})
        self.assertIsNone(abonnes.trouver(self.conn, 111))
        textes = [a.get("text") for a in actions]
        self.assertIn(bot_ecoute.MSG_DEMANDE.format(ville="Dakar"), textes)

    def test_on_ne_promet_pas_une_couverture_deja_acquise(self):
        actions, _ = bot_ecoute.traiter_update(
            self.conn, _start("attente_dakar"), CODE, T0)

        self.assertFalse(any("sera couverte" in (a.get("text") or "")
                             for a in actions))
