"""Tests du lecteur de clics Travelpayouts (enquete du 2026-09-20).

Aucun test ne touche le reseau : le poster est injecte. Les evenements
d'exemple sont de VRAIES lignes de l'API, reprises de l'enquete sur
l'ecart « dakar = 4 » -- y compris leurs particularites (un redirect sans
external, un external de lien direct a traffic_source 0).
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clics


def _evenement(**champs):
    base = {"date": "2026-09-16", "created_at": "2026-09-16 13:47:34",
            "type": "redirect", "sub_id": "dakar", "is_bot": 0,
            "traffic_source": 574520, "user_device_type": "Mobile",
            "user_country": "Senegal", "trace_id": "Zz1", "referrer_domain": ""}
    base.update(champs)
    return base


class _FausseReponse:
    def __init__(self, charge, status_code=200):
        self._charge = charge
        self.status_code = status_code
        self.text = json.dumps(charge)

    def json(self):
        return self._charge


class TestInterroger(unittest.TestCase):
    def test_demande_les_evenements_depuis_la_date_donnee(self):
        envois = []

        def poster(url, json=None, headers=None, timeout=None):
            envois.append((url, json, headers))
            return _FausseReponse({"results": [_evenement()], "total_rows": 1})

        evenements = clics.interroger("2026-09-14", token="jeton", poster=poster)

        self.assertEqual(len(evenements), 1)
        url, corps, entetes = envois[0]
        self.assertIn("statistics/v1/execute_query", url)
        self.assertIn({"field": "date", "op": "ge", "value": "2026-09-14"},
                      corps["filters"])
        self.assertEqual(entetes["X-Access-Token"], "jeton")

    def test_lit_toutes_les_pages(self):
        """Sans pagination, un compte partiel passerait pour un compte
        complet : c'est le genre de silence que ce module existe pour eviter."""
        pages = [
            _FausseReponse({"results": [_evenement(trace_id="a")] * 2,
                            "total_rows": 3}),
            _FausseReponse({"results": [_evenement(trace_id="b")],
                            "total_rows": 3}),
        ]
        vus = []

        def poster(url, json=None, headers=None, timeout=None):
            vus.append(json["offset"])
            return pages[len(vus) - 1]

        evenements = clics.interroger("2026-09-14", token="jeton", poster=poster,
                                      par_page=2)

        self.assertEqual(len(evenements), 3)
        self.assertEqual(vus, [0, 2])

    def test_une_erreur_http_leve_au_lieu_de_rendre_zero_clic(self):
        """Avalee, elle donnerait « 0 clic » -- indiscernable d'un vrai zero."""
        def poster(url, json=None, headers=None, timeout=None):
            return _FausseReponse({"error": "Unauthorized"}, status_code=401)

        with self.assertRaises(clics.ErreurAPI) as cas:
            clics.interroger("2026-09-14", token="faux", poster=poster)

        self.assertIn("401", str(cas.exception))

    def test_sans_token_leve(self):
        """L'environnement est vide de force : sur la machine du relevé le
        jeton est bien present, et sans ce vidage ce test partirait pour de
        vrai sur le reseau (meme piege que TRAVELPAYOUTS_PROJET dans les
        tests de hub_deals_db)."""
        propre = {k: v for k, v in os.environ.items()
                  if k != "TRAVELPAYOUTS_TOKEN"}

        with mock.patch.dict(os.environ, propre, clear=True):
            with self.assertRaises(clics.ErreurAPI):
                clics.interroger("2026-09-14", token=None, poster=None)


class TestCompter(unittest.TestCase):
    """Un clic = une redirection par tpk.ro NON marquee robot. C'est la
    definition prouvee le 2026-09-20 : elle reproduit exactement les
    chiffres du tableau de bord (dakar = 4 le 16/09, 1 le 17/09)."""

    def test_un_redirect_humain_est_un_clic(self):
        compte = clics.compter([_evenement()])

        self.assertEqual(compte[("2026-09-16", "dakar")]["clics"], 1)

    def test_un_redirect_robot_est_compte_a_part(self):
        """Apercu de lien Telegram ou verification technique : le tableau
        de bord les exclut, nous les montrons sans les melanger."""
        compte = clics.compter([_evenement(is_bot=1)])

        ligne = compte[("2026-09-16", "dakar")]
        self.assertEqual(ligne["clics"], 0)
        self.assertEqual(ligne["robots"], 1)

    def test_l_arrivee_qui_suit_un_clic_n_est_pas_recomptee(self):
        """redirect puis external partagent le trace_id : c'est une seule
        visite, pas deux."""
        compte = clics.compter([
            _evenement(),
            _evenement(type="external", created_at="2026-09-16 13:47:39",
                       referrer_domain="m.aviasales.com")])

        self.assertEqual(compte[("2026-09-16", "dakar")]["clics"], 1)

    def test_un_lien_direct_est_compte_a_part(self):
        """traffic_source 0 et aucun redirect : c'est un lien
        aviasales.com?marker=... Il n'entre PAS dans la colonne Clicks du
        tableau de bord -- la raison d'etre des liens courts."""
        compte = clics.compter([
            _evenement(type="external", traffic_source=0, trace_id="01a0",
                       created_at="2026-09-15 14:44:30", date="2026-09-15")])

        ligne = compte[("2026-09-15", "dakar")]
        self.assertEqual(ligne["clics"], 0)
        self.assertEqual(ligne["directs"], 1)

    def test_un_redirect_sans_arrivee_reste_un_clic(self):
        """Observe le 16/09 a 13:51:39 : la redirection a eu lieu, la page
        n'a pas ete atteinte. Travelpayouts l'a bien compte."""
        compte = clics.compter([_evenement(created_at="2026-09-16 13:51:39",
                                           trace_id="Zz9")])

        self.assertEqual(compte[("2026-09-16", "dakar")]["clics"], 1)

    def test_separe_les_jours_et_les_sous_id(self):
        compte = clics.compter([
            _evenement(sub_id="dakar"),
            _evenement(sub_id="proprietaire", trace_id="Zz2"),
            _evenement(sub_id="dakar", date="2026-09-17",
                       created_at="2026-09-17 12:56:05", trace_id="Zz3")])

        self.assertEqual(compte[("2026-09-16", "dakar")]["clics"], 1)
        self.assertEqual(compte[("2026-09-16", "proprietaire")]["clics"], 1)
        self.assertEqual(compte[("2026-09-17", "dakar")]["clics"], 1)


class TestFormater(unittest.TestCase):
    def test_une_ligne_par_jour_et_sous_id_avec_le_total(self):
        compte = clics.compter([
            _evenement(),
            _evenement(is_bot=1, trace_id="Zz2"),
            _evenement(type="external", traffic_source=0, trace_id="01a0")])

        texte = clics.formater(compte)

        self.assertIn("2026-09-16", texte)
        self.assertIn("dakar", texte)
        self.assertIn("Total", texte)

    def test_sans_evenement_le_dit(self):
        """« Aucun evenement » plutot qu'un tableau vide : le tableau vide
        se lit comme un bug de l'outil."""
        self.assertIn("Aucun", clics.formater({}))


class TestMain(unittest.TestCase):
    def test_affiche_le_tableau(self):
        def interroger(depuis, token=None, poster=None, par_page=None):
            return [_evenement()]

        sorties = []
        code = clics.main(argv=["--depuis", "2026-09-14"],
                          interroger=interroger, ecrire=sorties.append)

        self.assertEqual(code, 0)
        self.assertIn("dakar", "\n".join(sorties))

    def test_un_echec_est_visible_et_fait_echouer(self):
        def interroger(depuis, token=None, poster=None, par_page=None):
            raise clics.ErreurAPI("HTTP 401 Unauthorized")

        sorties = []
        code = clics.main(argv=[], interroger=interroger, ecrire=sorties.append)

        self.assertEqual(code, 1)
        self.assertIn("401", "\n".join(sorties))

    def test_filtre_sur_un_sous_id(self):
        def interroger(depuis, token=None, poster=None, par_page=None):
            return [_evenement(sub_id="dakar"),
                    _evenement(sub_id="proprietaire", trace_id="Zz2")]

        sorties = []
        clics.main(argv=["--sub-id", "dakar"], interroger=interroger,
                   ecrire=sorties.append)

        texte = "\n".join(sorties)
        self.assertIn("dakar", texte)
        self.assertNotIn("proprietaire", texte)


if __name__ == "__main__":
    unittest.main()
