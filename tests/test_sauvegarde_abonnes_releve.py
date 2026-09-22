"""Raccordement de l'instantane des abonnes au releve (2026-09-22).

Meme regle que la sauvegarde hors machine : on ne notifie QUE l'echec.
Un « copie OK » quotidien deviendrait un bruit qu'on cesse de lire, et
le jour ou il manquerait, personne ne le remarquerait.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import hub_deals_db
import magasin


class TestCopieAbonnesEtAlerter(unittest.TestCase):

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.copies = os.path.join(self.dossier.name, "copies")
        self.conn = magasin.ouvrir(chemin=":memory:")
        quand = "2026-09-22T10:00:00+00:00"
        abonnes.inscrire(self.conn, 8889245697, "Sy Abou", quand)
        abonnes.choisir_ville(self.conn, 8889245697, "Abidjan", quand)

        self.envoyes = []
        self.journal = []
        self._vrai_envoyer = hub_deals_db.envoyer_telegram
        self._vrai_log = hub_deals_db.log
        hub_deals_db.envoyer_telegram = self._envoyer
        hub_deals_db.log = self.journal.append
        self._vrai_ouvrir = magasin.ouvrir
        magasin.ouvrir = lambda *a, **k: _SansFermeture(self.conn)

    def tearDown(self):
        hub_deals_db.envoyer_telegram = self._vrai_envoyer
        hub_deals_db.log = self._vrai_log
        magasin.ouvrir = self._vrai_ouvrir
        self.conn.close()
        self.dossier.cleanup()

    def _envoyer(self, message):
        self.envoyes.append(message)
        return True

    def test_ecrit_la_copie_et_n_alerte_pas(self):
        ok = hub_deals_db.copier_abonnes_et_alerter(dossier=self.copies)

        self.assertTrue(ok)
        self.assertEqual(os.listdir(self.copies),
                         [f"abonnes-{abonnes.maintenant()[:10].replace('-', '')}.json"])
        self.assertEqual(self.envoyes, [])

    def test_alerte_si_la_copie_echoue(self):
        """Une base injoignable ne doit pas faire echouer la fin du
        releve, mais elle ne doit pas passer inapercue non plus."""
        magasin.ouvrir = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("base injoignable"))

        ok = hub_deals_db.copier_abonnes_et_alerter(dossier=self.copies)

        self.assertFalse(ok)
        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("abonn", self.envoyes[0].lower())
        self.assertTrue(any("base injoignable" in l for l in self.journal))

    def test_alerte_si_le_nombre_d_abonnes_a_chute(self):
        """C'est l'incident qu'on veut voir arriver : la table videe.
        Sans ce signal, la copie du jour enregistre la perte sans que
        personne ne l'apprenne."""
        abonnes.ecrire_instantane(self.conn, self.copies,
                                  "2026-09-21T13:00:00+00:00")
        abonnes.inscrire(self.conn, 111, "efface", "2026-09-21T10:00:00+00:00")
        abonnes.ecrire_instantane(self.conn, self.copies,
                                  "2026-09-21T13:00:00+00:00")
        self.conn.execute("DELETE FROM abonnes WHERE chat_id = 111")
        self.conn.commit()

        ok = hub_deals_db.copier_abonnes_et_alerter(dossier=self.copies)

        self.assertTrue(ok)  # la copie a bien ete ecrite
        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("2", self.envoyes[0])  # l'effectif d'avant
        self.assertTrue(any("chute" in l.lower() for l in self.journal))

    def test_une_alerte_non_partie_se_lit_au_journal(self):
        """Lecon du 17/09 : le journal affirmait « envoyee » juste sous
        « message Telegram NON parti »."""
        hub_deals_db.envoyer_telegram = lambda message: False
        magasin.ouvrir = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("base injoignable"))

        hub_deals_db.copier_abonnes_et_alerter(dossier=self.copies)

        self.assertTrue(any("NON envoyee" in l for l in self.journal))


class _SansFermeture:
    """close() ne doit pas fermer la connexion partagee du test."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, nom):
        return getattr(self._conn, nom)

    def close(self):
        pass


if __name__ == "__main__":
    unittest.main()
