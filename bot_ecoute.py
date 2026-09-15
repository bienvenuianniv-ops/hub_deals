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

import abonnes

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
