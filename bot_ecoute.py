"""
Ecoute du bot Telegram @ianniv_vols_bot : inscriptions au test prive.

Programme permanent, lance a l'ouverture de session par la tache planifiee
« Bot vols - ecoute » (pythonw, sans fenetre). SEUL lecteur de getUpdates :
le releve (hub_deals_db.py) ne fait qu'envoyer -- deux lecteurs simultanes
provoquent un HTTP 409 cote Telegram.

N'appelle jamais l'API des prix. Les messages recus ne sont jamais recopies
au journal : ils contiennent le code d'invitation ou des donnees
personnelles des invites.

Spec : docs/superpowers/specs/2026-09-15-bot-abonnes-design.md
"""

import hmac
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests

import abonnes
import hub_deals_db

LOG_PATH = "bot_ecoute_log.txt"
DELAI_LONG_POLLING = 50   # secondes : Telegram garde la requete ouverte
ATTENTE_MAX = 300         # plafond de l'attente croissante apres erreur
ATTENTE_CONFLIT = 30      # HTTP 409 : un autre lecteur de getUpdates

MSG_INVITATION = "Ce bot est pour l'instant sur invitation."
MSG_COMPLET = "Le test est complet pour le moment."
MSG_BIENVENUE = "Bienvenue ! Choisis ta ville de départ :"
MSG_MENU = "Choisis ta ville de départ :"
MSG_STOP = "Tu es désabonné. /start pour revenir."
MSG_AIDE = "Commandes : /ville pour changer de ville, /stop pour arrêter."


def msg_confirmation(ville: str) -> str:
    nom = abonnes.NOMS_AFFICHES[ville]
    return (f"C'est noté : {nom}. Tu recevras les bonnes affaires au départ de "
            f"{nom}, au plus une fois par jour. /ville pour changer, /stop pour arrêter.")


def code_valide(code):
    """Le code d'invitation utilisable, ou None (inscriptions fermees).
    Au moins 12 caracteres : le code est masque dans le journal par simple
    remplacement, un code court y mutilerait des lignes legitimes."""
    if code and re.fullmatch(r"[A-Za-z0-9_-]{12,64}", code):
        return code
    return None


CODE_INVITATION = code_valide(os.environ.get("HUB_DEALS_CODE_INVITATION"))


def masquer(message: str) -> str:
    """Tokens (via hub_deals_db) et code d'invitation remplaces par ***."""
    message = hub_deals_db.masquer_secrets(message)
    if CODE_INVITATION:
        message = message.replace(CODE_INVITATION, "***")
    return message


def log(message: str) -> None:
    """Journal separe de celui du releve : deux processus, deux fichiers."""
    horodatage = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ligne = f"[{horodatage}] {masquer(message)}"
    print(ligne)  # silencieux sous pythonw (stdout None)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(ligne + "\n")


def journaliser_plantage(type_exc, valeur, trace) -> None:
    """Crochet sys.excepthook : sous pythonw, stderr vaut None."""
    import traceback
    log("=== PLANTAGE de l'ecoute ===")
    log("".join(traceback.format_exception(type_exc, valeur, trace)).rstrip())


def appeler(token, methode, params, timeout=15):
    """Seule fonction reseau du module. Renvoie (code HTTP, corps JSON)."""
    reponse = requests.post(f"https://api.telegram.org/bot{token}/{methode}",
                            json=params, timeout=timeout)
    try:
        corps = reponse.json()
    except ValueError:
        corps = {}
    return reponse.status_code, corps


def executer_actions(token, actions, appeler_fn) -> None:
    for action in actions:
        params = {k: v for k, v in action.items() if k != "methode"}
        try:
            statut, corps = appeler_fn(token, action["methode"], params)
        except requests.exceptions.RequestException as e:
            log(f"ERREUR reseau {action['methode']} : {e}")
            continue
        if statut != 200:
            log(f"ECHEC {action['methode']} HTTP {statut} "
                f"{(corps or {}).get('description', '')} chat_id={params.get('chat_id')}")


def _envoi(chat_id, texte, clavier=False) -> dict:
    action = {"methode": "sendMessage", "chat_id": chat_id, "text": texte}
    if clavier:
        action["reply_markup"] = {"inline_keyboard": [
            [{"text": nom, "callback_data": f"ville:{cle}"}]
            for cle, nom in abonnes.NOMS_AFFICHES.items()]}
    return action


def _traiter_bouton(conn, cq: dict, quand: str):
    chat_id = cq["message"]["chat"]["id"]
    actions = [{"methode": "answerCallbackQuery", "callback_query_id": cq["id"]}]
    abonne = abonnes.trouver(conn, chat_id)
    if abonne is None:
        return actions + [_envoi(chat_id, MSG_INVITATION)], f"bouton refuse (inconnu) chat_id={chat_id}"
    if not abonne["actif"]:
        return actions + [_envoi(chat_id, MSG_STOP)], f"bouton refuse (desabonne) chat_id={chat_id}"
    data = cq.get("data") or ""
    ville = data[len("ville:"):] if data.startswith("ville:") else None
    if ville not in abonnes.NOMS_AFFICHES:
        return actions, f"bouton inconnu chat_id={chat_id}"
    abonnes.choisir_ville(conn, chat_id, ville, quand)
    return actions + [_envoi(chat_id, msg_confirmation(ville))], f"ville {ville} chat_id={chat_id}"


def _code_correct(argument: str, code) -> bool:
    # compare_digest exige de l'ASCII : un argument accentue est simplement faux
    return bool(code) and argument.isascii() and hmac.compare_digest(argument, code)


def traiter_update(conn, update: dict, code, quand: str):
    """Traduit une mise a jour Telegram en actions a executer. Aucun appel
    reseau. Idempotent : rejouer une mise a jour ne change rien de plus.

    Renvoie (actions, resume) ; le resume, destine au journal, ne contient
    jamais le texte recu."""
    if "callback_query" in update:
        return _traiter_bouton(conn, update["callback_query"], quand)

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or "text" not in message:
        return [], None
    if chat.get("type") != "private":
        return [], f"ignore (conversation non privee) chat_id={chat_id}"

    commande, _, argument = message["text"].strip().partition(" ")
    commande = commande.split("@")[0]
    argument = argument.strip()
    prenom = (message.get("from") or {}).get("first_name")
    abonne = abonnes.trouver(conn, chat_id)
    complet = abonnes.nb_actifs(conn) >= abonnes.PLAFOND_ABONNES

    if commande == "/start":
        if abonne is not None and abonne["actif"]:
            return [_envoi(chat_id, MSG_BIENVENUE, clavier=True)], f"start (deja abonne) chat_id={chat_id}"
        if abonne is not None:
            if complet:
                return [_envoi(chat_id, MSG_COMPLET)], f"start refuse (complet) chat_id={chat_id}"
            abonnes.inscrire(conn, chat_id, prenom, quand)
            return [_envoi(chat_id, MSG_BIENVENUE, clavier=True)], f"start (retour) chat_id={chat_id}"
        if not _code_correct(argument, code):
            return [_envoi(chat_id, MSG_INVITATION)], f"start refuse (code absent ou faux) chat_id={chat_id}"
        if complet:
            return [_envoi(chat_id, MSG_COMPLET)], f"start refuse (complet) chat_id={chat_id}"
        abonnes.inscrire(conn, chat_id, prenom, quand)
        return [_envoi(chat_id, MSG_BIENVENUE, clavier=True)], f"start (nouvel abonne) chat_id={chat_id}"

    if abonne is None:
        return [_envoi(chat_id, MSG_INVITATION)], f"refuse (inconnu) chat_id={chat_id}"

    if commande == "/ville":
        if not abonne["actif"]:
            return [_envoi(chat_id, MSG_STOP)], f"ville refuse (desabonne) chat_id={chat_id}"
        return [_envoi(chat_id, MSG_MENU, clavier=True)], f"ville (menu) chat_id={chat_id}"

    if commande == "/stop":
        abonnes.desactiver(conn, chat_id, "stop", quand)
        return [_envoi(chat_id, MSG_STOP)], f"stop chat_id={chat_id}"

    return [_envoi(chat_id, MSG_AIDE)], f"message libre chat_id={chat_id}"


def _attente_apres_echec(echecs: int) -> int:
    return min(ATTENTE_MAX, 5 * 2 ** (echecs - 1))


def boucle(conn, token, code, appeler_fn=appeler, dormir=time.sleep,
           quand_fn=abonnes.maintenant, tours=None) -> None:
    """Long polling getUpdates. tours=None : sans fin (production)."""
    offset = None
    echecs = 0
    tour = 0
    while tours is None or tour < tours:
        tour += 1
        params = {"timeout": DELAI_LONG_POLLING,
                  "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        try:
            statut, corps = appeler_fn(token, "getUpdates", params,
                                       timeout=DELAI_LONG_POLLING + 10)
        except requests.exceptions.RequestException as e:
            echecs += 1
            attente = _attente_apres_echec(echecs)
            log(f"ERREUR reseau getUpdates ({e}) : nouvel essai dans {attente} s")
            dormir(attente)
            continue

        if statut == 409:
            log(f"CONFLIT HTTP 409 : un autre programme lit getUpdates, "
                f"nouvel essai dans {ATTENTE_CONFLIT} s")
            dormir(ATTENTE_CONFLIT)
            continue
        if statut != 200:
            echecs += 1
            attente = _attente_apres_echec(echecs)
            log(f"ECHEC getUpdates HTTP {statut} {(corps or {}).get('description', '')} : "
                f"nouvel essai dans {attente} s")
            dormir(attente)
            continue

        echecs = 0
        try:
            abonnes.noter_ecoute(conn, quand_fn())
        except sqlite3.OperationalError as e:
            log(f"ERREUR temoin d'ecoute : {e}")

        for update in corps.get("result", []):
            try:
                actions, resume = traiter_update(conn, update, code, quand_fn())
            except sqlite3.OperationalError as e:
                # base occupee par le releve : on ne confirme pas cette mise
                # a jour, Telegram la renverra au tour suivant
                log(f"Base occupee, mise a jour {update.get('update_id')} rejouee : {e}")
                break
            except Exception as e:
                log(f"ERREUR traitement mise a jour {update.get('update_id')} : {e}")
                offset = update["update_id"] + 1
                continue
            offset = update["update_id"] + 1
            if resume:
                log(resume)
            executer_actions(token, actions, appeler_fn)


if __name__ == "__main__":
    # sous pythonw.exe, rien ne s'affiche : tout plantage doit aller au journal
    sys.excepthook = journaliser_plantage

    token = hub_deals_db.TELEGRAM_BOT_TOKEN
    if not token:
        log("ARRET : il manque TELEGRAM_BOT_TOKEN dans l'environnement.")
        sys.exit(1)
    if CODE_INVITATION is None:
        log("Inscriptions FERMEES : HUB_DEALS_CODE_INVITATION absent ou invalide "
            "(12 a 64 caracteres parmi A-Z a-z 0-9 _ -).")

    # timeout > duree d'une transaction du releve (~25 s par hub)
    conn = sqlite3.connect(hub_deals_db.DB_PATH, timeout=60)
    abonnes.init_abonnes(conn)
    log("=== Demarrage de l'ecoute ===")
    boucle(conn, token, CODE_INVITATION)
