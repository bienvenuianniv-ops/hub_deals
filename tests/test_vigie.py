"""Tests de la vigie externe (spec du 2026-09-18)."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vigie


# Sortie reelle de :
#   git log --format=%cI --numstat -n11 origin/sauvegardes -- flight_deals.sql
GIT_LOG_REEL = """2026-09-17T13:41:54+00:00

908\t2\tflight_deals.sql
2026-09-17T09:28:55+00:00

944\t2\tflight_deals.sql
2026-09-16T13:05:07+00:00

947\t2\tflight_deals.sql
"""


class TestAnalyserGitLog(unittest.TestCase):
    def test_lit_la_date_et_le_volume_de_chaque_releve(self):
        releves = vigie.analyser_git_log(GIT_LOG_REEL)

        self.assertEqual(len(releves), 3)
        self.assertEqual(releves[0]["lignes"], 908)
        self.assertEqual(
            releves[0]["date"],
            datetime(2026, 9, 17, 13, 41, 54, tzinfo=timezone.utc))
        self.assertEqual(releves[2]["lignes"], 947)

    def test_les_dates_sont_comparables_a_une_heure_utc(self):
        """Une date sans fuseau leverait des qu'on la compare a maintenant."""
        releves = vigie.analyser_git_log(GIT_LOG_REEL)

        ecart = datetime.now(timezone.utc) - releves[0]["date"]

        self.assertIsInstance(ecart, timedelta)

    def test_une_sortie_vide_ne_fait_pas_planter(self):
        """Depot neuf ou branche absente : pas de releve, pas d'exception."""
        self.assertEqual(vigie.analyser_git_log(""), [])

    def test_un_commit_sans_ligne_de_volume_est_ignore(self):
        """Un commit qui ne touche pas au dump n'a pas de bloc --numstat."""
        texte = ("2026-09-17T13:41:54+00:00\n\n"
                 "2026-09-16T13:05:07+00:00\n\n947\t2\tflight_deals.sql\n")

        releves = vigie.analyser_git_log(texte)

        self.assertEqual([r["lignes"] for r in releves], [947])


class TestLireReleves(unittest.TestCase):
    def test_appelle_git_et_rend_les_releves(self):
        commandes = []

        def faux_git(commande):
            commandes.append(commande)
            return GIT_LOG_REEL

        releves = vigie.lire_releves(executer=faux_git)

        self.assertEqual(len(releves), 3)
        self.assertEqual(commandes, [vigie.COMMANDE_GIT])


class TestJuger(unittest.TestCase):
    """Les seuils viennent de la spec : 26 h (le releve de 13h peut prendre
    45 min un mauvais jour, et un cron GitHub glisse), et la moitie de la
    mediane des 10 precedents (mesure : 900 a 971 lignes par releve)."""

    def _releves(self, volumes, base=None, pas_h=24):
        base = base or datetime(2026, 9, 18, 13, 0, tzinfo=timezone.utc)
        return [{"date": base - timedelta(hours=pas_h * i), "lignes": v}
                for i, v in enumerate(volumes)]

    def test_un_releve_recent_et_normal_ne_dit_rien(self):
        maintenant = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)

        self.assertEqual(vigie.juger(self._releves([940] * 11), maintenant), [])

    def test_aucune_sauvegarde_depuis_30h_est_signalee(self):
        maintenant = datetime(2026, 9, 19, 19, 0, tzinfo=timezone.utc)

        problemes = vigie.juger(self._releves([940] * 11), maintenant)

        self.assertEqual(len(problemes), 1)
        self.assertIn("30 h", problemes[0])
        self.assertIn("Traqueur de vols", problemes[0])

    def test_25h_passe_encore(self):
        """Marge volontaire : un releve lent ne doit pas alerter."""
        maintenant = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)

        self.assertEqual(vigie.juger(self._releves([940] * 11), maintenant), [])

    def test_un_releve_ampute_est_signale(self):
        maintenant = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)

        problemes = vigie.juger(self._releves([400] + [940] * 10), maintenant)

        self.assertEqual(len(problemes), 1)
        self.assertIn("400", problemes[0])
        self.assertIn("940", problemes[0])

    def test_une_valeur_aberrante_dans_l_historique_ne_fausse_pas_le_verdict(self):
        """Mediane et non moyenne : le 2026-09-09 porte 1727 lignes (deux
        releves reunis dans un seul commit)."""
        maintenant = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)
        volumes = [900, 1727, 940, 940, 940, 940, 940, 940, 940, 940, 940]

        self.assertEqual(vigie.juger(self._releves(volumes), maintenant), [])

    def test_sans_historique_suffisant_le_volume_n_est_pas_juge(self):
        """Deux releves ne disent pas ce qui est normal : alerter la-dessus
        serait du bruit, pas une panne."""
        maintenant = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)

        self.assertEqual(vigie.juger(self._releves([100, 940]), maintenant), [])

    def test_aucun_releve_du_tout_est_signale(self):
        """Branche vide ou git muet : c'est une panne, pas un silence sain."""
        maintenant = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)

        problemes = vigie.juger([], maintenant)

        self.assertEqual(len(problemes), 1)
        self.assertIn("aucune sauvegarde", problemes[0].lower())

    def test_les_deux_problemes_peuvent_se_cumuler(self):
        maintenant = datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)

        problemes = vigie.juger(self._releves([300] + [940] * 10), maintenant)

        self.assertEqual(len(problemes), 2)


class TestBilanHebdo(unittest.TestCase):
    """Sans ce signe de vie, une vigie MORTE serait indiscernable d'une
    vigie qui n'a rien a dire -- exactement l'angle mort qu'elle corrige."""

    def _releves(self, n, base=None):
        base = base or datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)
        return [{"date": base - timedelta(days=i), "lignes": 940}
                for i in range(n)]

    def test_le_lundi_un_bilan_est_produit(self):
        lundi = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(lundi.weekday(), 0)

        texte = vigie.bilan_hebdo(self._releves(7), lundi)

        self.assertIsNotNone(texte)
        self.assertIn("7", texte)

    def test_les_autres_jours_rien(self):
        mardi = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)

        self.assertIsNone(vigie.bilan_hebdo(self._releves(7), mardi))

    def test_le_bilan_ne_compte_que_les_sept_derniers_jours(self):
        lundi = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)
        vieux = [{"date": datetime(2026, 8, 1, 13, 0, tzinfo=timezone.utc),
                  "lignes": 940}]

        texte = vigie.bilan_hebdo(self._releves(3) + vieux, lundi)

        self.assertIn("3", texte)

    def test_un_lundi_sans_aucun_releve_le_dit(self):
        lundi = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)

        texte = vigie.bilan_hebdo([], lundi)

        self.assertIsNotNone(texte)
        self.assertIn("0", texte)


class TestMain(unittest.TestCase):
    """L'envoi et la lecture sont injectes : aucun test ne doit toucher
    Telegram ni git (lecon du 2026-08-16 : une fonction modifiee fait
    partir de vrais appels reseau depuis les tests d'a cote)."""

    def setUp(self):
        self.envoyes = []

    def _envoyer(self, message):
        self.envoyes.append(message)
        return True

    def _releves(self, volumes, base):
        return [{"date": base - timedelta(hours=24 * i), "lignes": v}
                for i, v in enumerate(volumes)]

    def test_rien_a_signaler_rien_envoye(self):
        mardi = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)

        code = vigie.main(argv=[], releves=self._releves([940] * 11, mardi),
                          envoyer=self._envoyer, maintenant=mardi)

        self.assertEqual(code, 0)
        self.assertEqual(self.envoyes, [])

    def test_un_probleme_part_sur_telegram(self):
        jeudi = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        vieux = self._releves([940] * 11,
                              datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc))

        code = vigie.main(argv=[], releves=vieux, envoyer=self._envoyer,
                          maintenant=jeudi)

        self.assertEqual(code, 0)
        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("relevé", self.envoyes[0])

    def test_sans_envoi_rien_ne_part(self):
        """Mode d'essai, pour la verification en conditions reelles."""
        jeudi = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        vieux = self._releves([940] * 11,
                              datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc))

        code = vigie.main(argv=["--sans-envoi"], releves=vieux,
                          envoyer=self._envoyer, maintenant=jeudi)

        self.assertEqual(code, 0)
        self.assertEqual(self.envoyes, [])

    def test_le_bilan_du_lundi_part_aussi(self):
        lundi = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)

        vigie.main(argv=[], releves=self._releves([940] * 11, lundi),
                   envoyer=self._envoyer, maintenant=lundi)

        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("semaine", self.envoyes[0])

    def test_un_echec_d_envoi_fait_echouer_la_tache(self):
        """Sinon la panne serait doublement silencieuse. Un code non nul
        declenche le courriel d'echec de GitHub."""
        jeudi = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        vieux = self._releves([940] * 11,
                              datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc))

        code = vigie.main(argv=[], releves=vieux, envoyer=lambda m: False,
                          maintenant=jeudi)

        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
