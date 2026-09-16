"""Abonnes au bot : donnees, filtrage, message, envoi, temoin d'ecoute."""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import hub_deals_db

# jamais d'appel reel a l'API des liens courts depuis les tests, meme
# quand TRAVELPAYOUTS_PROJET est pose sur la machine
hub_deals_db.TRAVELPAYOUTS_PROJET = None

T0 = "2026-09-15T12:00:00+00:00"
T1 = "2026-09-15T12:05:00+00:00"


def _base():
    conn = sqlite3.connect(":memory:")
    abonnes.init_abonnes(conn)
    return conn


class TestStructure(unittest.TestCase):
    def test_les_villes_proposees_sont_exactement_celles_de_rabattement(self):
        """Une ville proposee sans entree dans RABATTEMENT ne recevrait
        jamais rien ; une ville de RABATTEMENT absente serait inaccessible."""
        self.assertEqual(set(abonnes.NOMS_AFFICHES), set(hub_deals_db.RABATTEMENT))


class TestDonneesAbonnes(unittest.TestCase):
    def setUp(self):
        self.conn = _base()

    def tearDown(self):
        self.conn.close()

    def test_init_est_idempotent(self):
        abonnes.init_abonnes(self.conn)
        abonnes.init_abonnes(self.conn)

    def test_inscription_puis_lecture(self):
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        a = abonnes.trouver(self.conn, 111)
        self.assertEqual(a["prenom"], "Awa")
        self.assertIs(a["actif"], True)
        self.assertIsNone(a["ville_depart"])
        self.assertEqual(a["inscrit_le"], T0)

    def test_inconnu(self):
        self.assertIsNone(abonnes.trouver(self.conn, 999))

    def test_choix_de_ville(self):
        ville = sorted(abonnes.NOMS_AFFICHES)[0]
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        abonnes.choisir_ville(self.conn, 111, ville, T1)
        a = abonnes.trouver(self.conn, 111)
        self.assertEqual(a["ville_depart"], ville)
        self.assertEqual(a["modifie_le"], T1)

    def test_ville_inconnue_refusee(self):
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        with self.assertRaises(ValueError):
            abonnes.choisir_ville(self.conn, 111, "Atlantide", T1)

    def test_desactivation_conserve_la_ligne(self):
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        abonnes.desactiver(self.conn, 111, "stop", T1)
        a = abonnes.trouver(self.conn, 111)
        self.assertIs(a["actif"], False)
        self.assertEqual(a["motif_inactif"], "stop")

    def test_reinscription_reactive_et_garde_la_ville(self):
        ville = sorted(abonnes.NOMS_AFFICHES)[0]
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        abonnes.choisir_ville(self.conn, 111, ville, T0)
        abonnes.desactiver(self.conn, 111, "bloque", T0)
        abonnes.inscrire(self.conn, 111, "Awa", T1)
        a = abonnes.trouver(self.conn, 111)
        self.assertIs(a["actif"], True)
        self.assertIsNone(a["motif_inactif"])
        self.assertEqual(a["ville_depart"], ville)
        self.assertEqual(a["inscrit_le"], T0)

    def test_nb_actifs(self):
        abonnes.inscrire(self.conn, 1, "a", T0)
        abonnes.inscrire(self.conn, 2, "b", T0)
        abonnes.desactiver(self.conn, 2, "stop", T0)
        self.assertEqual(abonnes.nb_actifs(self.conn), 1)

    def test_abonnes_a_servir_ignore_inactifs_et_sans_ville(self):
        ville = sorted(abonnes.NOMS_AFFICHES)[0]
        for chat_id in (1, 2, 3):
            abonnes.inscrire(self.conn, chat_id, "x", T0)
        abonnes.choisir_ville(self.conn, 1, ville, T0)
        abonnes.choisir_ville(self.conn, 2, ville, T0)
        abonnes.desactiver(self.conn, 2, "stop", T0)
        # 3 n'a pas choisi de ville
        self.assertEqual([a["chat_id"] for a in abonnes.abonnes_a_servir(self.conn)], [1])

    def test_abonnes_a_servir_exclut_le_proprietaire(self):
        """Le proprietaire recoit deja le message complet : pas de doublon.
        TELEGRAM_CHAT_ID est une chaine, chat_id un entier."""
        ville = sorted(abonnes.NOMS_AFFICHES)[0]
        for chat_id in (1, 2):
            abonnes.inscrire(self.conn, chat_id, "x", T0)
            abonnes.choisir_ville(self.conn, chat_id, ville, T0)
        servis = abonnes.abonnes_a_servir(self.conn, exclure_chat_id="2")
        self.assertEqual([a["chat_id"] for a in servis], [1])


def _anomalie(ville, dest="Sao Paulo", hub="Abidjan", prix=1716.0, baisse=23.4,
              economie=500.0, lien="/search/ABJ0302SAO1", mesure=385.0):
    return {"destination": dest, "hub": hub, "ville_depart": ville,
            "prix_actuel": prix, "moyenne_historique": prix + economie,
            "baisse_pct": baisse, "economie": economie,
            "rabattement_mesure": mesure, "lien": lien}


class TestMessageAbonne(unittest.TestCase):
    def setUp(self):
        self._marker = hub_deals_db.TRAVELPAYOUTS_MARKER
        hub_deals_db.TRAVELPAYOUTS_MARKER = "123456"
        villes = sorted(abonnes.NOMS_AFFICHES)
        self.v1, self.v2 = villes[0], villes[1]

    def tearDown(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = self._marker

    def test_filtre_un_groupe_multi_villes_a_la_bonne_ligne(self):
        groupes = [[_anomalie(self.v1, prix=1716), _anomalie(self.v2, prix=2327)]]
        filtres = abonnes.filtrer_groupes(groupes, self.v2)
        self.assertEqual(len(filtres), 1)
        self.assertEqual([a["ville_depart"] for a in filtres[0]], [self.v2])

    def test_retire_les_groupes_sans_la_ville(self):
        groupes = [[_anomalie(self.v1)], [_anomalie(self.v2, dest="Rome", lien="/r")]]
        filtres = abonnes.filtrer_groupes(groupes, self.v1)
        self.assertEqual([g[0]["destination"] for g in filtres], ["Sao Paulo"])

    def test_aucune_affaire_aucun_message(self):
        self.assertEqual(abonnes.messages_abonne([[_anomalie(self.v1)]], self.v2), [])

    def test_contenu_du_message(self):
        [m] = abonnes.messages_abonne([[_anomalie(self.v1)]], self.v1)
        self.assertIn(f"<b>1 bonne affaire au départ de {abonnes.NOMS_AFFICHES[self.v1]}</b>", m)
        self.assertIn("<b>Sao Paulo</b> via Abidjan", m)
        self.assertIn("1716€ (-23%)", m)
        self.assertIn("500€ de moins que d'habitude", m)
        self.assertIn(f"?marker=123456.{self.v1.lower()}", m)
        self.assertTrue(m.endswith(abonnes.MENTION_PRIX))

    def test_pluriel(self):
        groupes = [[_anomalie(self.v1)], [_anomalie(self.v1, dest="Rome", lien="/r")]]
        [m] = abonnes.messages_abonne(groupes, self.v1)
        self.assertIn("<b>2 bonnes affaires au départ de", m)

    def test_aucun_detail_de_rabattement(self):
        """Information technique reservee au proprietaire."""
        for mesure in (385.0, None):
            [m] = abonnes.messages_abonne([[_anomalie(self.v1, mesure=mesure)]], self.v1)
            self.assertNotIn("rabattement", m.lower())

    def test_la_mention_est_dans_chaque_morceau(self):
        groupes = [[_anomalie(self.v1, dest="Destination %d" % i, lien="/s%d" % i)]
                   for i in range(80)]
        morceaux = abonnes.messages_abonne(groupes, self.v1)
        self.assertGreater(len(morceaux), 1)
        for m in morceaux:
            self.assertIn(abonnes.MENTION_PRIX, m)
            self.assertLessEqual(len(m), hub_deals_db.LIMITE_TELEGRAM)


class TestNotifierAbonnes(unittest.TestCase):
    def setUp(self):
        self.conn = _base()
        self._marker = hub_deals_db.TRAVELPAYOUTS_MARKER
        hub_deals_db.TRAVELPAYOUTS_MARKER = None
        villes = sorted(abonnes.NOMS_AFFICHES)
        self.v1, self.v2 = villes[0], villes[1]
        self.lignes = []
        self.envois = []
        self.pauses = []
        self.groupes = [[_anomalie(self.v1)]]

    def tearDown(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = self._marker
        self.conn.close()

    def _abonne(self, chat_id, ville):
        abonnes.inscrire(self.conn, chat_id, "x", T0)
        abonnes.choisir_ville(self.conn, chat_id, ville, T0)

    def _notifier(self, reponses=None, **kw):
        """reponses : chat_id -> liste de statuts renvoyes dans l'ordre."""
        reponses = reponses or {}

        def envoyer(chat_id, message):
            self.envois.append((chat_id, message))
            file = reponses.get(chat_id)
            return file.pop(0) if file else ("ok", None)

        return abonnes.notifier_abonnes(
            self.conn, self.groupes, envoyer, self.lignes.append,
            dormir=self.pauses.append, **kw)

    def test_seuls_les_abonnes_de_la_ville_recoivent(self):
        self._abonne(1, self.v1)
        self._abonne(2, self.v2)
        compte = self._notifier()
        self.assertEqual([c for c, _ in self.envois], [1])
        self.assertEqual(compte["envoyes"], 1)
        self.assertEqual(compte["sans_affaire"], 1)

    def test_le_proprietaire_n_est_pas_servi_en_double(self):
        self._abonne(1, self.v1)
        self._abonne(2, self.v1)
        self._notifier(exclure_chat_id="2")
        self.assertEqual([c for c, _ in self.envois], [1])

    def test_403_desactive_l_abonne(self):
        self._abonne(1, self.v1)
        compte = self._notifier({1: [("bloque", None)]})
        self.assertEqual(compte["bloques"], 1)
        a = abonnes.trouver(self.conn, 1)
        self.assertIs(a["actif"], False)
        self.assertEqual(a["motif_inactif"], "bloque")

    def test_429_attend_puis_un_seul_nouvel_essai(self):
        self._abonne(1, self.v1)
        compte = self._notifier({1: [("trop_vite", 7), ("trop_vite", 7)]})
        self.assertEqual(len(self.envois), 2)
        self.assertIn(7, self.pauses)
        self.assertEqual(compte["echecs"], ["trop de requetes"])

    def test_429_puis_succes(self):
        self._abonne(1, self.v1)
        compte = self._notifier({1: [("trop_vite", 3), ("ok", None)]})
        self.assertEqual(compte["envoyes"], 1)
        self.assertEqual(compte["echecs"], [])

    def test_un_echec_n_arrete_pas_les_suivants(self):
        self._abonne(1, self.v1)
        self._abonne(2, self.v1)
        compte = self._notifier({1: [("echec", "HTTP 400")]})
        self.assertEqual([c for c, _ in self.envois], [1, 2])
        self.assertEqual(compte["envoyes"], 1)
        self.assertEqual(compte["echecs"], ["HTTP 400"])

    def test_une_exception_inattendue_n_arrete_pas_les_suivants(self):
        self._abonne(1, self.v1)
        self._abonne(2, self.v1)

        def envoyer(chat_id, message):
            if chat_id == 1:
                raise RuntimeError("panne")
            self.envois.append((chat_id, message))
            return ("ok", None)

        compte = abonnes.notifier_abonnes(self.conn, self.groupes, envoyer,
                                          self.lignes.append, dormir=self.pauses.append)
        self.assertEqual([c for c, _ in self.envois], [2])
        self.assertEqual(len(compte["echecs"]), 1)

    def test_pause_entre_deux_envois(self):
        self._abonne(1, self.v1)
        self._abonne(2, self.v1)
        self._notifier()
        self.assertIn(0.05, self.pauses)

    def test_compte_rendu_exact(self):
        self._abonne(1, self.v1)
        self._abonne(2, self.v1)
        self._abonne(3, self.v1)
        self._abonne(4, self.v2)
        self._notifier({2: [("bloque", None)], 3: [("echec", "HTTP 400")]})
        self.assertEqual(
            self.lignes,
            ["Abonnes : 1/3 envoye(s), 1 bloque(s), 1 echec(s) (HTTP 400), 1 sans affaire."])

    def test_compte_rendu_sans_abonne(self):
        self._notifier()
        self.assertEqual(self.lignes, ["Abonnes : aucun abonne a servir."])


class TestReleveNotifieLesAbonnes(unittest.TestCase):
    """Le message du proprietaire part en premier, et rien cote abonnes ne
    peut l'empecher ni interrompre le releve."""

    def setUp(self):
        self.ordre = []
        self.lignes = []
        self._sauve = {n: getattr(hub_deals_db, n) for n in (
            "log", "envoyer_telegram", "envoyer_telegram_a", "mesurer_rabattements",
            "detecter_anomalies", "TELEGRAM_CHAT_ID", "TRAVELPAYOUTS_MARKER")}
        hub_deals_db.log = self.lignes.append
        hub_deals_db.TELEGRAM_CHAT_ID = "999"
        hub_deals_db.TRAVELPAYOUTS_MARKER = None
        hub_deals_db.mesurer_rabattements = lambda couples: {}
        self.ville = sorted(abonnes.NOMS_AFFICHES)[0]
        hub_deals_db.detecter_anomalies = (
            lambda conn, date_collecte=None: [_anomalie(self.ville, mesure=None)])
        hub_deals_db.envoyer_telegram = lambda msg: self.ordre.append("proprietaire") or True

        def envoyer_a(chat_id, message, journaliser=True):
            self.ordre.append(chat_id)
            return ("ok", None)
        hub_deals_db.envoyer_telegram_a = envoyer_a

        self.conn = _base()
        for chat_id in (1, 999):
            abonnes.inscrire(self.conn, chat_id, "x", T0)
            abonnes.choisir_ville(self.conn, chat_id, self.ville, T0)

    def tearDown(self):
        for n, v in self._sauve.items():
            setattr(hub_deals_db, n, v)
        self.conn.close()

    def test_proprietaire_d_abord_puis_abonnes_sans_doublon(self):
        hub_deals_db.verifier_et_notifier_anomalies(self.conn, "2026-09-15")
        self.assertEqual(self.ordre, ["proprietaire", 1])

    def test_une_panne_cote_abonnes_n_interrompt_rien(self):
        def exploser(*a, **k):
            raise RuntimeError("panne abonnes")
        hub_deals_db.envoyer_telegram_a = exploser
        original = abonnes.abonnes_a_servir
        abonnes.abonnes_a_servir = exploser
        try:
            hub_deals_db.verifier_et_notifier_anomalies(self.conn, "2026-09-15")
        finally:
            abonnes.abonnes_a_servir = original
        self.assertEqual(self.ordre, ["proprietaire"])
        self.assertIn("envoi aux abonnes impossible", "\n".join(self.lignes))

    def test_une_base_sans_table_abonnes_ne_casse_pas(self):
        conn = sqlite3.connect(":memory:")
        hub_deals_db.verifier_et_notifier_anomalies(conn, "2026-09-15")
        self.assertEqual(self.ordre, ["proprietaire"])
        self.assertIn("Abonnes : aucun abonne a servir.", self.lignes)

    def test_le_releve_attend_la_base_occupee_par_l_ecoute(self):
        import inspect
        bloc = inspect.getsource(hub_deals_db).split('if __name__ == "__main__":')[1]
        self.assertIn("sqlite3.connect(DB_PATH, timeout=30)", bloc)


class TestTemoinEcoute(unittest.TestCase):
    def setUp(self):
        self.conn = _base()

    def tearDown(self):
        self.conn.close()

    def test_temoin_recent(self):
        abonnes.noter_ecoute(self.conn, "2026-09-15T12:55:00+00:00")
        self.assertFalse(abonnes.ecoute_muette(self.conn, "2026-09-15T13:04:00+00:00"))

    def test_temoin_ancien(self):
        abonnes.noter_ecoute(self.conn, "2026-09-15T12:50:00+00:00")
        self.assertTrue(abonnes.ecoute_muette(self.conn, "2026-09-15T13:04:00+00:00"))

    def test_temoin_mis_a_jour(self):
        abonnes.noter_ecoute(self.conn, "2026-09-15T08:00:00+00:00")
        abonnes.noter_ecoute(self.conn, "2026-09-15T13:00:00+00:00")
        self.assertFalse(abonnes.ecoute_muette(self.conn, "2026-09-15T13:04:00+00:00"))

    def test_temoin_absent_sans_abonne_n_alerte_pas(self):
        """Bot jamais installe : rien a surveiller."""
        self.assertFalse(abonnes.ecoute_muette(self.conn, T0))

    def test_temoin_absent_avec_abonnes_alerte(self):
        abonnes.inscrire(self.conn, 1, "x", T0)
        self.assertTrue(abonnes.ecoute_muette(self.conn, T1))


class TestAlerteEcouteArretee(unittest.TestCase):
    def setUp(self):
        self.envois = []
        self.lignes = []
        self._log = hub_deals_db.log
        self._envoyer = hub_deals_db.envoyer_telegram
        hub_deals_db.log = self.lignes.append
        hub_deals_db.envoyer_telegram = lambda msg: self.envois.append(msg) or True
        self.conn = _base()

    def tearDown(self):
        hub_deals_db.log = self._log
        hub_deals_db.envoyer_telegram = self._envoyer
        self.conn.close()

    def test_alerte_si_ecoute_muette(self):
        abonnes.noter_ecoute(self.conn, "2020-01-01T00:00:00+00:00")
        hub_deals_db.verifier_ecoute_et_alerter(self.conn)
        self.assertEqual(len(self.envois), 1)
        self.assertIn("ecoute du bot est arretee", self.envois[0])

    def test_rien_si_ecoute_vivante(self):
        abonnes.noter_ecoute(self.conn, abonnes.maintenant())
        hub_deals_db.verifier_ecoute_et_alerter(self.conn)
        self.assertEqual(self.envois, [])

    def test_ne_leve_jamais(self):
        hub_deals_db.verifier_ecoute_et_alerter(None)
        self.assertEqual(self.envois, [])

    def test_appele_avant_la_fin_du_releve(self):
        import inspect
        bloc = inspect.getsource(hub_deals_db).split('if __name__ == "__main__":')[1]
        self.assertLess(bloc.index("verifier_ecoute_et_alerter(conn)"),
                        bloc.index("=== Fin d'execution ==="))


if __name__ == "__main__":
    unittest.main()
