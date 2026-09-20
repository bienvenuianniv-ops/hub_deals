"""Envoi Telegram a un destinataire quelconque, avec statut exploitable."""

import os
import sys
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db

# jamais d'appel reel a l'API des liens courts depuis les tests, meme
# quand TRAVELPAYOUTS_PROJET est pose sur la machine
hub_deals_db.TRAVELPAYOUTS_PROJET = None


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


class TestSourceDesAbonnes(unittest.TestCase):
    """Le releve tourne sur le portable, l'ecoute sur Render : les abonnes
    ne sont plus dans flight_deals.db."""

    def setUp(self):
        import abonnes
        import magasin
        import tempfile
        from unittest import mock
        self.mock, self.magasin, self.abonnes = mock, magasin, abonnes
        # la vraie fonction, capturee avant tout remplacement : sans cela
        # _ouvrir s'appellerait lui-meme une fois magasin.ouvrir patche.
        self.vrai_ouvrir = magasin.ouvrir
        self.dossier = tempfile.TemporaryDirectory()
        self.chemin = os.path.join(self.dossier.name, "abonnes.db")
        self.ouvertes = []
        self._log = hub_deals_db.log
        self._envoyer_a = hub_deals_db.envoyer_telegram_a
        self.lignes = []
        hub_deals_db.log = self.lignes.append
        self.groupes = [[{
            "destination": "Rome", "destination_code": "ROM", "hub": "Abidjan",
            "ville_depart": "Dakar", "prix_actuel": 975.0,
            "moyenne_historique": 1419.0, "baisse_pct": 31.3, "economie": 444.0,
            "rabattement_mesure": None, "lien": "/search/ABJ0511ROM1"}]]

    def tearDown(self):
        for conn in self.ouvertes:
            conn.close()
        self.dossier.cleanup()
        hub_deals_db.log = self._log
        hub_deals_db.envoyer_telegram_a = self._envoyer_a

    def _ouvrir(self):
        conn = self.vrai_ouvrir(chemin=self.chemin)
        self.ouvertes.append(conn)
        return conn

    def test_les_abonnes_viennent_du_magasin_pas_de_la_base_des_offres(self):
        conn = self._ouvrir()
        quand = self.abonnes.maintenant()
        self.abonnes.inscrire(conn, 862000001, "Awa", quand)
        self.abonnes.choisir_ville(conn, 862000001, "Dakar", quand)
        recus = []
        hub_deals_db.envoyer_telegram_a = lambda cid, msg, journaliser=False: (
            recus.append(cid) or True)

        # patcher magasin.ouvrir et NON hub_deals_db.magasin.ouvrir :
        # l'import est fait DANS la fonction, donc hub_deals_db n'a pas
        # d'attribut « magasin » a patcher.
        with self.mock.patch.object(self.magasin, "ouvrir", self._ouvrir):
            hub_deals_db.notifier_abonnes_sans_risque(self.groupes)

        self.assertEqual(recus, [862000001], "\n".join(self.lignes))

    def test_une_base_injoignable_n_interrompt_pas_le_releve(self):
        """Contrat inchange : ne leve jamais. Le proprietaire a deja recu
        son message, le releve doit finir."""
        def ouvrir_casse():
            raise RuntimeError("connexion refusee")

        with self.mock.patch.object(self.magasin, "ouvrir", ouvrir_casse):
            hub_deals_db.notifier_abonnes_sans_risque(self.groupes)   # ne leve pas


if __name__ == "__main__":
    unittest.main()
