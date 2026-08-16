"""
Sauvegarde de la base, en local et hors machine.

Deux risques DIFFERENTS, deux mecanismes :

  - la copie LOCALE protege de l'erreur logique (migration ratee,
    purge trop large). Elle est instantanee et ne demande aucun
    reseau, mais elle vit sur le meme disque : elle ne protege pas
    d'une panne materielle ;
  - le dump DISTANT protege de la perte de la machine. C'est le seul
    qui survit a un disque mort ou a un vol.

Le dump est un fichier SQL texte, pas une copie binaire : il est
lisible, git l'encode en deltas efficaces, et il se restaure avec
n'importe quel sqlite3 sans dependre du format de fichier.

Restauration :
    git show sauvegardes:flight_deals.sql | sqlite3 restauree.db

Aucune erreur de sauvegarde ne doit interrompre un releve -- meme
regle que pour Telegram : perdre une sauvegarde est ennuyeux, perdre
la collecte du jour l'est davantage.
"""

import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone

NOM_DUMP = "flight_deals.sql"
BRANCHE = "sauvegardes"
COPIES_LOCALES_GARDEES = 5


def generer_dump(conn: sqlite3.Connection) -> str:
    """Rend le contenu de la base sous forme de script SQL complet."""
    return "\n".join(conn.iterdump())


def _horodatage() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def purger_anciennes(db_path: str, garder: int = COPIES_LOCALES_GARDEES) -> list:
    """Supprime les copies locales les plus anciennes.

    Ne touche QUE les fichiers nommes <base>.sauvegarde-* : le tri se
    fait sur le nom, dont l'horodatage est ordonnable alphabetiquement.
    Renvoie la liste des fichiers supprimes.
    """
    dossier = os.path.dirname(os.path.abspath(db_path))
    prefixe = os.path.basename(db_path) + ".sauvegarde-"
    copies = sorted(f for f in os.listdir(dossier) if f.startswith(prefixe))

    supprimes = []
    for nom in copies[:max(0, len(copies) - garder)]:
        os.remove(os.path.join(dossier, nom))
        supprimes.append(nom)
    return supprimes


def sauvegarder_local(db_path: str, garder: int = COPIES_LOCALES_GARDEES,
                      horodatage: str = None) -> str:
    """Copie la base a cote d'elle-meme, puis purge les plus anciennes.

    A appeler AVANT toute operation destructive. Renvoie le chemin de
    la copie creee.
    """
    horodatage = horodatage or _horodatage()
    destination = f"{db_path}.sauvegarde-{horodatage}"
    shutil.copy2(db_path, destination)
    purger_anciennes(db_path, garder)
    return destination


def _executer(args, cwd=None):
    """Lance une commande et rend (code de retour, sortie)."""
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def sauvegarder_distant(conn: sqlite3.Connection, dossier: str,
                        executer=None, journaliser=None) -> bool:
    """Ecrit le dump dans `dossier` et le pousse sur la branche dediee.

    `dossier` est un worktree git positionne sur la branche de
    sauvegarde -- voir preparer_worktree(). `executer` est injectable
    pour les tests : aucun appel git reel n'y est fait.

    Renvoie True si la sauvegarde est a jour (poussee, ou deja
    identique), False en cas d'echec. Ne leve JAMAIS.
    """
    executer = executer or _executer
    journaliser = journaliser or (lambda m: None)

    try:
        chemin = os.path.join(dossier, NOM_DUMP)
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(generer_dump(conn))

        code, sortie = executer(["git", "add", NOM_DUMP], cwd=dossier)
        if code != 0:
            journaliser(f"   -> sauvegarde distante : echec de add ({sortie[:120]})")
            return False

        # rien de nouveau : ne pas produire un commit vide
        code, _ = executer(["git", "diff", "--cached", "--quiet"], cwd=dossier)
        if code == 0:
            journaliser("   -> sauvegarde distante : base inchangee, rien a pousser")
            return True

        message = f"sauvegarde {_horodatage()}"
        code, sortie = executer(["git", "commit", "-m", message], cwd=dossier)
        if code != 0:
            journaliser(f"   -> sauvegarde distante : echec du commit ({sortie[:120]})")
            return False

        code, sortie = executer(["git", "push", "origin", BRANCHE], cwd=dossier)
        if code != 0:
            journaliser(f"   -> sauvegarde distante : echec du push ({sortie[:120]})")
            return False

        journaliser("   -> sauvegarde distante poussee")
        return True

    except Exception as e:
        # une panne de sauvegarde ne doit jamais interrompre un releve
        journaliser(f"   -> sauvegarde distante impossible : {e}")
        return False


def restaurer(chemin_dump: str, chemin_db: str) -> int:
    """Reconstruit une base a partir d'un dump SQL. Rend le nombre de
    lignes restaurees.

    Outille plutot que documente : sqlite3 n'existe pas en ligne de
    commande sur toutes les machines -- notamment pas sur celle-ci --
    et une procedure de restauration qu'on decouvre inexecutable le
    jour de la panne ne vaut rien.

    Refuse d'ecraser un fichier existant : restaurer par-dessus la base
    en service detruirait ce qu'on cherche a proteger.
    """
    if os.path.exists(chemin_db):
        raise FileExistsError(
            f"{chemin_db} existe deja. Choisis un autre nom : restaurer "
            f"par-dessus une base en service la detruirait.")

    with open(chemin_dump, encoding="utf-8") as f:
        dump = f.read()

    conn = sqlite3.connect(chemin_db)
    try:
        conn.executescript(dump)
        conn.commit()
        return conn.execute("SELECT COUNT(*) FROM offres").fetchone()[0]
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    USAGE = """Usage :
  python sauvegarde.py --sauver              sauvegarde locale + distante
  python sauvegarde.py --restaurer <dump> <destination>

Pour recuperer le dump depuis la branche de sauvegarde :
  git show sauvegardes:flight_deals.sql > dump.sql"""

    argv = sys.argv[1:]
    if argv[:1] == ["--restaurer"] and len(argv) == 3:
        n = restaurer(argv[1], argv[2])
        print(f"{n} lignes restaurees dans {argv[2]}")
    elif argv[:1] == ["--sauver"]:
        import hub_deals_db as collecteur
        copie = sauvegarder_local(collecteur.DB_PATH)
        print(f"copie locale : {copie}")
        conn = sqlite3.connect(collecteur.DB_PATH)
        ok = sauvegarder_distant(conn, ".sauvegardes", journaliser=print)
        conn.close()
        print("sauvegarde distante :", "OK" if ok else "ECHEC")
    else:
        print(USAGE)
        sys.exit(1)
