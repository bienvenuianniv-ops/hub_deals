"""Villes proposees a l'inscription (mesure du 2026-09-22).

Le clavier offrait les 13 villes connues. La mesure sur 114 releves
rejoues montre deux familles nettes :

  - les villes qui se rabattent vers PLUSIEURS hubs (8 a 10) :
    1,6 a 2,6 affaires par releve, 21 a 38 % de jours muets ;
  - les villes RESIDENTES, qui sont leur propre hub : un seul point de
    depart, 82 routes au lieu de 585, et 54 a 82 % de jours muets.

Verifie : ce n'est pas un probleme de calibrage. En retirant tout le
plancher en euros, les residentes passent de 71 % a 65 % de jours muets
seulement, pendant que les temoins ne bougent pas (1,98 -> 1,99
affaires par releve). Elles n'ont pas assez de routes a comparer.

Proposer ces villes, c'est promettre un service quotidien qui se tait
3 jours sur 4. On ne les propose plus -- sans jamais casser un abonne
deja inscrit dans l'une d'elles.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import bot_ecoute
import hub_deals_db
import magasin

CODE = "invitation_test_2026"
T0 = "2026-09-15T12:00:00+00:00"


def _message(texte, chat_id=111, prenom="Awa"):
    return {"update_id": 1, "message": {
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": chat_id, "first_name": prenom}, "text": texte}}


def _bouton(data, chat_id=111):
    return {"update_id": 1, "callback_query": {
        "id": "cb1", "data": data, "from": {"id": chat_id},
        "message": {"chat": {"id": chat_id, "type": "private"}}}}


class TestStructure(unittest.TestCase):

    def test_toute_ville_proposee_est_une_ville_connue(self):
        """Une ville proposee absente de NOMS_AFFICHES ferait planter la
        composition du message a la premiere affaire."""
        self.assertTrue(set(abonnes.VILLES_PROPOSEES) <= set(abonnes.NOMS_AFFICHES))

    def test_toute_ville_connue_a_un_rabattement(self):
        """Invariant d'origine, conserve : une ville sans entree dans
        RABATTEMENT ne recevrait jamais rien."""
        self.assertEqual(set(abonnes.NOMS_AFFICHES), set(hub_deals_db.RABATTEMENT))

    def test_aucune_ville_residente_n_est_proposee(self):
        """Une ville residente est son propre hub : son rabattement vers
        lui-meme vaut 0 et c'est son SEUL hub. C'est la definition
        structurelle de la famille pauvre -- la regle, pas la liste."""
        for ville in abonnes.VILLES_PROPOSEES:
            hubs = hub_deals_db.RABATTEMENT[ville]
            self.assertGreater(
                len(hubs), 1,
                f"{ville} n'a qu'un hub : elle se taira 3 jours sur 4")


class TestClavier(unittest.TestCase):

    def setUp(self):
        self.conn = magasin.ouvrir(chemin=":memory:")

    def tearDown(self):
        self.conn.close()

    def _villes_du_clavier(self, actions):
        clavier = actions[0]["reply_markup"]["inline_keyboard"]
        return [b[0]["callback_data"][len("ville:"):] for b in clavier]

    def test_le_clavier_ne_propose_que_les_villes_retenues(self):
        actions, _ = bot_ecoute.traiter_update(
            self.conn, _message(f"/start {CODE}"), CODE, T0)

        self.assertEqual(sorted(self._villes_du_clavier(actions)),
                         sorted(abonnes.VILLES_PROPOSEES))

    def test_le_menu_ville_propose_la_meme_liste(self):
        bot_ecoute.traiter_update(self.conn, _message(f"/start {CODE}"), CODE, T0)

        actions, _ = bot_ecoute.traiter_update(
            self.conn, _message("/ville"), CODE, T0)

        self.assertEqual(sorted(self._villes_du_clavier(actions)),
                         sorted(abonnes.VILLES_PROPOSEES))


class TestChoixDeVille(unittest.TestCase):

    def setUp(self):
        self.conn = magasin.ouvrir(chemin=":memory:")
        bot_ecoute.traiter_update(self.conn, _message(f"/start {CODE}"), CODE, T0)

    def tearDown(self):
        self.conn.close()

    def test_une_ville_proposee_est_acceptee(self):
        ville = sorted(abonnes.VILLES_PROPOSEES)[0]

        bot_ecoute.traiter_update(self.conn, _bouton(f"ville:{ville}"), CODE, T0)

        self.assertEqual(abonnes.trouver(self.conn, 111)["ville_depart"], ville)

    def test_une_ville_connue_mais_non_proposee_est_refusee(self):
        """Telegram garde les anciens messages indefiniment : un clavier
        d'avant ce changement reste cliquable dans l'historique."""
        retiree = sorted(set(abonnes.NOMS_AFFICHES) - set(abonnes.VILLES_PROPOSEES))[0]

        bot_ecoute.traiter_update(self.conn, _bouton(f"ville:{retiree}"), CODE, T0)

        self.assertIsNone(abonnes.trouver(self.conn, 111)["ville_depart"])


class TestAbonneDejaDansUneVilleRetiree(unittest.TestCase):
    """Le cas Mariama, inscrite a Paris le 2026-09-20.

    Elle garde sa ville et continue de recevoir ce qu'il y a. Retirer
    Paris de la table des noms affiches, au lieu de la retirer du seul
    clavier, ferait planter la composition de son message : c'est
    exactement le bug que ce fichier existe pour empecher.
    """

    def setUp(self):
        self.conn = magasin.ouvrir(chemin=":memory:")
        self.retiree = sorted(
            set(abonnes.NOMS_AFFICHES) - set(abonnes.VILLES_PROPOSEES))[0]
        abonnes.inscrire(self.conn, 8296006641, "Mariama", T0)
        # ecriture directe : le bot ne permet plus de choisir cette ville
        self.conn.execute(
            "UPDATE abonnes SET ville_depart = ? WHERE chat_id = ?",
            (self.retiree, 8296006641))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_elle_reste_servie_par_le_releve(self):
        self.assertIn(
            self.retiree,
            [a["ville_depart"] for a in abonnes.abonnes_a_servir(self.conn)])

    def test_son_message_se_compose_sans_erreur(self):
        groupes = [[{
            "ville_depart": self.retiree, "destination": "Milan",
            "hub": "CDG", "prix_actuel": 73.0, "baisse_pct": 34.0,
            "economie": 37.0, "rabattement": 0, "lien": "/vol/CDGMIL",
        }]]

        morceaux = abonnes.messages_abonne(groupes, self.retiree)

        self.assertTrue(morceaux)
        self.assertIn(abonnes.NOMS_AFFICHES[self.retiree], morceaux[0])


if __name__ == "__main__":
    unittest.main()
