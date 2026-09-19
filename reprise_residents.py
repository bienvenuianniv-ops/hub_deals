"""Reprise d'historique des villes residentes (migration ponctuelle).

Une ville residente est une ville dont le rabattement vers son propre hub
vaut 0 : son total estime est exactement le prix du vol depuis ce hub, deja
stocke dans la colonne prix_vol_hub depuis le 2026-07-21. Le report est
donc arithmetiquement exact, et non une estimation.

Sans lui, chaque ville ouverte le 2026-09-19 resterait muette trois
releves, le temps que MIN_RELEVES_HISTORIQUE soit atteint.

Rejouable sans risque : une ligne deja presente n'est jamais reinseree.

Usage : python reprise_residents.py [chemin_base]
"""
import sqlite3
import sys

import hub_deals_db


def villes_residentes(rabattement=None, hubs=None) -> dict:
    """{nom de ville: code IATA de son hub} pour toute ville dont le
    rabattement vers un hub vaut 0."""
    rabattement = hub_deals_db.RABATTEMENT if rabattement is None else rabattement
    hubs = hub_deals_db.HUBS if hubs is None else hubs
    trouvees = {}
    for ville, couts in rabattement.items():
        for hub_iata, cout in couts.items():
            if cout["prix"] == 0:
                trouvees[ville] = hub_iata
    return trouvees


def reprendre(conn: sqlite3.Connection) -> int:
    """Insere l'historique des vols directs de chaque ville residente.

    Renvoie le nombre de lignes inserees.

    L'index temporaire n'est pas une optimisation de confort : la table
    'offres' n'en a aucun, et le NOT EXISTS correle de la requete balaie
    sinon 68 000 lignes pour chacune des 16 000 candidates. Mesure du
    2026-09-19 sur une copie de la base reelle : plus de 5 minutes sans
    terminer 2 villes sur 13 sans index, 0,4 s avec. Il est supprime
    ensuite -- une migration ponctuelle ne laisse pas de residu de schema.
    """
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_reprise_residents
        ON offres(ville_depart, date_collecte, hub_origine, destination_code)
    """)
    try:
        total = 0
        for ville, hub_iata in villes_residentes().items():
            hub_nom = hub_deals_db.HUBS[hub_iata]["nom"]
            code_ville = hub_deals_db.VILLE_IATA[ville]
            cur = conn.execute("""
                INSERT INTO offres (
                    date_collecte, ville_depart, hub_origine, destination_code,
                    destination_nom, prix_vol_hub, rabattement, total_estime,
                    date_depart, lien
                )
                SELECT DISTINCT o.date_collecte, ?, o.hub_origine, o.destination_code,
                       o.destination_nom, o.prix_vol_hub, 0, o.prix_vol_hub,
                       o.date_depart, o.lien
                FROM offres o
                WHERE o.hub_origine = ?
                  AND o.ville_depart != ?
                  AND o.destination_code != ?
                  AND o.prix_vol_hub IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM offres deja
                      WHERE deja.ville_depart = ?
                        AND deja.date_collecte = o.date_collecte
                        AND deja.hub_origine = o.hub_origine
                        AND deja.destination_code = o.destination_code
                  )
            """, (ville, hub_nom, ville, code_ville, ville))
            total += cur.rowcount
        conn.commit()
    finally:
        conn.execute("DROP INDEX IF EXISTS idx_reprise_residents")
        conn.commit()
    return total


if __name__ == "__main__":
    chemin = sys.argv[1] if len(sys.argv) > 1 else hub_deals_db.DB_PATH
    connexion = sqlite3.connect(chemin)
    try:
        n = reprendre(connexion)
        print(f"{n} ligne(s) reportee(s) dans {chemin}")
    finally:
        connexion.close()
