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
