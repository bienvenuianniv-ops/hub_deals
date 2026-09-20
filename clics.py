"""
Lit les clics affilies Travelpayouts, par Sous-ID et par jour, sans passer
par le tableau de bord et sans cliquer nulle part.

Pourquoi ce module existe : le 2026-09-16, le Sous-ID `dakar` affichait
4 clics la ou un seul etait attendu, et l'ecart est reste inexplique
quatre jours faute de pouvoir regarder le detail. L'API des statistiques
donne chaque evenement a la seconde, avec son marqueur robot -- elle a
montre quatre VRAIES ouvertures du meme lien (13:47:34, 13:51:05,
13:51:17, 13:51:39), quatre trace_id differents, donc quatre passages
distincts. Aucun bug : juste un destinataire qui rouvre son lien.

Ce que compte le tableau de bord, et donc ce module :

  clic   = type « redirect » (passage par aviasales.tpk.ro) NON marque robot
  robot  = le meme, marque is_bot -- apercu de lien Telegram, sonde, curl ;
           exclu de la colonne Clicks, montre ici a part plutot qu'efface
  direct = type « external » a traffic_source 0 : un lien
           aviasales.com?marker=... ouvert sans redirection. Il n'entre PAS
           dans les clics -- c'est tout l'interet des liens courts.

L'arrivee (« external ») qui suit un clic partage son trace_id : c'est une
seule visite, elle n'est pas recomptee.

    python clics.py                      # les 30 derniers jours
    python clics.py --depuis 2026-09-16
    python clics.py --sub-id dakar

Jeton : TRAVELPAYOUTS_TOKEN dans l'environnement (Profile > API token).
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

import requests

URL = "https://api.travelpayouts.com/statistics/v1/execute_query"
PAR_PAGE = 1000          # maximum documente : 10 000
JOURS_PAR_DEFAUT = 30

CHAMPS = ["date", "created_at", "type", "sub_id", "is_bot", "traffic_source",
          "user_device_type", "user_country", "trace_id", "referrer_domain"]


class ErreurAPI(RuntimeError):
    """L'interrogation n'a pas abouti.

    Une exception et non un resultat vide : avalee, l'erreur donnerait
    « 0 clic », indiscernable d'un vrai zero. C'est la lecon des sondes du
    projet -- sans cas temoin, « pas de donnees » et « requete fausse » se
    ressemblent trop.
    """


def interroger(depuis: str, token=None, poster=None, par_page: int = PAR_PAGE) -> list:
    """Tous les evenements bruts depuis `depuis` (AAAA-MM-JJ), pagines.

    La pagination n'est pas une precaution de style : sans elle, un compte
    partiel se lirait comme un compte complet.
    """
    token = token or os.environ.get("TRAVELPAYOUTS_TOKEN")
    if not token:
        raise ErreurAPI("TRAVELPAYOUTS_TOKEN absent de l'environnement")
    poster = poster or requests.post

    evenements = []
    offset = 0
    while True:
        corps = {
            "fields": CHAMPS,
            "filters": [{"field": "date", "op": "ge", "value": depuis}],
            "sort": [{"field": "created_at", "order": "asc"}],
            "offset": offset,
            "limit": par_page,
        }
        reponse = poster(URL, json=corps, headers={"X-Access-Token": token},
                         timeout=60)
        if reponse.status_code != 200:
            raise ErreurAPI(f"HTTP {reponse.status_code} {reponse.text[:200]}")
        charge = reponse.json()
        lot = charge.get("results", [])
        evenements.extend(lot)
        total = charge.get("total_rows", len(evenements))
        offset += len(lot)
        if not lot or offset >= total:
            return evenements


def compter(evenements: list) -> dict:
    """{(jour, sub_id): {clics, robots, directs}}."""
    compte = defaultdict(lambda: {"clics": 0, "robots": 0, "directs": 0})
    for evenement in evenements:
        cle = (evenement["date"], evenement["sub_id"] or "(sans)")
        if evenement["type"] == "redirect":
            compte[cle]["robots" if evenement["is_bot"] else "clics"] += 1
        elif evenement["type"] == "external" and not evenement["traffic_source"]:
            compte[cle]["directs"] += 1
    return dict(compte)


def formater(compte: dict) -> str:
    """Un tableau lisible dans un terminal, trie par jour puis Sous-ID."""
    if not compte:
        return "Aucun evenement sur la periode."

    lignes = [f"{'Jour':<12} {'Sous-ID':<16} {'clics':>6} {'robots':>7} {'directs':>8}",
              "-" * 52]
    totaux = {"clics": 0, "robots": 0, "directs": 0}
    for (jour, sub_id) in sorted(compte):
        ligne = compte[(jour, sub_id)]
        lignes.append(f"{jour:<12} {sub_id:<16} {ligne['clics']:>6} "
                      f"{ligne['robots']:>7} {ligne['directs']:>8}")
        for clef in totaux:
            totaux[clef] += ligne[clef]
    lignes.append("-" * 52)
    lignes.append(f"{'Total':<12} {'':<16} {totaux['clics']:>6} "
                  f"{totaux['robots']:>7} {totaux['directs']:>8}")
    lignes.append("")
    lignes.append("clics = redirections tpk.ro hors robots (ce que compte le "
                  "tableau de bord)")
    lignes.append("robots = apercus Telegram, sondes : exclus des clics")
    lignes.append("directs = liens aviasales.com sans redirection : jamais "
                  "comptes comme clics")
    return "\n".join(lignes)


def main(argv=None, interroger=interroger, ecrire=print) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    analyseur.add_argument("--depuis", help="date AAAA-MM-JJ (defaut : il y a 30 jours)")
    analyseur.add_argument("--sub-id", help="ne montrer que ce Sous-ID")
    options = analyseur.parse_args(sys.argv[1:] if argv is None else argv)

    depuis = options.depuis or str(date.today() - timedelta(days=JOURS_PAR_DEFAUT))
    try:
        evenements = interroger(depuis)
    except ErreurAPI as erreur:
        ecrire(f"ECHEC : {erreur}")
        return 1

    if options.sub_id:
        evenements = [e for e in evenements if e["sub_id"] == options.sub_id]
    ecrire(f"Depuis le {depuis} : {len(evenements)} evenement(s)\n")
    ecrire(formater(compter(evenements)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
