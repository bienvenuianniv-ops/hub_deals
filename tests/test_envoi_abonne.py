"""Envoi Telegram a un destinataire quelconque, avec statut exploitable."""

import os
import sys
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db


class _Reponse:
    def __init__(self, status_code=200, text='{"ok":true}'):
        self.status_code = status_code
        self.text = text


class TestEnvoyerTelegramA(unittest.TestCase):
    def setUp(self):
        self._log = hub_deals_db.log
        self._post = hub_deals_db.requests.post
        self._bot = hub_deals_db.TELEGRAM_BOT_TOKEN
        self.lignes = []
        self.appels = []
        hub_deals_db.log = self.lignes.append
        hub_deals_db.TELEGRAM_BOT_TOKEN = "bot-factice"

    def tearDown(self):
        hub_deals_db.log = self._log
        hub_deals_db.requests.post = self._post
        hub_deals_db.TELEGRAM_BOT_TOKEN = self._bot

    def _repondre(self, reponse):
        def poster(url, data=None, timeout=None):
            self.appels.append(data)
            return reponse
        hub_deals_db.requests.post = poster

    def test_succes(self):
        self._repondre(_Reponse())
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"), ("ok", None))
        self.assertEqual(self.appels[0]["chat_id"], 777)

    def test_bot_bloque_par_l_abonne(self):
        self._repondre(_Reponse(403, '{"ok":false,"error_code":403,'
                                     '"description":"Forbidden: bot was blocked by the user"}'))
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"), ("bloque", None))

    def test_trop_de_requetes_renvoie_le_delai(self):
        self._repondre(_Reponse(429, '{"ok":false,"error_code":429,'
                                     '"parameters":{"retry_after":7}}'))
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"), ("trop_vite", 7))

    def test_429_sans_delai_lisible_attend_une_seconde(self):
        self._repondre(_Reponse(429, "pas du json"))
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"), ("trop_vite", 1))

    def test_autre_refus(self):
        self._repondre(_Reponse(400, '{"ok":false}'))
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"), ("echec", "HTTP 400"))

    def test_erreur_reseau(self):
        def poster(*a, **k):
            raise requests.exceptions.RequestException("coupure")
        hub_deals_db.requests.post = poster
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"), ("echec", "reseau"))

    def test_bot_non_configure(self):
        hub_deals_db.TELEGRAM_BOT_TOKEN = None
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"),
                         ("echec", "non configure"))

    def test_sans_journalisation_le_corps_n_est_pas_ecrit(self):
        """Les messages d'abonnes ne sont pas recopies au journal : 30
        abonnes par jour le feraient enfler sans rien apprendre de plus
        que le compte rendu."""
        self._repondre(_Reponse())
        hub_deals_db.envoyer_telegram_a(777, "CORPS-UNIQUE", journaliser=False)
        self.assertNotIn("CORPS-UNIQUE", "\n".join(self.lignes))

    def test_envoyer_telegram_vise_toujours_le_proprietaire(self):
        chat = hub_deals_db.TELEGRAM_CHAT_ID
        hub_deals_db.TELEGRAM_CHAT_ID = "12345"
        try:
            self._repondre(_Reponse())
            self.assertIs(hub_deals_db.envoyer_telegram("x"), True)
            self.assertEqual(self.appels[0]["chat_id"], "12345")
        finally:
            hub_deals_db.TELEGRAM_CHAT_ID = chat


if __name__ == "__main__":
    unittest.main()
