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

import json
import sqlite3
import sys
import time

import requests

import hub_deals_db as collecteur

MAX_DESTINATIONS_PERSO = 15  # chaque destination coute 9 appels par releve


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


def contexte_historique(conn, ville, dest):
    """Ce que la base sait deja de cette route, ou None si elle l'ignore.

    Lecture seule : ce module n'ecrit jamais dans flight_deals.db.
    """
    ligne = conn.execute("""
        SELECT COUNT(DISTINCT date_collecte), MIN(total_estime)
        FROM offres
        WHERE ville_depart = ? AND destination_code = ?
    """, (ville, dest)).fetchone()

    if not ligne or not ligne[0]:
        return None

    nb_releves, minimum = ligne
    date_minimum = conn.execute("""
        SELECT date_collecte FROM offres
        WHERE ville_depart = ? AND destination_code = ? AND total_estime = ?
        ORDER BY date_collecte LIMIT 1
    """, (ville, dest, minimum)).fetchone()[0]

    return {
        "nb_releves": nb_releves,
        "minimum": minimum,
        "date_minimum": date_minimum,
    }


def formater(ville, dest, options, erreurs, contexte):
    """Produit la sortie texte de la recherche."""
    nom_dest = collecteur.DESTINATIONS.get(dest, dest)
    lignes = [f"\n{ville} -> {nom_dest} ({dest})\n"]

    if not options:
        lignes.append("  Aucun itineraire trouve : le cache de l'API ne connait")
        lignes.append("  aucun prix pour cette route. Ce n'est pas une panne.")
    else:
        for o in options:
            if o["hub"] is None:
                detail = " " * 18
            elif o["aller_estime"]:
                detail = f"({o['prix_aller']:.0f})+{o['prix_principal']:5.0f} ="
            else:
                detail = f" {o['prix_aller']:4.0f} +{o['prix_principal']:5.0f} ="
            provenance = "[aller estime]" if o["aller_estime"] else "[API]"
            lignes.append(
                f"  {o['libelle']:<18}{detail}{o['total']:6.0f} EUR   "
                f"depart {o['date_depart'][:10]}   {provenance}")

        meilleure = options[0]
        lignes.append(f"\n  Meilleure option : {meilleure['libelle']}, "
                      f"{meilleure['total']:.0f} EUR")
        if meilleure["lien"]:
            lignes.append(f"  https://www.aviasales.com{meilleure['lien']}")
        if meilleure["estime"]:
            lignes.append("  Attention : son prix d'aller est estime, pas mesure -- "
                          "le total reel peut etre plus eleve.")

    lignes.append("")
    if contexte is None:
        lignes.append("  Historique : route inconnue de ta base.")
        lignes.append(f"  -> python recherche.py --surveiller {dest}"
                      f"  (+{len(collecteur.HUBS)} appels par releve)")
    else:
        lignes.append(
            f"  Historique : {contexte['nb_releves']} releves, meilleur prix vu "
            f"{contexte['minimum']:.0f} EUR le {contexte['date_minimum'][:10]}.")
        if options:
            ecart = (options[0]["total"] - contexte["minimum"]) / contexte["minimum"] * 100
            lignes.append(f"  Prix actuel : {ecart:+.0f} % par rapport a ce minimum.")

    if erreurs:
        lignes.append("")
        lignes.append("  Segments non interroges :")
        for e in erreurs:
            lignes.append(f"    - {e}")

    return "\n".join(lignes)


def ajouter_destination(code, chemin=None):
    """Place une destination sous surveillance du releve quotidien."""
    chemin = chemin or collecteur.CHEMIN_DESTINATIONS_PERSO
    perso = collecteur.charger_destinations_perso(chemin)

    if code not in perso and len(perso) >= MAX_DESTINATIONS_PERSO:
        raise ValueError(
            f"Plafond atteint : {MAX_DESTINATIONS_PERSO} destinations "
            f"personnelles au maximum (chacune coute "
            f"{len(collecteur.HUBS)} appels par releve). "
            f"Retires-en une avec --oublier.")

    perso[code] = collecteur.DESTINATIONS.get(code, code)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(perso, f, ensure_ascii=False, indent=2, sort_keys=True)


def retirer_destination(code, chemin=None):
    """Retire une destination de la surveillance. Renvoie True si elle y
    etait, False sinon."""
    chemin = chemin or collecteur.CHEMIN_DESTINATIONS_PERSO
    perso = collecteur.charger_destinations_perso(chemin)
    if code not in perso:
        return False
    del perso[code]
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(perso, f, ensure_ascii=False, indent=2, sort_keys=True)
    return True


USAGE = """Usage :
  python recherche.py <ville> <destination>   cherche un itineraire
  python recherche.py --surveiller <dest>     ajoute au releve quotidien
  python recherche.py --oublier <dest>        retire du releve quotidien
  python recherche.py --liste                 destinations surveillees

Villes disponibles : {villes}
La destination est un code IATA (BKK) ou le nom d'une destination connue."""


def main(argv):
    villes = ", ".join(sorted(collecteur.RABATTEMENT))

    if not argv:
        print(USAGE.format(villes=villes))
        return 1

    try:
        if argv[0] == "--liste":
            # chemin passe explicitement : un argument par defaut est fige a
            # la definition de la fonction, donc lire l'attribut du module
            # ici est le seul moyen d'en tenir compte s'il a ete change.
            perso = collecteur.charger_destinations_perso(
                collecteur.CHEMIN_DESTINATIONS_PERSO)
            if not perso:
                print("Aucune destination personnelle sous surveillance.")
            else:
                cout = len(perso) * len(collecteur.HUBS)
                print(f"{len(perso)} destination(s) surveillee(s) "
                      f"(+{cout} appels par releve) :")
                for code, nom in sorted(perso.items()):
                    print(f"  {code}  {nom}")
            return 0

        if argv[0] == "--surveiller":
            code = resoudre_destination(argv[1])
            ajouter_destination(code)
            print(f"{code} ajoute au releve quotidien "
                  f"(+{len(collecteur.HUBS)} appels par releve).")
            return 0

        if argv[0] == "--oublier":
            code = resoudre_destination(argv[1])
            if retirer_destination(code):
                print(f"{code} retire du releve quotidien.")
            else:
                print(f"{code} n'etait pas sous surveillance.")
            return 0

        if len(argv) < 2:
            print(USAGE.format(villes=villes))
            return 1

        ville = valider_ville(argv[0])
        dest = resoudre_destination(argv[1])

    except ValueError as e:
        print(f"Erreur : {e}")
        return 1
    except IndexError:
        print(USAGE.format(villes=villes))
        return 1

    if not collecteur.TOKEN:
        print("Erreur : TRAVELPAYOUTS_TOKEN absent de l'environnement.")
        return 1

    print(f"Recherche en cours ({1 + 2 * len(collecteur.RABATTEMENT[ville])} "
          f"appels API, une dizaine de secondes)...")
    try:
        options, erreurs = chercher_itineraires(ville, dest)
    except ValueError as e:
        print(f"Erreur : {e}")
        return 1

    conn = sqlite3.connect(collecteur.DB_PATH)
    try:
        contexte = contexte_historique(conn, ville, dest)
    finally:
        conn.close()

    print(formater(ville, dest, options, erreurs, contexte))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
