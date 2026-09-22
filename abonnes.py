"""
Abonnes au bot Telegram (test prive du 2026-09-15).

Ce module ne fait AUCUN appel reseau : l'envoi est injecte par l'appelant.
Il est partage par le releve (hub_deals_db.py, qui envoie) et par l'ecoute
(bot_ecoute.py, qui inscrit) -- deux processus sur la meme base SQLite,
d'ou des transactions courtes, commitees aussitot.

Spec : docs/superpowers/specs/2026-09-15-bot-abonnes-design.md
"""

import io
import json
import os
import time
from datetime import datetime, timezone

import hub_deals_db

# cle de RABATTEMENT -> nom affiche aux abonnes. Un test verifie que les
# deux ensembles de cles sont identiques.
NOMS_AFFICHES = {
    "Dakar": "Dakar",
    "Abidjan": "Abidjan",
    "Lome": "Lomé",
    "Kinshasa": "Kinshasa",
    "Brazzaville": "Brazzaville",
    "Paris": "Paris",
    "Istanbul": "Istanbul",
    "Casablanca": "Casablanca",
    "Le Caire": "Le Caire",
    "Lagos": "Lagos",
    "Nairobi": "Nairobi",
    "Addis-Abeba": "Addis-Abeba",
    "Johannesburg": "Johannesburg",
}

# garde-fou du test prive : protege si le lien d'invitation circule
PLAFOND_ABONNES = 50

_COLONNES = ("chat_id", "prenom", "ville_depart", "actif",
             "inscrit_le", "modifie_le", "motif_inactif")


# Nombre de copies quotidiennes conservees. Une seule copie reecrite
# chaque jour serait effacee par la catastrophe elle-meme : le lendemain
# d'un DELETE, elle ne contiendrait plus que la table vide.
COPIES_GARDEES = 30


def instantane(conn) -> list:
    """Toutes les lignes de la table, toutes colonnes comprises.

    Toutes les colonnes et pas seulement celles qui servent a l'envoi :
    une restauration doit pouvoir recreer la ligne telle quelle.
    """
    lignes = conn.execute(
        f"SELECT {', '.join(_COLONNES)} FROM abonnes ORDER BY chat_id"
    ).fetchall()
    return [_en_dict(l) for l in lignes]


def _copies_existantes(dossier: str) -> list:
    if not os.path.isdir(dossier):
        return []
    noms = [n for n in os.listdir(dossier)
            if n.startswith("abonnes-") and n.endswith(".json")]
    return sorted(noms)  # le nom porte la date : l'ordre alphabetique suffit


def _lignes_de(chemin: str):
    """Nombre d'abonnes d'une copie, ou None si elle est illisible.

    Illisible n'est pas vide : renvoyer 0 ici ferait crier a la chute
    sur un fichier tronque, et l'alerte cesserait d'etre croyable.
    """
    try:
        with io.open(chemin, encoding="utf-8") as f:
            return json.load(f)["lignes"]
    except (OSError, ValueError, KeyError):
        return None


def ecrire_instantane(conn, dossier: str, quand: str) -> dict:
    """Ecrit la copie du jour et rend un compte rendu.

    Les abonnes n'existent qu'a un seul endroit, la base distante : le
    dump public les vide volontairement depuis la fuite du 2026-09-20.
    Cette copie locale est leur seule sauvegarde.

    Ne rattrape aucune erreur : l'appelant decide quoi en faire, et c'est
    lui qui alerte. Une sauvegarde qui echoue en silence ne protege rien
    -- lecon des 37 alertes perdues en aout.
    """
    os.makedirs(dossier, exist_ok=True)
    anciennes = _copies_existantes(dossier)
    precedent = None
    for nom in reversed(anciennes):
        if nom[len("abonnes-"):-len(".json")] < quand[:10].replace("-", ""):
            precedent = _lignes_de(os.path.join(dossier, nom))
            break

    lignes = instantane(conn)
    chemin = os.path.join(dossier, f"abonnes-{quand[:10].replace('-', '')}.json")
    contenu = {"pris_le": quand, "lignes": len(lignes), "abonnes": lignes}

    # ecriture atomique : un processus tue en plein milieu ne doit pas
    # laisser une copie a moitie ecrite a la place d'une copie valable
    provisoire = chemin + ".partiel"
    try:
        with io.open(provisoire, "w", encoding="utf-8", newline="\n") as f:
            json.dump(contenu, f, ensure_ascii=False, indent=2)
        os.replace(provisoire, chemin)
    finally:
        if os.path.exists(provisoire):
            os.remove(provisoire)

    for nom in _copies_existantes(dossier)[:-COPIES_GARDEES]:
        os.remove(os.path.join(dossier, nom))

    return {
        "chemin": chemin,
        "lignes": len(lignes),
        "precedent": precedent,
        "chute": precedent is not None and len(lignes) < precedent,
    }


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _en_dict(ligne) -> dict:
    a = dict(zip(_COLONNES, ligne))
    a["actif"] = bool(a["actif"])
    return a


def trouver(conn, chat_id):
    ligne = conn.execute(
        f"SELECT {', '.join(_COLONNES)} FROM abonnes WHERE chat_id = ?",
        (chat_id,)).fetchone()
    return _en_dict(ligne) if ligne else None


def nb_actifs(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM abonnes WHERE actif = 1").fetchone()[0]


def inscrire(conn, chat_id, prenom, quand: str) -> None:
    """Cree l'abonne, ou le reactive en conservant sa ville et sa date
    d'inscription d'origine (historique du test)."""
    conn.execute("""
        INSERT INTO abonnes (chat_id, prenom, actif, inscrit_le, modifie_le)
        VALUES (?, ?, 1, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            prenom = excluded.prenom, actif = 1, motif_inactif = NULL,
            modifie_le = excluded.modifie_le
    """, (chat_id, prenom, quand, quand))
    conn.commit()


def choisir_ville(conn, chat_id, ville: str, quand: str) -> None:
    if ville not in NOMS_AFFICHES:
        raise ValueError(f"ville inconnue : {ville}")
    conn.execute("UPDATE abonnes SET ville_depart = ?, modifie_le = ? WHERE chat_id = ?",
                 (ville, quand, chat_id))
    conn.commit()


def desactiver(conn, chat_id, motif: str, quand: str) -> None:
    """Ne supprime jamais : la ligne reste pour l'historique du test."""
    conn.execute("""
        UPDATE abonnes SET actif = 0, motif_inactif = ?, modifie_le = ?
        WHERE chat_id = ?
    """, (motif, quand, chat_id))
    conn.commit()


def abonnes_a_servir(conn, exclure_chat_id=None) -> list:
    lignes = conn.execute(f"""
        SELECT {', '.join(_COLONNES)} FROM abonnes
        WHERE actif = 1 AND ville_depart IS NOT NULL
        ORDER BY chat_id
    """).fetchall()
    servis = [_en_dict(l) for l in lignes]
    if exclure_chat_id is not None:
        servis = [a for a in servis if str(a["chat_id"]) != str(exclure_chat_id)]
    return servis


# Le temoin d'ecoute n'est plus un garde-fou depuis le 2026-09-20 :
# avec un webhook il n'y a plus de boucle a surveiller, le service dort
# et l'absence de message ne signale aucune panne. La sonde
# getWebhookInfo de la vigie l'a remplace. Il reste ecrit : il aide au
# diagnostic, il n'alerte plus personne.
def noter_ecoute(conn, quand: str) -> None:
    conn.execute("""
        INSERT INTO etat_bot (cle, valeur) VALUES ('derniere_ecoute', ?)
        ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur
    """, (quand,))
    conn.commit()


# Les prix viennent d'un cache Aviasales (jusqu'a 7 jours) : les regles
# Travelpayouts interdisent de presenter une remise comme garantie.
MENTION_PRIX = "<i>Prix repéré aujourd'hui, il peut avoir changé : vérifie avant de réserver.</i>"


def filtrer_groupes(groupes: list, ville: str) -> list:
    """Reduit chaque groupe (une affaire hub -> destination) aux lignes de
    la ville de l'abonne ; les groupes qui n'en ont pas disparaissent."""
    filtres = []
    for groupe in groupes:
        garde = [a for a in groupe if a["ville_depart"] == ville]
        if garde:
            filtres.append(garde)
    return filtres


def _bloc_abonne(a: dict, ville: str) -> str:
    lien = hub_deals_db.url_aviasales(a["lien"], hub_deals_db.etiquette_ville(ville))
    # rabattement nul = l'abonne part de chez lui ; « via Paris » pour un
    # Parisien n'aurait aucun sens
    trajet = "— vol direct" if a.get("rabattement") == 0 else f"via {a['hub']}"
    return (
        f"\n<b>{a['destination']}</b> {trajet}\n"
        f"{a['prix_actuel']:.0f}€ (-{a['baisse_pct']:.0f}%) — "
        f"{a['economie']:.0f}€ de moins que d'habitude\n"
        f"{lien}"
    )


def messages_abonne(groupes: list, ville: str) -> list:
    """Morceaux de message a envoyer a un abonne de 'ville' ; [] si aucune
    affaire -- on n'envoie pas de « rien aujourd'hui », qui deviendrait un
    bruit qu'on cesse de lire."""
    filtres = filtrer_groupes(groupes, ville)
    if not filtres:
        return []
    n = len(filtres)
    affaires = "1 bonne affaire" if n == 1 else f"{n} bonnes affaires"
    entete = f"<b>{affaires} au départ de {NOMS_AFFICHES[ville]}</b>"
    # une ville n'a qu'une ligne par groupe : le premier element suffit
    blocs = [_bloc_abonne(g[0], ville) for g in filtres]
    # ligne vide avant la mention
    return hub_deals_db.decouper_message(blocs, entete, "\n" + MENTION_PRIX)


def _envoyer_un_morceau(envoyer, chat_id, message, dormir):
    """Un seul nouvel essai sur 429, apres le delai impose par Telegram."""
    statut, detail = envoyer(chat_id, message)
    if statut == "trop_vite":
        dormir(detail)
        statut, detail = envoyer(chat_id, message)
        if statut == "trop_vite":
            return ("echec", "trop de requetes")
    return (statut, detail)


def notifier_abonnes(conn, groupes, envoyer, log, exclure_chat_id=None,
                     dormir=time.sleep, pause: float = 0.05) -> dict:
    """Envoie a chaque abonne actif les affaires de sa ville.

    Ne leve jamais pour un abonne : une panne sur l'un n'empeche pas les
    suivants. Le compte rendu ne dit QUE ce qui a ete verifie (lecon du
    2026-09-11 : un « envoye » inconditionnel a masque 24 jours de refus).
    """
    compte = {"envoyes": 0, "bloques": 0, "echecs": [], "sans_affaire": 0, "servis": 0}
    servis = abonnes_a_servir(conn, exclure_chat_id)
    if not servis:
        log("Abonnes : aucun abonne a servir.")
        return compte

    premier = True
    for abonne in servis:
        morceaux = messages_abonne(groupes, abonne["ville_depart"])
        if not morceaux:
            compte["sans_affaire"] += 1
            continue
        compte["servis"] += 1
        try:
            resultat = ("ok", None)
            for morceau in morceaux:
                if not premier:
                    dormir(pause)  # limite Telegram : ~30 messages/seconde
                premier = False
                resultat = _envoyer_un_morceau(envoyer, abonne["chat_id"], morceau, dormir)
                if resultat[0] != "ok":
                    break
        except Exception as e:
            resultat = ("echec", f"erreur inattendue : {e}")

        statut, detail = resultat
        if statut == "ok":
            compte["envoyes"] += 1
        elif statut == "bloque":
            compte["bloques"] += 1
            desactiver(conn, abonne["chat_id"], "bloque", maintenant())
        else:
            compte["echecs"].append(str(detail))

    details = f" ({', '.join(compte['echecs'])})" if compte["echecs"] else ""
    log(f"Abonnes : {compte['envoyes']}/{compte['servis']} envoye(s), "
        f"{compte['bloques']} bloque(s), {len(compte['echecs'])} echec(s){details}, "
        f"{compte['sans_affaire']} sans affaire.")
    return compte
