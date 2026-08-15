import sqlite3
import sys
import os
import unittest

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

    def tearDown(self):
        hub_deals_db.envoyer_telegram = self._envoyer_telegram_original
        hub_deals_db.log = self._log_original
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


if __name__ == "__main__":
    unittest.main()
