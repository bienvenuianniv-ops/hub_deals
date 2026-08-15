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
RABATTEMENT = {
    "Dakar": {
        "CMN": {"prix": 400, "duree_h": 4},
        "CDG": {"prix": 300, "duree_h": 6},
        "IST": {"prix": 400, "duree_h": 7},
        "ADD": {"prix": 500, "duree_h": 6},
        "NBO": {"prix": 500, "duree_h": 8},
        "ABJ": {"prix": 200, "duree_h": 2},
        "JNB": {"prix": 500, "duree_h": 10},
        "CAI": {"prix": 380, "duree_h": 7},
        "LOS": {"prix": 450, "duree_h": 4},
    },
    "Abidjan": {
        "CMN": {"prix": 563, "duree_h": 3},
        "CDG": {"prix": 511, "duree_h": 8},
        "IST": {"prix": 700, "duree_h": 9},
        "NBO": {"prix": 374, "duree_h": 8},
        "JNB": {"prix": 350, "duree_h": 9},
        "CAI": {"prix": 340, "duree_h": 6},
        "LOS": {"prix": 400, "duree_h": 2},
    },
    "Brazzaville": {
        "CMN": {"prix": 700, "duree_h": 6},
        "CDG": {"prix": 600, "duree_h": 7},
        "IST": {"prix": 800, "duree_h": 9},
        "ADD": {"prix": 700, "duree_h": 5},
        "NBO": {"prix": 750, "duree_h": 6},  # estimation, pas de prix reel trouve -- a verifier
        "JNB": {"prix": 450, "duree_h": 4},  # estimation, pas de prix direct trouve -- a verifier
        "CAI": {"prix": 750, "duree_h": 8},  # estimation, pas de prix direct trouve -- a verifier
        "LOS": {"prix": 400, "duree_h": 3},
    },
    "Lome": {
        "CMN": {"prix": 1289, "duree_h": 4},
        "CDG": {"prix": 279, "duree_h": 12},
        "IST": {"prix": 718, "duree_h": 11},
        "NBO": {"prix": 849, "duree_h": 9},  # prix reel ; duree estimee, non renvoyee par l'API
        "ABJ": {"prix": 441, "duree_h": 2},
        "CAI": {"prix": 552, "duree_h": 13},
        "LOS": {"prix": 313, "duree_h": 7},
    },
    "Kinshasa": {
        "CMN": {"prix": 738, "duree_h": 7},
        "CDG": {"prix": 377, "duree_h": 11},
        "IST": {"prix": 650, "duree_h": 12},
        "ADD": {"prix": 682, "duree_h": 21},
        "NBO": {"prix": 555, "duree_h": 13},
        "ABJ": {"prix": 796, "duree_h": 6},  # prix reel ; duree estimee, non renvoyee par l'API
        "JNB": {"prix": 321, "duree_h": 22},
        "CAI": {"prix": 412, "duree_h": 6},
        "LOS": {"prix": 630, "duree_h": 8},
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
                     offre: dict, date_collecte: str) -> int:
    """
    Insere un prix de route dans la base, une fois par ville de depart
    ayant un cout de rabattement defini pour ce hub.

    Point important : on n'interroge l'API qu'UNE fois par couple
    (hub, destination), puis on insere une ligne par ville. Interroger
    une fois par ville triplerait les appels pour un resultat identique.

    Renvoie le nombre de lignes inserees.
    """
    hub_nom = HUBS[hub_iata]["nom"]
    dest_nom = DESTINATIONS.get(dest_iata, dest_iata)
    prix_vol = offre.get("price") or 0
    date_depart = offre.get("departure_at") or ""
    lien = construire_lien(hub_iata, dest_iata, date_depart)

    lignes = 0
    for ville, couts_ville in RABATTEMENT.items():
        rabattement = couts_ville.get(hub_iata)
        if rabattement is None:
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


def log(message: str) -> None:
    """Ecrit dans la console ET dans un fichier log -- utile car quand la
    tache tourne via le Planificateur (pas depuis PowerShell), les print()
    normaux ne s'affichent nulle part et on ne peut jamais voir si ca a
    plante ni pourquoi."""
    horodatage = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ligne = f"[{horodatage}] {message}"
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

    lignes = [f"<b>{len(anomalies)} bonne(s) affaire(s) detectee(s) !</b>\n"]
    for a in anomalies:
        lignes.append(
            f"\n<b>{a['destination']}</b> (depuis {a['hub']}, au depart de {a['ville_depart']})\n"
            f"{a['prix_actuel']:.0f}\u20ac (moyenne habituelle : {a['moyenne_historique']:.0f}\u20ac, "
            f"-{a['baisse_pct']:.0f}%)\n"
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

    for hub_iata, hub_info in HUBS.items():
        log(f"Interrogation des destinations depuis {hub_info['nom']} ({hub_iata})...")
        routes_hub = 0

        for dest_iata in DESTINATIONS:
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
                conn, hub_iata, dest_iata, offre, date_collecte
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
