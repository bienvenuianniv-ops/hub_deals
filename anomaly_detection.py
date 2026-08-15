"""
Logique partagee de detection d'anomalie de prix -- source unique utilisee
par hub_deals_db.py (notification Telegram a chaque releve) et
detect_anomalies.py (analyse/diagnostic en ligne de commande).

Principe (hybride : z-score quand l'historique le permet, pourcentage sinon)
  Pour chaque route (ville de depart + hub + destination), on compare le
  prix du releve du jour a l'historique DES AUTRES releves de cette meme
  route.

  Deux points sont essentiels ici, et tous deux ont ete des bugs :

  1. Le releve juge est EXCLU de sa propre reference. Sinon le prix du
     jour tire la moyenne vers lui et se compare a une reference qu'il a
     lui-meme deformee. Avec un ecart-type d'echantillon, cela plafonne
     mecaniquement le z-score a (n-1)/racine(n) : 0.71 sur 2 releves,
     1.16 sur 3, 1.50 sur 4. Un seuil a 1.5 etait donc litteralement
     inatteignable tant qu'une route n'avait pas 4 releves -- l'immense
     majorite des routes ne pouvait JAMAIS declencher d'alerte.

  2. Le z-score n'est utilise que si l'historique compte assez de points
     pour que l'ecart-type veuille dire quelque chose. En dessous de
     MIN_RELEVES_ZSCORE, on retombe sur le seuil en pourcentage, qui ne
     demande pas d'estimer une dispersion.

  Et en deca de MIN_RELEVES_HISTORIQUE releves anterieurs, la route n'est
  pas jugee du tout : comparer a une observation unique reviendrait a
  signaler le bruit quotidien d'un prix de billet.

  z_score = (moyenne_historique - prix_du_jour) / ecart_type_historique

  Un z-score de 1.5 signifie : ce prix est 1.5 ecart-type en dessous de
  la moyenne habituelle de cette route precise.
"""

import sqlite3
import statistics

SEUIL_ZSCORE = 1.5  # nombre d'ecarts-types sous la moyenne pour declencher
                    # une anomalie, quand l'historique est assez fourni

SEUIL_BAISSE = 0.08  # repli en pourcentage, utilise tant que la route n'a
                     # pas MIN_RELEVES_ZSCORE releves d'historique

MIN_RELEVES_HISTORIQUE = 2  # nombre minimal de releves ANTERIEURS pour
                            # qu'une route soit jugee. A 1, on comparerait
                            # le prix du jour a une unique observation
                            # passee : sur un billet d'avion, qui bouge
                            # beaucoup d'un jour a l'autre, une telle
                            # reference n'a pas de sens.

MIN_RELEVES_ZSCORE = 4  # en dessous, l'ecart-type calcule sur si peu de
                        # points n'est pas fiable -- on prefere le seuil
                        # en pourcentage

PLANCHER_BAISSE_ZSCORE = 0.03  # meme en mode z-score, on ignore les
                               # baisses inferieures a 3% : sur une route
                               # tres stable, 1.5 ecart-type peut ne
                               # representer que quelques euros


def get_dernier_releve(conn: sqlite3.Connection) -> str:
    """Renvoie la date du releve le plus recent dans la base."""
    return conn.execute("SELECT MAX(date_collecte) FROM offres").fetchone()[0]


def calculer_stats_historiques(conn: sqlite3.Connection,
                               exclure_date: str = None) -> dict:
    """
    Calcule la moyenne ET l'ecart-type du total_estime par (ville de
    depart, hub, destination).

    exclure_date : date_collecte a retirer du calcul. C'est ainsi qu'on
    obtient une reference independante du releve qu'on s'apprete a juger
    (voir le point 1 de l'en-tete du module).

    SQLite n'a pas de fonction STDEV native -- on recupere donc les
    valeurs brutes et on calcule nous-memes avec le module 'statistics'
    de Python. statistics.stdev() utilise la formule N-1 (echantillon),
    plus prudente que N (population) quand on a peu de points -- ce qui
    est notre cas ici : chaque releve n'est qu'un instantane du marche,
    pas la totalite des prix possibles.

    Cle : (ville_depart, hub_origine, destination_code)
    """
    if exclure_date is None:
        cur = conn.execute("""
            SELECT ville_depart, hub_origine, destination_code, total_estime
            FROM offres
        """)
    else:
        cur = conn.execute("""
            SELECT ville_depart, hub_origine, destination_code, total_estime
            FROM offres
            WHERE date_collecte != ?
        """, (exclure_date,))

    valeurs_par_route = {}
    for ville, hub, dest_code, total_estime in cur.fetchall():
        if total_estime is None:
            continue
        cle = (ville, hub, dest_code)
        valeurs_par_route.setdefault(cle, []).append(total_estime)

    stats = {}
    for cle, valeurs in valeurs_par_route.items():
        stats[cle] = {
            "moyenne": statistics.mean(valeurs),
            # l'ecart-type demande au moins 2 points
            "ecart_type": statistics.stdev(valeurs) if len(valeurs) >= 2 else None,
            "nb_releves": len(valeurs),
        }
    return stats


def detecter_anomalies(
    conn: sqlite3.Connection,
    date_collecte: str = None,
    mode_diagnostic: bool = False,
) -> list:
    """
    Compare chaque offre du releve donne (par defaut le plus recent dans
    la base) a l'historique des AUTRES releves de la meme route. Renvoie
    la liste des anomalies detectees, triees par baisse decroissante (la
    plus grosse baisse en premier -- critere lisible et commun aux deux
    methodes de detection).

    Si mode_diagnostic=True, renvoie TOUTES les comparaisons calculables
    (meme celles sous le seuil), pour voir ce qui se passe reellement.
    """
    if date_collecte is None:
        date_collecte = get_dernier_releve(conn)

    # reference construite SANS le releve du jour
    stats = calculer_stats_historiques(conn, exclure_date=date_collecte)

    cur = conn.execute("""
        SELECT ville_depart, hub_origine, destination_code, destination_nom,
               total_estime, date_depart, lien
        FROM offres
        WHERE date_collecte = ?
    """, (date_collecte,))

    resultats = []
    for ville, hub, dest_code, dest_nom, total_estime, date_depart, lien in cur.fetchall():
        info = stats.get((ville, hub, dest_code))

        if not info or total_estime is None:
            continue  # premiere apparition de cette route : rien a comparer

        if info["nb_releves"] < MIN_RELEVES_HISTORIQUE:
            continue  # un seul point de reference ne fait pas un historique

        moyenne = info["moyenne"]
        if not moyenne or moyenne <= 0:
            continue  # moyenne inexploitable, evite une division par zero

        baisse_pct = (moyenne - total_estime) / moyenne * 100
        ecart_type = info["ecart_type"]

        # z-score seulement si l'historique est assez fourni ET reellement
        # variable ; sinon repli sur le seuil en pourcentage
        if (info["nb_releves"] >= MIN_RELEVES_ZSCORE
                and ecart_type is not None and ecart_type > 0):
            methode = "z-score"
            z_score = (moyenne - total_estime) / ecart_type
            declenche = (z_score >= SEUIL_ZSCORE
                         and baisse_pct >= PLANCHER_BAISSE_ZSCORE * 100)
        else:
            methode = "pourcentage"
            z_score = None
            declenche = baisse_pct >= SEUIL_BAISSE * 100

        if mode_diagnostic or declenche:
            resultats.append({
                "destination": dest_nom,
                "destination_code": dest_code,
                "hub": hub,
                "ville_depart": ville,
                "prix_actuel": total_estime,
                "moyenne_historique": round(moyenne, 2),
                "ecart_type": round(ecart_type, 2) if ecart_type is not None else None,
                "z_score": round(z_score, 2) if z_score is not None else None,
                "baisse_pct": round(baisse_pct, 1),
                "methode": methode,
                "nb_releves_historique": info["nb_releves"],
                "date_depart": date_depart,
                "lien": lien,
            })

    resultats.sort(key=lambda x: x["baisse_pct"], reverse=True)
    return resultats
