"""Ecoute du bot : commandes, boucle getUpdates, execution sous pythonw."""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import magasin
import bot_ecoute

CODE = "invitation_test_2026"
T0 = "2026-09-15T12:00:00+00:00"


def _message(texte, chat_id=111, prenom="Awa", type_chat="private", update_id=1):
    return {"update_id": update_id, "message": {
        "chat": {"id": chat_id, "type": type_chat},
        "from": {"id": chat_id, "first_name": prenom}, "text": texte}}


def _bouton(data, chat_id=111, update_id=1):
    return {"update_id": update_id, "callback_query": {
        "id": "cb1", "data": data, "from": {"id": chat_id},
        "message": {"chat": {"id": chat_id, "type": "private"}}}}


def _textes(actions):
    return [a["text"] for a in actions if a["methode"] == "sendMessage"]


class TestCodeValide(unittest.TestCase):
    def test_code_correct(self):
        self.assertEqual(bot_ecoute.code_valide(CODE), CODE)

    def test_absent_trop_court_ou_caracteres_interdits(self):
        """Fail closed : un code inutilisable ferme les inscriptions. Un code
        court serait aussi masque a tort dans des lignes de journal."""
        for code in (None, "", "court", "espace interdit 12", "x" * 65):
            self.assertIsNone(bot_ecoute.code_valide(code))


class TestCommandes(unittest.TestCase):
    def setUp(self):
        self.conn = magasin.ouvrir(chemin=":memory:")
        self.ville = sorted(abonnes.NOMS_AFFICHES)[0]

    def tearDown(self):
        self.conn.close()

    def _traiter(self, update, code=CODE):
        return bot_ecoute.traiter_update(self.conn, update, code, T0)

    def test_start_avec_le_bon_code_inscrit_et_propose_les_villes(self):
        actions, _ = self._traiter(_message(f"/start {CODE}"))
        self.assertIsNotNone(abonnes.trouver(self.conn, 111))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_BIENVENUE])
        boutons = actions[0]["reply_markup"]["inline_keyboard"]
        self.assertEqual({ligne[0]["callback_data"] for ligne in boutons},
                         {f"ville:{v}" for v in abonnes.NOMS_AFFICHES})

    def test_start_avec_un_code_faux_ou_absent(self):
        for texte in ("/start mauvais_code_2026", "/start"):
            actions, _ = self._traiter(_message(texte))
            self.assertEqual(_textes(actions), [bot_ecoute.MSG_INVITATION])
        self.assertIsNone(abonnes.trouver(self.conn, 111))

    def test_code_non_ascii_refuse_sans_planter(self):
        actions, _ = self._traiter(_message("/start clé_non_ascii_2026"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_INVITATION])

    def test_inscriptions_fermees_sans_code_configure(self):
        actions, _ = self._traiter(_message(f"/start {CODE}"), code=None)
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_INVITATION])
        self.assertIsNone(abonnes.trouver(self.conn, 111))

    def test_start_avec_le_nom_du_bot_accole(self):
        """Telegram peut envoyer /start@ianniv_vols_bot dans certains clients."""
        self._traiter(_message(f"/start@ianniv_vols_bot {CODE}"))
        self.assertIsNotNone(abonnes.trouver(self.conn, 111))

    def test_plafond_atteint(self):
        for chat_id in range(abonnes.PLAFOND_ABONNES):
            abonnes.inscrire(self.conn, 10_000 + chat_id, "x", T0)
        actions, _ = self._traiter(_message(f"/start {CODE}"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_COMPLET])
        self.assertIsNone(abonnes.trouver(self.conn, 111))

    def test_retour_sans_code_apres_stop(self):
        self._traiter(_message(f"/start {CODE}"))
        self._traiter(_message("/stop"))
        actions, _ = self._traiter(_message("/start"))
        self.assertIs(abonnes.trouver(self.conn, 111)["actif"], True)
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_BIENVENUE])

    def test_retour_refuse_si_plafond_atteint(self):
        self._traiter(_message(f"/start {CODE}"))
        self._traiter(_message("/stop"))
        for chat_id in range(abonnes.PLAFOND_ABONNES):
            abonnes.inscrire(self.conn, 10_000 + chat_id, "x", T0)
        actions, _ = self._traiter(_message("/start"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_COMPLET])
        self.assertIs(abonnes.trouver(self.conn, 111)["actif"], False)

    def test_un_abonne_actif_n_est_pas_bloque_par_le_plafond(self):
        self._traiter(_message(f"/start {CODE}"))
        for chat_id in range(abonnes.PLAFOND_ABONNES):
            abonnes.inscrire(self.conn, 10_000 + chat_id, "x", T0)
        actions, _ = self._traiter(_message("/start"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_BIENVENUE])

    def test_bouton_de_ville(self):
        self._traiter(_message(f"/start {CODE}"))
        actions, _ = self._traiter(_bouton(f"ville:{self.ville}"))
        self.assertEqual(abonnes.trouver(self.conn, 111)["ville_depart"], self.ville)
        self.assertEqual(actions[0], {"methode": "answerCallbackQuery",
                                      "callback_query_id": "cb1"})
        self.assertEqual(_textes(actions), [bot_ecoute.msg_confirmation(self.ville)])
        self.assertIn(abonnes.NOMS_AFFICHES[self.ville],
                      bot_ecoute.msg_confirmation(self.ville))

    def test_bouton_idempotent(self):
        """Un redemarrage peut rejouer une mise a jour deja traitee."""
        self._traiter(_message(f"/start {CODE}"))
        self._traiter(_bouton(f"ville:{self.ville}"))
        self._traiter(_bouton(f"ville:{self.ville}"))
        self.assertEqual(abonnes.trouver(self.conn, 111)["ville_depart"], self.ville)
        self.assertEqual(abonnes.nb_actifs(self.conn), 1)

    def test_bouton_d_un_inconnu_ou_d_un_desabonne(self):
        actions, _ = self._traiter(_bouton(f"ville:{self.ville}"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_INVITATION])
        self._traiter(_message(f"/start {CODE}"))
        self._traiter(_message("/stop"))
        actions, _ = self._traiter(_bouton(f"ville:{self.ville}"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_STOP])
        self.assertIsNone(abonnes.trouver(self.conn, 111)["ville_depart"])

    def test_bouton_de_ville_inconnue(self):
        self._traiter(_message(f"/start {CODE}"))
        actions, _ = self._traiter(_bouton("ville:Atlantide"))
        self.assertEqual(_textes(actions), [])
        self.assertIsNone(abonnes.trouver(self.conn, 111)["ville_depart"])

    def test_ville(self):
        self._traiter(_message(f"/start {CODE}"))
        actions, _ = self._traiter(_message("/ville"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_MENU])
        self.assertIn("reply_markup", actions[0])

    def test_stop(self):
        self._traiter(_message(f"/start {CODE}"))
        actions, _ = self._traiter(_message("/stop"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_STOP])
        a = abonnes.trouver(self.conn, 111)
        self.assertIs(a["actif"], False)
        self.assertEqual(a["motif_inactif"], "stop")

    def test_commandes_d_un_inconnu(self):
        for texte in ("/ville", "/stop", "bonjour"):
            actions, _ = self._traiter(_message(texte))
            self.assertEqual(_textes(actions), [bot_ecoute.MSG_INVITATION])

    def test_message_libre_d_un_abonne(self):
        self._traiter(_message(f"/start {CODE}"))
        actions, _ = self._traiter(_message("bonjour"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_AIDE])

    def test_groupes_et_messages_sans_texte_ignores(self):
        actions, _ = self._traiter(_message(f"/start {CODE}", type_chat="group"))
        self.assertEqual(actions, [])
        actions, resume = self._traiter({"update_id": 1, "message": {
            "chat": {"id": 111, "type": "private"}}})
        self.assertEqual(actions, [])
        self.assertIsNone(abonnes.trouver(self.conn, 111))

    def test_le_resume_ne_contient_ni_le_code_ni_le_texte(self):
        for update in (_message(f"/start {CODE}"), _message("mon numero 0612"),
                       _message("/start mauvais_code_2026")):
            _, resume = self._traiter(update)
            self.assertNotIn(CODE, resume or "")
            self.assertNotIn("0612", resume or "")
            self.assertNotIn("mauvais_code_2026", resume or "")
            self.assertIn("111", resume)


class _FauxTelegram:
    """Remplace appeler() : reponses getUpdates programmees, appels notes."""

    def __init__(self, reponses_get_updates, statut_envoi=200):
        self.file = list(reponses_get_updates)
        self.appels = []
        self.statut_envoi = statut_envoi

    def __call__(self, token, methode, params, timeout=15):
        self.appels.append((methode, dict(params)))
        if methode == "getUpdates":
            reponse = self.file.pop(0)
            if isinstance(reponse, Exception):
                raise reponse
            return reponse
        return (self.statut_envoi, {"ok": self.statut_envoi == 200})


class TestMasquage(unittest.TestCase):
    def test_token_et_code_masques(self):
        import hub_deals_db
        bot, code = hub_deals_db.TELEGRAM_BOT_TOKEN, bot_ecoute.CODE_INVITATION
        hub_deals_db.TELEGRAM_BOT_TOKEN = "123:secret_bot_token"
        bot_ecoute.CODE_INVITATION = CODE
        try:
            texte = bot_ecoute.masquer(
                f"https://api.telegram.org/bot123:secret_bot_token/getUpdates {CODE}")
        finally:
            hub_deals_db.TELEGRAM_BOT_TOKEN, bot_ecoute.CODE_INVITATION = bot, code
        self.assertNotIn("secret_bot_token", texte)
        self.assertNotIn(CODE, texte)

    def test_le_journal_ecrit_est_masque(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "bot.txt")
            with mock.patch.object(bot_ecoute, "LOG_PATH", chemin), \
                 mock.patch.object(bot_ecoute, "CODE_INVITATION", CODE), \
                 mock.patch("builtins.print"):
                bot_ecoute.log(f"essai {CODE}")
            with open(chemin, encoding="utf-8") as f:
                contenu = f.read()
        self.assertIn("essai ***", contenu)


if __name__ == "__main__":
    unittest.main()
