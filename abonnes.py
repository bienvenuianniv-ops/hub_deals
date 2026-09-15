"""
Abonnes au bot Telegram (test prive du 2026-09-15).

Ce module ne fait AUCUN appel reseau : l'envoi est injecte par l'appelant.
Il est partage par le releve (hub_deals_db.py, qui envoie) et par l'ecoute
(bot_ecoute.py, qui inscrit) -- deux processus sur la meme base SQLite,
d'ou des transactions courtes, commitees aussitot.

Spec : docs/superpowers/specs/2026-09-15-bot-abonnes-design.md
"""

from datetime import datetime, timezone

# cle de RABATTEMENT -> nom affiche aux abonnes. Un test verifie que les
# deux ensembles de cles sont identiques.
NOMS_AFFICHES = {
    "Dakar": "Dakar",
    "Abidjan": "Abidjan",
    "Lome": "Lomé",
    "Kinshasa": "Kinshasa",
    "Brazzaville": "Brazzaville",
}

# garde-fou du test prive : protege si le lien d'invitation circule
PLAFOND_ABONNES = 50

_COLONNES = ("chat_id", "prenom", "ville_depart", "actif",
             "inscrit_le", "modifie_le", "motif_inactif")


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_abonnes(conn) -> None:
    """Cree les tables si besoin. Idempotent."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS abonnes (
            chat_id       INTEGER PRIMARY KEY,
            prenom        TEXT,
            ville_depart  TEXT,
            actif         INTEGER NOT NULL DEFAULT 1,
            inscrit_le    TEXT NOT NULL,
            modifie_le    TEXT NOT NULL,
            motif_inactif TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS etat_bot (
            cle    TEXT PRIMARY KEY,
            valeur TEXT NOT NULL
        )
    """)
    conn.commit()


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
