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
DELAI_COMMANDE = 300   # secondes ; un push de sauvegarde en prend quelques-unes


# Tables qui ne doivent JAMAIS sortir : le dump part sur un depot public.
# Constate le 2026-09-20 -- chat_id Telegram et prenom de chaque abonne y
# etaient publies deux fois par jour. Recruter, c'etait publier.
TABLES_PRIVEES = ("abonnes", "etat_bot", "villes_souhaitees")


def generer_dump(conn: sqlite3.Connection,
                 tables_privees=TABLES_PRIVEES) -> str:
    """Rend le contenu de la base sous forme de script SQL, sans les
    donnees des tables privees.

    La base est d'abord copiee en memoire, puis les tables privees y sont
    VIDEES avant le dump. On ne filtre pas le SQL produit ligne a ligne :
    une valeur peut contenir apostrophes, virgules ou sauts de ligne, et
    une regex qui laisse passer une seule ligne publie une personne. Ici
    la garantie est structurelle -- ce qui n'est plus dans la copie ne
    peut pas etre dans le dump.

    Le schema est conserve : une base restauree doit rester utilisable par
    le bot, qui ecrirait sinon dans une table inexistante.

    Mesure sur la vraie base (15,5 Mo, 85 885 lignes) : environ 0,5 s.

    Contrepartie : les abonnes ne partent plus avec ce dump. Ils ont
    depuis le 2026-09-22 leur propre copie locale, quotidienne et datee
    (hub_deals_db.copier_abonnes_et_alerter). Elle reste sur le
    portable : ce dump-ci va sur un depot PUBLIC, et une copie chiffree
    hors machine serait le prochain cran.
    """
    copie = sqlite3.connect(":memory:")
    try:
        conn.backup(copie)
        presentes = {ligne[0] for ligne in copie.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table in tables_privees:
            if table in presentes:
                copie.execute(f"DELETE FROM {table}")
        return "\n".join(copie.iterdump())
    finally:
        copie.close()


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


def _executer(args, cwd=None, delai=DELAI_COMMANDE):
    """Lance une commande et rend (code de retour, sortie). Ne bloque jamais.

    La tache planifiee tourne sans personne devant l'ecran. Sans identifiant
    valide, `git push` ouvrait le gestionnaire d'identifiants et attendait
    une saisie indefiniment (reproduit le 2026-09-13) : le releve ne finissait
    jamais, l'alerte ne partait pas, et la tache refusait de se relancer.
    D'ou trois parades :

      - saisie interdite (GIT_TERMINAL_PROMPT, GCM_INTERACTIVE) : git echoue
        tout de suite, avec un message explicite ;
      - delai maximal, en dernier recours. On tue alors tout l'ARBRE de
        processus : le gestionnaire d'identifiants herite des tubes de
        sortie, et tant qu'il vit, lire la sortie de git bloque aussi ;
      - sortie decodee en UTF-8 (celui de git) et non en cp1252, qui
        affichait n'importe quoi, voire perdait toute la sortie.

    CREATE_NO_WINDOW : la tache tourne sous pythonw.exe, sans console ;
    chaque commande console lancee en ouvrirait sinon une a elle.
    """
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    p = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        brut, _ = p.communicate(timeout=delai)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                       capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        p.kill()
        return 1, f"delai de {delai} s depasse, commande interrompue : {' '.join(args)}"
    return p.returncode, brut.decode("utf-8", errors="replace")


def _fin(sortie: str, longueur: int = 200) -> str:
    """Resume une sortie de commande pour le journal, en gardant la FIN :
    git y met la cause (`fatal: Authentication failed`)."""
    sortie = " ".join(sortie.split())
    return sortie if len(sortie) <= longueur else "..." + sortie[-longueur:]


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
            journaliser(f"   -> sauvegarde distante : echec de add ({_fin(sortie)})")
            return False

        # rien de nouveau : ne pas produire un commit vide
        code, _ = executer(["git", "diff", "--cached", "--quiet"], cwd=dossier)
        if code == 0:
            journaliser("   -> sauvegarde distante : base inchangee, rien a pousser")
            return True

        message = f"sauvegarde {_horodatage()}"
        code, sortie = executer(["git", "commit", "-m", message], cwd=dossier)
        if code != 0:
            journaliser(f"   -> sauvegarde distante : echec du commit ({_fin(sortie)})")
            return False

        code, sortie = executer(["git", "push", "origin", BRANCHE], cwd=dossier)
        if code != 0:
            journaliser(f"   -> sauvegarde distante : echec du push ({_fin(sortie)})")
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
    except Exception:
        # connect() a deja cree le fichier : le laisser interdirait de
        # reessayer sous le meme nom (voir le garde-fou ci-dessus)
        conn.close()
        os.remove(chemin_db)
        raise
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
