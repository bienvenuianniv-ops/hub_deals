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
import urllib.parse

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
        )""", """
        CREATE TABLE IF NOT EXISTS villes_souhaitees (
            chat_id  INTEGER NOT NULL,     -- BIGINT en postgres
            ville    TEXT NOT NULL,
            quand    TEXT NOT NULL,
            PRIMARY KEY (chat_id, ville)
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
        )""", """
        CREATE TABLE IF NOT EXISTS villes_souhaitees (
            chat_id  BIGINT NOT NULL,     -- INTEGER en sqlite
            ville    TEXT NOT NULL,
            quand    TEXT NOT NULL,
            PRIMARY KEY (chat_id, ville)
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


# Les tests Postgres tournent contre un conteneur jetable, toujours sur
# la machine qui les lance (voir .github/workflows/tests.yml). La
# production, elle, est chez Neon. Cette liste est donc la frontiere
# exacte entre « je peux effacer cette table » et « surtout pas ».
_HOTES_JETABLES = ("localhost", "127.0.0.1", "::1", "[::1]")


def exiger_base_jetable(url: str) -> None:
    """Leve si `url` n'est pas une base locale et jetable.

    A appeler avant toute operation destructive d'un test. Le jour ou
    HUB_DEALS_TEST_PG_URL pointerait la production, un simple « pytest »
    effacerait les abonnes -- un recrutement fait a la main, que rien ne
    reconstruit. Le doute se tranche du cote sur : sans hote lisible, on
    refuse.

    Le message ne contient jamais l'URL : il finit dans un journal, et
    l'URL porte le mot de passe.
    """
    hote = urllib.parse.urlsplit(url).hostname
    if hote not in _HOTES_JETABLES:
        raise RuntimeError(
            "base de test refusee : une operation destructive n'est "
            "permise que sur localhost. Verifie HUB_DEALS_TEST_PG_URL.")


def ouvrir(url=None, chemin=None):
    """Connexion aux tables d'abonnes, tables creees si besoin.

    `url` presente : Postgres. `chemin` present : SQLite dans ce fichier.
    Aucun des deux : l'environnement (HUB_DEALS_ABONNES_URL) decide, et a
    defaut le fichier local -- c'est ce que fait la production, qui
    n'appelle ouvrir() sans argument.

    Un `chemin` donne explicitement N'EST JAMAIS supplante par
    l'environnement. Incident du 2026-09-22 : HUB_DEALS_ABONNES_URL est
    posee sur le portable pour le releve, et toute la suite de tests
    locale ouvrait donc la base de PRODUCTION au lieu de son fichier
    temporaire -- 58 faux abonnes y ont ete inseres. Le defaut d'un
    argument ne doit pas ecraser l'argument.
    """
    if url is None and chemin is None:
        url = os.environ.get(URL_ENV)
    chemin = chemin if chemin is not None else "flight_deals.db"
    if url:
        import psycopg
        conn = ConnexionPostgres(psycopg.connect(url))
        dialecte = "postgres"
    else:
        conn = sqlite3.connect(chemin, timeout=60)
        dialecte = "sqlite"
    if _tables_manquantes(conn, dialecte):
        for instruction in _DDL[dialecte]:
            conn.execute(instruction)
        conn.commit()
    return conn


def _tables_manquantes(conn, dialecte: str) -> bool:
    """Les tables sont-elles a creer ?

    Sans cette question, ouvrir() lancerait un CREATE TABLE IF NOT EXISTS
    a chaque connexion. En production le role du bot n'a que
    SELECT/INSERT/UPDATE : Postgres lui refuse CREATE meme quand la table
    existe deja, et la connexion echouerait -- constate sur la vraie base
    le 2026-09-20. Les tables sont creees une fois, a la main, par un role
    qui en a le droit.
    """
    if dialecte == "sqlite":
        trouvees = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('abonnes', 'etat_bot', 'villes_souhaitees')").fetchone()[0]
    else:
        trouvees = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = current_schema() "
            "AND table_name IN ('abonnes', 'etat_bot', 'villes_souhaitees')").fetchone()[0]
    return trouvees < 3
