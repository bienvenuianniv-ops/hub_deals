"""
Detecteur de bonnes affaires -- version avec stockage SQLite.

Chaque lancement du script :
  1. Recupere les offres actuelles depuis les hubs surveilles
  2. Les enregistre dans une base SQLite locale (historique cumulatif)
  3. Affiche le classement du jour

L'historique permet, a terme, de comparer un prix du jour a la moyenne
des relevés precedents pour la meme destination -- et donc de detecter
une vraie anomalie plutot qu'un prix bas ponctuel sans reference.

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
}

# Cout de rabattement par ville de depart -> chaque hub, base sur des prix
# reels (juillet 2026 pour Dakar). Ajouter une ville = ajouter une entree
# ici, meme structure -- aucun autre changement de code necessaire.
RABATTEMENT = {
    "Dakar": {
        "CMN": {"prix": 400, "duree_h": 4},
        "CDG": {"prix": 300, "duree_h": 6},
        "IST": {"prix": 400, "duree_h": 7},
        "ADD": {"prix": 500, "duree_h": 6},
        "NBO": {"prix": 500, "duree_h": 8},
        "ABJ": {"prix": 200, "duree_h": 2},
    },
    # "Abidjan": { ... },  # a ajouter plus tard, meme structure
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


def get_special_offers(origin: str) -> list:
    """Bonnes affaires detectees par Travelpayouts depuis un hub donne."""
    url = f"{BASE_URL}/aviasales/v3/get_special_offers"
    params = {"origin": origin, "currency": "eur", "token": TOKEN}
    response = requests.get(url, params=params)
    response.raise_for_status()
    return response.json().get("data", [])


def enregistrer_offres(conn: sqlite3.Connection, hub_iata: str, offres: list, date_collecte: str) -> None:
    """Insere chaque offre du jour dans la base, une fois par ville de
    depart ayant un cout de rabattement defini pour ce hub."""
    hub_nom = HUBS[hub_iata]["nom"]
    for ville, couts_ville in RABATTEMENT.items():
        rabattement = couts_ville.get(hub_iata)
        if rabattement is None:
            continue
        for offre in offres:
            prix_vol = offre.get("price", 0)
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
                offre.get("destination"),
                offre.get("destination_name"),
                prix_vol,
                rabattement["prix"],
                total_estime,
                offre.get("departure_at"),
                offre.get("link"),
            ))
    conn.commit()


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
            f"\n<b>{a['destination']}</b> (depuis {a['hub']})\n"
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

    for hub_iata, hub_info in HUBS.items():
        log(f"Recuperation des offres depuis {hub_info['nom']} ({hub_iata})...")
        try:
            offres = get_special_offers(hub_iata)
            enregistrer_offres(conn, hub_iata, offres, date_collecte)
            log(f"   -> {len(offres)} offres recuperees pour {hub_iata}")
        except requests.exceptions.RequestException as e:
            log(f"   -> ERREUR reseau pour {hub_iata} : {e}")
        except Exception as e:
            log(f"   -> ERREUR inattendue pour {hub_iata} : {e}")

    resultats = classement_du_jour(conn, date_collecte)
    log(f"{len(resultats)} offres enregistrees pour ce releve")

    verifier_et_notifier_anomalies(conn, date_collecte)

    total_lignes = conn.execute("SELECT COUNT(*) FROM offres").fetchone()[0]
    log(f"Total cumule dans la base : {total_lignes} lignes")
    log("=== Fin d'execution ===")

    conn.close()
