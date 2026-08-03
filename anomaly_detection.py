"""
Logique partagee de detection d'anomalie de prix -- source unique utilisee
par hub_deals_db.py (notification Telegram a chaque releve) et
detect_anomalies.py (analyse/diagnostic en ligne de commande).

Principe :
  Pour chaque destination (hub + code destination), on calcule la moyenne
  du total_estime sur TOUT l'historique. Si le prix d'un releve donne est
  nettement en dessous de cette moyenne (seuil configurable), on le
  signale comme une anomalie -- un vrai bon plan, pas juste un prix qui
  semble bas dans l'absolu.
"""

import sqlite3

SEUIL_BAISSE = 0.08  # 8% en dessous de la moyenne historique = anomalie
                       # (les offres speciales Aviasales sont deja
                       # preselectionnees, donc fluctuent moins qu'un
                       # prix de vol classique)


def get_dernier_releve(conn: sqlite3.Connection) -> str:
    """Renvoie la date du releve le plus recent dans la base."""
    return conn.execute("SELECT MAX(date_collecte) FROM offres").fetchone()[0]


def calculer_moyennes_historiques(conn: sqlite3.Connection) -> dict:
    """
    Calcule la moyenne du total_estime par destination, sur tout
    l'historique (tous les releves confondus).
    Cle : (hub_origine, destination_code)
    """
    cur = conn.execute("""
        SELECT hub_origine, destination_code, AVG(total_estime), COUNT(*)
        FROM offres
        GROUP BY hub_origine, destination_code
    """)
    moyennes = {}
    for hub, dest_code, moyenne, nb_releves in cur.fetchall():
        moyennes[(hub, dest_code)] = {"moyenne": moyenne, "nb_releves": nb_releves}
    return moyennes


def detecter_anomalies(
    conn: sqlite3.Connection,
    date_collecte: str = None,
    mode_diagnostic: bool = False,
) -> list:
    """
    Compare chaque offre du releve donne (par defaut le plus recent dans
    la base) a la moyenne historique de sa destination. Renvoie la liste
    des anomalies detectees, triees par pourcentage de baisse (la
    meilleure affaire en premier).

    Si mode_diagnostic=True, renvoie TOUTES les comparaisons (meme celles
    sous le seuil), pour voir ce qui se passe reellement.
    """
    if date_collecte is None:
        date_collecte = get_dernier_releve(conn)
    moyennes = calculer_moyennes_historiques(conn)

    cur = conn.execute("""
        SELECT hub_origine, destination_code, destination_nom,
               total_estime, date_depart, lien
        FROM offres
        WHERE date_collecte = ?
    """, (date_collecte,))

    resultats = []
    for hub, dest_code, dest_nom, total_estime, date_depart, lien in cur.fetchall():
        cle = (hub, dest_code)
        info_historique = moyennes.get(cle)

        if not info_historique or info_historique["nb_releves"] < 2:
            continue

        moyenne = info_historique["moyenne"]
        baisse = (moyenne - total_estime) / moyenne

        if mode_diagnostic or baisse >= SEUIL_BAISSE:
            resultats.append({
                "destination": dest_nom,
                "destination_code": dest_code,
                "hub": hub,
                "prix_actuel": total_estime,
                "moyenne_historique": round(moyenne, 2),
                "baisse_pct": round(baisse * 100, 1),
                "nb_releves_historique": info_historique["nb_releves"],
                "date_depart": date_depart,
                "lien": lien,
            })

    resultats.sort(key=lambda x: x["baisse_pct"], reverse=True)
    return resultats
