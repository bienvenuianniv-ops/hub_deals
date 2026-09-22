"""
Detecteur de bonnes affaires -- version avec stockage SQLite.

Chaque lancement du script :
  1. Interroge, pour chaque hub, une liste de destinations IMPOSEE
     (matrice hubs x destinations)
  2. Enregistre les prix dans une base SQLite locale (historique cumulatif)
  3. Compare a la moyenne historique et notifie les anomalies

Changement majeur par rapport aux versions precedentes : on n'utilise plus
get_special_offers (qui renvoyait ce que le cache Aviasales contenait --
majoritairement des routes CEI/Asie centrale, car sa base d'utilisateurs
est russophone). On impose desormais origine ET destination via
v1/prices/cheap, ce qui donne une couverture choisie : Europe occidentale,
Amerique, Asie, Golfe, Afrique.

Usage :
    export TRAVELPAYOUTS_TOKEN="ton_token"
    export TELEGRAM_BOT_TOKEN="ton_token_bot"
    export TELEGRAM_CHAT_ID="ton_chat_id"
    python3 hub_deals_db.py

Secrets geres exclusivement via variables d'environnement (voir setx / tache
planifiee "Traqueur de vols" pour la persistance cote Windows).
"""

import os
import json
import re
import sqlite3
import time
import requests
from datetime import datetime, timezone

from anomaly_detection import detecter_anomalies

TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN")
BASE_URL = "https://api.travelpayouts.com"
DB_PATH = "flight_deals.db"
LOG_PATH = "flight_deals_log.txt"

# Telegram : optionnel -- si absent, envoyer_telegram() ne fait rien (voir plus bas)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Identifiant d'affilie Travelpayouts. Absent : liens sans parametres
# affilies (comportement d'avant le 2026-09-15), jamais de blocage.
TRAVELPAYOUTS_MARKER = os.environ.get("TRAVELPAYOUTS_MARKER")

# Identifiant du projet Travelpayouts (« trs », parametre source= dans
# l'adresse du tableau de bord). Absent : liens directs, jamais de blocage.
TRAVELPAYOUTS_PROJET = os.environ.get("TRAVELPAYOUTS_PROJET")

# Liens courts du releve en cours, (chemin, etiquette) -> URL courte.
# Rempli par preparer_liens_courts() juste avant l'envoi des alertes.
LIENS_COURTS: dict = {}
LIENS_PAR_REQUETE = 10  # plafond de l'API links/v1/create

LIMITE_TELEGRAM = 4096  # plafond impose par l'API Telegram sur sendMessage.
                        # Au-dela : HTTP 400 « message is too long », et le
                        # message entier est perdu -- pas tronque.

HUBS = {
    "CMN": {"nom": "Casablanca"},
    "CDG": {"nom": "Paris"},
    "IST": {"nom": "Istanbul"},
    "ADD": {"nom": "Addis-Abeba"},
    "NBO": {"nom": "Nairobi"},
    "ABJ": {"nom": "Abidjan"},
    "JNB": {"nom": "Johannesburg"},
    "CAI": {"nom": "Le Caire"},
    "LOS": {"nom": "Lagos"},
    # Ajoutes le 2026-09-19 : ces villes ne sont pas des hubs de
    # correspondance, elles sont interrogees pour que leurs propres
    # habitants voient leurs vols directs. Aucune autre ville n'a de
    # rabattement vers eux, la boucle d'insertion les saute donc d'elle-meme.
    "DKR": {"nom": "Dakar"},
    "FIH": {"nom": "Kinshasa"},
    "BZV": {"nom": "Brazzaville"},
    "LFW": {"nom": "Lome"},
}

# Destinations surveillees -- c'est NOUS qui les imposons, au lieu de subir
# ce que le cache Aviasales remonte. Choisies parmi les places a fort trafic
# aerien sur chaque continent. Ajouter/retirer une ligne suffit : aucun autre
# changement de code necessaire.
DESTINATIONS = {
    # Europe occidentale
    "LON": "Londres", "PAR": "Paris", "MAD": "Madrid", "BCN": "Barcelone",
    "LIS": "Lisbonne", "ROM": "Rome", "MIL": "Milan", "FRA": "Francfort",
    "AMS": "Amsterdam", "BRU": "Bruxelles",
    # Amerique
    "NYC": "New York", "WAS": "Washington", "YTO": "Toronto", "SAO": "Sao Paulo",
    # Asie / Golfe
    "DXB": "Dubai", "DOH": "Doha", "IST": "Istanbul", "BJS": "Pekin",
    "CAN": "Guangzhou", "BOM": "Mumbai", "BKK": "Bangkok",
    # Afrique
    "CMN": "Casablanca", "CAI": "Le Caire", "LOS": "Lagos", "ACC": "Accra",
    "ABJ": "Abidjan", "NBO": "Nairobi", "ADD": "Addis-Abeba",
    "JNB": "Johannesburg", "DKR": "Dakar", "BZV": "Brazzaville", "FIH": "Kinshasa",
}
# Certains codes designent la meme ville : CDG est un aeroport de PAR.
# L'API renvoie 400 si origine et destination sont la meme ville.
EQUIVALENCES = {
    "CDG": {"PAR"},
    "PAR": {"CDG"},
}

# Destinations ajoutees par l'utilisateur via recherche.py --surveiller.
# Fichier de confort, local et non versionne : un releve ne doit jamais
# echouer parce qu'il est absent ou mal forme.
CHEMIN_DESTINATIONS_PERSO = "destinations_perso.json"


def charger_destinations_perso(chemin=CHEMIN_DESTINATIONS_PERSO):
    """Lit les destinations personnelles. Renvoie {} si le fichier est
    absent, illisible ou mal forme -- jamais d'exception."""
    try:
        with open(chemin, encoding="utf-8") as f:
            contenu = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(contenu, dict):
        return {}
    return {str(k): str(v) for k, v in contenu.items()}


def destinations_actives(chemin=CHEMIN_DESTINATIONS_PERSO):
    """Destinations imposees + destinations personnelles.

    Les originales ne sont jamais ecrasees : en cas de doublon, c'est le
    nom d'origine qui prime."""
    return {**charger_destinations_perso(chemin), **DESTINATIONS}

# Code IATA de chaque ville de depart. Sert a ne pas enregistrer de route
# qui ramene une ville chez elle : plusieurs villes de depart figurent aussi
# dans DESTINATIONS (DKR, ABJ, BZV, FIH), et sans ce garde-fou on produit des
# lignes « Dakar -> via Paris -> Dakar ». L'exclusion ne peut PAS se faire
# dans la boucle d'appels API : la route CDG->DKR reste parfaitement valable
# pour Abidjan, Lome, Brazzaville et Kinshasa. Elle se fait donc a
# l'insertion, ville par ville.
#
# Toute ville ajoutee a RABATTEMENT doit avoir son code ici -- un test
# structurel le verifie.
VILLE_IATA = {
    "Dakar": "DKR",
    "Abidjan": "ABJ",
    "Brazzaville": "BZV",
    "Lome": "LFW",
    "Kinshasa": "FIH",
    # villes residentes ajoutees le 2026-09-19
    "Paris": "PAR",          # PAR est le code destination ; CDG, le code hub
    "Istanbul": "IST",
    "Casablanca": "CMN",
    "Le Caire": "CAI",
    "Lagos": "LOS",
    "Nairobi": "NBO",
    "Addis-Abeba": "ADD",
    "Johannesburg": "JNB",
}

# Pause entre deux appels API -- evite de saturer Travelpayouts.
# 13 hubs x 32 destinations, moins les auto-exclusions = 404 appels. Ce
# total ne depend PAS du nombre de villes de depart : celles-ci se
# contentent de demultiplier les lignes inserees.
#
# La duree d'un releve n'est PAS 404 x 0.4s (2m41) : cette pause n'est
# qu'une borne basse volontaire, pas le cout reel d'un appel. Mesure sur
# le journal du releve du 2026-09-19 : 381s pour 279 appels, soit 1.37s
# par appel, latence de l'API Travelpayouts comprise -- plus de trois
# fois la pause. A 404 appels, compter ~9 minutes, pas ~3.
PAUSE_ENTRE_APPELS = 0.4

# Cout de rabattement par ville de depart -> chaque hub.
#
#   Dakar       : estimation manuelle arrondie (prix reels de juillet 2026).
#   Abidjan     : valeurs exactes obtenues par requete directe a l'API
#                 Travelpayouts le 2026-08-03 (v1/prices/cheap, complete par
#                 v3/prices_for_dates pour NBO) -- ADD omis, aucune donnee
#                 disponible pour cette route ; pas d'entree ABJ->ABJ,
#                 Abidjan etant deja un hub.
#   Brazzaville : partiellement estime, voir les commentaires en ligne.
#   Lome (LFW)  : releve reel du 2026-08-15, meme methode qu'Abidjan.
#                 ADD et JNB omis : aucun prix sur aucun des endpoints.
#   Kinshasa (FIH) : releve reel du 2026-08-15, les 9 hubs couverts.
#
# duree_h est indicative (elle n'entre dans aucun calcul) : pour Lome et
# Kinshasa c'est la duree d'itineraire renvoyee par v3/prices_for_dates,
# escales comprises -- d'ou des valeurs plus elevees que les estimations
# "temps de vol" des premieres villes.
#
# Ajouter une ville = ajouter une entree ici, meme structure -- aucun autre
# changement de code necessaire, et AUCUN appel API supplementaire : le prix
# du vol hub->destination est interroge une seule fois puis reutilise pour
# chaque ville de depart.
# Provenance de chaque valeur, marquee en fin de ligne :
#   [M]  mesure le 2026-08-16 via v1/prices/cheap (23 segments)
#   [M3] mesure le 2026-08-16 via v3/prices_for_dates (5 segments, tous
#        CDG) -- v1 ne renvoie rien pour ces routes, mais v3 si
#   [NM] aucun prix sur AUCUN des trois endpoints (12 segments) : valeur
#        conservee, potentiellement vieillie ; son age est indique en
#        tete de bloc
#
# Les 12 [NM] ne sont pas des oublis. Aucune valeur n'est inventee pour
# les combler.
#
# ATTENTION -- PIEGE ALLER SIMPLE / ALLER-RETOUR. v1/prices/cheap renvoie
# des ALLER-RETOUR. v2/prices/latest et v3/prices_for_dates acceptent un
# parametre one_way qui, laisse a "true", renvoie des allers simples
# environ 43 % moins chers. Toute mesure faite avec ces endpoints DOIT
# passer one_way=false, sinon la table melange deux natures de prix et
# sous-estime massivement. Controle de non-regression fait le 2026-08-16
# sur DKR->CMN, couvert par les trois : v1=468, v2=467, v3=468 en
# aller-retour -- les endpoints concordent, les valeurs sont comparables.
#
# Les 5 [M3] etaient auparavant les valeurs les plus fausses de la table
# alors que Paris sort en tete de 20 des 20 meilleurs prix d'un releve :
# le classement etait donc structurellement biaise en faveur de Paris,
# par artefact de cette table et non par realite du marche.
RABATTEMENT = {
    # mesure 2026-08-16 ; les [NM] datent d'une estimation manuelle de
    # juillet 2026 et sont donc les plus suspectes de la table
    "Dakar": {
        "CMN": {"prix": 468, "duree_h": 4},   # [M] etait 400
        "CDG": {"prix": 496, "duree_h": 6},   # [M3] etait 300 (+65 %)
        "IST": {"prix": 525, "duree_h": 7},   # [M] etait 400
        "ADD": {"prix": 500, "duree_h": 6},   # [NM]
        "NBO": {"prix": 500, "duree_h": 8},   # [NM]
        "ABJ": {"prix": 409, "duree_h": 2},   # [M] etait 200
        "JNB": {"prix": 500, "duree_h": 10},  # [NM]
        "CAI": {"prix": 380, "duree_h": 7},   # [NM]
        "LOS": {"prix": 450, "duree_h": 4},   # [NM]
        "DKR": {"prix": 0, "duree_h": 0},
    },
    # mesure 2026-08-16 ; les [NM] datent du releve API du 2026-08-03
    "Abidjan": {
        "CMN": {"prix": 574, "duree_h": 3},   # [M] etait 563
        "CDG": {"prix": 486, "duree_h": 8},   # [M3] etait 511 -- SUR-estime
        "IST": {"prix": 672, "duree_h": 9},   # [M] etait 700 -- SUR-estime
        "NBO": {"prix": 883, "duree_h": 8},   # [M] etait 374
        "JNB": {"prix": 350, "duree_h": 9},   # [NM]
        "CAI": {"prix": 715, "duree_h": 6},   # [M] etait 340
        "LOS": {"prix": 806, "duree_h": 2},   # [M] etait 400
        "ABJ": {"prix": 0, "duree_h": 0},
    },
    # mesure 2026-08-16 ; les [NM] etaient des estimations manuelles
    "Brazzaville": {
        "CMN": {"prix": 700, "duree_h": 6},   # [NM]
        "CDG": {"prix": 1306, "duree_h": 7},  # [M3] etait 600 (+118 %)
        "IST": {"prix": 800, "duree_h": 9},   # [NM]
        "ADD": {"prix": 700, "duree_h": 5},   # [NM]
        "NBO": {"prix": 970, "duree_h": 6},   # [M] etait 750 (estimation)
        "JNB": {"prix": 634, "duree_h": 4},   # [M] etait 450 (estimation)
        "CAI": {"prix": 1193, "duree_h": 8},  # [M] etait 750 (estimation)
        "LOS": {"prix": 1083, "duree_h": 3},  # [M] etait 400
        "BZV": {"prix": 0, "duree_h": 0},
    },
    # mesure 2026-08-16 ; les [NM] datent du releve API du 2026-08-15,
    # donc encore frais
    "Lome": {
        "CMN": {"prix": 1289, "duree_h": 4},  # [M] inchange
        "CDG": {"prix": 862, "duree_h": 12},  # [M3] etait 279 (+209 %)
        "IST": {"prix": 718, "duree_h": 11},  # [NM]
        "NBO": {"prix": 848, "duree_h": 9},   # [M] etait 849 ; duree estimee
        "ABJ": {"prix": 434, "duree_h": 2},   # [M] etait 441
        "CAI": {"prix": 552, "duree_h": 13},  # [NM]
        "LOS": {"prix": 313, "duree_h": 7},   # [NM]
        "LFW": {"prix": 0, "duree_h": 0},
    },
    # mesure 2026-08-16 ; les [NM] datent du releve API du 2026-08-15.
    # Ville la plus fiable : 8 des 9 valeurs confirmees inchangees a un
    # jour d'intervalle -- c'est ce qui a montre que le probleme de cette
    # table est le VIEILLISSEMENT, pas un biais systematique.
    "Kinshasa": {
        "CMN": {"prix": 738, "duree_h": 7},   # [M] inchange
        "CDG": {"prix": 708, "duree_h": 11},  # [M3] etait 377 (+88 %)
        "IST": {"prix": 651, "duree_h": 12},  # [M] etait 650
        "ADD": {"prix": 682, "duree_h": 21},  # [M] inchange
        "NBO": {"prix": 555, "duree_h": 13},  # [M] inchange
        "ABJ": {"prix": 796, "duree_h": 6},   # [M] inchange ; duree estimee
        "JNB": {"prix": 321, "duree_h": 22},  # [M] inchange
        "CAI": {"prix": 412, "duree_h": 6},   # [M] inchange
        "LOS": {"prix": 630, "duree_h": 8},   # [M] inchange
        "FIH": {"prix": 0, "duree_h": 0},
    },
    # --- Villes residentes, ajoutees le 2026-09-19 ---------------------
    # Un resident n'est pas un concept nouveau : c'est une ville dont le
    # rabattement vers son propre hub vaut 0. Le 0 est la valeur sincere,
    # pas un code d'exception -- et c'est lui qui bascule le plancher
    # d'economie sur sa forme relative (voir plancher_economie).
    #
    # Une seule entree par ville, volontairement : les correspondances
    # pour residents produisent des itineraires absurdes (Paris -> Abidjan
    # -> Rome a 1048 EUR quand notre propre base a le direct a 88 EUR).
    "Paris": {"CDG": {"prix": 0, "duree_h": 0}},
    "Istanbul": {"IST": {"prix": 0, "duree_h": 0}},
    "Casablanca": {"CMN": {"prix": 0, "duree_h": 0}},
    "Le Caire": {"CAI": {"prix": 0, "duree_h": 0}},
    "Lagos": {"LOS": {"prix": 0, "duree_h": 0}},
    "Nairobi": {"NBO": {"prix": 0, "duree_h": 0}},
    "Addis-Abeba": {"ADD": {"prix": 0, "duree_h": 0}},
    "Johannesburg": {"JNB": {"prix": 0, "duree_h": 0}},
}


def init_db(conn: sqlite3.Connection) -> None:
    """Cree la table si elle n'existe pas encore, et applique les
    migrations de schema necessaires. Idempotent -- sans danger a
    re-executer a chaque lancement du script."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS offres (
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
    colonnes = [row[1] for row in conn.execute("PRAGMA table_info(offres)")]
    if "ville_depart" not in colonnes:
        conn.execute("ALTER TABLE offres ADD COLUMN ville_depart TEXT NOT NULL DEFAULT 'Dakar'")
    conn.commit()


def get_prix_route(origin: str, destination: str) -> dict:
    """
    Prix en cache pour UNE route precise. Contrairement a
    get_special_offers, c'est nous qui imposons la destination --
    on ne subit plus ce que le cache Aviasales contient.

    Renvoie {} si aucun prix connu pour cette route, sinon l'option
    la moins chere parmi celles renvoyees.
    """
    url = f"{BASE_URL}/v1/prices/cheap"
    params = {
        "origin": origin,
        "destination": destination,
        "currency": "eur",
        "token": TOKEN,
    }
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json().get("data", {})
    # Structure renvoyee : {"MAD": {"0": {"price": 120, "airline": "...",
    #   "departure_at": "...", "return_at": "...", ...}}}
    offres = data.get(destination, {})
    if not offres:
        return {}
    return min(offres.values(), key=lambda o: o.get("price") or 999999)


def get_prix_segment(origin: str, destination: str) -> dict:
    """
    Prix en cache pour un trajet ville -> hub, via v3/prices_for_dates.

    Sert a mesurer le rabattement au moment de l'alerte. v1/prices/cheap
    (get_prix_route) ne renvoie rien pour les segments vers Paris, qui ne
    furent donc jamais mesures ; sonde du 2026-09-13 sur les 40 segments :
    v1 en couvre 17, v3 21 -- les memes 17 au meme prix, plus 4 vers CDG.

    one_way=false est OBLIGATOIRE : voir le piege aller simple /
    aller-retour documente au-dessus de RABATTEMENT.

    Renvoie {} si aucun prix connu, sinon l'offre la moins chere. Leve une
    RequestException sur erreur HTTP, que l'appelant rattrape.
    """
    url = f"{BASE_URL}/aviasales/v3/prices_for_dates"
    params = {
        "origin": origin,
        "destination": destination,
        "currency": "eur",
        "one_way": "false",
        "sorting": "price",
        "limit": 30,
        "token": TOKEN,
    }
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    offres = [o for o in (response.json().get("data") or []) if o.get("price")]
    if not offres:
        return {}
    return min(offres, key=lambda o: o["price"])


def construire_lien(origin: str, destination: str, departure_at: str) -> str:
    """
    Reconstruit un lien de recherche Aviasales. v1/prices/cheap ne renvoie
    pas de lien direct (contrairement a get_special_offers), donc on le
    fabrique au format attendu : /search/{ORIG}{JJMM}{DEST}1
    """
    if not departure_at:
        return ""
    try:
        # departure_at ressemble a "2026-09-01T20:35:00Z" ou avec offset
        date_part = departure_at[:10]
        annee, mois, jour = date_part.split("-")
        return f"/search/{origin}{jour}{mois}{destination}1"
    except (ValueError, IndexError):
        return ""


def etiquette_ville(ville: str) -> str:
    """
    Normalise un nom de ville en etiquette (SubID) valide pour
    url_aviasales et raccourcir_liens : voir le docstring de url_aviasales
    pour la regle exacte (source Travelpayouts), lettres latines, chiffres
    et _ uniquement.

    A appeler aux DEUX sites qui fabriquent l'etiquette -- hub_deals_db.py
    (paires envoyees a l'API pour creer les liens courts) et abonnes.py
    (cle de recherche dans LIENS_COURTS au moment de composer le message
    de l'abonne) -- jamais chacun de son cote : sinon elles divergent des
    qu'une ville a un nom compose, la cle ne correspond plus, le lien
    court cree n'est jamais retrouve, et l'abonne recoit le lien direct --
    prouve le 2026-09-16 comme n'etant PAS compte comme clic.

    « Le Caire » et « Addis-Abeba », premieres villes du projet a nom
    compose, ont revele ce defaut : leur etiquette brute contenait une
    espace ou un tiret. Les etiquettes deja valides (un seul mot en
    minuscules, comme « Dakar ») ressortent inchangees, ce qui preserve
    les liens courts deja en circulation.
    """
    return re.sub(r"[^a-z0-9_]", "_", ville.lower())


def url_aviasales(chemin: str, etiquette: str) -> str:
    """
    Lien complet vers Aviasales, avec l'identifiant d'affilie et une
    etiquette (SubID) qui separe les clics par destinataire dans le
    tableau de bord Travelpayouts : une ville par abonne, 'proprietaire'
    pour le message complet.

    Source de la syntaxe : aide Travelpayouts « ID and SubID (Affiliate
    marker and additional marker) » et « Aviasales affiliate links »
    (extraits lus le 2026-09-15, pages elles-memes en 403) : le lien porte
    marker=<ID>, et le SubID suit l'ID apres un point ; lettres latines,
    chiffres et _ uniquement. A confirmer par un clic reel visible dans le
    tableau de bord avec son etiquette.

    Constat du 2026-09-16 : ce lien direct n'est PAS compte comme clic
    (0 clic pour celui du 15/09), un lien court aviasales.tpk.ro l'est.
    Le lien court est donc prefere des qu'il a pu etre cree.
    """
    court = LIENS_COURTS.get((chemin, etiquette))
    if court:
        return court
    url = f"https://www.aviasales.com{chemin}"
    if not TRAVELPAYOUTS_MARKER:
        return url
    return f"{url}?marker={TRAVELPAYOUTS_MARKER}.{etiquette}"


def raccourcir_liens(paires, poster=requests.post) -> dict:
    """
    Cree les liens courts Travelpayouts pour des paires (chemin, etiquette).

    API documentee « API for Travelpayouts partner links » (lue le
    2026-09-16) : POST links/v1/create, au plus 10 liens par requete. Un
    lien peut echouer sous une reponse globale 200/success : on lit donc le
    code de CHAQUE lien. En-tete X-Access-Token prouve le 2026-09-16 (token
    bidon -> 401, projet bidon -> 400 « invalid traffic source »).

    Ne leve jamais : toute paire non convertie garde son lien direct.
    """
    if not (TRAVELPAYOUTS_PROJET and TRAVELPAYOUTS_MARKER):
        return {}
    uniques = list(dict.fromkeys(paires))
    courts = {}
    for debut in range(0, len(uniques), LIENS_PAR_REQUETE):
        lot = uniques[debut:debut + LIENS_PAR_REQUETE]
        corps = {
            "trs": int(TRAVELPAYOUTS_PROJET),
            "marker": int(TRAVELPAYOUTS_MARKER),
            "shorten": True,
            "links": [{"url": f"https://www.aviasales.com{chemin}", "sub_id": etiquette}
                      for chemin, etiquette in lot],
        }
        try:
            r = poster(f"{BASE_URL}/links/v1/create", json=corps,
                       headers={"X-Access-Token": TOKEN}, timeout=20)
            if r.status_code != 200:
                log(f"   -> liens courts refuses : HTTP {r.status_code} {r.text[:200]}")
                continue
            resultats = r.json()["result"]["links"]
        except Exception as e:
            log(f"   -> liens courts impossibles : {e}")
            continue
        # association par position : sans compte exact, on ne saurait pas
        # quel lien court revient a quel destinataire
        if len(resultats) != len(lot):
            log(f"   -> liens courts ignores : {len(resultats)} recus pour {len(lot)} demandes")
            continue
        for paire, res in zip(lot, resultats):
            if res.get("code") == "success" and res.get("partner_url"):
                courts[paire] = res["partner_url"]
            else:
                log(f"   -> lien court non cree ({paire[1]}) : {res.get('message')}")
    return courts


def preparer_liens_courts(groupes: list) -> None:
    """Remplit LIENS_COURTS pour les liens que le releve va envoyer : celui
    du proprietaire (premier de chaque groupe) et celui de chaque ville
    (messages des abonnes). Ne leve jamais."""
    import page
    global LIENS_COURTS
    LIENS_COURTS = {}
    paires = []
    for groupe in groupes:
        paires.append((groupe[0]["lien"], "proprietaire"))
        paires.extend((a["lien"], etiquette_ville(a["ville_depart"])) for a in groupe)
        # la page publique a ses propres etiquettes : c'est ce qui rendra
        # son trafic distinguable de celui du bot dans Travelpayouts
        paires.extend((a["lien"], page.etiquette_page(a["ville_depart"]))
                      for a in groupe)
    try:
        LIENS_COURTS = raccourcir_liens(paires)
    except Exception as e:
        log(f"   -> liens courts impossibles : {e}")
        return
    if TRAVELPAYOUTS_PROJET and TRAVELPAYOUTS_MARKER:
        log(f"Liens courts : {len(LIENS_COURTS)}/{len(set(paires))} cree(s).")


def enregistrer_prix(conn: sqlite3.Connection, hub_iata: str, dest_iata: str,
                     offre: dict, date_collecte: str, dest_nom: str = None) -> int:
    """
    Insere un prix de route dans la base, une fois par ville de depart
    ayant un cout de rabattement defini pour ce hub.

    Point important : on n'interroge l'API qu'UNE fois par couple
    (hub, destination), puis on insere une ligne par ville. Interroger
    une fois par ville triplerait les appels pour un resultat identique.

    `dest_nom` permet a l'appelant de fournir le nom d'une destination
    personnelle (absente de DESTINATIONS) sans que cette fonction ait a
    relire le fichier : elle est appelee une fois par route trouvee,
    ~200 fois par releve.

    Renvoie le nombre de lignes inserees.
    """
    hub_nom = HUBS[hub_iata]["nom"]
    dest_nom = dest_nom or DESTINATIONS.get(dest_iata, dest_iata)
    prix_vol = offre.get("price") or 0
    date_depart = offre.get("departure_at") or ""
    lien = construire_lien(hub_iata, dest_iata, date_depart)

    lignes = 0
    for ville, couts_ville in RABATTEMENT.items():
        rabattement = couts_ville.get(hub_iata)
        if rabattement is None:
            continue
        # pas de route qui ramene la ville chez elle
        if dest_iata == VILLE_IATA.get(ville):
            continue
        total_estime = prix_vol + rabattement["prix"]
        conn.execute("""
            INSERT INTO offres (
                date_collecte, ville_depart, hub_origine, destination_code, destination_nom,
                prix_vol_hub, rabattement, total_estime, date_depart, lien
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_collecte,
            ville,
            hub_nom,
            dest_iata,
            dest_nom,
            prix_vol,
            rabattement["prix"],
            total_estime,
            date_depart,
            lien,
        ))
        lignes += 1
    return lignes


def classement_du_jour(conn: sqlite3.Connection, date_collecte: str) -> list:
    """Recupere le classement des offres collectees a cette date precise."""
    cur = conn.execute("""
        SELECT destination_nom, destination_code, hub_origine,
               prix_vol_hub, rabattement, total_estime, date_depart, lien
        FROM offres
        WHERE date_collecte = ?
        ORDER BY total_estime ASC
    """, (date_collecte,))
    colonnes = [d[0] for d in cur.description]
    return [dict(zip(colonnes, ligne)) for ligne in cur.fetchall()]


def mesurer_rabattements(couples, get_prix=None, pause: bool = True) -> dict:
    """
    Interroge l'API pour le cout reel de chaque trajet ville -> hub.

    La table RABATTEMENT vieillit : mesure du 2026-08-16, 23 des 40
    segments ont un prix API, avec des ecarts allant de -4 % a +171 %
    selon l'anciennete de la valeur. On mesure donc au moment de
    l'alerte, sans jamais toucher a ce qui est enregistre en base.

    La liste des segments mesurables CHANGE d'un jour a l'autre (c'est un
    cache) : les marques [M]/[NM] de la table sont un instantane, pas une
    propriete durable. 21 des 40 repondaient le 2026-09-13.

    `couples` porte le NOM du hub (« Paris »), comme la colonne
    hub_origine des anomalies -- pas le code IATA.

    Renvoie {(ville, hub_nom): {"prix", "table", "mesure"}}. `table` est
    rendue avec la mesure pour que le calcul du decalage reste une
    fonction pure de son entree.
    """
    if get_prix is None:
        get_prix = get_prix_segment   # v3 : v1 ne couvre pas les segments vers Paris

    iata_par_nom = {info["nom"]: iata for iata, info in HUBS.items()}
    mesures = {}

    # dict.fromkeys dedoublonne en preservant l'ordre : plusieurs
    # anomalies partagent souvent le meme couple, un seul appel suffit
    for ville, hub_nom in dict.fromkeys(couples):
        hub_iata = iata_par_nom.get(hub_nom)
        if hub_iata is None:
            continue  # nom inconnu : on ignore plutot que de lever

        cout = RABATTEMENT.get(ville, {}).get(hub_iata)
        if cout is None:
            continue  # pas de rabattement connu pour ce couple

        # rabattement nul = l'abonne reside dans la ville du hub. Mesurer
        # ce trajet interrogerait l'API sur une ville vers elle-meme, qui
        # repond 400 (voir EQUIVALENCES).
        if cout["prix"] == 0:
            mesures[(ville, hub_nom)] = {"prix": 0, "table": 0, "mesure": False}
            continue

        repli = {"prix": cout["prix"], "table": cout["prix"], "mesure": False}
        origine = VILLE_IATA.get(ville)
        if origine is None:
            mesures[(ville, hub_nom)] = repli
            continue

        try:
            offre = get_prix(origine, hub_iata)
        except requests.exceptions.RequestException as e:
            # log() masque les secrets : l'URL de l'exception porte le token
            log(f"   -> rabattement {origine}->{hub_iata} non mesure : {e}")
            mesures[(ville, hub_nom)] = repli
            continue

        if pause:
            time.sleep(PAUSE_ENTRE_APPELS)

        if offre and offre.get("price"):
            mesures[(ville, hub_nom)] = {
                "prix": offre["price"],
                "table": cout["prix"],
                "mesure": True,
            }
        else:
            mesures[(ville, hub_nom)] = repli

    return mesures


def corriger_anomalies(anomalies: list, mesures: dict) -> list:
    """
    Applique le rabattement mesure au prix du jour ET a la moyenne.

    Le rabattement est une constante additive de tout l'historique d'une
    route : la meme valeur entre dans chacune de ses lignes. Decaler les
    deux du meme montant preserve donc exactement l'ecart absolu et
    l'ecart-type, donc le z-score. Seul le pourcentage change, son
    denominateur ayant augmente.

    Hypothese assumee : on substitue une constante a une autre. Le total
    affiche est « ce que vaudrait cette route si le rabattement mesure
    aujourd'hui s'appliquait a tout l'historique ». C'est la seule
    transformation qui garde tous les chiffres du message coherents.

    On NE refiltre PAS sur le pourcentage corrige, et c'est delibere.
    Mesure du 2026-09-12 : la correction abaisse le pourcentage dans 27 cas
    sur 34, de 0.9 point en mediane, mais elle le REMONTE dans les 7 autres.
    Or le rabattement n'est mesure que pour les routes deja detectees : un
    refiltrage ne pourrait donc qu'en retirer, jamais rattraper celles que
    la meme correction ferait passer au-dessus du plancher. Il serait
    unilateral, et couperait des affaires reelles pour un effet de
    denominateur.

    La coherence du message est assuree autrement : l'economie en euros,
    elle, est exactement preservee par le decalage (l'ecart absolu ne
    bouge pas), et c'est un critere de declenchement a part entiere depuis
    le recalibrage. C'est donc elle que le message affiche pour justifier
    l'alerte, plutot qu'un pourcentage qui depend du rabattement.

    Les anomalies d'origine ne sont pas modifiees.
    """
    corrigees = []
    for a in anomalies:
        b = dict(a)
        mesure = mesures.get((a["ville_depart"], a["hub"]))

        if mesure and mesure["mesure"]:
            delta = mesure["prix"] - mesure["table"]
            b["prix_actuel"] = a["prix_actuel"] + delta
            b["moyenne_historique"] = a["moyenne_historique"] + delta
            if b["moyenne_historique"] > 0:
                b["baisse_pct"] = round(
                    (b["moyenne_historique"] - b["prix_actuel"])
                    / b["moyenne_historique"] * 100, 1)
            b["rabattement_mesure"] = mesure["prix"]
        else:
            b["rabattement_mesure"] = None

        corrigees.append(b)

    # le decalage change les pourcentages : sans re-tri, l'ordre affiche
    # ne correspondrait plus aux pourcentages affiches
    corrigees.sort(key=lambda x: x["baisse_pct"], reverse=True)
    return corrigees


def masquer_secrets(message: str) -> str:
    """
    Remplace les secrets par *** dans un message destine au log.

    Necessaire parce que requests place l'URL COMPLETE dans ses exceptions
    reseau -- token de query string compris. Le message d'erreur brut
    contient donc le token Travelpayouts en clair, et l'URL de l'API
    Telegram porte le token du bot dans son chemin (/bot<token>/sendMessage).

    Le chat_id n'est volontairement PAS masque : il ne circule que dans le
    corps du POST (donc jamais dans une exception), et c'est souvent un
    nombre court -- le remplacer aveuglement mutilerait des messages
    legitimes contenant la meme suite de chiffres.
    """
    for secret in (TOKEN, TELEGRAM_BOT_TOKEN):
        if secret:
            message = message.replace(secret, "***")
    return message


def log(message: str) -> None:
    """Ecrit dans la console ET dans un fichier log -- utile car quand la
    tache tourne via le Planificateur (pas depuis PowerShell), les print()
    normaux ne s'affichent nulle part et on ne peut jamais voir si ca a
    plante ni pourquoi.

    Le masquage des secrets se fait ici, et non chez les appelants : c'est
    le point de passage unique de tout ce qui est journalise, donc le seul
    endroit ou l'oubli est impossible."""
    horodatage = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ligne = f"[{horodatage}] {masquer_secrets(message)}"
    print(ligne)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(ligne + "\n")


def journaliser_plantage(type_exc, valeur, trace) -> None:
    """Crochet sys.excepthook : ecrit la trace d'un plantage dans le journal.

    La tache tourne sous pythonw.exe, pour qu'aucune fenetre ne s'ouvre (et
    ne soit fermee par megarde : 4 releves tues ainsi en septembre 2026).
    Contrepartie : sys.stderr vaut None, et la trace d'une exception non
    rattrapee ne s'afficherait NULLE PART. Passe par log(), donc masquee.
    """
    import traceback
    log("=== PLANTAGE du releve ===")
    log("".join(traceback.format_exception(type_exc, valeur, trace)).rstrip())


def journaliser_message(message: str, entete: str) -> None:
    """Ecrit dans le journal le message destine a Telegram, encadre par des
    marqueurs.

    Necessaire parce qu'un bot ne peut PAS relire ses propres messages
    sortants : l'API Telegram n'expose que ce qu'il RECOIT (getUpdates).
    Sans cette trace, verifier apres coup ce qui a ete notifie suppose
    d'avoir le telephone sous la main.

    Le corps est conserve tel quel, HTML compris, pour rester fidele a ce
    qui part reellement. Il passe par log(), donc par masquer_secrets().
    """
    log(f"--- {entete} ---")
    log(message)
    log("--- fin du message ---")


def decouper_message(blocs: list, entete: str, pied: str = "") -> list:
    """Repartit des blocs de texte en messages respectant LIMITE_TELEGRAM.

    Telegram refuse tout sendMessage de plus de 4096 caracteres avec un
    HTTP 400 « message is too long ». Un releve de 72 anomalies pese
    ~11 000 caracteres : entre le 2026-08-17 et le 2026-09-08, 32
    notifications ont ete perdues faute de ce decoupage.

    La coupe tombe TOUJOURS entre deux blocs, jamais a l'interieur : un
    bloc porte du HTML (<b>...</b>), et le couper en deux produirait un
    balisage mal ferme -- donc un 400 de plus, pour une autre raison.

    L'entete est repete sur chaque morceau, suivi de « (i/n) » des qu'il
    y en a plusieurs. Un message unique n'est pas numerote : « (1/1) »
    n'apprend rien.

    Un pied (mention a repeter sous chaque morceau) peut etre fourni : sa
    place est retiree du budget avant la repartition, sinon il ferait
    deborder les morceaux pleins.
    """
    if not blocs:
        return []

    # marge pour le suffixe « (12/12) » ajoute apres coup a l'entete
    RESERVE_NUMEROTATION = 16
    budget = LIMITE_TELEGRAM - len(entete) - RESERVE_NUMEROTATION
    if pied:
        budget -= len(pied) + 1  # +1 pour le "\n" qui le precede

    groupes = []
    courant = []
    taille = 0
    for bloc in blocs:
        cout = len(bloc) + 1  # +1 pour le "\n" de jointure
        # 'courant' non vide : un bloc seul plus gros que le budget part
        # quand meme dans son propre message, sinon la boucle ne finirait pas
        if courant and taille + cout > budget:
            groupes.append(courant)
            courant, taille = [], 0
        courant.append(bloc)
        taille += cout
    if courant:
        groupes.append(courant)

    nb = len(groupes)
    suffixe = f"\n{pied}" if pied else ""
    return [
        "\n".join([entete if nb == 1 else f"{entete} ({i}/{nb})"] + groupe) + suffixe
        for i, groupe in enumerate(groupes, start=1)
    ]


def envoyer_telegram_a(chat_id, message: str, journaliser: bool = True) -> tuple:
    """Envoie un message a un destinataire quelconque et renvoie un statut
    que l'appelant peut exploiter : ('ok', None), ('bloque', None) sur 403
    (l'abonne a bloque le bot), ('trop_vite', secondes) sur 429, ou
    ('echec', raison courte).

    journaliser=False : le corps n'est pas recopie au journal (messages
    d'abonnes). Les erreurs, elles, sont toujours journalisees.
    """
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        if journaliser:
            journaliser_message(
                message, "message NON envoye (Telegram non configure)")
        return ("echec", "non configure")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        reponse = requests.post(url, data={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=15)
    except requests.exceptions.RequestException as e:
        log(f"   -> ERREUR envoi Telegram : {e}")
        if journaliser:
            journaliser_message(message, "message Telegram NON parti (erreur reseau)")
        return ("echec", "reseau")

    if reponse.status_code == 403:
        return ("bloque", None)

    if reponse.status_code == 429:
        try:
            delai = int(json.loads(reponse.text)["parameters"]["retry_after"])
        except (ValueError, KeyError, TypeError):
            delai = 1
        return ("trop_vite", delai)

    if reponse.status_code != 200:
        log(f"   -> ECHEC Telegram : HTTP {reponse.status_code} {reponse.text[:200]}")
        if journaliser:
            journaliser_message(message, "message Telegram REFUSE")
        return ("echec", f"HTTP {reponse.status_code}")

    if journaliser:
        journaliser_message(message, "message Telegram envoye")
    return ("ok", None)


def envoyer_telegram(message: str) -> bool:
    """Envoie un message via le bot Telegram, si le token et le chat_id
    sont renseignes. Renvoie True si Telegram l'a accepte.

    Le message est journalise dans tous les cas -- y compris quand Telegram
    n'est pas configure, cas jusqu'ici totalement silencieux ou l'on ne
    savait meme pas ce qui AURAIT ete notifie.

    Le code HTTP de reponse est verifie : Telegram refuse par exemple un
    HTML mal ferme avec un 400. Sans ce controle, un message refuse etait
    compte comme envoye et le journal devenait un faux temoignage.

    Le booleen renvoye est ce qui permet a l'appelant de ne pas rejouer ce
    mensonge a son tour : sans lui, il ne POUVAIT pas savoir.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        journaliser_message(
            message, "message NON envoye (Telegram non configure)")
        return False
    statut, detail = envoyer_telegram_a(TELEGRAM_CHAT_ID, message)
    if statut in ("bloque", "trop_vite"):
        # le proprietaire ne bloque pas son propre bot : ces cas restent
        # des echecs, et doivent se lire comme tels au journal
        code = 403 if statut == "bloque" else 429
        log(f"   -> ECHEC Telegram : HTTP {code} ({statut})")
        journaliser_message(message, "message Telegram REFUSE")
    return statut == "ok"


def sauvegarder_et_alerter(conn: sqlite3.Connection,
                           dossier: str = ".sauvegardes",
                           sauver=None) -> bool:
    """Sauvegarde hors machine, et NOTIFIE si ca echoue.

    Sans cette alerte, une sauvegarde qui echoue est invisible : le
    releve s'enregistre, l'alerte de prix part normalement, tout
    parait sain -- et les donnees ont cesse d'etre protegees sans
    aucun signe. Le silence est ambigu, c'est le meme angle mort que
    le NextRunTime vide de la tache planifiee.

    On ne notifie QUE l'echec : un message quotidien « sauvegarde OK »
    deviendrait un bruit qu'on cesse de lire en une semaine, et le
    jour ou il manquerait, personne ne le remarquerait.

    Ne leve jamais : le releve est deja enregistre a ce stade.
    """
    try:
        # import DANS le try : un sauvegarde.py absent ou casse doit
        # declencher l'alerte, pas faire planter la fin du releve
        if sauver is None:
            from sauvegarde import sauvegarder_distant as sauver
        ok = sauver(conn, dossier, journaliser=log)
    except Exception as e:
        log(f"   -> sauvegarde distante impossible : {e}")
        ok = False

    if not ok:
        # le booleen compte : sans lui le journal affirmait « envoyee »
        # juste sous « message Telegram NON parti » (reseau coupe, 17/09)
        envoye = envoyer_telegram(
            "<b>Probleme technique -- sauvegarde impossible</b>\n\n"
            "Le releve du jour est bien enregistre, mais la sauvegarde "
            "hors machine a echoue.\n\n"
            "Tes donnees ne sont plus protegees contre une panne de "
            "disque tant que ce n'est pas repare.\n\n"
            "A verifier : git -C .sauvegardes status"
        )
        log("   -> ALERTE sauvegarde envoyee" if envoye
            else "   -> ALERTE sauvegarde NON envoyee")

    return ok


def grouper_anomalies(anomalies: list) -> list:
    """
    Regroupe les anomalies par affaire reelle : le troncon hub -> destination.

    Une bonne affaire se joue sur ce troncon. La ville de depart n'ajoute
    qu'un rabattement constant, si bien que la meme aubaine remontait
    autant de fois qu'il y a de villes rattachees au hub, avec une economie
    identique a l'euro pres. Mesure du 2026-09-12 : sur les 15 derniers
    releves, 241 alertes ne recouvraient que 62 affaires distinctes, et 28
    de ces 62 remontaient avec les 5 villes au complet.

    La cle inclut le lien, qui encode deja la date de depart : deux dates
    sont deux affaires, meme sur le meme couple hub/destination.

    Villes triees par prix croissant, groupes par economie decroissante.
    Les economies d'un meme groupe peuvent differer de quelques centimes
    (les historiques n'ont pas tous la meme longueur selon la ville) : c'est
    la PLUS BASSE qui classe le groupe, pour ne pas survendre l'affaire.
    """
    groupes = {}
    for a in anomalies:
        cle = (a["hub"], a["destination"], a["lien"])
        groupes.setdefault(cle, []).append(a)

    for membres in groupes.values():
        membres.sort(key=lambda a: a["prix_actuel"])

    return sorted(groupes.values(),
                  key=lambda g: min(a["economie"] for a in g),
                  reverse=True)


def construire_bloc(groupe: list) -> str:
    """
    Rend un groupe d'anomalies en un bloc de message Telegram.

    A plusieurs villes, l'economie passe en entete : elle est commune au
    groupe et c'est le critere de declenchement. Le pourcentage reste sur
    chaque ligne de ville -- son denominateur change avec le rabattement,
    contrairement a l'economie.

    Le statut du rabattement est donne ligne par ligne plutot qu'en note de
    bas de bloc : plusieurs villes d'un meme groupe peuvent etre mesurees,
    avec des valeurs differentes, ce qu'une note unique ne saurait porter.

    A une seule ville, on retombe sur le format plat : un entete de groupe
    et une liste d'une ligne ne mettraient rien en facteur commun. Le cas
    n'a jamais ete observe (0 groupe sur 62 en 15 releves) mais reste
    possible -- une ville a l'historique plus court peut rester seule.
    """
    premier = groupe[0]
    lien = url_aviasales(premier["lien"], "proprietaire")

    if len(groupe) == 1:
        a = premier
        if a.get("rabattement") == 0:
            note = "Vol direct, sans rabattement"
        elif a["rabattement_mesure"] is not None:
            note = f"Rabattement mesure ce jour : {a['rabattement_mesure']:.0f}\u20ac"
        else:
            note = "Rabattement estime, non mesure ce jour"
        # Un resident du hub part du hub : repeter sa ville ne dit rien de
        # plus (« depuis Istanbul, au depart de Istanbul »).
        provenance = a["hub"]
        if a["ville_depart"] != a["hub"]:
            provenance += f", au depart de {a['ville_depart']}"
        return (
            f"\n<b>{a['destination']}</b> (depuis {provenance})\n"
            f"{a['prix_actuel']:.0f}\u20ac (moyenne habituelle : "
            f"{a['moyenne_historique']:.0f}\u20ac, -{a['baisse_pct']:.0f}%)\n"
            f"Economie : {a['economie']:.0f}\u20ac\n"
            f"{note}\n"
            f"{lien}"
        )

    economie = min(a["economie"] for a in groupe)
    lignes = [f"\n<b>{premier['destination']}</b> (depuis {premier['hub']}) "
              f"- economie {economie:.0f}\u20ac"]
    for a in groupe:
        if a.get("rabattement") == 0:
            etat = "vol direct"
        elif a["rabattement_mesure"] is not None:
            etat = f"rabattement mesure {a['rabattement_mesure']:.0f}\u20ac"
        else:
            etat = "rabattement estime"
        lignes.append(f"{a['ville_depart']} {a['prix_actuel']:.0f}\u20ac "
                      f"(-{a['baisse_pct']:.0f}%) - {etat}")
    lignes.append(lien)
    return "\n".join(lignes)

def copier_abonnes_et_alerter(dossier: str = "abonnes_copies") -> bool:
    """Copie locale des abonnes, et NOTIFIE si elle echoue ou si leur
    nombre a chute.

    Les abonnes n'existent qu'a un seul endroit, la base distante : le
    dump public les vide volontairement depuis la fuite du 2026-09-20,
    donc ils ne sont sauvegardes nulle part. Un recrutement fait a la
    main est la seule chose du projet qui ne se reconstruit pas toute
    seule -- et le 2026-09-22 un simple « pytest » a ecrit dans cette
    table.

    Deux alertes, jamais de « copie OK » : un message quotidien de
    succes deviendrait un bruit qu'on cesse de lire, et le jour ou il
    manquerait, personne ne le remarquerait.

    Ne leve jamais : le releve est deja enregistre a ce stade.
    """
    conn = None
    try:
        # import DANS le try : un module absent ou casse doit declencher
        # l'alerte, pas faire echouer la fin du releve
        import abonnes
        import magasin
        conn = magasin.ouvrir()
        rapport = abonnes.ecrire_instantane(conn, dossier, abonnes.maintenant())
    except Exception as e:
        log(f"   -> copie des abonnes impossible : {e}")
        envoye = envoyer_telegram(
            "<b>Probleme technique -- abonnes non sauvegardes</b>\n\n"
            "Le releve du jour est bien enregistre, mais la copie locale "
            "des abonnes a echoue.\n\n"
            "Ils n'existent qu'au seul endroit de la base distante : "
            "tant que ce n'est pas repare, une erreur sur cette base "
            "effacerait le recrutement.\n\n"
            f"Detail : {e}"
        )
        log("   -> ALERTE abonnes envoyee" if envoye
            else "   -> ALERTE abonnes NON envoyee")
        return False
    finally:
        if conn is not None:
            conn.close()

    if rapport["chute"]:
        log(f"   -> CHUTE du nombre d'abonnes : {rapport['precedent']} "
            f"-> {rapport['lignes']}")
        envoye = envoyer_telegram(
            "<b>Le nombre d'abonnes a baisse</b>\n\n"
            f"Ils etaient {rapport['precedent']}, ils sont "
            f"{rapport['lignes']}.\n\n"
            "Des desinscriptions sont normales. Une chute brutale ne "
            "l'est pas : la copie de la veille est dans "
            f"{dossier}, elle permet de restaurer."
        )
        log("   -> ALERTE chute envoyee" if envoye
            else "   -> ALERTE chute NON envoyee")

    return True



def notifier_abonnes_sans_risque(groupes: list) -> None:

    """Envoie aux abonnes du bot les affaires de leur ville.

    Les abonnes vivent hors de flight_deals.db depuis le 2026-09-20 : le
    releve tourne sur le portable, l'ecoute sur Render, et les deux voient
    la meme base distante (magasin.ouvrir).

    Appelee APRES le message du proprietaire. Ne leve jamais : import DANS
    le try, comme pour la sauvegarde -- ni un module casse ni une base
    injoignable ne doivent faire echouer la fin du releve.
    """
    conn = None
    try:
        import abonnes
        import magasin
        conn = magasin.ouvrir()
        abonnes.notifier_abonnes(
            conn, groupes,
            envoyer=lambda chat_id, message: envoyer_telegram_a(
                chat_id, message, journaliser=False),
            log=log,
            exclure_chat_id=TELEGRAM_CHAT_ID,
        )
    except Exception as e:
        log(f"   -> envoi aux abonnes impossible : {e}")
    finally:
        if conn is not None:
            conn.close()


def verifier_et_notifier_anomalies(conn: sqlite3.Connection, date_collecte: str) -> None:
    """Compare le releve du jour a la moyenne historique de chaque
    destination (logique centralisee dans anomaly_detection.py), et
    envoie une notification Telegram pour toute baisse superieure au
    seuil."""
    anomalies = detecter_anomalies(conn, date_collecte=date_collecte)

    if not anomalies:
        log("Aucune anomalie a notifier pour ce releve.")
        return

    # cout reel du trajet vers le hub, mesure maintenant : la table
    # RABATTEMENT vieillit (jusqu'a +171 % d'ecart mesure le 2026-08-16)
    couples = [(a["ville_depart"], a["hub"]) for a in anomalies]
    try:
        mesures = mesurer_rabattements(couples)
    except Exception as e:
        # une alerte aux totaux non corriges vaut mieux qu'une alerte perdue
        log(f"   -> mesure des rabattements impossible : {e}")
        mesures = {}
    anomalies = corriger_anomalies(anomalies, mesures)

    nb_mesures = sum(1 for a in anomalies if a["rabattement_mesure"] is not None)
    log(f"Rabattement mesure pour {nb_mesures}/{len(anomalies)} anomalie(s).")

    groupes = grouper_anomalies(anomalies)
    log(f"{len(anomalies)} anomalie(s) regroupee(s) en {len(groupes)} affaire(s).")
    if not TRAVELPAYOUTS_MARKER:
        log("   -> liens sans identifiant d'affilie (TRAVELPAYOUTS_MARKER absent)")

    preparer_liens_courts(groupes)

    entete = f"<b>{len(groupes)} bonne(s) affaire(s) detectee(s) !</b>"
    blocs = [construire_bloc(g) for g in groupes]

    morceaux = decouper_message(blocs, entete)
    partis = sum(1 for m in morceaux if envoyer_telegram(m))

    # ce compte rendu ne doit affirmer QUE ce qui est verifie : l'ancienne
    # version annoncait l'envoi sans regarder le resultat, et a masque
    # 32 refus consecutifs pendant trois semaines
    if partis == len(morceaux):
        log(f"Notification Telegram envoyee pour {len(anomalies)} anomalie(s) "
            f"en {len(morceaux)} message(s).")
    elif partis == 0:
        log(f"ECHEC : aucune notification partie pour {len(anomalies)} "
            f"anomalie(s) ({len(morceaux)} message(s) refuse(s)).")
    else:
        log(f"ECHEC partiel : {partis}/{len(morceaux)} message(s) partis "
            f"pour {len(anomalies)} anomalie(s).")

    notifier_abonnes_sans_risque(groupes)


if __name__ == "__main__":
    import sys
    # sous pythonw.exe, rien ne s'affiche : tout plantage doit aller au journal
    sys.excepthook = journaliser_plantage

    if not TOKEN:
        # pas de SystemExit("message") : ce message part sur stderr, qui
        # n'existe pas sous pythonw, et ne passe pas par excepthook
        log("ARRET : il manque TRAVELPAYOUTS_TOKEN dans l'environnement.")
        sys.exit(1)

    date_collecte = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    log("=== Debut d'execution ===")

    # Petit delai au demarrage : a l'ouverture de session, le reseau n'est
    # parfois pas encore pret -- sans ca, l'appel internet plante avant
    # meme d'avoir une chance de se connecter.
    log("Attente de 30 secondes pour laisser le reseau se stabiliser...")
    time.sleep(30)

    # timeout : bot_ecoute.py ecrit dans la meme base (inscriptions, temoin)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    init_db(conn)

    total_routes_trouvees = 0
    total_lignes_inserees = 0

    destinations = destinations_actives()
    nb_perso = len(destinations) - len(DESTINATIONS)
    if nb_perso:
        log(f"{nb_perso} destination(s) personnelle(s) active(s) "
            f"(+{nb_perso * len(HUBS)} appels)")

    for hub_iata, hub_info in HUBS.items():
        log(f"Interrogation des destinations depuis {hub_info['nom']} ({hub_iata})...")
        routes_hub = 0

        for dest_iata in destinations:
            # Pas de vol vers le hub lui-meme
            if dest_iata == hub_iata or dest_iata in EQUIVALENCES.get(hub_iata, set()):
                continue

            try:
                offre = get_prix_route(hub_iata, dest_iata)
                time.sleep(PAUSE_ENTRE_APPELS)
            except requests.exceptions.RequestException as e:
                log(f"   -> ERREUR reseau {hub_iata}->{dest_iata} : {e}")
                continue
            except Exception as e:
                log(f"   -> ERREUR {hub_iata}->{dest_iata} : {e}")
                continue

            if not offre:
                continue

            total_lignes_inserees += enregistrer_prix(
                conn, hub_iata, dest_iata, offre, date_collecte,
                dest_nom=destinations[dest_iata]
            )
            routes_hub += 1

        conn.commit()
        total_routes_trouvees += routes_hub
        log(f"   -> {routes_hub} routes avec prix depuis {hub_iata}")

    log(f"{total_routes_trouvees} routes trouvees, {total_lignes_inserees} lignes enregistrees")

    verifier_et_notifier_anomalies(conn, date_collecte)

    total_lignes = conn.execute("SELECT COUNT(*) FROM offres").fetchone()[0]
    log(f"Total cumule dans la base : {total_lignes} lignes")

    # Sauvegarde hors machine. Placee en DERNIER et absorbant toute
    # erreur : a ce stade le releve est deja enregistre, une panne de
    # git ou de reseau ne doit pas le faire echouer -- mais elle doit
    # se voir, d'ou la notification en cas d'echec.
    # avant la sauvegarde hors machine : celle-ci pousse sur un depot
    # PUBLIC et vide donc les tables privees. Les abonnes ont besoin
    # de leur propre copie, locale.
    copier_abonnes_et_alerter()

    sauvegarder_et_alerter(conn)

    log("=== Fin d'execution ===")

    conn.close()
