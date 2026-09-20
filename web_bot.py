"""
Service webhook Telegram, heberge hors du portable (Render).

Remplace le long polling de bot_ecoute.py, qui ne repondait que lorsque
le portable etait allume : une inscription lancee a 23 h n'obtenait le
clavier des villes que le lendemain matin.

La logique d'inscription n'est PAS reecrite ici : traiter_update() est
pure et idempotente, elle est appelee telle quelle.

Spec : docs/superpowers/specs/2026-09-20-abonnes-hebergement-design.md
"""

import os
import sys

from flask import Flask, request

import abonnes
import bot_ecoute
import hub_deals_db
import magasin

EN_TETE_SECRET = "X-Telegram-Bot-Api-Secret-Token"


def journal(message: str) -> None:
    """Sur Render, le journal est la sortie standard. Jamais le texte recu,
    jamais le code d'invitation : masquer() s'en charge."""
    print(bot_ecoute.masquer(message), flush=True)


def creer_app(ouvrir=None, appeler=None, token=None, code=None, secret=None):
    """Fabrique l'application. Tout est injectable : aucun test ne doit
    ouvrir une vraie base ni appeler Telegram."""
    ouvrir = ouvrir or magasin.ouvrir
    appeler = appeler or bot_ecoute.appeler
    token = token if token is not None else hub_deals_db.TELEGRAM_BOT_TOKEN
    code = code if code is not None else bot_ecoute.CODE_INVITATION
    secret = secret if secret is not None else os.environ.get("TELEGRAM_WEBHOOK_SECRET")

    app = Flask(__name__)

    @app.get("/sante")
    def sante():
        return {"etat": "ok"}

    @app.post("/telegram")
    def telegram():
        # Un secret absent ferme la porte, il ne l'ouvre pas : un service
        # deploye sans TELEGRAM_WEBHOOK_SECRET n'accepte personne.
        if not secret or request.headers.get(EN_TETE_SECRET) != secret:
            journal("REFUS : secret de webhook absent ou faux")
            return {"erreur": "interdit"}, 403

        update = request.get_json(silent=True) or {}
        try:
            conn = ouvrir()
        except Exception as erreur:
            # 500 et non 200 : Telegram reessaiera. Un 200 sur un message
            # non traite perdrait l'inscription en silence.
            journal(f"ECHEC : base injoignable ({erreur})")
            return {"erreur": "base injoignable"}, 500

        try:
            actions, resume = bot_ecoute.traiter_update(
                conn, update, code, abonnes.maintenant())
            if resume:
                journal(resume)
            if actions:
                bot_ecoute.executer_actions(token, actions, appeler)
        finally:
            conn.close()
        return {"ok": True}

    return app


# Cree seulement quand la base distante est configuree : gunicorn importe
# ce module, et une application a moitie configuree repondrait 500 a tout
# le monde sans que la cause soit lisible.
app = creer_app() if os.environ.get(magasin.URL_ENV) else None

if __name__ == "__main__":
    if app is None:
        sys.exit(f"ARRET : {magasin.URL_ENV} absente de l'environnement")
    app.run(port=int(os.environ.get("PORT", 5000)))
