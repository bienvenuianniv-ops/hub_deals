"""
Vigie externe : verifie, depuis GitHub Actions, que le releve quotidien
du portable tourne toujours.

La collecte vit sur un portable Windows qui dort, perd le reseau et subit
des coupures de courant. Une vigie hebergee sur cette machine se tairait
en meme temps qu'elle : celle-ci tourne ailleurs et ne lit que ce que le
portable a deja pousse sur GitHub.

N'appelle JAMAIS l'API des prix et n'ecrit rien dans le depot.

Spec : docs/superpowers/specs/2026-09-18-vigie-externe-design.md
"""

import subprocess
import sys
from datetime import datetime, timezone
from statistics import median

BRANCHE = "origin/sauvegardes"
NOM_DUMP = "flight_deals.sql"
NB_RELEVES_LUS = 11   # le dernier + les 10 qui servent de reference

COMMANDE_GIT = ["git", "log", "--format=%cI", "--numstat",
                f"-n{NB_RELEVES_LUS}", BRANCHE, "--", NOM_DUMP]

AGE_MAX_H = 26          # spec : un releve lent (45 min mesurees) ne doit pas alerter
FRACTION_MIN = 0.5      # spec : moitie de la mediane des 10 precedents
MIN_HISTORIQUE = 5      # en dessous, « l'habitude » n'a pas de sens


def _executer(commande: list) -> str:
    """Seul appel a git du module. Sortie decodee en UTF-8 (lecon du
    2026-09-13 : cp1252 perdait des sorties entieres sans lever)."""
    return subprocess.run(commande, capture_output=True, check=True,
                          encoding="utf-8", errors="replace").stdout


def analyser_git_log(texte: str) -> list:
    """Transforme la sortie de `git log --format=%cI --numstat` en releves.

    Chaque commit donne une ligne de date, puis une ligne
    « ajoutees<TAB>retirees<TAB>fichier ». Un commit sans bloc --numstat
    (il ne touche pas au dump) est ignore : sans volume, il ne prouve pas
    qu'un releve a eu lieu.
    """
    releves = []
    date = None
    for ligne in texte.splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        if ligne[0].isdigit() and "\t" not in ligne:
            date = datetime.fromisoformat(ligne)
        elif "\t" in ligne and date is not None:
            ajoutees = ligne.split("\t")[0]
            if ajoutees.isdigit():
                releves.append({"date": date, "lignes": int(ajoutees)})
            date = None
    return releves


def lire_releves(executer=None, limite: int = NB_RELEVES_LUS) -> list:
    """Les `limite` derniers releves, du plus recent au plus ancien."""
    if executer is None:
        executer = _executer
    commande = list(COMMANDE_GIT)
    commande[4] = f"-n{limite}"
    return analyser_git_log(executer(commande))


def juger(releves: list, maintenant: datetime,
          age_max_h: float = AGE_MAX_H,
          fraction_min: float = FRACTION_MIN) -> list:
    """Les problemes constates, en francais, prets a etre envoyes.

    Liste vide = rien a signaler. Chaque message dit CE QUI EST CONSTATE
    et CE QU'IL FAUT VERIFIER : un constat sans suite ne sert a rien a
    quelqu'un qui recoit ca sur son telephone.
    """
    if not releves:
        return ["<b>Vigie : aucune sauvegarde trouvée</b>\n\n"
                "La branche des sauvegardes est vide ou illisible. "
                "À vérifier : que la tâche « Traqueur de vols » tourne, "
                "et que <code>git -C .sauvegardes status</code> soit sain."]

    problemes = []
    dernier = releves[0]
    age_h = (maintenant - dernier["date"]).total_seconds() / 3600
    if age_h > age_max_h:
        problemes.append(
            f"<b>Vigie : plus de relevé depuis {age_h:.0f} h</b>\n\n"
            f"Dernière sauvegarde le {dernier['date']:%d/%m à %H:%M} UTC.\n\n"
            "L'ordinateur est peut-être éteint, endormi ou sans réseau. "
            "À vérifier : la tâche « Traqueur de vols » et la connexion.")

    reference = [r["lignes"] for r in releves[1:]]
    if len(reference) >= MIN_HISTORIQUE:
        habituel = median(reference)
        if dernier["lignes"] < fraction_min * habituel:
            problemes.append(
                f"<b>Vigie : dernier relevé anormalement court</b>\n\n"
                f"{dernier['lignes']} lignes contre {habituel:.0f} d'habitude.\n\n"
                "Le réseau a probablement coupé en cours de relevé. "
                "À vérifier : les erreurs réseau dans flight_deals_log.txt.")
    return problemes


def bilan_hebdo(releves: list, maintenant: datetime):
    """Une ligne, le lundi : le signe de vie de la vigie elle-meme.

    Rend None les autres jours. Un message quotidien de bonne sante
    cesserait d'etre lu en une semaine (meme raisonnement que pour
    l'alerte de sauvegarde).
    """
    if maintenant.weekday() != 0:
        return None
    recents = [r for r in releves if (maintenant - r["date"]).days < 7]
    if not recents:
        return ("<b>Vigie : 0 relevé la semaine passée</b>\n\n"
                "À vérifier : la tâche « Traqueur de vols ».")
    habituel = median([r["lignes"] for r in recents])
    return (f"<b>Vigie : {len(recents)} relevé(s) la semaine passée</b>\n\n"
            f"Volume médian {habituel:.0f} lignes. Rien à signaler.")


def main(argv=None, releves=None, envoyer=None, maintenant=None) -> int:
    """Lit l'etat, envoie ce qu'il y a a dire, rend le code de retour.

    Un probleme constate n'est PAS un echec de la vigie : elle a fait son
    travail, elle rend 0. Elle ne rend 1 que si elle n'a pas pu parler --
    la, GitHub envoie son courriel d'echec, dernier filet.
    """
    argv = sys.argv[1:] if argv is None else argv
    maintenant = maintenant or datetime.now(timezone.utc)
    if releves is None:
        releves = lire_releves()
    if envoyer is None:
        # importe ici : hub_deals_db lit l'environnement Travelpayouts au
        # chargement, et le coeur de la vigie ne doit pas en dependre
        import hub_deals_db
        envoyer = hub_deals_db.envoyer_telegram

    messages = juger(releves, maintenant)
    if not messages:
        bilan = bilan_hebdo(releves, maintenant)
        if bilan:
            messages.append(bilan)

    for message in messages:
        print(message)
    if "--sans-envoi" in argv:
        return 0

    for message in messages:
        if not envoyer(message):
            print("ECHEC : message non envoye")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
