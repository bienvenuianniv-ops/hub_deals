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


class TestBoucle(unittest.TestCase):
    def setUp(self):
        self.conn = magasin.ouvrir(chemin=":memory:")
        self.lignes = []
        self.pauses = []
        self._log = bot_ecoute.log
        bot_ecoute.log = self.lignes.append

    def tearDown(self):
        bot_ecoute.log = self._log
        self.conn.close()

    def _boucle(self, faux, tours):
        bot_ecoute.boucle(self.conn, "bot-factice", CODE, appeler_fn=faux,
                          dormir=self.pauses.append, quand_fn=lambda: T0, tours=tours)

    def _derniere_ecoute(self):
        ligne = self.conn.execute(
            "SELECT valeur FROM etat_bot WHERE cle='derniere_ecoute'").fetchone()
        return ligne[0] if ligne else None

    def test_traite_les_mises_a_jour_et_avance_l_offset(self):
        faux = _FauxTelegram([
            (200, {"ok": True, "result": [_message(f"/start {CODE}", update_id=41)]}),
            (200, {"ok": True, "result": []})])
        self._boucle(faux, tours=2)
        self.assertIsNotNone(abonnes.trouver(self.conn, 111))
        methodes = [m for m, _ in faux.appels]
        self.assertEqual(methodes, ["getUpdates", "sendMessage", "getUpdates"])
        self.assertNotIn("offset", faux.appels[0][1])
        self.assertEqual(faux.appels[2][1]["offset"], 42)
        self.assertEqual(faux.appels[0][1]["allowed_updates"], ["message", "callback_query"])

    def test_le_temoin_est_rafraichi_meme_sans_message(self):
        faux = _FauxTelegram([(200, {"ok": True, "result": []})])
        self._boucle(faux, tours=1)
        self.assertEqual(self._derniere_ecoute(), T0)

    def test_erreur_reseau_attente_croissante_plafonnee(self):
        import requests
        faux = _FauxTelegram([requests.exceptions.ConnectionError("coupure")] * 9)
        self._boucle(faux, tours=9)
        self.assertEqual(self.pauses, [5, 10, 20, 40, 80, 160, 300, 300, 300])
        self.assertIsNone(self._derniere_ecoute())

    def test_conflit_409_journalise(self):
        faux = _FauxTelegram([(409, {"ok": False, "description": "Conflict"})])
        self._boucle(faux, tours=1)
        self.assertIn("409", "\n".join(self.lignes))
        self.assertEqual(self.pauses, [30])
        self.assertIsNone(self._derniere_ecoute())

    def test_autre_refus_attente_croissante(self):
        faux = _FauxTelegram([(401, {"ok": False, "description": "Unauthorized"})] * 2)
        self._boucle(faux, tours=2)
        self.assertEqual(self.pauses, [5, 10])
        self.assertIn("401", "\n".join(self.lignes))

    def test_une_mise_a_jour_qui_plante_est_sautee(self):
        """Sinon une seule mise a jour defectueuse bloquerait l'ecoute."""
        faux = _FauxTelegram([
            (200, {"ok": True, "result": [{"update_id": 7, "callback_query": {}}]}),
            (200, {"ok": True, "result": []})])
        self._boucle(faux, tours=2)
        self.assertEqual(faux.appels[1][1]["offset"], 8)
        self.assertIn("ERREUR", "\n".join(self.lignes))

    def test_base_verrouillee_la_mise_a_jour_est_rejouee(self):
        """Le releve tient la base ~25 s par hub : la mise a jour ne doit
        pas etre perdue, on la redemande au tour suivant."""
        original = bot_ecoute.traiter_update

        def verrouille(*a, **k):
            raise sqlite3.OperationalError("database is locked")
        bot_ecoute.traiter_update = verrouille
        try:
            faux = _FauxTelegram([
                (200, {"ok": True, "result": [_message("/stop", update_id=7)]}),
                (200, {"ok": True, "result": []})])
            self._boucle(faux, tours=2)
        finally:
            bot_ecoute.traiter_update = original
        self.assertNotIn("offset", faux.appels[1][1])

    def test_un_envoi_refuse_est_journalise_sans_arreter(self):
        faux = _FauxTelegram([
            (200, {"ok": True, "result": [_message("bonjour", update_id=1)]})],
            statut_envoi=400)
        self._boucle(faux, tours=1)
        self.assertIn("HTTP 400", "\n".join(self.lignes))

    def test_le_journal_ne_contient_pas_le_texte_recu(self):
        faux = _FauxTelegram([(200, {"ok": True, "result": [
            _message(f"/start {CODE}", update_id=1),
            _message("mon numero 0612", update_id=2)]})])
        self._boucle(faux, tours=1)
        journal = "\n".join(self.lignes)
        self.assertNotIn(CODE, journal)
        self.assertNotIn("0612", journal)


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


class TestSousPythonw(unittest.TestCase):
    def test_sans_token_l_arret_est_journalise_sous_pythonw(self):
        """Sous pythonw, stdout et stderr valent None : sans journal, un
        demarrage rate serait totalement muet. Le test passe lui-meme par
        pythonw (lecon du 2026-09-13 : depuis le runner, il ne prouvait rien)."""
        import subprocess
        import tempfile
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            self.skipTest("pythonw.exe introuvable")
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "bot_ecoute.py")
        env = {k: v for k, v in os.environ.items()
               if k not in ("TELEGRAM_BOT_TOKEN", "HUB_DEALS_CODE_INVITATION")}
        with tempfile.TemporaryDirectory() as dossier:
            code_retour = subprocess.run([pythonw, script], cwd=dossier, env=env,
                                         timeout=60).returncode
            with open(os.path.join(dossier, "bot_ecoute_log.txt"), encoding="utf-8") as f:
                journal = f.read()
        self.assertEqual(code_retour, 1)
        self.assertIn("ARRET", journal)
        self.assertIn("TELEGRAM_BOT_TOKEN", journal)

    def test_le_bloc_principal_installe_la_journalisation(self):
        import inspect
        bloc = inspect.getsource(bot_ecoute).split('if __name__ == "__main__":')[1]
        self.assertIn("sys.excepthook = journaliser_plantage", bloc)
        self.assertIn("timeout=60", bloc)


class TestEmpecherLaVeille(unittest.TestCase):
    """La machine s'est endormie 12 h le 17/09 au soir (motif « System
    Idle ») malgre `standby-timeout-ac 0` : le bot etait muet pendant ce
    temps. Un reglage se fait ecraser, un verrou tenu par le programme
    lui-meme tient tant que le programme tourne."""

    def setUp(self):
        self.lignes = []
        self._log = bot_ecoute.log
        bot_ecoute.log = self.lignes.append

    def tearDown(self):
        bot_ecoute.log = self._log

    def test_demande_a_windows_de_rester_eveille(self):
        appels = []

        def faux_api(drapeaux):
            appels.append(drapeaux)
            return 0x80000001  # etat precedent : Windows a accepte

        self.assertTrue(bot_ecoute.empecher_la_veille(regler=faux_api))
        # ES_CONTINUOUS (0x80000000) : le verrou dure tant qu'on tourne.
        # ES_SYSTEM_REQUIRED (0x1) : la machine, pas seulement l'ecran --
        # laisser l'ecran s'eteindre est souhaitable, il ne sert a rien.
        self.assertEqual(appels, [0x80000001])

    def test_un_refus_de_windows_est_journalise_sans_arreter_le_bot(self):
        """Renvoi 0 = echec. Ecouter reste plus utile que s'arreter : sans
        ce filet, une API indisponible tuerait le bot au demarrage."""
        self.assertFalse(bot_ecoute.empecher_la_veille(regler=lambda d: 0))

        self.assertTrue(any("veille" in l.lower() for l in self.lignes),
                        self.lignes)

    def test_une_machine_sans_cette_api_ne_fait_pas_planter_le_bot(self):
        def absente(drapeaux):
            raise AttributeError("pas de kernel32 ici")

        self.assertFalse(bot_ecoute.empecher_la_veille(regler=absente))

    def test_le_bloc_principal_pose_le_verrou_avant_la_boucle(self):
        import inspect
        source = inspect.getsource(bot_ecoute)
        bloc = source.split('if __name__ == "__main__":')[1]
        self.assertIn("empecher_la_veille()", bloc)
        self.assertLess(bloc.index("empecher_la_veille()"), bloc.index("boucle("))


if __name__ == "__main__":
    unittest.main()
