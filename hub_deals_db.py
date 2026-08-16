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
}

# Pause entre deux appels API -- evite de saturer Travelpayouts.
# 9 hubs x 32 destinations, moins les auto-exclusions = 279 appels,
# soit ~2 minutes a 0.4s. Ce total ne depend PAS du nombre de villes de
# depart : celles-ci se contentent de demultiplier les lignes inserees.
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
    },
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

    `couples` porte le NOM du hub (« Paris »), comme la colonne
    hub_origine des anomalies -- pas le code IATA.

    Renvoie {(ville, hub_nom): {"prix", "table", "mesure"}}. `table` est
    rendue avec la mesure pour que le calcul du decalage reste une
    fonction pure de son entree.
    """
    if get_prix is None:
        get_prix = get_prix_route

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


def envoyer_telegram(message: str) -> None:
    """Envoie un message via le bot Telegram, si le token et le chat_id
    sont renseignes. Ne fait rien silencieusement sinon (pour ne pas
    bloquer le script tant que Telegram n'est pas encore configure)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=15)
    except requests.exceptions.RequestException as e:
        log(f"   -> ERREUR envoi Telegram : {e}")


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

    lignes = [f"<b>{len(anomalies)} bonne(s) affaire(s) detectee(s) !</b>\n"]
    for a in anomalies:
        if a["rabattement_mesure"] is not None:
            note = f"Rabattement mesure ce jour : {a['rabattement_mesure']:.0f}\u20ac"
        else:
            note = "Rabattement estime, non mesure ce jour"
        lignes.append(
            f"\n<b>{a['destination']}</b> (depuis {a['hub']}, au depart de {a['ville_depart']})\n"
            f"{a['prix_actuel']:.0f}\u20ac (moyenne habituelle : {a['moyenne_historique']:.0f}\u20ac, "
            f"-{a['baisse_pct']:.0f}%)\n"
            f"{note}\n"
            f"https://www.aviasales.com{a['lien']}"
        )
    message = "\n".join(lignes)
    envoyer_telegram(message)
    log(f"Notification Telegram envoyee pour {len(anomalies)} anomalie(s).")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Il manque TRAVELPAYOUTS_TOKEN dans l'environnement.")

    date_collecte = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    log("=== Debut d'execution ===")

    # Petit delai au demarrage : a l'ouverture de session, le reseau n'est
    # parfois pas encore pret -- sans ca, l'appel internet plante avant
    # meme d'avoir une chance de se connecter.
    log("Attente de 30 secondes pour laisser le reseau se stabiliser...")
    time.sleep(30)

    conn = sqlite3.connect(DB_PATH)
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
    log("=== Fin d'execution ===")

    conn.close()
