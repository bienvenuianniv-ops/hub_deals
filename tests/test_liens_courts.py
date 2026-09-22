"""Liens courts Travelpayouts (API links/v1/create).

Constat du 2026-09-16 : un lien direct aviasales.com?marker=... n'est PAS
compte comme clic dans le tableau de bord (0 clic pour celui du 15/09),
alors qu'un lien court aviasales.tpk.ro l'est (1 clic le jour meme).
"""

import os
import sys
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import hub_deals_db


class _Reponse:
    def __init__(self, status_code, corps):
        self.status_code = status_code
        self._corps = corps
        self.text = str(corps)

    def json(self):
        return self._corps


def _succes(liens_envoyes):
    return _Reponse(200, {"code": "success", "status": 200, "result": {
        "links": [{"url": l["url"], "code": "success",
                   "partner_url": f"https://aviasales.tpk.ro/{l['sub_id']}{i}"}
                  for i, l in enumerate(liens_envoyes)]}})


class _Base(unittest.TestCase):
    def setUp(self):
        self._sauve = {n: getattr(hub_deals_db, n) for n in (
            "TRAVELPAYOUTS_MARKER", "TRAVELPAYOUTS_PROJET", "TOKEN", "log",
            "LIENS_COURTS")}
        hub_deals_db.TRAVELPAYOUTS_MARKER = "123456"
        hub_deals_db.TRAVELPAYOUTS_PROJET = "574520"
        hub_deals_db.TOKEN = "jeton-de-test"
        hub_deals_db.LIENS_COURTS = {}
        self.lignes = []
        hub_deals_db.log = self.lignes.append
        self.appels = []

    def tearDown(self):
        for n, v in self._sauve.items():
            setattr(hub_deals_db, n, v)

    def poster_succes(self, url, json=None, headers=None, timeout=None):
        self.appels.append({"url": url, "json": json, "headers": headers,
                            "timeout": timeout})
        return _succes(json["links"])


class TestRaccourcirLiens(_Base):
    def test_requete_conforme_a_la_documentation(self):
        hub_deals_db.raccourcir_liens([("/search/DKR2510BKK1", "dakar")],
                                      poster=self.poster_succes)
        [appel] = self.appels
        self.assertEqual(appel["url"], "https://api.travelpayouts.com/links/v1/create")
        self.assertEqual(appel["headers"], {"X-Access-Token": "jeton-de-test"})
        self.assertIsNotNone(appel["timeout"])
        self.assertEqual(appel["json"], {
            "trs": 574520, "marker": 123456, "shorten": True,
            "links": [{"url": "https://www.aviasales.com/search/DKR2510BKK1",
                       "sub_id": "dakar"}]})

    def test_associe_chaque_lien_court_a_sa_paire(self):
        paires = [("/search/A1", "dakar"), ("/search/A1", "proprietaire")]
        courts = hub_deals_db.raccourcir_liens(paires, poster=self.poster_succes)
        self.assertEqual(courts, {
            ("/search/A1", "dakar"): "https://aviasales.tpk.ro/dakar0",
            ("/search/A1", "proprietaire"): "https://aviasales.tpk.ro/proprietaire1"})

    def test_au_plus_10_liens_par_requete(self):
        paires = [(f"/search/X{i}", "dakar") for i in range(23)]
        courts = hub_deals_db.raccourcir_liens(paires, poster=self.poster_succes)
        self.assertEqual([len(a["json"]["links"]) for a in self.appels], [10, 10, 3])
        self.assertEqual(len(courts), 23)

    def test_les_doublons_ne_sont_demandes_qu_une_fois(self):
        paires = [("/search/A1", "dakar")] * 3
        hub_deals_db.raccourcir_liens(paires, poster=self.poster_succes)
        self.assertEqual(len(self.appels[0]["json"]["links"]), 1)

    def test_un_echec_par_lien_sous_http_200_est_ignore(self):
        """La doc : un lien peut echouer (« trs is not subscribed for brand »)
        alors que la requete entiere repond success/200."""
        def poster(url, json=None, headers=None, timeout=None):
            return _Reponse(200, {"code": "success", "status": 200, "result": {"links": [
                {"url": "u1", "code": "failed", "message": "trs is not subscribed for brand",
                 "partner_url": ""},
                {"url": "u2", "code": "success", "partner_url": "https://aviasales.tpk.ro/ok"}]}})
        courts = hub_deals_db.raccourcir_liens(
            [("/search/A1", "dakar"), ("/search/B2", "dakar")], poster=poster)
        self.assertEqual(courts, {("/search/B2", "dakar"): "https://aviasales.tpk.ro/ok"})
        self.assertIn("trs is not subscribed for brand", "\n".join(self.lignes))

    def test_un_refus_http_n_empeche_pas_les_lots_suivants(self):
        reponses = [_Reponse(401, {}), None]

        def poster(url, json=None, headers=None, timeout=None):
            r = reponses.pop(0)
            return r if r is not None else _succes(json["links"])
        paires = [(f"/search/X{i}", "dakar") for i in range(12)]
        courts = hub_deals_db.raccourcir_liens(paires, poster=poster)
        self.assertEqual(len(courts), 2)
        self.assertIn("401", "\n".join(self.lignes))

    def test_une_erreur_reseau_ne_leve_pas(self):
        def poster(*a, **k):
            raise requests.ConnectionError("coupure")
        courts = hub_deals_db.raccourcir_liens([("/search/A1", "dakar")], poster=poster)
        self.assertEqual(courts, {})
        self.assertIn("coupure", "\n".join(self.lignes))

    def test_reponse_de_longueur_inattendue_ignoree(self):
        """L'association se fait par position : si le compte ne tombe pas
        juste, on ne peut pas savoir quel lien court va a quel abonne."""
        def poster(url, json=None, headers=None, timeout=None):
            return _succes(json["links"][:1])
        courts = hub_deals_db.raccourcir_liens(
            [("/search/A1", "dakar"), ("/search/A1", "lome")], poster=poster)
        self.assertEqual(courts, {})

    def test_sans_projet_ni_marker_aucun_appel(self):
        for projet, marker in ((None, "123456"), ("574520", None)):
            hub_deals_db.TRAVELPAYOUTS_PROJET = projet
            hub_deals_db.TRAVELPAYOUTS_MARKER = marker
            self.assertEqual(hub_deals_db.raccourcir_liens(
                [("/search/A1", "dakar")], poster=self.poster_succes), {})
        self.assertEqual(self.appels, [])


class TestUrlAviasalesPrefereLeLienCourt(_Base):
    def test_lien_court_disponible(self):
        hub_deals_db.LIENS_COURTS = {("/search/A1", "dakar"): "https://aviasales.tpk.ro/xyz"}
        self.assertEqual(hub_deals_db.url_aviasales("/search/A1", "dakar"),
                         "https://aviasales.tpk.ro/xyz")

    def test_sinon_lien_direct_inchange(self):
        hub_deals_db.LIENS_COURTS = {("/search/A1", "dakar"): "https://aviasales.tpk.ro/xyz"}
        self.assertEqual(hub_deals_db.url_aviasales("/search/A1", "lome"),
                         "https://www.aviasales.com/search/A1?marker=123456.lome")


class TestPreparationAuMomentDeLAlerte(_Base):
    def setUp(self):
        super().setUp()
        self._orig = {n: getattr(hub_deals_db, n) for n in (
            "envoyer_telegram", "mesurer_rabattements", "detecter_anomalies",
            "notifier_abonnes_sans_risque", "raccourcir_liens")}
        self.messages = []
        hub_deals_db.envoyer_telegram = lambda msg: self.messages.append(msg) or True
        hub_deals_db.mesurer_rabattements = lambda couples: {}
        self.vus_par_abonnes = []
        hub_deals_db.notifier_abonnes_sans_risque = lambda groupes: \
            self.vus_par_abonnes.append(hub_deals_db.url_aviasales("/search/ABJ1", "lome"))
        base = {"destination": "Rome", "hub": "Abidjan", "prix_actuel": 900.0,
                "moyenne_historique": 1000.0, "baisse_pct": 10.0, "economie": 100.0,
                "rabattement_mesure": None, "lien": "/search/ABJ1"}
        hub_deals_db.detecter_anomalies = lambda conn, date_collecte=None: [
            dict(base, ville_depart="Dakar"), dict(base, ville_depart="Lome")]
        self.paires = []

        def raccourcir(paires):
            self.paires.extend(paires)
            return {p: f"https://aviasales.tpk.ro/{p[1]}" for p in paires}
        hub_deals_db.raccourcir_liens = raccourcir

    def tearDown(self):
        for n, v in self._orig.items():
            setattr(hub_deals_db, n, v)
        super().tearDown()

    def test_paires_du_proprietaire_et_de_chaque_ville(self):
        hub_deals_db.verifier_et_notifier_anomalies(None, "2026-09-16")
        self.assertEqual(set(self.paires), {
            ("/search/ABJ1", "proprietaire"), ("/search/ABJ1", "dakar"),
            ("/search/ABJ1", "lome"), ("/search/ABJ1", "page_dakar"),
            ("/search/ABJ1", "page_lome")})

    def test_le_proprietaire_et_les_abonnes_recoivent_le_lien_court(self):
        hub_deals_db.verifier_et_notifier_anomalies(None, "2026-09-16")
        self.assertIn("https://aviasales.tpk.ro/proprietaire", self.messages[0])
        self.assertNotIn("marker=", self.messages[0])
        self.assertEqual(self.vus_par_abonnes, ["https://aviasales.tpk.ro/lome"])

    def test_une_panne_du_raccourcissement_n_empeche_pas_l_alerte(self):
        def en_panne(paires):
            raise RuntimeError("imprevu")
        hub_deals_db.raccourcir_liens = en_panne
        hub_deals_db.verifier_et_notifier_anomalies(None, "2026-09-16")
        self.assertIn("marker=123456.proprietaire", self.messages[0])
        self.assertIn("imprevu", "\n".join(self.lignes))


class TestEtiquetteCoherenteEntreLesDeuxSites(_Base):
    """Le SubID est fabrique a deux endroits : hub_deals_db.py:490 (paires
    envoyees a l'API pour creer les liens courts, dans preparer_liens_courts)
    et abonnes.py:169 (cle de recherche dans LIENS_COURTS au moment de
    composer le message de l'abonne, dans _bloc_abonne). Ce test verifie
    qu'un lien court cree sous l'etiquette du premier site est bien
    retrouve sous la cle du second, pour une ville a nom compose (« Le
    Caire »). S'ils divergent, le lien court existe dans LIENS_COURTS sous
    une cle que personne ne relit : l'abonne recoit alors le lien direct
    de repli (marker=...), dont le 2026-09-16 a etabli qu'il n'est PAS
    compte comme clic."""

    def setUp(self):
        super().setUp()
        self._orig_raccourcir = hub_deals_db.raccourcir_liens

        def raccourcir(paires):
            # simule l'API : un lien court par paire (chemin, etiquette)
            # demandee, quelle que soit l'etiquette recue
            return {p: f"https://aviasales.tpk.ro/{i}" for i, p in enumerate(paires)}
        hub_deals_db.raccourcir_liens = raccourcir

    def tearDown(self):
        hub_deals_db.raccourcir_liens = self._orig_raccourcir
        super().tearDown()

    def test_lien_court_d_une_ville_composee_retrouve_dans_le_message(self):
        groupe = [{"destination": "Paris", "hub": "Le Caire", "ville_depart": "Le Caire",
                   "prix_actuel": 300.0, "moyenne_historique": 400.0,
                   "baisse_pct": 25.0, "economie": 100.0,
                   "rabattement_mesure": None, "lien": "/search/CAI1710FIH1"}]
        # site 1 : cree les liens courts (hub_deals_db.py:490)
        hub_deals_db.preparer_liens_courts([groupe])
        # site 2 : compose le message de l'abonne (abonnes.py:169)
        [m] = abonnes.messages_abonne([groupe], "Le Caire")
        self.assertIn("https://aviasales.tpk.ro/", m)
        # si les deux sites divergeaient, le lien court ne serait jamais
        # retrouve et on retomberait sur le lien direct marker=...
        self.assertNotIn("marker=", m)


if __name__ == "__main__":
    unittest.main()
