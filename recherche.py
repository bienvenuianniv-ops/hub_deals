"""
Recherche de billet a la demande.

Le collecteur (hub_deals_db.py) fonctionne en eventail : il interroge une
matrice hubs x destinations imposee et signale ce qu'il juge anormalement
bas. Ce module fait l'inverse : l'utilisateur pose sa propre question.

Deux differences importantes avec le collecteur :
  - le VOL DIRECT ville -> destination est interroge, ce que le collecteur
    ne fait jamais (il passe toujours par un hub) ;
  - les segments ville -> hub sont demandes a l'API plutot que lus dans la
    table RABATTEMENT, dont les valeurs estimees se sont revelees
    optimistes de 17 a 31 % (voir la spec).

Aucune ecriture dans flight_deals.db : une recherche manuelle ne doit pas
entrer dans l'historique qui nourrit la detection statistique, sinon les
recherches de l'utilisateur fausseraient ses propres alertes.
"""

import time

import requests

import hub_deals_db as collecteur


def valider_ville(argument: str) -> str:
    """Renvoie le nom canonique de la ville de depart, ou leve ValueError.

    Les villes disponibles sont celles de RABATTEMENT : ce sont les seules
    pour lesquelles on connait un cout de rabattement de repli.
    """
    for ville in collecteur.RABATTEMENT:
        if ville.lower() == argument.lower():
            return ville
    villes = ", ".join(sorted(collecteur.RABATTEMENT))
    raise ValueError(
        f"Ville de depart inconnue : {argument}. Villes disponibles : {villes}")


def resoudre_destination(argument: str) -> str:
    """Traduit l'argument en code IATA.

    Un argument de 3 lettres est pris pour un code IATA (permet de viser
    n'importe quelle ville du monde). Sinon on cherche parmi les noms des
    destinations connues. Aucun annuaire de villes n'est embarque : on ne
    devine pas un code a partir d'un nom inconnu.
    """
    if len(argument) == 3:
        return argument.upper()
    for code, nom in collecteur.DESTINATIONS.items():
        if nom.lower() == argument.lower():
            return code
    raise ValueError(
        f"Destination inconnue : {argument}. Donne son code IATA "
        f"(3 lettres, par exemple BKK pour Bangkok).")


def _appeler(get_prix, origine, destination, erreurs, pause):
    """Appelle la fonction de prix en absorbant les erreurs reseau.

    Renvoie l'offre (dict) ou {} si aucun prix. Une erreur reseau est
    consignee dans `erreurs` et traitee comme une absence de prix : une
    coupure sur un segment ne doit pas faire echouer toute la recherche.
    """
    try:
        offre = get_prix(origine, destination)
    except requests.exceptions.RequestException as e:
        erreurs.append(f"{origine}->{destination} : erreur reseau ({e})")
        return {}
    if pause:
        time.sleep(collecteur.PAUSE_ENTRE_APPELS)
    return offre or {}


def chercher_itineraires(ville, dest, get_prix=None, pause=True):
    """Construit et classe les itineraires de `ville` vers `dest`.

    Renvoie (options, erreurs). Le vol direct figure dans le meme
    classement que les trajets via hub.

    get_prix est injectable pour les tests ; par defaut on interroge
    reellement l'API via le collecteur.
    """
    if get_prix is None:
        get_prix = collecteur.get_prix_route

    origine = collecteur.VILLE_IATA[ville]
    if dest == origine:
        raise ValueError(
            f"Destination identique a la ville de depart ({dest}) : "
            f"cet itineraire n'a pas de sens.")

    erreurs = []
    options = []

    # 1. le vol direct -- le collecteur ne l'interroge jamais
    offre = _appeler(get_prix, origine, dest, erreurs, pause)
    if offre.get("price"):
        depart = offre.get("departure_at") or ""
        options.append({
            "libelle": "direct",
            "hub": None,
            "prix_aller": None,
            "aller_estime": False,
            "prix_principal": offre["price"],
            "total": offre["price"],
            "estime": False,
            "date_depart": depart,
            "lien": collecteur.construire_lien(origine, dest, depart),
        })

    # 2. un itineraire par hub disposant d'un rabattement pour cette ville
    for hub, cout in collecteur.RABATTEMENT[ville].items():
        if hub == dest:
            continue  # aller a X via X, c'est le vol direct deja traite

        offre_aller = _appeler(get_prix, origine, hub, erreurs, pause)
        if offre_aller.get("price"):
            prix_aller = offre_aller["price"]
            aller_estime = False
        else:
            prix_aller = cout["prix"]  # repli sur la valeur estimee
            aller_estime = True

        offre_principale = _appeler(get_prix, hub, dest, erreurs, pause)
        if not offre_principale.get("price"):
            continue  # aucun repli honnete possible sur ce segment

        depart = offre_principale.get("departure_at") or ""
        options.append({
            "libelle": f"via {collecteur.HUBS[hub]['nom']}",
            "hub": hub,
            "prix_aller": prix_aller,
            "aller_estime": aller_estime,
            "prix_principal": offre_principale["price"],
            "total": prix_aller + offre_principale["price"],
            "estime": aller_estime,
            "date_depart": depart,
            "lien": collecteur.construire_lien(hub, dest, depart),
        })

    # a total egal, l'option entierement mesuree passe devant l'estimee :
    # les estimations se sont revelees optimistes de 17 a 31 %
    options.sort(key=lambda o: (o["total"], o["estime"]))
    return options, erreurs
