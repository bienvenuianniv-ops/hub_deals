"""Garde-fou commun a toute la suite.

Depuis le 2026-09-20, notifier_abonnes_sans_risque ouvre lui-meme la base
des abonnes. Sans ce remplacement, tout test qui passe par un releve
complet CREE un flight_deals.db dans le repertoire courant -- constate sur
test_hub_deals_db, test_liens_affilies et test_telegram_decoupage.

Le repli SQLite de magasin.ouvrir() reste voulu en production : tant que
la base distante n'est pas en service, les abonnes vivent encore dans le
fichier local. Ce qui n'est pas voulu, c'est qu'un test y touche.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import magasin
import hub_deals_db

_VRAI_OUVRIR = magasin.ouvrir


@pytest.fixture(autouse=True)
def jamais_la_base_de_production(monkeypatch):
    """Deuxieme verrou : la suite n'herite pas de HUB_DEALS_ABONNES_URL.

    Le premier verrou est dans magasin.ouvrir(), qui ne consulte plus
    l'environnement des qu'un chemin est donne. Celui-ci couvre le cas
    restant : un appel sans argument, dans du code de production qu'un
    test traverse. Un test qui veut vraiment une URL la pose lui-meme
    dans son setUp, qui s'execute apres cette fixture.
    """
    monkeypatch.delenv(magasin.URL_ENV, raising=False)


@pytest.fixture(autouse=True)
def base_des_abonnes_en_memoire(monkeypatch):
    """magasin.ouvrir() sans argument ouvre « :memory: » et non un fichier.

    Un test qui passe un chemin explicite le garde ; un test qui remplace
    lui-meme magasin.ouvrir garde la main.
    """
    monkeypatch.setattr(
        magasin, "ouvrir",
        lambda url=None, chemin=":memory:": _VRAI_OUVRIR(url=url, chemin=chemin))


@pytest.fixture(autouse=True)
def jamais_de_vraie_publication(monkeypatch):
    """Depuis le 2026-09-22, verifier_et_notifier_anomalies appelle
    publier_page_sans_risque a chaque releve, y compris sans anomalie.

    Sans ce remplacement, toute la douzaine de tests qui passent par un
    releve complet (test_abonnes, test_hub_deals_db, test_liens_courts)
    ecrirait un index.html dans .pages/ (chemin relatif, donc dependant
    du repertoire courant), lancerait de VRAIS `git add`/`commit`/`push`
    sur la branche PUBLIQUE gh-pages, et en cas d'echec enverrait un
    VRAI message Telegram -- le jeton est present sur cette machine.
    Un test qui veut vraiment observer la publication remplace lui-meme
    hub_deals_db.publier_page_sans_risque, comme le fait
    tests/test_publier_page.py.
    """
    monkeypatch.setattr(hub_deals_db, "publier_page_sans_risque",
                        lambda *a, **k: None)
