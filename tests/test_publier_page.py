"""Publication de la page sur gh-pages (spec du 2026-09-22)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db

QUAND = "2026-09-22T09:18:00+00:00"

# Capturee a l'import, avant que le garde-fou autouse de conftest.py ne
# neutralise hub_deals_db.publier_page_sans_risque pour le reste de la
# suite : c'est la vraie fonction (celle qui delegue a publier_page),
# que TestRaccordementAuReleve remet en place pour son propre test.
_VRAIE_PUBLIER_PAGE_SANS_RISQUE = hub_deals_db.publier_page_sans_risque


class TestPublierPage(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.commandes = []
        self.codes = {}
        self.envoyes = []
        self.journal = []
        self._vrai_envoyer = hub_deals_db.envoyer_telegram
        self._vrai_log = hub_deals_db.log
        hub_deals_db.envoyer_telegram = self._envoyer
        hub_deals_db.log = self.journal.append

    def tearDown(self):
        hub_deals_db.envoyer_telegram = self._vrai_envoyer
        hub_deals_db.log = self._vrai_log
        self.dossier.cleanup()

    def _envoyer(self, message):
        self.envoyes.append(message)
        return True

    def _executer(self, args, cwd=None, delai=None):
        self.commandes.append(args)
        return self.codes.get(args[1], 0), ""

    def test_ecrit_la_page_puis_commit_et_pousse(self):
        # « git diff --cached --quiet » rend 0 quand RIEN n'est indexe :
        # 1 signifie donc « il y a des changements a pousser »
        self.codes["diff"] = 1

        ok = hub_deals_db.publier_page([], QUAND, dossier=self.dossier.name,
                                       executer=self._executer)

        self.assertTrue(ok)
        with open(os.path.join(self.dossier.name, "index.html"),
                  encoding="utf-8") as f:
            self.assertIn("<!DOCTYPE html>", f.read())
        verbes = [c[1] for c in self.commandes]
        self.assertEqual(verbes, ["add", "diff", "commit", "push"])
        self.assertEqual(self.envoyes, [])

    def test_page_inchangee_pas_de_commit_vide(self):
        self.codes["diff"] = 0  # rien d'indexe

        ok = hub_deals_db.publier_page([], QUAND, dossier=self.dossier.name,
                                       executer=self._executer)

        self.assertTrue(ok)
        self.assertNotIn("commit", [c[1] for c in self.commandes])

    def test_un_push_en_echec_alerte(self):
        self.codes["diff"] = 1
        self.codes["push"] = 1

        ok = hub_deals_db.publier_page([], QUAND, dossier=self.dossier.name,
                                       executer=self._executer)

        self.assertFalse(ok)
        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("page", self.envoyes[0].lower())

    def test_ne_leve_jamais(self):
        """Le releve est deja enregistre a ce stade."""
        ok = hub_deals_db.publier_page([], QUAND, dossier="/dossier/absent",
                                       executer=self._executer)

        self.assertFalse(ok)

    def test_une_alerte_non_partie_se_lit_au_journal(self):
        hub_deals_db.envoyer_telegram = lambda m: False
        self.codes["diff"] = 1
        self.codes["push"] = 1

        hub_deals_db.publier_page([], QUAND, dossier=self.dossier.name,
                                  executer=self._executer)

        self.assertTrue(any("NON envoyee" in l for l in self.journal))


class TestRaccordementAuReleve(unittest.TestCase):
    def test_la_page_est_publiee_meme_sans_anomalie(self):
        """publier_page_sans_risque delegue bien a publier_page.

        Ceci ne prouve PAS que verifier_et_notifier_anomalies appelle
        publier_page_sans_risque -- voir les deux tests suivants pour ca.
        """
        appels = []
        vrai_publier_page = hub_deals_db.publier_page
        # le garde-fou autouse de conftest.py (jamais_de_vraie_publication)
        # a neutralise publier_page_sans_risque pour toute la suite : ce
        # test veut justement observer sa delegation, donc il la remet en
        # place ici (publier_page reste stubbe juste en dessous, donc rien
        # de reel -- ni git, ni Telegram -- n'est declenche). Valeur avant
        # remplacement sauvee explicitement : la restauration ne repose
        # pas sur le demontage implicite de monkeypatch.
        guarde_conftest = hub_deals_db.publier_page_sans_risque
        hub_deals_db.publier_page = lambda *a, **k: appels.append(a) or True
        hub_deals_db.publier_page_sans_risque = _VRAIE_PUBLIER_PAGE_SANS_RISQUE
        try:
            hub_deals_db.publier_page_sans_risque([], QUAND)
        finally:
            hub_deals_db.publier_page = vrai_publier_page
            hub_deals_db.publier_page_sans_risque = guarde_conftest

        self.assertEqual(len(appels), 1)

    def _piloter_le_releve(self, anomalies):
        """Doublures minimales pour faire tourner verifier_et_notifier_anomalies
        sans aucun reseau, git ou Telegram reel -- meme demarche que
        TestPreparationAuMomentDeLAlerte (tests/test_liens_courts.py).
        Rend (valeurs_a_restaurer, liste_des_appels_a_publier_page_sans_risque).
        """
        noms = ("detecter_anomalies", "mesurer_rabattements", "envoyer_telegram",
                "notifier_abonnes_sans_risque", "raccourcir_liens",
                "publier_page_sans_risque", "log")
        sauve = {n: getattr(hub_deals_db, n) for n in noms}
        hub_deals_db.detecter_anomalies = lambda conn, date_collecte=None: anomalies
        hub_deals_db.mesurer_rabattements = lambda couples: {}
        hub_deals_db.envoyer_telegram = lambda msg: True
        hub_deals_db.notifier_abonnes_sans_risque = lambda groupes: None
        hub_deals_db.raccourcir_liens = lambda paires: {}
        hub_deals_db.log = lambda msg: None
        appels = []
        hub_deals_db.publier_page_sans_risque = \
            lambda groupes, quand: appels.append((groupes, quand))
        return sauve, appels

    def test_verifier_et_notifier_anomalies_publie_sans_aucune_affaire(self):
        """Cable le vrai chemin, branche « aucune anomalie » : la page
        doit quand meme etre publiee, sinon elle reste figee sur les
        prix de la veille sans le dire. Sans ce test, supprimer l'appel
        a publier_page_sans_risque dans cette branche ne ferait echouer
        aucun test."""
        sauve, appels = self._piloter_le_releve([])
        try:
            hub_deals_db.verifier_et_notifier_anomalies(None, QUAND)
        finally:
            for n, v in sauve.items():
                setattr(hub_deals_db, n, v)

        self.assertEqual(appels, [([], QUAND)])

    def test_verifier_et_notifier_anomalies_publie_avec_les_groupes(self):
        """Cable le vrai chemin, branche normale : la publication doit
        recevoir les affaires du jour regroupees, pas une liste vide.
        Sans ce test, supprimer l'appel a publier_page_sans_risque en
        fin de fonction ne ferait echouer aucun test."""
        base = {"destination": "Rome", "hub": "Abidjan", "prix_actuel": 900.0,
                "moyenne_historique": 1000.0, "baisse_pct": 10.0,
                "economie": 100.0, "rabattement_mesure": None,
                "lien": "/search/ABJ1"}
        anomalies = [dict(base, ville_depart="Dakar")]
        sauve, appels = self._piloter_le_releve(anomalies)
        try:
            hub_deals_db.verifier_et_notifier_anomalies(None, QUAND)
        finally:
            for n, v in sauve.items():
                setattr(hub_deals_db, n, v)

        self.assertEqual(len(appels), 1)
        groupes, quand = appels[0]
        self.assertEqual(quand, QUAND)
        self.assertTrue(groupes)  # les affaires du jour, pas une liste vide
