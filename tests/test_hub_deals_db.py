import sqlite3
import sys
import os
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db


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
            {"Dakar", "Abidjan", "Brazzaville", "Lome", "Kinshasa"},
        )

    def test_chaque_hub_reference_existe_dans_HUBS(self):
        for ville, couts in hub_deals_db.RABATTEMENT.items():
            for hub_iata in couts:
                self.assertIn(
                    hub_iata, hub_deals_db.HUBS,
                    msg=f"{ville} reference le hub inconnu {hub_iata}")

    def test_chaque_entree_a_un_prix_et_une_duree_positifs(self):
        for ville, couts in hub_deals_db.RABATTEMENT.items():
            for hub_iata, cout in couts.items():
                self.assertGreater(
                    cout["prix"], 0, msg=f"prix invalide pour {ville}->{hub_iata}")
                self.assertGreater(
                    cout["duree_h"], 0, msg=f"duree_h invalide pour {ville}->{hub_iata}")

    def test_aucune_ville_n_a_de_rabattement_vers_son_propre_hub(self):
        """Abidjan est a la fois ville de depart et hub : il ne doit pas y
        avoir de cout pour s'y rabattre depuis elle-meme."""
        for ville, couts in hub_deals_db.RABATTEMENT.items():
            noms_hubs = {hub_deals_db.HUBS[h]["nom"] for h in couts}
            self.assertNotIn(
                ville, noms_hubs,
                msg=f"{ville} a un cout de rabattement vers elle-meme")

    def test_abidjan_contient_exactement_les_hubs_attendus(self):
        self.assertEqual(
            set(hub_deals_db.RABATTEMENT["Abidjan"].keys()),
            {"CMN", "CDG", "IST", "NBO", "JNB", "CAI", "LOS"},
        )

    def test_lome_omet_les_hubs_sans_donnee_reelle(self):
        """ADD et JNB n'ont de prix sur aucun des endpoints Travelpayouts
        au depart de Lome : on les omet plutot que d'inventer une valeur."""
        self.assertEqual(
            set(hub_deals_db.RABATTEMENT["Lome"].keys()),
            {"CMN", "CDG", "IST", "NBO", "ABJ", "CAI", "LOS"},
        )

    def test_chaque_ville_de_depart_a_un_code_iata(self):
        """Sans code IATA, une ville de depart ne peut pas etre reconnue
        comme destination et le filtre anti-auto-route la laisse passer
        silencieusement. Ajouter une ville sans son code doit echouer ici,
        pas produire des lignes « X -> X » en base."""
        for ville in hub_deals_db.RABATTEMENT:
            self.assertIn(
                ville, hub_deals_db.VILLE_IATA,
                msg=f"{ville} n'a pas de code IATA dans VILLE_IATA")

    def test_kinshasa_couvre_tous_les_hubs(self):
        self.assertEqual(
            set(hub_deals_db.RABATTEMENT["Kinshasa"].keys()),
            set(hub_deals_db.HUBS.keys()),
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


class TestMesurerRabattements(unittest.TestCase):
    """La fonction de prix est injectee : aucun appel reseau ici."""

    def _prix(self, table):
        """Fabrique une fausse get_prix_route a partir d'un dict
        {(origine, destination): prix}."""
        def get_prix(origine, destination):
            valeur = table.get((origine, destination))
            if valeur is None:
                return {}
            return {"price": valeur, "departure_at": "2026-09-05T10:00:00+00:00"}
        return get_prix

    def test_renvoie_le_prix_api_quand_il_existe(self):
        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Casablanca")],
            get_prix=self._prix({("DKR", "CMN"): 468}), pause=False)

        self.assertEqual(mesures[("Dakar", "Casablanca")]["prix"], 468)
        self.assertEqual(mesures[("Dakar", "Casablanca")]["table"], 400)
        self.assertTrue(mesures[("Dakar", "Casablanca")]["mesure"])

    def test_replie_sur_la_table_quand_l_api_ne_repond_rien(self):
        """Cas le plus frequent : 17 des 40 segments n'ont aucun prix,
        dont CDG pour les cinq villes."""
        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Paris")], get_prix=self._prix({}), pause=False)

        self.assertEqual(mesures[("Dakar", "Paris")]["prix"], 300)
        self.assertEqual(mesures[("Dakar", "Paris")]["table"], 300)
        self.assertFalse(mesures[("Dakar", "Paris")]["mesure"])

    def test_replie_sur_la_table_en_cas_d_erreur_reseau(self):
        def get_prix(origine, destination):
            raise requests.exceptions.RequestException("coupure")

        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Casablanca")], get_prix=get_prix, pause=False)

        self.assertEqual(mesures[("Dakar", "Casablanca")]["prix"], 400)
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


class TestCorrigerAnomalies(unittest.TestCase):
    def _anomalie(self, ville="Dakar", hub="Abidjan", prix=810.0,
                  moyenne=900.0, baisse=10.0):
        return {
            "destination": "Nairobi", "destination_code": "NBO",
            "hub": hub, "ville_depart": ville,
            "prix_actuel": prix, "moyenne_historique": moyenne,
            "ecart_type": 50.0, "z_score": 1.8, "baisse_pct": baisse,
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

    def test_ne_modifie_pas_les_anomalies_d_origine(self):
        """La fonction rend de nouveaux dictionnaires : muter l'entree
        rendrait le diagnostic incoherent avec ce que la base contient."""
        origine = self._anomalie()
        mesures = {("Dakar", "Abidjan"): {"prix": 409, "table": 200,
                                          "mesure": True}}

        hub_deals_db.corriger_anomalies([origine], mesures)

        self.assertEqual(origine["prix_actuel"], 810)
        self.assertNotIn("rabattement_mesure", origine)


class TestNotificationAvecRabattementMesure(unittest.TestCase):
    """envoyer_telegram est remplace par un espion : aucun envoi reel."""

    def setUp(self):
        self.envois = []
        self._envoyer = hub_deals_db.envoyer_telegram
        self._mesurer = hub_deals_db.mesurer_rabattements
        hub_deals_db.envoyer_telegram = lambda msg: self.envois.append(msg)

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


if __name__ == "__main__":
    unittest.main()
