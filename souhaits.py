"""
Liste d'attente : les villes que des gens reclament et que le programme
ne sert pas encore.

Aucun appel reseau. La table porte des chat_id Telegram : elle est
PRIVEE (voir sauvegarde.TABLES_PRIVEES et abonnes.ecrire_instantane).

Spec : docs/superpowers/specs/2026-09-22-page-publique-design.md
"""

_COLONNES = ("chat_id", "ville", "quand")


def noter(conn, chat_id: int, ville: str, quand: str) -> None:
    """Enregistre un souhait. Rejouable : recliquer le lien ne gonfle
    pas la demande, ce qui rendrait le compte inutilisable."""
    conn.execute(
        "INSERT INTO villes_souhaitees (chat_id, ville, quand) "
        "VALUES (?, ?, ?) ON CONFLICT (chat_id, ville) DO NOTHING",
        (chat_id, ville, quand))
    conn.commit()


def compter(conn) -> dict:
    """Combien de personnes reclament chaque ville."""
    return {v: n for v, n in conn.execute(
        "SELECT ville, COUNT(*) FROM villes_souhaitees "
        "GROUP BY ville ORDER BY COUNT(*) DESC").fetchall()}


def instantane(conn) -> list:
    lignes = conn.execute(
        f"SELECT {', '.join(_COLONNES)} FROM villes_souhaitees "
        "ORDER BY chat_id, ville").fetchall()
    return [dict(zip(_COLONNES, l)) for l in lignes]
