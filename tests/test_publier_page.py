"""Publication de la page sur gh-pages (spec du 2026-09-22)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db

QUAND = "2026-09-22T09:18:00+00:00"


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
