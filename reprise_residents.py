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


def villes_residentes() -> dict:
    """{nom de ville: code IATA de son hub} pour toute ville dont le
    rabattement vers un hub vaut 0."""
    trouvees = {}
    for ville, couts in hub_deals_db.RABATTEMENT.items():
        for hub_iata, cout in couts.items():
            if cout["prix"] == 0:
                if hub_deals_db.HUBS[hub_iata]["nom"] != ville:
                    raise ValueError(
                        f"{ville}->{hub_iata} : rabattement nul vers un hub etranger")
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

    Une exception en cours de boucle (ville 5 sur 13, par exemple) est
    remontee apres le `finally` : celui-ci supprime l'index puis appelle
    conn.commit(), qui valide donc les villes deja inserees au lieu de les
    annuler. C'est sans danger : la requete est idempotente (NOT EXISTS
    exclut ce qui est deja present), un rejeu se contente de terminer les
    villes restantes, sans doublon ni ligne corrompue.
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
            # EQUIVALENCES comprises : Paris exclut PAR *et* CDG, sans quoi
            # un code equivalent ramene la ville chez elle tout autant que
            # son propre code (voir le bug du 2026-08-03 sur VILLE_IATA).
            codes_exclus = sorted({code_ville} | hub_deals_db.EQUIVALENCES.get(code_ville, set()))
            placeholders = ", ".join("?" for _ in codes_exclus)
            cur = conn.execute(f"""
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
                  AND o.destination_code NOT IN ({placeholders})
                  AND o.prix_vol_hub IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM offres deja
                      WHERE deja.ville_depart = ?
                        AND deja.date_collecte = o.date_collecte
                        AND deja.hub_origine = o.hub_origine
                        AND deja.destination_code = o.destination_code
                  )
            """, (ville, hub_nom, ville, *codes_exclus, ville))
            total += cur.rowcount
        conn.commit()
    finally:
        conn.execute("DROP INDEX IF EXISTS idx_reprise_residents")
        conn.commit()
    return total


if __name__ == "__main__":
    chemin = sys.argv[1] if len(sys.argv) > 1 else hub_deals_db.DB_PATH
    connexion = sqlite3.connect(chemin, timeout=30)
    try:
        n = reprendre(connexion)
        print(f"{n} ligne(s) reportee(s) dans {chemin}")
    finally:
        connexion.close()
