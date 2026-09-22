"""
Logique d'inscription au bot Telegram : traduit une mise a jour en
actions, sans aucun appel reseau.

Le long polling a ete retire le 2026-09-20 : l'ecoute est servie par
web_bot.py (webhook, hors du portable), parce qu'elle ne repondait que
lorsque le portable etait allume. Ce module reste la reference UNIQUE de
la logique d'inscription, appelee par le service.

Spec : docs/superpowers/specs/2026-09-20-abonnes-hebergement-design.md
"""

import hmac
import os
import re
from datetime import datetime, timezone

import requests

import abonnes
import hub_deals_db
import page

LOG_PATH = "bot_ecoute_log.txt"
DELAI_LONG_POLLING = 50   # secondes : Telegram garde la requete ouverte
ATTENTE_CONFLIT = 30      # HTTP 409 : un autre lecteur de getUpdates

MSG_INVITATION = "Ce bot est pour l'instant sur invitation."
MSG_COMPLET = "Le test est complet pour le moment."
MSG_BIENVENUE = "Bienvenue ! Choisis ta ville de départ :"
MSG_MENU = "Choisis ta ville de départ :"
MSG_STOP = "Tu es désabonné. /start pour revenir."
MSG_AIDE = "Commandes : /ville pour changer de ville, /stop pour arrêter."
MSG_ATTENTE = ("C'est noté. Je te préviens dès que {ville} sera couverte. "
               "En attendant, les affaires du jour sont sur la page publique.")


def msg_confirmation(ville: str) -> str:
    nom = abonnes.NOMS_AFFICHES[ville]
    return (f"C'est noté : {nom}. Tu recevras les bonnes affaires au départ de "
            f"{nom}, au plus une fois par jour. /ville pour changer, /stop pour arrêter.")


def ville_depuis_etiquette(etiquette: str):
    """Chemin inverse de etiquette_ville(), CONSTRUIT et non recopie.

    Une table ecrite a la main divergerait des que NOMS_AFFICHES change
    -- exactement le defaut du 2026-09-16, ou deux endroits fabriquaient
    l'etiquette chacun de leur cote.
    """
    for ville in abonnes.NOMS_AFFICHES:
        if hub_deals_db.etiquette_ville(ville) == etiquette:
            return ville
    return None


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
        # VILLES_PROPOSEES et non NOMS_AFFICHES : cette derniere reste la
        # table des villes CONNUES, dont celles ou un abonne est deja
        # inscrit. Voir la mesure du 2026-09-22 dans abonnes.py.
        action["reply_markup"] = {"inline_keyboard": [
            [{"text": abonnes.NOMS_AFFICHES[cle],
              "callback_data": f"ville:{cle}"}]
            for cle in abonnes.VILLES_PROPOSEES]}
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
    # on valide contre les villes PROPOSEES : Telegram garde les anciens
    # messages indefiniment, et un clavier d'avant le 2026-09-22 reste
    # cliquable dans l'historique de la conversation.
    if ville not in abonnes.VILLES_PROPOSEES:
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

    if commande == "/start" and argument.startswith(page.PREFIXE_ATTENTE):
        # une liste d'attente n'abonne pas : pas de code exige, pas de
        # plafond consomme, aucune alerte quotidienne promise
        import souhaits
        ville = ville_depuis_etiquette(argument[len(page.PREFIXE_ATTENTE):])
        if ville is None:
            return [_envoi(chat_id, MSG_INVITATION)], f"attente refusee (ville inconnue) chat_id={chat_id}"
        souhaits.noter(conn, chat_id, ville, quand)
        return ([_envoi(chat_id, MSG_ATTENTE.format(ville=abonnes.NOMS_AFFICHES[ville]))],
                f"attente {ville} chat_id={chat_id}")

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
