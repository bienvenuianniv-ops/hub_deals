"""
Detecteur d'anomalie de prix -- outil CLI d'analyse/diagnostic.

La logique de detection (moyenne historique, seuil) est centralisee dans
anomaly_detection.py, partagee avec hub_deals_db.py.

Usage :
    python3 detect_anomalies.py
"""

import sqlite3
import json

from anomaly_detection import SEUIL_BAISSE, get_dernier_releve, detecter_anomalies
from hub_deals_db import init_db

DB_PATH = "flight_deals.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    # Meme base que hub_deals_db.py -- la migration doit etre appliquee ici
    # aussi, sinon les requetes qui suivent plantent sur une base pas encore
    # migree (colonne ville_depart absente). Idempotent, sans danger sur une
    # base deja a jour.
    init_db(conn)

    dernier_releve = get_dernier_releve(conn)
    total_lignes = conn.execute("SELECT COUNT(*) FROM offres").fetchone()[0]
    nb_releves = conn.execute("SELECT COUNT(DISTINCT date_collecte) FROM offres").fetchone()[0]

    print(f"Base : {total_lignes} lignes, {nb_releves} releves distincts")
    print(f"Dernier releve analyse : {dernier_releve}\n")

    anomalies = detecter_anomalies(conn)

    if not anomalies:
        print(f"Aucune anomalie detectee (seuil : -{int(SEUIL_BAISSE*100)}% vs moyenne historique).\n")
        print("--- Mode diagnostic : toutes les comparaisons disponibles ---\n")
        diagnostic = detecter_anomalies(conn, mode_diagnostic=True)
        print(json.dumps(diagnostic, indent=2, ensure_ascii=False))
    else:
        print(f"{len(anomalies)} anomalie(s) detectee(s) :\n")
        print(json.dumps(anomalies, indent=2, ensure_ascii=False))

    conn.close()


if __name__ == "__main__":
    main()
