"""
Ouvre la connexion aux tables d'abonnes, en SQLite ou en Postgres.

Les abonnes quittent flight_deals.db : le releve tourne sur le portable,
l'ecoute sur Render, et les deux doivent voir les memes inscriptions.
Voir docs/superpowers/specs/2026-09-20-abonnes-hebergement-design.md

abonnes.py n'est PAS modifie : ses requetes restent ecrites avec des
placeholders « ? », et c'est cette couche qui les traduit pour psycopg.
"""

import os
import re
import sqlite3

URL_ENV = "HUB_DEALS_ABONNES_URL"

# Un litteral SQL ('...') est capture en premier par l'alternance, donc
# rendu tel quel : un « ? » a l'interieur n'est jamais traduit.
_MORCEAUX = re.compile(r"('(?:[^']|'')*')|(\?)|(%)")


def traduire(sql: str) -> str:
    """Traduit les placeholders « ? » en « %s » pour psycopg.

    Les pourcents litteraux sont doubles : psycopg prendrait un « % » seul
    pour le debut d'un placeholder.
    """
    def _remplacer(m):
        if m.group(1) is not None:
            return m.group(1).replace("%", "%%")
        if m.group(2) is not None:
            return "%s"
        return "%%"

    return _MORCEAUX.sub(_remplacer, sql)


# Le DDL diverge entre moteurs, le DML non : chat_id est un BIGINT en
# Postgres, ou INTEGER plafonne a 2 147 483 647 -- Telegram attribue des
# identifiants au-dela. La panne n'arriverait pas au test, elle arriverait
# au premier inconnu inscrit.
_DDL = {
    "sqlite": ("""
        CREATE TABLE IF NOT EXISTS abonnes (
            chat_id       INTEGER PRIMARY KEY,
            prenom        TEXT,
            ville_depart  TEXT,
            actif         INTEGER NOT NULL DEFAULT 1,
            inscrit_le    TEXT NOT NULL,
            modifie_le    TEXT NOT NULL,
            motif_inactif TEXT
        )""", """
        CREATE TABLE IF NOT EXISTS etat_bot (
            cle    TEXT PRIMARY KEY,
            valeur TEXT NOT NULL
        )"""),
    "postgres": ("""
        CREATE TABLE IF NOT EXISTS abonnes (
            chat_id       BIGINT PRIMARY KEY,
            prenom        TEXT,
            ville_depart  TEXT,
            actif         INTEGER NOT NULL DEFAULT 1,
            inscrit_le    TEXT NOT NULL,
            modifie_le    TEXT NOT NULL,
            motif_inactif TEXT
        )""", """
        CREATE TABLE IF NOT EXISTS etat_bot (
            cle    TEXT PRIMARY KEY,
            valeur TEXT NOT NULL
        )"""),
}


class ConnexionPostgres:
    """Donne a psycopg la forme d'une connexion sqlite3 : execute() rend
    un curseur, les placeholders s'ecrivent « ? ».

    Assez pour abonnes.py, volontairement : cette couche n'existe pas pour
    abstraire une base, mais pour ne pas avoir deux versions des requetes.
    """

    def __init__(self, connexion):
        self._connexion = connexion

    def execute(self, sql, parametres=()):
        return self._connexion.execute(traduire(sql), tuple(parametres))

    def commit(self):
        self._connexion.commit()

    def close(self):
        self._connexion.close()


def ouvrir(url=None, chemin="flight_deals.db"):
    """Connexion aux tables d'abonnes, tables creees si besoin.

    `url` absente : SQLite dans `chemin` (tests, et secours si la base
    distante tombe pendant une mise au point locale). `url` presente :
    Postgres. L'environnement (HUB_DEALS_ABONNES_URL) sert de defaut.
    """
    url = url if url is not None else os.environ.get(URL_ENV)
    if url:
        import psycopg
        conn = ConnexionPostgres(psycopg.connect(url))
        dialecte = "postgres"
    else:
        conn = sqlite3.connect(chemin, timeout=60)
        dialecte = "sqlite"
    for instruction in _DDL[dialecte]:
        conn.execute(instruction)
    conn.commit()
    return conn
