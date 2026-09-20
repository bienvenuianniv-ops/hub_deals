"""
Reprend les abonnes de la base SQLite locale vers la base distante.

Migration ponctuelle de la bascule du 2026-09-20, gardee au depot : une
migration qu'on ne peut pas rejouer est une migration qu'on ne peut pas
verifier.

    python migrer_abonnes.py            # HUB_DEALS_ABONNES_URL en cible
    python migrer_abonnes.py --a-blanc  # montre sans ecrire
"""

import argparse
import sys

import abonnes
import magasin

# Volontairement recopiees plutot qu'importees d'abonnes (privees la-bas) :
# un test verifie que les deux listes restent identiques, sinon une colonne
# ajoutee un jour serait oubliee ici en silence.
_COLONNES = ("chat_id", "prenom", "ville_depart", "actif",
             "inscrit_le", "modifie_le", "motif_inactif")


def migrer(source, cible) -> dict:
    """Copie abonnes et etat_bot de `source` vers `cible`.

    La CIBLE fait foi : une ligne deja presente n'est jamais ecrasee. Si
    quelqu'un s'inscrit par le webhook pendant la bascule, sa ligne toute
    fraiche ne doit pas etre remplacee par la vieille copie locale.
    """
    reprises = {"abonnes": 0, "etat_bot": 0}

    lignes = source.execute(
        f"SELECT {', '.join(_COLONNES)} FROM abonnes").fetchall()
    for ligne in lignes:
        curseur = cible.execute(
            f"""INSERT INTO abonnes ({', '.join(_COLONNES)})
                VALUES ({', '.join('?' * len(_COLONNES))})
                ON CONFLICT (chat_id) DO NOTHING""", tuple(ligne))
        reprises["abonnes"] += max(curseur.rowcount, 0)

    for cle, valeur in source.execute("SELECT cle, valeur FROM etat_bot").fetchall():
        curseur = cible.execute(
            """INSERT INTO etat_bot (cle, valeur) VALUES (?, ?)
               ON CONFLICT (cle) DO NOTHING""", (cle, valeur))
        reprises["etat_bot"] += max(curseur.rowcount, 0)

    cible.commit()
    return reprises


def main(argv=None) -> int:
    analyseur = argparse.ArgumentParser(description="Reprise des abonnes")
    analyseur.add_argument("--a-blanc", action="store_true",
                           help="montrer ce qui serait repris, sans ecrire")
    options = analyseur.parse_args(sys.argv[1:] if argv is None else argv)

    source = magasin.ouvrir(url="")            # chaine vide : SQLite locale
    cible = magasin.ouvrir()                   # HUB_DEALS_ABONNES_URL
    a_reprendre = source.execute("SELECT COUNT(*) FROM abonnes").fetchone()[0]
    print(f"Source : {a_reprendre} abonne(s)")

    if options.a_blanc:
        print("A blanc : rien n'a ete ecrit.")
        return 0

    compte = migrer(source, cible)
    print(f"Reprises : {compte['abonnes']} abonne(s), "
          f"{compte['etat_bot']} ligne(s) d'etat")
    print(f"Cible : {abonnes.nb_actifs(cible)} abonne(s) actif(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
