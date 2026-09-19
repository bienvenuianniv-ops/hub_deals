import sqlite3
import sys
import os
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db

# jamais d'appel reel a l'API des liens courts depuis les tests, meme
# quand TRAVELPAYOUTS_PROJET est pose sur la machine
hub_deals_db.TRAVELPAYOUTS_PROJET = None


class TestInitDbMigration(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def _colonnes(self):
        return [row[1] for row in self.conn.execute("PRAGMA table_info(offres)")]

    def test_cree_la_table_avec_colonne_ville_depart(self):
        hub_deals_db.init_db(self.conn)
        self.assertIn("ville_depart", self._colonnes())

    def test_idempotent_sur_une_base_deja_a_jour(self):
        hub_deals_db.init_db(self.conn)
        hub_deals_db.init_db(self.conn)  # ne doit pas lever d'erreur
        self.assertIn("ville_depart", self._colonnes())

    def test_migre_une_table_existante_sans_ville_depart(self):
        # simule l'ancien schema (avant la colonne ville_depart)
        self.conn.execute("""
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
            )
        """)
        self.conn.execute("""
            INSERT INTO offres (date_collecte, hub_origine, destination_code, total_estime)
            VALUES ('2026-07-21 10:00:00', 'Casablanca', 'SID', 612)
        """)
        self.conn.commit()

        hub_deals_db.init_db(self.conn)

        self.assertIn("ville_depart", self._colonnes())
        ville = self.conn.execute("SELECT ville_depart FROM offres WHERE destination_code = 'SID'").fetchone()[0]
        self.assertEqual(ville, "Dakar")


class TestEnregistrerPrixMultiVilles(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)

        # sauvegarde la config reelle, remplacee par une config de test
        self._hubs_original = hub_deals_db.HUBS
        self._rabattement_original = hub_deals_db.RABATTEMENT
        hub_deals_db.HUBS = {"CMN": {"nom": "Casablanca"}}
        hub_deals_db.RABATTEMENT = {
            "Dakar": {"CMN": {"prix": 400, "duree_h": 4}},
            "Abidjan": {"CMN": {"prix": 150, "duree_h": 2}},
        }

    def tearDown(self):
        hub_deals_db.HUBS = self._hubs_original
        hub_deals_db.RABATTEMENT = self._rabattement_original
        self.conn.close()

    def _offre_test(self, prix=100):
        # forme renvoyee par v1/prices/cheap pour UNE route
        return {
            "price": prix,
            "airline": "AT",
            "departure_at": "2026-09-01T10:00:00+00:00",
            "return_at": "2026-09-15T10:00:00+00:00",
        }

    def test_insere_une_ligne_par_ville_ayant_un_cout_pour_ce_hub(self):
        lignes_inserees = hub_deals_db.enregistrer_prix(
            self.conn, "CMN", "SID", self._offre_test(), "2026-08-03 12:00:00")

        lignes = self.conn.execute(
            "SELECT ville_depart, total_estime FROM offres ORDER BY ville_depart"
        ).fetchall()

        self.assertEqual(lignes_inserees, 2)
        self.assertEqual(len(lignes), 2)
        self.assertEqual(lignes, [("Abidjan", 250.0), ("Dakar", 500.0)])

    def test_ignore_une_ville_sans_cout_defini_pour_ce_hub(self):
        hub_deals_db.RABATTEMENT["Nairobi"] = {}  # aucune entree pour CMN

        hub_deals_db.enregistrer_prix(
            self.conn, "CMN", "SID", self._offre_test(), "2026-08-03 12:00:00")

        villes = [row[0] for row in self.conn.execute("SELECT ville_depart FROM offres")]
        self.assertNotIn("Nairobi", villes)
        self.assertEqual(len(villes), 2)

    def test_n_interroge_l_api_qu_une_fois_par_couple_hub_destination(self):
        """La ligne par ville est produite a partir d'UNE seule offre :
        ajouter une ville de depart ne doit pas multiplier les appels API."""
        hub_deals_db.RABATTEMENT["Lome"] = {"CMN": {"prix": 300, "duree_h": 4}}

        lignes_inserees = hub_deals_db.enregistrer_prix(
            self.conn, "CMN", "SID", self._offre_test(), "2026-08-03 12:00:00")

        self.assertEqual(lignes_inserees, 3)

    def test_n_insere_pas_de_ligne_vers_la_ville_de_depart_elle_meme(self):
        """Une destination egale a la ville de depart donne une route
        absurde -- « Dakar via Casablanca -> Dakar ». Les autres villes,
        pour qui cette destination est un vrai voyage, restent inserees."""
        lignes_inserees = hub_deals_db.enregistrer_prix(
            self.conn, "CMN", "DKR", self._offre_test(), "2026-08-03 12:00:00")

        villes = [row[0] for row in self.conn.execute("SELECT ville_depart FROM offres")]

        self.assertNotIn("Dakar", villes)
        self.assertEqual(villes, ["Abidjan"])
        self.assertEqual(lignes_inserees, 1)


class TestNotificationMentionneLaVille(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)
        self._envoyer_telegram_original = hub_deals_db.envoyer_telegram
        self.messages_envoyes = []
        hub_deals_db.envoyer_telegram = self.messages_envoyes.append

        # evite de polluer le vrai flight_deals_log.txt avec des lignes de
        # test (verifier_et_notifier_anomalies appelle log() a la fin)
        self._log_original = hub_deals_db.log
        self.messages_logges = []
        hub_deals_db.log = self.messages_logges.append

        # verifier_et_notifier_anomalies mesure desormais le rabattement
        # reel avant d'envoyer : sans ce remplacement, ce test ferait un
        # VRAI appel a l'API Travelpayouts, avec sa pause de 0,4 s.
        self._mesurer_original = hub_deals_db.mesurer_rabattements
        hub_deals_db.mesurer_rabattements = lambda couples, **kw: {}

    def tearDown(self):
        hub_deals_db.envoyer_telegram = self._envoyer_telegram_original
        hub_deals_db.log = self._log_original
        hub_deals_db.mesurer_rabattements = self._mesurer_original
        self.conn.close()

    def test_le_message_mentionne_la_ville_de_depart(self):
        for total, date in [(600, "2026-08-01 10:00:00"), (600, "2026-08-02 10:00:00"), (500, "2026-08-03 10:00:00")]:
            self.conn.execute("""
                INSERT INTO offres (
                    date_collecte, ville_depart, hub_origine, destination_code,
                    destination_nom, prix_vol_hub, rabattement, total_estime, date_depart, lien
                ) VALUES (?, 'Dakar', 'Casablanca', 'SID', 'Sal', 0, 0, ?, '2026-09-01T10:00:00+00:00', '/search/x')
            """, (date, total))
        self.conn.commit()

        hub_deals_db.verifier_et_notifier_anomalies(self.conn, "2026-08-03 10:00:00")

        self.assertEqual(len(self.messages_envoyes), 1)
        self.assertIn("Dakar", self.messages_envoyes[0])


class TestMasquageDesSecrets(unittest.TestCase):
    """Sur erreur reseau, requests place l'URL complete dans l'exception --
    token d'API compris. Le message part ensuite dans le log, ou il reste
    en clair. Le masquage se fait dans log(), point de passage unique."""

    def setUp(self):
        self._token_original = hub_deals_db.TOKEN
        self._bot_original = hub_deals_db.TELEGRAM_BOT_TOKEN
        hub_deals_db.TOKEN = "secret_travelpayouts_abc123"
        hub_deals_db.TELEGRAM_BOT_TOKEN = "8000000:secret_bot_xyz789"

    def tearDown(self):
        hub_deals_db.TOKEN = self._token_original
        hub_deals_db.TELEGRAM_BOT_TOKEN = self._bot_original

    def test_masque_le_token_travelpayouts(self):
        message = hub_deals_db.masquer_secrets(
            "ERREUR reseau : .../v1/prices/cheap?origin=CMN&token=secret_travelpayouts_abc123")

        self.assertNotIn("secret_travelpayouts_abc123", message)
        self.assertIn("token=***", message)

    def test_masque_le_token_du_bot_telegram(self):
        message = hub_deals_db.masquer_secrets(
            "ERREUR envoi Telegram : https://api.telegram.org/bot8000000:secret_bot_xyz789/sendMessage")

        self.assertNotIn("secret_bot_xyz789", message)

    def test_laisse_intact_un_message_sans_secret(self):
        message = hub_deals_db.masquer_secrets("199 routes trouvees, 914 lignes enregistrees")

        self.assertEqual(message, "199 routes trouvees, 914 lignes enregistrees")

    def test_ne_plante_pas_quand_aucun_secret_n_est_defini(self):
        """Secrets absents de l'environnement : le message doit passer tel
        quel, sans que str.replace() recoive None."""
        hub_deals_db.TOKEN = None
        hub_deals_db.TELEGRAM_BOT_TOKEN = None

        self.assertEqual(hub_deals_db.masquer_secrets("message"), "message")

    def test_log_ecrit_le_message_masque_dans_le_fichier(self):
        """Le masquage doit s'appliquer par le seul fait d'appeler log() --
        sinon chaque futur appel devrait y penser lui-meme."""
        import tempfile

        chemin_original = hub_deals_db.LOG_PATH
        with tempfile.TemporaryDirectory() as dossier:
            hub_deals_db.LOG_PATH = os.path.join(dossier, "log_test.txt")
            try:
                hub_deals_db.log("token=secret_travelpayouts_abc123")
                with open(hub_deals_db.LOG_PATH, encoding="utf-8") as f:
                    contenu = f.read()
            finally:
                hub_deals_db.LOG_PATH = chemin_original

        self.assertNotIn("secret_travelpayouts_abc123", contenu)
        self.assertIn("token=***", contenu)


class TestRabattement(unittest.TestCase):
    """Invariants structurels de la table de rabattement. Ces tests valent
    pour toute ville de depart, presente ou future -- ajouter une ville la
    fait automatiquement verifier."""

    def test_les_villes_de_depart_attendues_sont_presentes(self):
        self.assertEqual(
            set(hub_deals_db.RABATTEMENT.keys()),
            {"Dakar", "Abidjan", "Brazzaville", "Lome", "Kinshasa",
             "Paris", "Istanbul", "Casablanca", "Le Caire", "Lagos",
             "Nairobi", "Addis-Abeba", "Johannesburg"},
        )

    def test_chaque_hub_reference_existe_dans_HUBS(self):
        for ville, couts in hub_deals_db.RABATTEMENT.items():
            for hub_iata in couts:
                self.assertIn(
                    hub_iata, hub_deals_db.HUBS,
                    msg=f"{ville} reference le hub inconnu {hub_iata}")

    def test_chaque_entree_a_un_prix_et_une_duree_coherents(self):
        """Un rabattement vaut 0 exactement quand la ville est celle du hub
        (l'abonne part de chez lui) ; partout ailleurs il est positif."""
        for ville, couts in hub_deals_db.RABATTEMENT.items():
            for hub_iata, cout in couts.items():
                propre_hub = hub_deals_db.HUBS[hub_iata]["nom"] == ville
                if propre_hub:
                    self.assertEqual(
                        cout["prix"], 0,
                        msg=f"{ville}->{hub_iata} est son propre hub : prix attendu 0")
                    self.assertEqual(cout["duree_h"], 0)
                else:
                    self.assertGreater(
                        cout["prix"], 0, msg=f"prix invalide pour {ville}->{hub_iata}")
                    self.assertGreater(
                        cout["duree_h"], 0, msg=f"duree_h invalide pour {ville}->{hub_iata}")

    def test_chaque_ville_a_un_rabattement_nul_vers_son_propre_hub(self):
        """L'inverse de l'invariant d'avant le 2026-09-19 : une ville qui est
        aussi un hub ne voyait jamais ses propres vols directs (0 ligne
        Abidjan -> via Abidjan en base sur 111 releves)."""
        noms_hubs = {info["nom"]: iata for iata, info in hub_deals_db.HUBS.items()}
        for ville, couts in hub_deals_db.RABATTEMENT.items():
            iata_propre = noms_hubs.get(ville)
            self.assertIsNotNone(
                iata_propre, msg=f"{ville} n'a pas de hub a son nom")
            self.assertIn(
                iata_propre, couts,
                msg=f"{ville} n'a pas de route directe depuis chez elle")
            self.assertEqual(couts[iata_propre]["prix"], 0)

    def test_abidjan_contient_exactement_les_hubs_attendus(self):
        self.assertEqual(
            set(hub_deals_db.RABATTEMENT["Abidjan"].keys()),
            {"CMN", "CDG", "IST", "NBO", "JNB", "CAI", "LOS", "ABJ"},
        )

    def test_lome_omet_les_hubs_sans_donnee_reelle(self):
        """ADD et JNB n'ont de prix sur aucun des endpoints Travelpayouts
        au depart de Lome : on les omet plutot que d'inventer une valeur.
        LFW, lui, est le hub de Lome elle-meme : rabattement nul."""
        self.assertEqual(
            set(hub_deals_db.RABATTEMENT["Lome"].keys()),
            {"CMN", "CDG", "IST", "NBO", "ABJ", "CAI", "LOS", "LFW"},
        )

    def test_une_ville_residente_n_a_que_son_propre_hub(self):
        """Les correspondances pour residents produisent des itineraires
        absurdes (Paris -> Abidjan -> Rome a 1048 EUR quand le direct est a
        88 EUR). Mesure du 2026-09-19, spec section (e)."""
        residentes = {"Paris", "Istanbul", "Casablanca", "Le Caire", "Lagos",
                      "Nairobi", "Addis-Abeba", "Johannesburg"}
        noms_hubs = {info["nom"]: iata for iata, info in hub_deals_db.HUBS.items()}
        for ville in residentes:
            self.assertEqual(
                set(hub_deals_db.RABATTEMENT[ville].keys()),
                {noms_hubs[ville]},
                msg=f"{ville} ne doit avoir que son propre hub")

    def test_les_quatre_hubs_de_residence_ne_servent_que_leur_ville(self):
        """DKR, FIH, BZV et LFW ont ete ajoutes pour que Dakar, Kinshasa,
        Brazzaville et Lome voient leurs vols directs. Aucune autre ville
        n'a de rabattement vers eux -- sans quoi il faudrait des valeurs
        qu'on n'a jamais mesurees."""
        for hub in ("DKR", "FIH", "BZV", "LFW"):
            villes = [v for v, couts in hub_deals_db.RABATTEMENT.items() if hub in couts]
            self.assertEqual(
                len(villes), 1,
                msg=f"{hub} devrait ne servir qu'une ville, il en sert {villes}")
            self.assertEqual(hub_deals_db.HUBS[hub]["nom"], villes[0])

    def test_chaque_ville_de_depart_a_un_code_iata(self):
        """Sans code IATA, une ville de depart ne peut pas etre reconnue
        comme destination et le filtre anti-auto-route la laisse passer
        silencieusement. Ajouter une ville sans son code doit echouer ici,
        pas produire des lignes « X -> X » en base."""
        for ville in hub_deals_db.RABATTEMENT:
            self.assertIn(
                ville, hub_deals_db.VILLE_IATA,
                msg=f"{ville} n'a pas de code IATA dans VILLE_IATA")

    def test_kinshasa_couvre_tous_les_hubs_de_correspondance(self):
        """Les hubs ajoutes le 2026-09-19 (DKR, BZV, LFW) ne servent que
        leur propre ville : Kinshasa n'a pas a s'y rabattre."""
        self.assertEqual(
            set(hub_deals_db.RABATTEMENT["Kinshasa"].keys()),
            {"CMN", "CDG", "IST", "ADD", "NBO", "ABJ", "JNB", "CAI", "LOS", "FIH"},
        )


class TestDestinationsActives(unittest.TestCase):
    """La boucle de collecte doit balayer les destinations personnelles en
    plus des destinations imposees."""

    def test_destinations_actives_contient_les_originales_sans_fichier(self):
        actives = hub_deals_db.destinations_actives("fichier-qui-nexiste-pas.json")
        self.assertEqual(actives, hub_deals_db.DESTINATIONS)

    def test_la_boucle_principale_utilise_destinations_actives(self):
        """Garde-fou de non-regression : si quelqu'un remet DESTINATIONS en
        dur dans la boucle, les destinations personnelles cessent
        silencieusement d'etre collectees."""
        import inspect
        source = inspect.getsource(hub_deals_db)
        bloc_principal = source.split('if __name__ == "__main__":')[1]
        self.assertIn("destinations_actives", bloc_principal)
        self.assertNotIn("for dest_iata in DESTINATIONS", bloc_principal)

    def test_le_nom_d_une_destination_personnelle_arrive_en_base(self):
        """Sans cela, une destination personnelle serait enregistree avec son
        code IATA en guise de nom (BKK au lieu de Bangkok)."""
        conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(conn)
        offre = {"price": 100, "departure_at": "2026-09-01T10:00:00+00:00"}

        hub_deals_db.enregistrer_prix(
            conn, "CMN", "BKK", offre, "2026-08-15 12:00:00", dest_nom="Bangkok")

        noms = [r[0] for r in conn.execute(
            "SELECT DISTINCT destination_nom FROM offres")]
        conn.close()
        self.assertEqual(noms, ["Bangkok"])

    def test_le_nom_reste_celui_de_DESTINATIONS_si_non_precise(self):
        conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(conn)
        offre = {"price": 100, "departure_at": "2026-09-01T10:00:00+00:00"}

        hub_deals_db.enregistrer_prix(
            conn, "CMN", "DKR", offre, "2026-08-15 12:00:00")

        noms = [r[0] for r in conn.execute(
            "SELECT DISTINCT destination_nom FROM offres")]
        conn.close()
        self.assertEqual(noms, ["Dakar"])


class _ReponseV3:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return {"success": self.status_code < 400, "data": self.data}


class TestPrixSegment(unittest.TestCase):
    """get_prix_segment interroge v3/prices_for_dates. requests.get est
    remplace le temps de chaque test : aucun appel reseau."""

    def _appeler(self, reponse, origine="DKR", destination="CDG"):
        from unittest import mock
        appels = []

        def faux_get(url, params=None, timeout=None):
            appels.append((url, params, timeout))
            return reponse

        with mock.patch.object(hub_deals_db.requests, "get", faux_get):
            resultat = hub_deals_db.get_prix_segment(origine, destination)
        return resultat, appels

    def test_interroge_v3_en_aller_retour(self):
        """PIEGE : laisse a true, one_way renvoie des allers simples ~43 %
        moins chers, incomparables aux rabattements de la table (AR)."""
        _, appels = self._appeler(_ReponseV3([{"price": 486}]))

        url, params, timeout = appels[0]
        self.assertIn("/v3/prices_for_dates", url)
        self.assertEqual(params["one_way"], "false")
        self.assertEqual((params["origin"], params["destination"]), ("DKR", "CDG"))
        self.assertIsNotNone(timeout)

    def test_renvoie_l_offre_la_moins_chere(self):
        resultat, _ = self._appeler(_ReponseV3(
            [{"price": 700}, {"price": 486}, {"price": None}, {"price": 512}]))

        self.assertEqual(resultat["price"], 486)

    def test_renvoie_vide_sans_donnees(self):
        for data in ([], None, [{"price": None}]):
            with self.subTest(data=data):
                resultat, _ = self._appeler(_ReponseV3(data))
                self.assertEqual(resultat, {})

    def test_une_erreur_http_leve_une_exception_reseau(self):
        """mesurer_rabattements ne rattrape que RequestException pour se
        replier sur la table : un code invalide (HTTP 400) doit en etre une."""
        with self.assertRaises(requests.exceptions.RequestException):
            self._appeler(_ReponseV3(None, status_code=400))


class TestMesurerRabattements(unittest.TestCase):
    """La fonction de prix est injectee : aucun appel reseau ici."""

    def setUp(self):
        # mesurer_rabattements journalise les segments non mesures : sans
        # ce remplacement, les tests ecrivent dans le vrai
        # flight_deals_log.txt et polluent l'historique d'exploitation.
        self._log_original = hub_deals_db.log
        self.messages_logges = []
        hub_deals_db.log = self.messages_logges.append

    def tearDown(self):
        hub_deals_db.log = self._log_original

    def _prix(self, table):
        """Fabrique une fausse get_prix_route a partir d'un dict
        {(origine, destination): prix}."""
        def get_prix(origine, destination):
            valeur = table.get((origine, destination))
            if valeur is None:
                return {}
            return {"price": valeur, "departure_at": "2026-09-05T10:00:00+00:00"}
        return get_prix

    # Les valeurs de RABATTEMENT sont des DONNEES : elles changent a chaque
    # remise a jour de la table. Les lire ici plutot que de les coder en dur
    # fait porter le test sur le comportement (« le repli utilise la valeur
    # de la table ») et non sur le contenu du jour.
    def _table(self, ville, hub):
        return hub_deals_db.RABATTEMENT[ville][hub]["prix"]

    def test_renvoie_le_prix_api_quand_il_existe(self):
        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Casablanca")],
            get_prix=self._prix({("DKR", "CMN"): 999}), pause=False)

        self.assertEqual(mesures[("Dakar", "Casablanca")]["prix"], 999)
        self.assertEqual(mesures[("Dakar", "Casablanca")]["table"],
                         self._table("Dakar", "CMN"))
        self.assertTrue(mesures[("Dakar", "Casablanca")]["mesure"])

    def test_replie_sur_la_table_quand_l_api_ne_repond_rien(self):
        """Cas frequent : 19 des 40 segments n'avaient aucun prix sur aucun
        endpoint le 2026-09-13, et la liste change d'un jour a l'autre."""
        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Paris")], get_prix=self._prix({}), pause=False)

        attendu = self._table("Dakar", "CDG")
        self.assertEqual(mesures[("Dakar", "Paris")]["prix"], attendu)
        self.assertEqual(mesures[("Dakar", "Paris")]["table"], attendu)
        self.assertFalse(mesures[("Dakar", "Paris")]["mesure"])

    def test_replie_sur_la_table_en_cas_d_erreur_reseau(self):
        def get_prix(origine, destination):
            raise requests.exceptions.RequestException("coupure")

        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Casablanca")], get_prix=get_prix, pause=False)

        self.assertEqual(mesures[("Dakar", "Casablanca")]["prix"],
                         self._table("Dakar", "CMN"))
        self.assertFalse(mesures[("Dakar", "Casablanca")]["mesure"])

    def test_dedoublonne_les_couples(self):
        """Plusieurs anomalies partagent souvent le meme (ville, hub) :
        un seul appel doit etre fait."""
        appels = []

        def get_prix(origine, destination):
            appels.append((origine, destination))
            return {"price": 468, "departure_at": ""}

        hub_deals_db.mesurer_rabattements(
            [("Dakar", "Casablanca"), ("Dakar", "Casablanca"),
             ("Dakar", "Casablanca")], get_prix=get_prix, pause=False)

        self.assertEqual(len(appels), 1)

    def test_par_defaut_mesure_via_v3_et_non_v1(self):
        """v1/prices/cheap ne renvoie RIEN pour les segments vers Paris :
        mesure a l'alerte, Dakar->Paris ne l'a ete aucun jour sur 22. v3 les
        couvre, au meme prix que v1 partout ou les deux repondent (sonde du
        2026-09-13 : 17 segments, ecart nul)."""
        from unittest import mock

        def v1_interdit(origine, destination):
            raise AssertionError("la mesure ne doit plus passer par v1")

        with mock.patch.object(hub_deals_db, "get_prix_route", v1_interdit), \
             mock.patch.object(hub_deals_db, "get_prix_segment",
                               self._prix({("DKR", "CDG"): 486})):
            mesures = hub_deals_db.mesurer_rabattements(
                [("Dakar", "Paris")], pause=False)

        self.assertEqual(mesures[("Dakar", "Paris")]["prix"], 486)
        self.assertTrue(mesures[("Dakar", "Paris")]["mesure"])

    def test_ignore_un_nom_de_hub_inconnu_sans_lever(self):
        """Un nom absent de HUBS ne doit pas faire echouer une notification."""
        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Atlantide")], get_prix=self._prix({}), pause=False)

        self.assertEqual(mesures, {})

    def test_ignore_un_couple_sans_rabattement_en_table(self):
        """Abidjan n'a pas d'entree pour le hub ADD."""
        mesures = hub_deals_db.mesurer_rabattements(
            [("Abidjan", "Addis-Abeba")], get_prix=self._prix({}), pause=False)

        self.assertEqual(mesures, {})

    def test_les_noms_de_hubs_sont_uniques(self):
        """L'inversion nom -> IATA perdrait silencieusement un hub si deux
        hubs portaient le meme nom."""
        noms = [info["nom"] for info in hub_deals_db.HUBS.values()]
        self.assertEqual(len(noms), len(set(noms)))


class TestMesurerRabattementNul(unittest.TestCase):
    """Un resident part de chez lui : mesurer ce trajet reviendrait a
    interroger l'API sur une ville vers elle-meme, ce qui repond 400."""

    def setUp(self):
        self._original = hub_deals_db.RABATTEMENT
        hub_deals_db.RABATTEMENT = {
            "Paris": {"CDG": {"prix": 0, "duree_h": 0}},
            "Dakar": {"CDG": {"prix": 496, "duree_h": 6}},
        }

    def tearDown(self):
        hub_deals_db.RABATTEMENT = self._original

    def test_aucun_appel_api_pour_un_rabattement_nul(self):
        appels = []

        def faux_get_prix(origine, destination):
            appels.append((origine, destination))
            return {"price": 999}

        mesures = hub_deals_db.mesurer_rabattements(
            [("Paris", "Paris")], get_prix=faux_get_prix, pause=False)

        self.assertEqual(appels, [])
        self.assertEqual(mesures[("Paris", "Paris")],
                         {"prix": 0, "table": 0, "mesure": False})

    def test_une_ville_classique_est_toujours_mesuree(self):
        appels = []

        def faux_get_prix(origine, destination):
            appels.append((origine, destination))
            return {"price": 510}

        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Paris")], get_prix=faux_get_prix, pause=False)

        self.assertEqual(appels, [("DKR", "CDG")])
        self.assertTrue(mesures[("Dakar", "Paris")]["mesure"])
        self.assertEqual(mesures[("Dakar", "Paris")]["prix"], 510)


class TestCorrigerAnomalies(unittest.TestCase):
    def _anomalie(self, ville="Dakar", hub="Abidjan", prix=810.0,
                  moyenne=900.0, baisse=10.0):
        return {
            "destination": "Nairobi", "destination_code": "NBO",
            "hub": hub, "ville_depart": ville,
            "prix_actuel": prix, "moyenne_historique": moyenne,
            "ecart_type": 50.0, "z_score": 1.8, "baisse_pct": baisse,
            "economie": moyenne - prix,
            "methode": "z-score", "nb_releves_historique": 6,
            "date_depart": "2026-09-05T10:00:00+00:00", "lien": "/search/x",
        }

    def test_decale_le_prix_du_jour_et_la_moyenne(self):
        """Le rabattement est une constante additive de tout l'historique :
        on decale les deux du meme montant."""
        mesures = {("Dakar", "Abidjan"): {"prix": 409, "table": 200,
                                          "mesure": True}}

        [a] = hub_deals_db.corriger_anomalies([self._anomalie()], mesures)

        self.assertEqual(a["prix_actuel"], 1019)        # 810 + 209
        self.assertEqual(a["moyenne_historique"], 1109)  # 900 + 209
        self.assertEqual(a["rabattement_mesure"], 409)

    def test_preserve_l_ecart_absolu(self):
        """Le decalage ne doit pas creer ni detruire d'ecart : c'est ce qui
        garantit que le z-score reste valable."""
        mesures = {("Dakar", "Abidjan"): {"prix": 409, "table": 200,
                                          "mesure": True}}

        [a] = hub_deals_db.corriger_anomalies([self._anomalie()], mesures)

        self.assertEqual(a["moyenne_historique"] - a["prix_actuel"], 90)

    def test_recalcule_le_pourcentage_sur_l_echelle_decalee(self):
        mesures = {("Dakar", "Abidjan"): {"prix": 409, "table": 200,
                                          "mesure": True}}

        [a] = hub_deals_db.corriger_anomalies([self._anomalie()], mesures)

        self.assertEqual(a["baisse_pct"], 8.1)   # 90 / 1109

    def test_ne_decale_pas_une_anomalie_non_mesuree(self):
        mesures = {("Dakar", "Paris"): {"prix": 300, "table": 300,
                                        "mesure": False}}

        [a] = hub_deals_db.corriger_anomalies(
            [self._anomalie(hub="Paris")], mesures)

        self.assertEqual(a["prix_actuel"], 810)
        self.assertEqual(a["moyenne_historique"], 900)
        self.assertEqual(a["baisse_pct"], 10.0)
        self.assertIsNone(a["rabattement_mesure"])

    def test_ne_decale_pas_une_anomalie_sans_mesure_du_tout(self):
        [a] = hub_deals_db.corriger_anomalies([self._anomalie()], {})

        self.assertEqual(a["prix_actuel"], 810)
        self.assertIsNone(a["rabattement_mesure"])

    def test_retrie_apres_correction(self):
        """Le decalage reduit le pourcentage : sans re-tri, l'ordre affiche
        ne correspondrait plus aux pourcentages affiches.

        Le cas est choisi pour que l'ordre s'INVERSE reellement -- un jeu
        de donnees ou l'ordre resterait le meme passerait ce test meme si
        le tri etait absent, et ne prouverait donc rien."""
        anomalies = [
            self._anomalie(hub="Abidjan", prix=810, moyenne=900, baisse=10.0),
            self._anomalie(hub="Paris", prix=920, moyenne=1000, baisse=8.0),
        ]
        mesures = {
            # +300 de decalage : 90/1200 = 7,5 %, sous les 8 % de Paris
            ("Dakar", "Abidjan"): {"prix": 500, "table": 200, "mesure": True},
            ("Dakar", "Paris"): {"prix": 300, "table": 300, "mesure": False},
        }

        corrigees = hub_deals_db.corriger_anomalies(anomalies, mesures)

        self.assertEqual([a["hub"] for a in corrigees], ["Paris", "Abidjan"])
        self.assertEqual(corrigees[0]["baisse_pct"], 8.0)
        self.assertEqual(corrigees[1]["baisse_pct"], 7.5)

    def test_l_economie_en_euros_est_invariante_au_rabattement(self):
        """Decaler prix ET moyenne du meme montant laisse l'ecart absolu
        intact : c'est la propriete qui permet au message de justifier
        l'alerte en euros, un chiffre que le rabattement ne deforme pas --
        contrairement au pourcentage."""
        mesures = {("Dakar", "Abidjan"): {"prix": 1200, "table": 200,
                                          "mesure": True}}

        [a] = hub_deals_db.corriger_anomalies([self._anomalie()], mesures)

        self.assertEqual(a["economie"], 90.0)
        self.assertLess(a["baisse_pct"], 10.0)  # le pourcentage, lui, bouge

    def test_ne_modifie_pas_les_anomalies_d_origine(self):
        """La fonction rend de nouveaux dictionnaires : muter l'entree
        rendrait le diagnostic incoherent avec ce que la base contient."""
        origine = self._anomalie()
        mesures = {("Dakar", "Abidjan"): {"prix": 409, "table": 200,
                                          "mesure": True}}

        hub_deals_db.corriger_anomalies([origine], mesures)

        self.assertEqual(origine["prix_actuel"], 810)
        self.assertNotIn("rabattement_mesure", origine)


class TestGrouperAnomalies(unittest.TestCase):
    """Une bonne affaire se joue sur le troncon hub -> destination. La ville
    de depart n'ajoute qu'un rabattement constant, donc la meme aubaine
    remonte autant de fois qu'il y a de villes rattachees au hub -- avec la
    meme economie a l'euro pres. Mesure du 2026-09-12 : 241 alertes sur les
    15 derniers releves ne recouvrent que 62 affaires distinctes."""

    def _a(self, ville, dest="Rome", hub="Abidjan", prix=1000.0,
           economie=444.0, lien="/search/ABJ0511ROM1", baisse=30.0):
        return {
            "destination": dest, "destination_code": dest[:3].upper(),
            "hub": hub, "ville_depart": ville, "prix_actuel": prix,
            "moyenne_historique": prix + economie, "baisse_pct": baisse,
            "economie": economie, "rabattement_mesure": None, "lien": lien,
        }

    def test_regroupe_les_villes_partageant_hub_destination_et_lien(self):
        groupes = hub_deals_db.grouper_anomalies([
            self._a("Dakar", prix=975), self._a("Lome", prix=1000),
            self._a("Kinshasa", prix=1362)])

        self.assertEqual(len(groupes), 1)
        self.assertEqual([a["ville_depart"] for a in groupes[0]],
                         ["Dakar", "Lome", "Kinshasa"])

    def test_ne_regroupe_pas_deux_liens_differents(self):
        """Le lien encode la date de depart : deux liens distincts sont deux
        affaires distinctes, meme sur le meme couple hub/destination."""
        groupes = hub_deals_db.grouper_anomalies([
            self._a("Dakar", lien="/search/ABJ0511ROM1"),
            self._a("Dakar", lien="/search/ABJ1211ROM1")])

        self.assertEqual(len(groupes), 2)

    def test_trie_les_villes_par_prix_croissant(self):
        groupes = hub_deals_db.grouper_anomalies([
            self._a("Kinshasa", prix=1362), self._a("Dakar", prix=975),
            self._a("Lome", prix=1000)])

        self.assertEqual([a["ville_depart"] for a in groupes[0]],
                         ["Dakar", "Lome", "Kinshasa"])

    def test_trie_les_groupes_par_economie_decroissante(self):
        groupes = hub_deals_db.grouper_anomalies([
            self._a("Dakar", dest="Pekin", economie=106.0, lien="/p"),
            self._a("Dakar", dest="Rome", economie=444.0, lien="/r"),
            self._a("Dakar", dest="Paris", economie=141.0, lien="/pa")])

        self.assertEqual([g[0]["destination"] for g in groupes],
                         ["Rome", "Paris", "Pekin"])

    def test_classe_un_groupe_sur_son_economie_la_plus_basse(self):
        """Les economies d'un groupe different de quelques centimes (les
        historiques n'ont pas tous la meme longueur). On classe et on annonce
        sur la plus basse : une annonce prudente ne survend pas l'affaire."""
        groupes = hub_deals_db.grouper_anomalies([
            self._a("Dakar", dest="Paris", economie=141.0, lien="/pa"),
            self._a("Lome", dest="Paris", economie=140.4, lien="/pa"),
            self._a("Dakar", dest="Pekin", economie=140.7, lien="/pe")])

        self.assertEqual([g[0]["destination"] for g in groupes],
                         ["Pekin", "Paris"])


class TestBlocDAlerte(unittest.TestCase):
    def _a(self, ville, prix, baisse, economie=444.0, mesure=None):
        return {
            "destination": "Rome", "destination_code": "ROM",
            "hub": "Abidjan", "ville_depart": ville, "prix_actuel": prix,
            "moyenne_historique": prix + economie, "baisse_pct": baisse,
            "economie": economie, "rabattement_mesure": mesure,
            "lien": "/search/ABJ0511ROM1",
        }

    def test_le_groupe_annonce_l_economie_une_seule_fois(self):
        bloc = hub_deals_db.construire_bloc([
            self._a("Dakar", 975, 31.3), self._a("Lome", 1000, 30.8)])

        self.assertEqual(bloc.count("economie"), 1)
        self.assertIn("<b>Rome</b> (depuis Abidjan) - economie 444", bloc)

    def test_le_groupe_liste_chaque_ville_avec_son_pourcentage(self):
        """Le pourcentage reste propre a la ville : son denominateur change
        avec le rabattement, l'economie non."""
        bloc = hub_deals_db.construire_bloc([
            self._a("Dakar", 975, 31.3), self._a("Kinshasa", 1362, 24.6)])

        self.assertIn("Dakar 975€ (-31%)", bloc)
        self.assertIn("Kinshasa 1362€ (-25%)", bloc)

    def test_le_groupe_annonce_l_economie_la_plus_basse(self):
        bloc = hub_deals_db.construire_bloc([
            self._a("Lome", 812, 14.8, economie=141.0),
            self._a("Dakar", 949, 12.9, economie=140.4)])

        self.assertIn("economie 140€", bloc)

    def test_chaque_ville_dit_si_son_rabattement_est_mesure(self):
        """Statut affiche ligne par ligne plutot qu'en note de bas de bloc :
        plusieurs villes d'un meme groupe peuvent etre mesurees, avec des
        valeurs differentes."""
        bloc = hub_deals_db.construire_bloc([
            self._a("Dakar", 975, 31.3, mesure=418),
            self._a("Lome", 1000, 30.8)])

        self.assertIn("Dakar 975€ (-31%) - rabattement mesure 418€", bloc)
        self.assertIn("Lome 1000€ (-31%) - rabattement estime", bloc)

    def test_le_lien_n_apparait_qu_une_fois(self):
        bloc = hub_deals_db.construire_bloc([
            self._a("Dakar", 975, 31.3), self._a("Lome", 1000, 30.8),
            self._a("Kinshasa", 1362, 24.6)])

        self.assertEqual(bloc.count("aviasales.com"), 1)

    def test_une_seule_ville_retombe_sur_le_format_plat(self):
        """Jamais observe en 15 releves (0 groupe sur 62), mais possible :
        une ville dont l'historique est plus court peut rester seule. L'entete
        de groupe et la liste ne mettraient alors rien en facteur commun."""
        bloc = hub_deals_db.construire_bloc([self._a("Dakar", 975, 31.3, mesure=418)])

        self.assertIn("(depuis Abidjan, au depart de Dakar)", bloc)
        self.assertIn("moyenne habituelle", bloc)
        self.assertIn("Economie : 444", bloc)
        self.assertIn("Rabattement mesure ce jour : 418", bloc)
        self.assertNotIn("- economie", bloc)


class TestNotificationAvecRabattementMesure(unittest.TestCase):
    """envoyer_telegram est remplace par un espion : aucun envoi reel."""

    def setUp(self):
        self.envois = []
        self._envoyer = hub_deals_db.envoyer_telegram
        self._mesurer = hub_deals_db.mesurer_rabattements
        hub_deals_db.envoyer_telegram = lambda msg: self.envois.append(msg)

        # sans ce remplacement, ces tests ecrivent « Notification Telegram
        # envoyee » dans le vrai flight_deals_log.txt, ou la ligne devient
        # un faux temoignage d'exploitation.
        self._log_original = hub_deals_db.log
        self.messages_logges = []
        hub_deals_db.log = self.messages_logges.append

        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)
        # une route jugee anormalement basse au dernier releve
        for date, total in (("2026-08-10 10:00:00", 900),
                            ("2026-08-11 10:00:00", 900),
                            ("2026-08-12 10:00:00", 900),
                            ("2026-08-13 10:00:00", 810)):
            self.conn.execute("""
                INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                    destination_code, destination_nom, prix_vol_hub,
                    rabattement, total_estime, date_depart, lien)
                VALUES (?, 'Dakar', 'Abidjan', 'NBO', 'Nairobi', 0, 200, ?, '', '/x')
            """, (date, total))
        self.conn.commit()

    def tearDown(self):
        hub_deals_db.envoyer_telegram = self._envoyer
        hub_deals_db.mesurer_rabattements = self._mesurer
        hub_deals_db.log = self._log_original
        self.conn.close()

    def test_le_message_affiche_le_rabattement_mesure(self):
        hub_deals_db.mesurer_rabattements = lambda couples, **kw: {
            ("Dakar", "Abidjan"): {"prix": 409, "table": 200, "mesure": True}}

        hub_deals_db.verifier_et_notifier_anomalies(
            self.conn, "2026-08-13 10:00:00")

        self.assertEqual(len(self.envois), 1)
        self.assertIn("Rabattement mesure ce jour", self.envois[0])
        self.assertIn("409", self.envois[0])
        self.assertIn("1019", self.envois[0])   # 810 + 209

    def test_le_message_affiche_l_economie_en_euros(self):
        """Le pourcentage affiche est calcule sur l'echelle decalee par le
        rabattement : il peut passer sous le plancher de detection sans que
        l'affaire ait change. L'economie en euros, elle, ne bouge pas -- le
        message doit la porter."""
        hub_deals_db.mesurer_rabattements = lambda couples, **kw: {
            ("Dakar", "Abidjan"): {"prix": 409, "table": 200, "mesure": True}}

        hub_deals_db.verifier_et_notifier_anomalies(
            self.conn, "2026-08-13 10:00:00")

        self.assertIn("Economie : 90", self.envois[0])

    def test_une_alerte_dont_le_pourcentage_tombe_sous_le_plancher_est_conservee(self):
        """Decision de conception (2026-09-12) : on NE refiltre PAS sur le
        pourcentage corrige. Le rabattement n'est mesure que pour les routes
        deja detectees, donc un refiltrage ne pourrait qu'en retirer, jamais
        en rattraper -- il serait unilateral. Ici un rabattement mesure tres
        au-dessus de la table fait tomber le pourcentage a 4,7 %, sous le
        plancher de 6 % : l'alerte doit partir quand meme, justifiee par ses
        90 EUR d'economie."""
        hub_deals_db.mesurer_rabattements = lambda couples, **kw: {
            ("Dakar", "Abidjan"): {"prix": 1200, "table": 200, "mesure": True}}

        hub_deals_db.verifier_et_notifier_anomalies(
            self.conn, "2026-08-13 10:00:00")

        self.assertEqual(len(self.envois), 1)
        self.assertIn("Nairobi", self.envois[0])
        self.assertIn("Economie : 90", self.envois[0])
        self.assertIn("-5%", self.envois[0])  # 90 / 1900, arrondi

    def test_le_message_signale_un_rabattement_non_mesure(self):
        hub_deals_db.mesurer_rabattements = lambda couples, **kw: {
            ("Dakar", "Abidjan"): {"prix": 200, "table": 200, "mesure": False}}

        hub_deals_db.verifier_et_notifier_anomalies(
            self.conn, "2026-08-13 10:00:00")

        self.assertIn("non mesure", self.envois[0])
        self.assertIn("810", self.envois[0])   # non decale

    def test_une_erreur_de_mesure_n_empeche_pas_la_notification(self):
        """Une alerte avec des totaux non corriges vaut infiniment mieux
        qu'une alerte perdue."""
        def exploser(couples, **kw):
            raise RuntimeError("panne inattendue")
        hub_deals_db.mesurer_rabattements = exploser

        hub_deals_db.verifier_et_notifier_anomalies(
            self.conn, "2026-08-13 10:00:00")

        self.assertEqual(len(self.envois), 1)
        self.assertIn("810", self.envois[0])


class TestAlerteSauvegarde(unittest.TestCase):
    """Une sauvegarde qui echoue en silence est pire que pas de
    sauvegarde : on se croit protege. L'echec doit se voir."""

    def setUp(self):
        self.envois = []
        self._envoyer = hub_deals_db.envoyer_telegram
        hub_deals_db.envoyer_telegram = lambda msg: self.envois.append(msg)
        self._log = hub_deals_db.log
        hub_deals_db.log = lambda m: None
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)

    def tearDown(self):
        hub_deals_db.envoyer_telegram = self._envoyer
        hub_deals_db.log = self._log
        self.conn.close()

    def test_aucune_notification_quand_la_sauvegarde_reussit(self):
        """Notifier chaque succes produirait un bruit quotidien qu'on
        cesserait de lire -- et l'absence du message passerait inapercue."""
        ok = hub_deals_db.sauvegarder_et_alerter(
            self.conn, sauver=lambda conn, dossier, journaliser=None: True)

        self.assertTrue(ok)
        self.assertEqual(self.envois, [])

    def test_notifie_quand_la_sauvegarde_echoue(self):
        ok = hub_deals_db.sauvegarder_et_alerter(
            self.conn, sauver=lambda conn, dossier, journaliser=None: False)

        self.assertFalse(ok)
        self.assertEqual(len(self.envois), 1)
        self.assertIn("auvegarde", self.envois[0])

    def test_le_message_ne_ressemble_pas_a_une_alerte_de_prix(self):
        """Sans distinction nette, une panne technique se confondrait avec
        une bonne affaire dans le fil Telegram."""
        hub_deals_db.sauvegarder_et_alerter(
            self.conn, sauver=lambda conn, dossier, journaliser=None: False)

        message = self.envois[0]
        self.assertNotIn("bonne(s) affaire(s)", message)
        self.assertNotIn("aviasales.com", message)

    def test_une_exception_est_absorbee_et_notifiee(self):
        def exploser(conn, dossier, journaliser=None):
            raise OSError("git introuvable")

        ok = hub_deals_db.sauvegarder_et_alerter(self.conn, sauver=exploser)

        self.assertFalse(ok)
        self.assertEqual(len(self.envois), 1)

    def test_un_module_de_sauvegarde_inchargeable_est_notifie(self):
        """Les autres tests injectent `sauver` et ne franchissent donc jamais
        l'import reel : un sauvegarde.py absent ou casse levait jusqu'au
        bloc principal, sans alerte."""
        from unittest import mock
        with mock.patch.dict(sys.modules, {"sauvegarde": None}):
            ok = hub_deals_db.sauvegarder_et_alerter(self.conn)

        self.assertFalse(ok)
        self.assertEqual(len(self.envois), 1)

    def test_le_journal_ne_dit_pas_envoyee_quand_telegram_echoue(self):
        """Le 17/09, reseau coupe : « message Telegram NON parti » puis
        « ALERTE sauvegarde envoyee » deux lignes plus bas. Le journal
        se contredisait et l'alerte etait comptee comme recue."""
        lignes = []
        hub_deals_db.log = lignes.append
        hub_deals_db.envoyer_telegram = lambda msg: False

        hub_deals_db.sauvegarder_et_alerter(
            self.conn, sauver=lambda conn, dossier, journaliser=None: False)

        self.assertNotIn("   -> ALERTE sauvegarde envoyee", lignes)
        self.assertIn("   -> ALERTE sauvegarde NON envoyee", lignes)

    def test_le_journal_dit_envoyee_quand_telegram_accepte(self):
        lignes = []
        hub_deals_db.log = lignes.append
        hub_deals_db.envoyer_telegram = lambda msg: True

        hub_deals_db.sauvegarder_et_alerter(
            self.conn, sauver=lambda conn, dossier, journaliser=None: False)

        self.assertIn("   -> ALERTE sauvegarde envoyee", lignes)

    def test_le_bloc_principal_appelle_le_garde_fou(self):
        import inspect
        bloc = inspect.getsource(hub_deals_db).split(
            'if __name__ == "__main__":')[1]
        self.assertIn("sauvegarder_et_alerter", bloc)


class TestPlantageJournalise(unittest.TestCase):
    """La tache tourne sous pythonw.exe : sys.stderr vaut None, donc une
    trace de plantage ou un message de SystemExit ne s'affiche NULLE PART.
    Sans journalisation, un releve qui plante serait parfaitement muet."""

    def test_la_trace_d_un_plantage_est_journalisee_et_masquee(self):
        """Vrai log(), vers un journal temporaire : c'est le chemin reel,
        masquage des secrets compris."""
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as dossier:
            journal_tmp = os.path.join(dossier, "journal.txt")
            with mock.patch.object(hub_deals_db, "LOG_PATH", journal_tmp), \
                 mock.patch.object(hub_deals_db, "TOKEN", "secret_tp_123"), \
                 mock.patch("builtins.print"):
                try:
                    raise RuntimeError("echec sur ?token=secret_tp_123")
                except RuntimeError as e:
                    hub_deals_db.journaliser_plantage(type(e), e, e.__traceback__)

            with open(journal_tmp, encoding="utf-8") as f:
                journal = f.read()

        self.assertIn("PLANTAGE", journal)
        self.assertIn("Traceback", journal)
        self.assertIn("RuntimeError", journal)
        self.assertNotIn("secret_tp_123", journal)

    def test_le_bloc_principal_installe_la_journalisation(self):
        import inspect
        bloc = inspect.getsource(hub_deals_db).split(
            'if __name__ == "__main__":')[1]
        self.assertIn("sys.excepthook = journaliser_plantage", bloc)
        # SystemExit ne passe pas par excepthook : son message serait perdu
        self.assertNotIn("raise SystemExit(", bloc)


class _FausseReponse:
    def __init__(self, status_code=200, text="{\"ok\":true}"):
        self.status_code = status_code
        self.text = text


class TestJournalisationDuMessageTelegram(unittest.TestCase):
    """Le message envoye doit etre tracable apres coup : un bot ne peut pas
    relire ses propres messages sortants via l'API Telegram."""

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

    def _journal(self):
        return "\n".join(self.lignes)

    def test_journalise_le_message_envoye(self):
        hub_deals_db.requests.post = lambda *a, **k: _FausseReponse()

        hub_deals_db.envoyer_telegram("<b>Nairobi</b>\n894 EUR")

        journal = self._journal()
        self.assertIn("message Telegram", journal)
        self.assertIn("894 EUR", journal)
        self.assertIn("Nairobi", journal)

    def test_journalise_meme_si_telegram_n_est_pas_configure(self):
        """Ce cas est aujourd'hui totalement silencieux : on ne sait pas ce
        qui AURAIT ete notifie."""
        hub_deals_db.TELEGRAM_BOT_TOKEN = None
        hub_deals_db.requests.post = lambda *a, **k: _FausseReponse()

        hub_deals_db.envoyer_telegram("contenu qui n'est pas parti")

        journal = self._journal()
        self.assertIn("contenu qui n'est pas parti", journal)
        self.assertIn("non configure", journal)

    def test_signale_un_refus_de_telegram(self):
        """Sans verification du code HTTP, un message refuse etait compte
        comme envoye -- le journal devenait un faux temoignage."""
        hub_deals_db.requests.post = lambda *a, **k: _FausseReponse(
            400, '{"ok":false,"description":"can\'t parse entities"}')

        hub_deals_db.envoyer_telegram("<b>mal ferme")

        journal = self._journal()
        self.assertIn("400", journal)
        self.assertIn("ECHEC", journal)

    def test_signale_une_erreur_reseau_sans_lever(self):
        def poster(*a, **k):
            raise requests.exceptions.RequestException("coupure")
        hub_deals_db.requests.post = poster

        hub_deals_db.envoyer_telegram("peu importe")

        self.assertIn("ERREUR envoi Telegram", self._journal())


class TestConstruireBlocResident(unittest.TestCase):
    def _anomalie(self, ville, rabattement, prix):
        return {
            "destination": "Dubai", "destination_code": "DXB",
            "hub": "Paris", "ville_depart": ville,
            "prix_actuel": prix, "moyenne_historique": prix + 150.0,
            "baisse_pct": 20.0, "economie": 150.0,
            "rabattement": rabattement, "rabattement_mesure": None,
            "lien": "/search/x", "date_depart": "2026-10-01",
        }

    def test_un_groupe_d_une_seule_ville_residente_dit_vol_direct(self):
        bloc = hub_deals_db.construire_bloc([self._anomalie("Paris", 0, 320.0)])
        self.assertIn("vol direct", bloc)
        self.assertNotIn("Rabattement estime", bloc)

    def test_un_groupe_mixte_distingue_les_deux_natures(self):
        """Un resident de Paris et un Dakarois peuvent partager la meme
        affaire CDG -> DXB : le groupe les affiche cote a cote."""
        bloc = hub_deals_db.construire_bloc([
            self._anomalie("Paris", 0, 320.0),
            self._anomalie("Dakar", 496, 816.0),
        ])
        self.assertIn("vol direct", bloc)
        self.assertIn("rabattement estime", bloc)

    def test_une_ville_classique_seule_est_inchangee(self):
        bloc = hub_deals_db.construire_bloc([self._anomalie("Dakar", 496, 816.0)])
        self.assertIn("Rabattement estime, non mesure ce jour", bloc)
        self.assertNotIn("vol direct", bloc)


if __name__ == "__main__":
    unittest.main()
