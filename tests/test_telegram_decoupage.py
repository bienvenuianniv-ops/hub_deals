"""
Tests du decoupage des notifications Telegram et de la sincerite du journal.

Contexte : Telegram refuse tout message de plus de 4096 caracteres avec un
HTTP 400. Un releve de 72 anomalies pese ~11 000 caracteres. Entre le
2026-08-17 et le 2026-09-08, 32 notifications ont ete refusees pour cette
raison -- sans que personne ne s'en apercoive, parce que l'appelant
journalisait « Notification Telegram envoyee » de facon inconditionnelle.
"""

import os
import sys
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db

# jamais d'appel reel a l'API des liens courts depuis les tests, meme
# quand TRAVELPAYOUTS_PROJET est pose sur la machine
hub_deals_db.TRAVELPAYOUTS_PROJET = None


class _FausseReponse:
    def __init__(self, status_code=200, text='{"ok":true}'):
        self.status_code = status_code
        self.text = text


def _anomalie(dest):
    """Anomalie minimale, telle que verifier_et_notifier_anomalies l'attend."""
    return {
        "destination": dest,
        "hub": "Istanbul",
        "ville_depart": "Brazzaville",
        "prix_actuel": 900.0,
        "moyenne_historique": 1000.0,
        "baisse_pct": 10.0,
        "economie": 100.0,
        "rabattement_mesure": None,
        "lien": "/search/IST3110XXX1",
    }


def _blocs(n=72, taille=140):
    return ["<b>destination %d</b>\n%s" % (i, "x" * taille) for i in range(n)]


class TestDecoupageDuMessage(unittest.TestCase):

    def test_un_lot_court_tient_dans_un_seul_message(self):
        morceaux = hub_deals_db.decouper_message(
            ["<b>bloc A</b>", "<b>bloc B</b>"], "<b>2 affaires</b>")
        self.assertEqual(len(morceaux), 1)

    def test_un_lot_court_n_est_pas_numerote(self):
        """Numeroter un message unique ajoute du bruit sans rien apporter."""
        morceaux = hub_deals_db.decouper_message(
            ["<b>bloc</b>"], "<b>1 affaire</b>")
        self.assertNotIn("1/1", morceaux[0])

    def test_un_lot_long_est_decoupe(self):
        morceaux = hub_deals_db.decouper_message(_blocs(), "<b>72 affaires</b>")
        self.assertGreater(len(morceaux), 1)

    def test_chaque_morceau_respecte_la_limite(self):
        morceaux = hub_deals_db.decouper_message(_blocs(), "<b>72 affaires</b>")
        for i, m in enumerate(morceaux):
            self.assertLessEqual(
                len(m), hub_deals_db.LIMITE_TELEGRAM,
                "morceau %d : %d caracteres" % (i, len(m)))

    def test_aucune_anomalie_n_est_coupee_en_deux(self):
        """Couper au milieu d'un bloc casserait le HTML -- donc un 400 de
        plus, et une annonce illisible."""
        blocs = _blocs()
        assemble = "\n".join(
            hub_deals_db.decouper_message(blocs, "<b>72 affaires</b>"))
        for b in blocs:
            self.assertIn(b, assemble)

    def test_aucune_anomalie_n_est_perdue_ni_dupliquee(self):
        assemble = "\n".join(
            hub_deals_db.decouper_message(_blocs(), "<b>72 affaires</b>"))
        for i in range(72):
            self.assertEqual(assemble.count("<b>destination %d</b>" % i), 1)

    def test_les_morceaux_sont_numerotes(self):
        morceaux = hub_deals_db.decouper_message(_blocs(), "<b>72 affaires</b>")
        n = len(morceaux)
        for i, m in enumerate(morceaux, start=1):
            self.assertIn("%d/%d" % (i, n), m)

    def test_chaque_morceau_porte_l_entete(self):
        morceaux = hub_deals_db.decouper_message(_blocs(), "<b>72 affaires</b>")
        for m in morceaux:
            self.assertIn("72 affaires", m)

    def test_un_bloc_seul_plus_gros_que_la_limite_ne_boucle_pas(self):
        """Cas theorique (un bloc pese ~200 caracteres), mais une boucle
        infinie ici gelerait le releve quotidien."""
        enorme = "y" * (hub_deals_db.LIMITE_TELEGRAM + 500)
        morceaux = hub_deals_db.decouper_message(
            [enorme], "<b>1 affaire</b>")
        self.assertEqual(len(morceaux), 1)

    def test_liste_vide_ne_produit_aucun_message(self):
        self.assertEqual(
            hub_deals_db.decouper_message([], "<b>rien</b>"), [])


class TestEnvoiRendCompteDeSonResultat(unittest.TestCase):
    """envoyer_telegram ne renvoyait rien : l'appelant ne POUVAIT pas
    savoir si le message etait parti."""

    def setUp(self):
        self._log = hub_deals_db.log
        self.lignes = []
        hub_deals_db.log = self.lignes.append
        self._post = hub_deals_db.requests.post
        self._bot = hub_deals_db.TELEGRAM_BOT_TOKEN
        self._chat = hub_deals_db.TELEGRAM_CHAT_ID
        hub_deals_db.TELEGRAM_BOT_TOKEN = "bot-factice"
        hub_deals_db.TELEGRAM_CHAT_ID = "12345"

    def tearDown(self):
        hub_deals_db.log = self._log
        hub_deals_db.requests.post = self._post
        hub_deals_db.TELEGRAM_BOT_TOKEN = self._bot
        hub_deals_db.TELEGRAM_CHAT_ID = self._chat

    def test_renvoie_vrai_quand_telegram_accepte(self):
        hub_deals_db.requests.post = lambda *a, **k: _FausseReponse()
        self.assertIs(hub_deals_db.envoyer_telegram("ok"), True)

    def test_renvoie_faux_quand_telegram_refuse(self):
        hub_deals_db.requests.post = lambda *a, **k: _FausseReponse(
            400, '{"ok":false,"description":"message is too long"}')
        self.assertIs(hub_deals_db.envoyer_telegram("trop long"), False)

    def test_renvoie_faux_sur_erreur_reseau(self):
        def poster(*a, **k):
            raise requests.exceptions.RequestException("coupure")
        hub_deals_db.requests.post = poster
        self.assertIs(hub_deals_db.envoyer_telegram("peu importe"), False)

    def test_renvoie_faux_quand_telegram_n_est_pas_configure(self):
        hub_deals_db.TELEGRAM_BOT_TOKEN = None
        self.assertIs(hub_deals_db.envoyer_telegram("pas configure"), False)

    def test_le_token_invalide_est_signale(self):
        """Le 401 apparu le 2026-09-09 : token revoque cote BotFather."""
        hub_deals_db.requests.post = lambda *a, **k: _FausseReponse(
            401, '{"ok":false,"error_code":401,"description":"Unauthorized"}')
        self.assertIs(hub_deals_db.envoyer_telegram("x"), False)
        self.assertIn("401", "\n".join(self.lignes))


class TestLeJournalNeMentPlus(unittest.TestCase):
    """Le compte rendu final doit refleter ce qui est REELLEMENT parti."""

    def setUp(self):
        self._log = hub_deals_db.log
        self.lignes = []
        hub_deals_db.log = self.lignes.append
        self._envoyer = hub_deals_db.envoyer_telegram
        self._mesurer = hub_deals_db.mesurer_rabattements
        self._detecter = hub_deals_db.detecter_anomalies
        hub_deals_db.mesurer_rabattements = lambda couples: {}
        hub_deals_db.detecter_anomalies = (
            lambda conn, date_collecte=None:
                [_anomalie("Destination %d" % i) for i in range(72)])

    def tearDown(self):
        hub_deals_db.log = self._log
        hub_deals_db.envoyer_telegram = self._envoyer
        hub_deals_db.mesurer_rabattements = self._mesurer
        hub_deals_db.detecter_anomalies = self._detecter

    def _journal(self):
        return "\n".join(self.lignes)

    def test_n_annonce_pas_un_envoi_quand_tout_echoue(self):
        hub_deals_db.envoyer_telegram = lambda msg: False

        hub_deals_db.verifier_et_notifier_anomalies(None, "2026-09-11")

        journal = self._journal()
        self.assertNotIn("Notification Telegram envoyee pour 72", journal)
        self.assertIn("ECHEC", journal.upper())

    def test_annonce_l_envoi_quand_tout_passe(self):
        hub_deals_db.envoyer_telegram = lambda msg: True

        hub_deals_db.verifier_et_notifier_anomalies(None, "2026-09-11")

        self.assertIn("72", self._journal())

    def test_signale_un_envoi_partiel(self):
        """Si 1 morceau sur 4 passe, l'annoncer comme un succes complet
        serait le meme mensonge sous une autre forme."""
        etat = {"n": 0}

        def envoyer(msg):
            etat["n"] += 1
            return etat["n"] == 1

        hub_deals_db.envoyer_telegram = envoyer

        hub_deals_db.verifier_et_notifier_anomalies(None, "2026-09-11")

        journal = self._journal().lower()
        self.assertIn("partiel", journal)

    def test_envoie_plusieurs_morceaux_pour_72_anomalies(self):
        envoyes = []

        def envoyer(msg):
            envoyes.append(msg)
            return True

        hub_deals_db.envoyer_telegram = envoyer

        hub_deals_db.verifier_et_notifier_anomalies(None, "2026-09-11")

        self.assertGreater(len(envoyes), 1)
        for m in envoyes:
            self.assertLessEqual(len(m), hub_deals_db.LIMITE_TELEGRAM)


if __name__ == "__main__":
    unittest.main()
