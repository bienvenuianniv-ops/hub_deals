import json
import os
import sqlite3
import sys
import tempfile
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import recherche


class TestResolutionDesEntrees(unittest.TestCase):
    def test_accepte_une_ville_connue(self):
        self.assertEqual(recherche.valider_ville("Dakar"), "Dakar")

    def test_accepte_une_ville_sans_tenir_compte_de_la_casse(self):
        self.assertEqual(recherche.valider_ville("kinshasa"), "Kinshasa")

    def test_refuse_une_ville_inconnue_en_listant_les_villes_valides(self):
        with self.assertRaises(ValueError) as ctx:
            recherche.valider_ville("Marseille")
        message = str(ctx.exception)
        self.assertIn("Marseille", message)
        self.assertIn("Dakar", message)  # la liste des villes valides est proposee

    def test_un_code_de_trois_lettres_est_pris_tel_quel(self):
        self.assertEqual(recherche.resoudre_destination("bkk"), "BKK")

    def test_un_nom_connu_est_traduit_en_code_iata(self):
        self.assertEqual(recherche.resoudre_destination("Brazzaville"), "BZV")

    def test_un_nom_connu_est_traduit_sans_tenir_compte_de_la_casse(self):
        self.assertEqual(recherche.resoudre_destination("brazzaville"), "BZV")

    def test_refuse_un_nom_inconnu_en_demandant_le_code_iata(self):
        with self.assertRaises(ValueError) as ctx:
            recherche.resoudre_destination("Ouagadougou")
        self.assertIn("code IATA", str(ctx.exception))


class TestChercherItineraires(unittest.TestCase):
    """La fonction de prix est injectee : aucun appel reseau ici."""

    def setUp(self):
        self._rabattement = recherche.collecteur.RABATTEMENT
        self._hubs = recherche.collecteur.HUBS
        self._ville_iata = recherche.collecteur.VILLE_IATA
        recherche.collecteur.HUBS = {
            "CDG": {"nom": "Paris"},
            "IST": {"nom": "Istanbul"},
            "CMN": {"nom": "Casablanca"},
        }
        recherche.collecteur.RABATTEMENT = {
            "Testville": {
                "CDG": {"prix": 300, "duree_h": 6},
                "IST": {"prix": 400, "duree_h": 7},
                "CMN": {"prix": 350, "duree_h": 4},
            },
        }
        recherche.collecteur.VILLE_IATA = {"Testville": "TST"}

    def tearDown(self):
        recherche.collecteur.RABATTEMENT = self._rabattement
        recherche.collecteur.HUBS = self._hubs
        recherche.collecteur.VILLE_IATA = self._ville_iata

    def _prix(self, table):
        """Fabrique une fausse get_prix_route a partir d'un dict
        {(origine, destination): prix ou None}."""
        def get_prix(origine, destination):
            valeur = table.get((origine, destination))
            if valeur is None:
                return {}
            return {"price": valeur, "departure_at": "2026-09-05T10:00:00+00:00"}
        return get_prix

    def test_classe_les_options_du_moins_cher_au_plus_cher(self):
        options, _ = recherche.chercher_itineraires(
            "Testville", "BKK",
            get_prix=self._prix({
                ("TST", "BKK"): 1120,          # direct
                ("TST", "CDG"): 320, ("CDG", "BKK"): 540,   # 860
                ("TST", "IST"): 525, ("IST", "BKK"): 410,   # 935
                ("TST", "CMN"): 468, ("CMN", "BKK"): 690,   # 1158
            }), pause=False)

        self.assertEqual([o["total"] for o in options], [860, 935, 1120, 1158])

    def test_inclut_le_vol_direct_que_le_collecteur_n_interroge_jamais(self):
        options, _ = recherche.chercher_itineraires(
            "Testville", "BKK",
            get_prix=self._prix({("TST", "BKK"): 500}), pause=False)

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["libelle"], "direct")
        self.assertIsNone(options[0]["hub"])
        self.assertEqual(options[0]["total"], 500)

    def test_replie_sur_le_rabattement_quand_l_aller_n_a_pas_de_prix(self):
        options, _ = recherche.chercher_itineraires(
            "Testville", "BKK",
            get_prix=self._prix({("CDG", "BKK"): 540}), pause=False)

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["prix_aller"], 300)   # valeur de RABATTEMENT
        self.assertTrue(options[0]["aller_estime"])
        self.assertTrue(options[0]["estime"])
        self.assertEqual(options[0]["total"], 840)

    def test_ecarte_l_option_dont_le_segment_principal_n_a_pas_de_prix(self):
        """Sans prix pour hub -> destination, il n'existe aucune valeur de
        repli honnete : l'option disparait au lieu d'etre valorisee a 0."""
        options, _ = recherche.chercher_itineraires(
            "Testville", "BKK",
            get_prix=self._prix({("TST", "CDG"): 320}), pause=False)

        self.assertEqual(options, [])

    def test_a_total_egal_l_option_mesuree_passe_devant_l_estimee(self):
        options, _ = recherche.chercher_itineraires(
            "Testville", "BKK",
            get_prix=self._prix({
                ("TST", "CDG"): 500, ("CDG", "BKK"): 340,   # 840, mesure
                ("IST", "BKK"): 440,                        # 840, aller estime a 400
            }), pause=False)

        self.assertEqual([o["total"] for o in options], [840, 840])
        self.assertFalse(options[0]["estime"])
        self.assertTrue(options[1]["estime"])

    def test_saute_le_hub_egal_a_la_destination(self):
        """Aller a Paris via Paris n'est pas un itineraire : c'est le direct."""
        options, _ = recherche.chercher_itineraires(
            "Testville", "CDG",
            get_prix=self._prix({
                ("TST", "CDG"): 320,
                ("IST", "CDG"): 200,
            }), pause=False)

        libelles = [o["libelle"] for o in options]
        self.assertIn("direct", libelles)
        self.assertNotIn("via Paris", libelles)

    def test_refuse_une_destination_egale_a_la_ville_de_depart(self):
        with self.assertRaises(ValueError) as ctx:
            recherche.chercher_itineraires(
                "Testville", "TST", get_prix=self._prix({}), pause=False)
        self.assertIn("TST", str(ctx.exception))

    def test_une_erreur_reseau_n_interrompt_pas_la_recherche(self):
        def get_prix(origine, destination):
            if origine == "TST" and destination == "CDG":
                raise requests.exceptions.RequestException("coupure")
            if (origine, destination) == ("CDG", "BKK"):
                return {"price": 540, "departure_at": "2026-09-05T10:00:00+00:00"}
            return {}

        options, erreurs = recherche.chercher_itineraires(
            "Testville", "BKK", get_prix=get_prix, pause=False)

        self.assertEqual(len(options), 1)          # repli sur RABATTEMENT
        self.assertTrue(options[0]["aller_estime"])
        self.assertEqual(len(erreurs), 1)
        self.assertIn("TST", erreurs[0])


class TestContexteHistorique(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        recherche.collecteur.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def _inserer(self, date, ville, dest, total):
        self.conn.execute("""
            INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                destination_code, destination_nom, prix_vol_hub, rabattement,
                total_estime, date_depart, lien)
            VALUES (?, ?, 'Paris', ?, 'Test', 0, 0, ?, '', '')
        """, (date, ville, dest, total))
        self.conn.commit()

    def test_renvoie_none_pour_une_route_inconnue(self):
        self.assertIsNone(
            recherche.contexte_historique(self.conn, "Dakar", "BKK"))

    def test_renvoie_le_minimum_et_sa_date(self):
        self._inserer("2026-08-10 10:00:00", "Dakar", "BZV", 810)
        self._inserer("2026-08-14 10:00:00", "Dakar", "BZV", 1001)
        self._inserer("2026-08-15 10:00:00", "Dakar", "BZV", 1001)

        contexte = recherche.contexte_historique(self.conn, "Dakar", "BZV")

        self.assertEqual(contexte["nb_releves"], 3)
        self.assertEqual(contexte["minimum"], 810)
        self.assertEqual(contexte["date_minimum"], "2026-08-10 10:00:00")

    def test_ne_melange_pas_les_villes_de_depart(self):
        self._inserer("2026-08-10 10:00:00", "Dakar", "BZV", 810)
        self._inserer("2026-08-10 10:00:00", "Kinshasa", "BZV", 200)

        contexte = recherche.contexte_historique(self.conn, "Dakar", "BZV")

        self.assertEqual(contexte["nb_releves"], 1)
        self.assertEqual(contexte["minimum"], 810)


class TestFormatage(unittest.TestCase):
    def _option(self, libelle, total, estime=False, hub="CDG"):
        return {
            "libelle": libelle, "hub": hub, "prix_aller": 300,
            "aller_estime": estime, "prix_principal": total - 300,
            "total": total, "estime": estime,
            "date_depart": "2026-09-05T10:00:00+00:00", "lien": "/search/x",
        }

    def test_affiche_la_meilleure_option_en_tete(self):
        sortie = recherche.formater(
            "Dakar", "BKK", [self._option("via Paris", 840),
                             self._option("via Istanbul", 935)], [], None)

        self.assertIn("Meilleure option", sortie)
        self.assertIn("via Paris", sortie)

    def test_signale_un_prix_estime_par_des_parentheses(self):
        sortie = recherche.formater(
            "Dakar", "BKK", [self._option("via Paris", 840, estime=True)], [], None)

        self.assertIn("(300)", sortie)

    def test_avertit_quand_la_meilleure_option_repose_sur_un_estime(self):
        sortie = recherche.formater(
            "Dakar", "BKK", [self._option("via Paris", 840, estime=True)], [], None)

        self.assertIn("estime", sortie.lower())
        self.assertIn("plus eleve", sortie.lower())

    def test_propose_la_surveillance_quand_la_route_est_inconnue(self):
        sortie = recherche.formater(
            "Dakar", "BKK", [self._option("via Paris", 840)], [], None)

        self.assertIn("--surveiller BKK", sortie)

    def test_situe_le_prix_par_rapport_au_minimum_historique(self):
        sortie = recherche.formater(
            "Dakar", "BZV", [self._option("via Paris", 1001)], [],
            {"nb_releves": 46, "minimum": 810, "date_minimum": "2026-08-10 10:00:00"})

        self.assertIn("810", sortie)
        self.assertIn("46", sortie)
        # (1001 - 810) / 810 = 23,58 % -> arrondi a +24 %
        self.assertIn("+24 %", sortie)

    def test_message_honnete_quand_aucun_itineraire_n_est_trouve(self):
        sortie = recherche.formater("Dakar", "XXX", [], [], None)

        self.assertIn("aucun", sortie.lower())
        self.assertNotIn("Meilleure option", sortie)

    def test_mentionne_les_erreurs_reseau_rencontrees(self):
        sortie = recherche.formater(
            "Dakar", "BKK", [self._option("via Paris", 840)],
            ["DKR->IST : erreur reseau (coupure)"], None)

        self.assertIn("erreur reseau", sortie)


class TestDestinationsPersonnelles(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.chemin = os.path.join(self.dossier.name, "destinations_perso.json")

    def tearDown(self):
        self.dossier.cleanup()

    def test_fichier_absent_renvoie_un_dictionnaire_vide(self):
        self.assertEqual(
            recherche.collecteur.charger_destinations_perso(self.chemin), {})

    def test_fichier_illisible_est_ignore_sans_faire_echouer(self):
        """Un JSON corrompu ne doit jamais faire echouer un releve : c'est
        un fichier de confort, pas une source de verite."""
        with open(self.chemin, "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du json")

        self.assertEqual(
            recherche.collecteur.charger_destinations_perso(self.chemin), {})

    def test_ajoute_une_destination(self):
        recherche.ajouter_destination("BKK", chemin=self.chemin)

        with open(self.chemin, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"BKK": "Bangkok"})

    def test_reprend_le_nom_de_DESTINATIONS_quand_il_existe(self):
        recherche.ajouter_destination("BZV", chemin=self.chemin)

        contenu = recherche.collecteur.charger_destinations_perso(self.chemin)
        self.assertEqual(contenu["BZV"], "Brazzaville")

    def test_utilise_le_code_comme_nom_pour_une_destination_inconnue(self):
        recherche.ajouter_destination("XYZ", chemin=self.chemin)

        contenu = recherche.collecteur.charger_destinations_perso(self.chemin)
        self.assertEqual(contenu["XYZ"], "XYZ")

    def test_retire_une_destination(self):
        recherche.ajouter_destination("BKK", chemin=self.chemin)

        self.assertTrue(recherche.retirer_destination("BKK", chemin=self.chemin))
        self.assertEqual(
            recherche.collecteur.charger_destinations_perso(self.chemin), {})

    def test_retirer_une_destination_absente_renvoie_faux(self):
        self.assertFalse(recherche.retirer_destination("BKK", chemin=self.chemin))

    def test_refuse_au_dela_du_plafond(self):
        for i in range(recherche.MAX_DESTINATIONS_PERSO):
            recherche.ajouter_destination(f"Z{i:02d}", chemin=self.chemin)

        with self.assertRaises(ValueError) as ctx:
            recherche.ajouter_destination("BKK", chemin=self.chemin)
        self.assertIn(str(recherche.MAX_DESTINATIONS_PERSO), str(ctx.exception))

    def test_destinations_actives_fusionne_sans_ecraser_les_originales(self):
        # SIN et non BKK : Bangkok figure DEJA dans DESTINATIONS, la fusion
        # n'ajouterait alors aucune entree et le test ne prouverait rien.
        self.assertNotIn("SIN", recherche.collecteur.DESTINATIONS)
        recherche.ajouter_destination("SIN", chemin=self.chemin)

        actives = recherche.collecteur.destinations_actives(self.chemin)

        self.assertIn("SIN", actives)                    # la personnelle
        self.assertIn("DKR", actives)                    # les originales
        self.assertEqual(actives["DKR"], "Dakar")
        self.assertEqual(len(actives), len(recherche.collecteur.DESTINATIONS) + 1)

    def test_une_destination_personnelle_deja_connue_garde_son_nom_d_origine(self):
        """BKK est deja dans DESTINATIONS : la fusion ne doit ni la dupliquer
        ni ecraser son nom par celui du fichier personnel."""
        recherche.ajouter_destination("BKK", chemin=self.chemin)

        actives = recherche.collecteur.destinations_actives(self.chemin)

        self.assertEqual(len(actives), len(recherche.collecteur.DESTINATIONS))
        self.assertEqual(actives["BKK"], recherche.collecteur.DESTINATIONS["BKK"])


if __name__ == "__main__":
    unittest.main()
