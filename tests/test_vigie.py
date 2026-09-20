"""Tests de la vigie externe (spec du 2026-09-18)."""
import contextlib
import io
import os
import sqlite3
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
                          volumes=[], envoyer=self._envoyer, maintenant=mardi)

        self.assertEqual(code, 0)
        self.assertEqual(self.envoyes, [])

    def test_un_probleme_part_sur_telegram(self):
        jeudi = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        vieux = self._releves([940] * 11,
                              datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc))

        code = vigie.main(argv=[], releves=vieux, volumes=[], envoyer=self._envoyer,
                          maintenant=jeudi)

        self.assertEqual(code, 0)
        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("relevé", self.envoyes[0])

    def test_sans_envoi_rien_ne_part(self):
        """Mode d'essai, pour la verification en conditions reelles."""
        jeudi = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        vieux = self._releves([940] * 11,
                              datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc))

        code = vigie.main(argv=["--sans-envoi"], releves=vieux, volumes=[],
                          envoyer=self._envoyer, maintenant=jeudi)

        self.assertEqual(code, 0)
        self.assertEqual(self.envoyes, [])

    def test_le_bilan_du_lundi_part_aussi(self):
        lundi = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)

        vigie.main(argv=[], releves=self._releves([940] * 11, lundi),
                   volumes=[], envoyer=self._envoyer, maintenant=lundi)

        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("semaine", self.envoyes[0])

    def test_une_ville_effondree_part_sur_telegram(self):
        """Le relevé est frais et complet : seul le critère par ville parle."""
        mardi = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)
        volumes = [{"Dakar": 200}] + [{"Dakar": 200, "Istanbul": 30}] * 10

        code = vigie.main(argv=[], releves=self._releves([940] * 11, mardi),
                          volumes=volumes, envoyer=self._envoyer,
                          maintenant=mardi)

        self.assertEqual(code, 0)
        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("Istanbul", self.envoyes[0])

    def test_le_bilan_du_lundi_se_tait_si_une_ville_est_effondree(self):
        """Un « rien à signaler » à côté d'une alerte la contredirait."""
        lundi = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)
        volumes = [{"Dakar": 200}] + [{"Dakar": 200, "Istanbul": 30}] * 10

        vigie.main(argv=[], releves=self._releves([940] * 11, lundi),
                   volumes=volumes, envoyer=self._envoyer, maintenant=lundi)

        self.assertEqual(len(self.envoyes), 1)
        self.assertNotIn("Rien à signaler", self.envoyes[0])

    def test_un_dump_illisible_fait_echouer_la_tache(self):
        """Code non nul = courriel d'echec de GitHub : la panne du critere
        par ville ne peut pas passer pour un « rien a signaler »."""
        mardi = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)

        def dump_casse():
            raise sqlite3.DatabaseError("dump tronque")

        with contextlib.redirect_stdout(io.StringIO()) as sortie:
            code = vigie.main(argv=[], releves=self._releves([940] * 11, mardi),
                              lire_volumes=dump_casse, envoyer=self._envoyer,
                              maintenant=mardi)

        self.assertEqual(code, 1)
        self.assertIn("dump tronque", sortie.getvalue())

    def test_un_dump_illisible_laisse_partir_les_alertes(self):
        """Elle echoue APRES avoir dit ce qu'elle savait : sinon la panne du
        relevé, elle, resterait muette."""
        jeudi = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        vieux = self._releves([940] * 11,
                              datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc))

        def dump_casse():
            raise sqlite3.DatabaseError("dump tronque")

        with contextlib.redirect_stdout(io.StringIO()):
            code = vigie.main(argv=[], releves=vieux, lire_volumes=dump_casse,
                              envoyer=self._envoyer, maintenant=jeudi)

        self.assertEqual(code, 1)
        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("plus de relevé", self.envoyes[0])

    def test_un_echec_d_envoi_fait_echouer_la_tache(self):
        """Sinon la panne serait doublement silencieuse. Un code non nul
        declenche le courriel d'echec de GitHub."""
        jeudi = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        vieux = self._releves([940] * 11,
                              datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc))

        code = vigie.main(argv=[], releves=vieux, volumes=[], envoyer=lambda m: False,
                          maintenant=jeudi)

        self.assertEqual(code, 1)



# Vrai debut d'un dump `sqlite3 .iterdump` : ville_depart est en DERNIERE
# colonne (ajoutee par ALTER TABLE), pas a sa place dans le CREATE TABLE.
# Un comptage a la position fixe se tromperait de colonne.
DUMP_REEL = """BEGIN TRANSACTION;
CREATE TABLE offres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_collecte TEXT NOT NULL,
            hub_origine TEXT NOT NULL,
            destination_code TEXT,
            destination_nom TEXT,
            prix_vol_hub REAL,
            rabattement REAL,
            total_estime REAL,
            date_depart TEXT,
            lien TEXT
        , ville_depart TEXT NOT NULL DEFAULT 'Dakar');
INSERT INTO offres VALUES(1,'2026-09-18 13:00:03','Abidjan','ROM','Rome',60.0,468.0,528.0,'2026-09-23','/s/1','Dakar');
INSERT INTO offres VALUES(2,'2026-09-18 13:00:03','Abidjan','PAR','Paris',70.0,468.0,538.0,'2026-09-23','/s/2','Dakar');
INSERT INTO offres VALUES(3,'2026-09-18 13:00:03','Istanbul','ROM','Rome',80.0,0.0,80.0,'2026-09-23','/s/3','Istanbul');
INSERT INTO offres VALUES(4,'2026-09-19 13:00:02','Abidjan','ROM','Rome',61.0,468.0,529.0,'2026-09-24','/s/4','Dakar');
INSERT INTO offres VALUES(5,'2026-09-19 13:00:02','Istanbul','ROM','Rome',81.0,0.0,81.0,'2026-09-24','/s/5','Istanbul');
COMMIT;"""


class TestVolumesParVille(unittest.TestCase):
    """Le volume global ne voit pas la panne d'une seule ville : la spec des
    abonnes residents note l'angle mort (~900 lignes contre 1 280 de mediane
    = 70 %, au-dessus du seuil global de 50 %)."""

    def test_compte_les_lignes_par_ville_du_plus_recent_au_plus_ancien(self):
        volumes = vigie.lire_volumes_par_ville(lire_dump=lambda: DUMP_REEL)

        self.assertEqual(volumes,
                         [{"Dakar": 1, "Istanbul": 1},
                          {"Dakar": 2, "Istanbul": 1}])

    def test_ne_lit_que_les_derniers_releves(self):
        volumes = vigie.lire_volumes_par_ville(lire_dump=lambda: DUMP_REEL,
                                               nb_releves=1)

        self.assertEqual(volumes, [{"Dakar": 1, "Istanbul": 1}])

    def test_un_dump_illisible_leve_au_lieu_de_se_taire(self):
        """Avale, l'echec de lecture rendrait [] -- donc « aucune ville
        effondree ». Se taire sur sa propre panne est exactement l'angle
        mort que ce critere ferme. C'est main qui rattrape."""
        with self.assertRaises(sqlite3.Error):
            vigie.lire_volumes_par_ville(lire_dump=lambda: "ceci n'est pas du SQL")

    def test_dit_dans_le_journal_ce_qu_elle_a_lu(self):
        """Sans cette trace, le journal du job ne distingue pas un critere
        qui s'est tu d'un critere qui n'a pas tourne."""
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            vigie.lire_volumes_par_ville(lire_dump=lambda: DUMP_REEL)

        self.assertIn("2 relevé(s), 2 ville(s)", sortie.getvalue())

    def test_lit_le_dump_de_la_branche_des_sauvegardes(self):
        commandes = []

        def faux_git(commande):
            commandes.append(commande)
            return DUMP_REEL

        vigie.lire_volumes_par_ville(executer=faux_git)

        self.assertEqual(commandes, [vigie.COMMANDE_DUMP])


class TestJugerParVille(unittest.TestCase):
    """Seuil par ville : la moitie de la mediane de CETTE ville, et non du
    total. Une ville sous MIN_LIGNES_VILLE n'est pas jugee -- a ce volume,
    une variation du cache de l'API suffirait a faire du bruit."""

    def _volumes(self, dernier, habituel, n=10):
        return [dernier] + [habituel] * n

    def test_des_villes_normales_ne_disent_rien(self):
        volumes = self._volumes({"Dakar": 212, "Istanbul": 30},
                                {"Dakar": 200, "Istanbul": 30})

        self.assertEqual(vigie.juger_par_ville(volumes), [])

    def test_une_ville_disparue_est_nommee(self):
        volumes = self._volumes({"Dakar": 212}, {"Dakar": 200, "Istanbul": 30})

        problemes = vigie.juger_par_ville(volumes)

        self.assertEqual(len(problemes), 1)
        self.assertIn("Istanbul", problemes[0])
        self.assertIn("0 ligne", problemes[0])

    def test_voit_la_panne_des_seules_lignes_residentes(self):
        """Le cas que le critere global rate. Les 8 villes qui ne partent que
        de leur propre hub tombent a 0 ; le total passe de 1 168 a 972, soit
        76 % de l'habitude -- au-dessus du seuil global de 50 %."""
        residentes = {v: 25 for v in ("Casablanca", "Paris", "Istanbul",
                                      "Addis-Abeba", "Nairobi", "Lagos",
                                      "Johannesburg", "Le Caire")}
        rabattues = {"Dakar": 212, "Kinshasa": 215, "Abidjan": 183,
                     "Brazzaville": 193, "Lome": 169}
        habituel = dict(residentes, **rabattues)

        muet = vigie.juger(
            [{"date": datetime(2026, 9, 20, 13, tzinfo=timezone.utc),
              "lignes": sum(rabattues.values())}]
            + [{"date": datetime(2026, 9, 19, 13, tzinfo=timezone.utc),
                "lignes": sum(habituel.values())}] * 10,
            datetime(2026, 9, 20, 15, tzinfo=timezone.utc))
        problemes = vigie.juger_par_ville(self._volumes(rabattues, habituel))

        self.assertEqual(muet, [], "le critere global est cense rester muet")
        self.assertEqual(len(problemes), 1)
        for ville in residentes:
            self.assertIn(ville, problemes[0])

    def test_plusieurs_villes_tiennent_dans_un_seul_message(self):
        """Une alerte par ville noierait le telephone : 13 messages pour une
        seule panne."""
        volumes = self._volumes({"Dakar": 10, "Istanbul": 2, "Lome": 169},
                                {"Dakar": 200, "Istanbul": 30, "Lome": 169})

        problemes = vigie.juger_par_ville(volumes)

        self.assertEqual(len(problemes), 1)
        self.assertNotIn("Lome", problemes[0])

    def test_une_ville_trop_petite_n_est_pas_jugee(self):
        volumes = self._volumes({"Essai": 1}, {"Essai": 8})

        self.assertEqual(vigie.juger_par_ville(volumes), [])

    def test_une_ville_nouvelle_n_alerte_pas(self):
        """Les 4 villes promues hubs le 2026-09-19 etaient absentes de tout
        l'historique : leur mediane vaut 0, rien a signaler."""
        volumes = self._volumes({"Dakar": 200, "Lome": 169}, {"Dakar": 200})

        self.assertEqual(vigie.juger_par_ville(volumes), [])

    def test_une_ville_intermittente_n_alerte_pas(self):
        """Presente dans 2 releves sur 10 : sa mediane est 0, une absence de
        plus ne prouve rien."""
        volumes = [{"Dakar": 200}] + [{"Dakar": 200, "Essai": 40}] * 2 \
            + [{"Dakar": 200}] * 8

        self.assertEqual(vigie.juger_par_ville(volumes), [])

    def test_sans_historique_suffisant_rien_n_est_juge(self):
        volumes = self._volumes({"Istanbul": 0}, {"Istanbul": 30}, n=3)

        self.assertEqual(vigie.juger_par_ville(volumes), [])

    def test_sans_volumes_rien_n_est_juge(self):
        """Dump illisible : c'est le critere global qui parle, pas celui-ci."""
        self.assertEqual(vigie.juger_par_ville([]), [])


if __name__ == "__main__":
    unittest.main()
