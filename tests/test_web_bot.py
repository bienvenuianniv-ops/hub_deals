"""Tests du service webhook (bascule du 2026-09-20).

Aucun appel reseau : l'appelant Telegram et l'ouverture de base sont
injectes. Les tests de bot_ecoute couvrent deja la logique d'inscription
elle-meme -- ici, c'est l'enveloppe HTTP qui est en jeu.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import magasin
import web_bot

SECRET = "secret_de_test_0123456789"
CODE = "code_invitation_essai"


def _update(texte, chat_id=862000001):
    return {"update_id": 1, "message": {
        "message_id": 7, "text": texte,
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": chat_id, "first_name": "Awa"}}}


class TestWebhook(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.chemin = os.path.join(self.dossier.name, "essai.db")
        self.appels = []
        self.ouvertes = []

        def appeler(token, methode, params):
            self.appels.append((methode, params))
            return 200, {"ok": True}

        self.app = web_bot.creer_app(
            ouvrir=self._ouvrir, appeler=appeler, token="jeton",
            code=CODE, secret=SECRET)
        self.client = self.app.test_client()

    def tearDown(self):
        for conn in self.ouvertes:
            conn.close()
        self.dossier.cleanup()

    def _ouvrir(self):
        conn = magasin.ouvrir(chemin=self.chemin)
        self.ouvertes.append(conn)
        return conn

    def _poster(self, update, secret=SECRET):
        entetes = {}
        if secret is not None:
            entetes["X-Telegram-Bot-Api-Secret-Token"] = secret
        return self.client.post("/telegram", json=update, headers=entetes)

    def test_sans_secret_rien_n_est_traite(self):
        reponse = self._poster(_update(f"/start {CODE}"), secret=None)

        self.assertEqual(reponse.status_code, 403)
        self.assertEqual(self.appels, [])

    def test_avec_un_mauvais_secret_rien_n_est_traite(self):
        reponse = self._poster(_update(f"/start {CODE}"), secret="faux")

        self.assertEqual(reponse.status_code, 403)
        self.assertEqual(self.appels, [])

    def test_un_start_valide_inscrit_et_repond(self):
        reponse = self._poster(_update(f"/start {CODE}"))

        conn = self._ouvrir()
        self.assertEqual(reponse.status_code, 200)
        self.assertIsNotNone(abonnes.trouver(conn, 862000001))
        self.assertEqual(self.appels[0][0], "sendMessage")

    def test_le_meme_update_rejoue_ne_cree_qu_un_abonne(self):
        """Render s'endort : Telegram peut reessayer pendant le reveil et
        livrer deux fois le meme update."""
        self._poster(_update(f"/start {CODE}"))
        self._poster(_update(f"/start {CODE}"))

        conn = self._ouvrir()
        self.assertEqual(abonnes.nb_actifs(conn), 1)

    def test_un_update_sans_message_repond_200_sans_rien_faire(self):
        reponse = self._poster({"update_id": 2})

        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(self.appels, [])

    def test_si_la_base_est_injoignable_la_reponse_est_500(self):
        """Repondre 200 sur un message non traite, c'est perdre une
        inscription en silence : Telegram ne reessaierait jamais."""
        def ouvrir_casse():
            raise RuntimeError("connexion refusee")

        app = web_bot.creer_app(ouvrir=ouvrir_casse, appeler=lambda *a: None,
                                token="jeton", code=CODE, secret=SECRET)

        reponse = app.test_client().post(
            "/telegram", json=_update("/start"),
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})

        self.assertEqual(reponse.status_code, 500)

    def test_sans_secret_configure_tout_est_refuse(self):
        """Un service deploye sans TELEGRAM_WEBHOOK_SECRET ne doit pas
        accepter n'importe qui : l'absence de secret ferme la porte, elle
        ne l'ouvre pas."""
        app = web_bot.creer_app(ouvrir=self._ouvrir, appeler=lambda *a: None,
                                token="jeton", code=CODE, secret=None)

        reponse = app.test_client().post("/telegram", json=_update("/start"))

        self.assertEqual(reponse.status_code, 403)

    def test_la_page_de_sante_repond_sans_secret(self):
        reponse = self.client.get("/sante")

        self.assertEqual(reponse.status_code, 200)
        self.assertNotIn(CODE, reponse.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
