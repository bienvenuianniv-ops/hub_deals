# Vigie externe du relevé quotidien — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Une tâche GitHub Actions quotidienne prévient le propriétaire sur Telegram quand le relevé du portable ne tourne plus ou tourne amputé.

**Architecture:** Un nouveau module `vigie.py` à la racine. Son cœur est pur : il reçoit la sortie de `git log` sur la branche `sauvegardes` et l'heure courante, et rend une liste de problèmes en français. Seules `lire_releves()` (appel à git) et `main()` (envoi Telegram) touchent au monde extérieur. Le workflow GitHub appelle `main()`.

**Tech Stack:** Python 3 (bibliothèque standard + `requests` via `hub_deals_db`), `unittest`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-18-vigie-externe-design.md`

## Global Constraints

- **Aucun appel à l'API des prix** (Travelpayouts) : la vigie ne consomme aucun quota.
- **Aucune écriture dans le dépôt** : le workflow tourne en `permissions: contents: read`.
- **Secrets** : `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` viennent des secrets du dépôt, jamais du code ni du fichier de workflow.
- **Seuils fixés par la spec** : relevé manquant au-delà de **26 h** ; relevé maigre sous **0,5 × la médiane des 10 relevés précédents**.
- **Silence quand tout va bien**, sauf le **lundi** (bilan d'une ligne).
- **Textes destinés au user en français**, disant le constat ET quoi vérifier. Commentaires et docstrings du code sans accents, comme le reste du dépôt.
- **Les tests ne font ni réseau ni git** : le cœur est pur, `lire_releves()` reçoit sa commande par injection.
- Le dépôt utilise `unittest` (exécuté par `python -m pytest -q tests`). Les tests vivent dans `tests/`.

---

### Task 1: Lire les relevés depuis la branche de sauvegarde

**Files:**
- Create: `vigie.py`
- Test: `tests/test_vigie.py`

**Interfaces:**
- Produces:
  - `analyser_git_log(texte: str) -> list[dict]` — rend `[{"date": datetime (aware, UTC), "lignes": int}, ...]`, du plus récent au plus ancien.
  - `lire_releves(executer=None, limite: int = 11) -> list[dict]` — même forme ; `executer` est une fonction `(list[str]) -> str` qui reçoit la commande git et rend sa sortie.
  - `COMMANDE_GIT: list[str]` — la commande passée à `executer`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests de la vigie externe (spec du 2026-09-18)."""
import unittest
from datetime import datetime, timedelta, timezone

import vigie


# Sortie reelle de :
#   git log --format=%cI --numstat -n11 origin/sauvegardes -- flight_deals.sql
GIT_LOG_REEL = """2026-09-17T13:41:54+00:00

908\t2\tflight_deals.sql
2026-09-17T09:28:55+00:00

944\t2\tflight_deals.sql
2026-09-16T13:05:07+00:00

947\t2\tflight_deals.sql
"""


class TestAnalyserGitLog(unittest.TestCase):
    def test_lit_la_date_et_le_volume_de_chaque_releve(self):
        releves = vigie.analyser_git_log(GIT_LOG_REEL)

        self.assertEqual(len(releves), 3)
        self.assertEqual(releves[0]["lignes"], 908)
        self.assertEqual(
            releves[0]["date"],
            datetime(2026, 9, 17, 13, 41, 54, tzinfo=timezone.utc))
        self.assertEqual(releves[2]["lignes"], 947)

    def test_les_dates_sont_comparables_a_une_heure_utc(self):
        """Une date sans fuseau leverait des qu'on la compare a maintenant."""
        releves = vigie.analyser_git_log(GIT_LOG_REEL)

        ecart = datetime.now(timezone.utc) - releves[0]["date"]

        self.assertIsInstance(ecart, timedelta)

    def test_une_sortie_vide_ne_fait_pas_planter(self):
        """Depot neuf ou branche absente : pas de releve, pas d'exception."""
        self.assertEqual(vigie.analyser_git_log(""), [])

    def test_un_commit_sans_ligne_de_volume_est_ignore(self):
        """Un commit qui ne touche pas au dump n'a pas de bloc --numstat."""
        texte = "2026-09-17T13:41:54+00:00\n\n2026-09-16T13:05:07+00:00\n\n947\t2\tflight_deals.sql\n"

        releves = vigie.analyser_git_log(texte)

        self.assertEqual([r["lignes"] for r in releves], [947])


class TestLireReleves(unittest.TestCase):
    def test_appelle_git_et_rend_les_releves(self):
        commandes = []

        def faux_git(commande):
            commandes.append(commande)
            return GIT_LOG_REEL

        releves = vigie.lire_releves(executer=faux_git)

        self.assertEqual(len(releves), 3)
        self.assertEqual(commandes, [vigie.COMMANDE_GIT])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_vigie.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'vigie'`

- [ ] **Step 3: Write minimal implementation**

```python
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
from datetime import datetime

BRANCHE = "origin/sauvegardes"
NOM_DUMP = "flight_deals.sql"
NB_RELEVES_LUS = 11   # le dernier + les 10 qui servent de reference

COMMANDE_GIT = ["git", "log", "--format=%cI", "--numstat",
                f"-n{NB_RELEVES_LUS}", BRANCHE, "--", NOM_DUMP]


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
    """Les `limite` derniers relevés, du plus récent au plus ancien."""
    if executer is None:
        executer = _executer
    commande = list(COMMANDE_GIT)
    commande[4] = f"-n{limite}"
    return analyser_git_log(executer(commande))
```

Note : `lire_releves(executer=faux_git)` doit passer exactement `COMMANDE_GIT` quand `limite` vaut la valeur par défaut — garder `commande[4]` cohérent avec l'ordre des éléments de `COMMANDE_GIT` (index 4 = `-n11`). Si le test échoue sur l'égalité des commandes, corriger l'index, pas le test.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_vigie.py`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add vigie.py tests/test_vigie.py
git commit -m "Vigie : lecture des releves depuis la branche de sauvegarde"
```

---

### Task 2: Juger l'état — les deux règles

**Files:**
- Modify: `vigie.py`
- Test: `tests/test_vigie.py`

**Interfaces:**
- Consumes: `analyser_git_log()`, la forme `{"date": datetime, "lignes": int}`.
- Produces: `juger(releves: list, maintenant: datetime, age_max_h: float = 26, fraction_min: float = 0.5) -> list[str]` — liste de problèmes en français, vide si tout va bien.

- [ ] **Step 1: Write the failing test**

```python
class TestJuger(unittest.TestCase):
    """Les seuils viennent de la spec : 26 h (le releve de 13h peut prendre
    45 min un mauvais jour, et un cron GitHub glisse), et la moitie de la
    mediane des 10 precedents (mesure : 900 a 971 lignes par releve)."""

    def _releves(self, volumes, base=None, pas_h=24):
        base = base or datetime(2026, 9, 18, 13, 0, tzinfo=timezone.utc)
        return [{"date": base - timedelta(hours=pas_h * i), "lignes": v}
                for i, v in enumerate(volumes)]

    def test_un_releve_recent_et_normal_ne_dit_rien(self):
        maintenant = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)

        self.assertEqual(vigie.juger(self._releves([940] * 11), maintenant), [])

    def test_aucune_sauvegarde_depuis_30h_est_signalee(self):
        maintenant = datetime(2026, 9, 19, 19, 0, tzinfo=timezone.utc)

        problemes = vigie.juger(self._releves([940] * 11), maintenant)

        self.assertEqual(len(problemes), 1)
        self.assertIn("30 h", problemes[0])
        self.assertIn("Traqueur de vols", problemes[0])

    def test_25h_passe_encore(self):
        """Marge volontaire : un releve lent ne doit pas alerter."""
        maintenant = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)

        self.assertEqual(vigie.juger(self._releves([940] * 11), maintenant), [])

    def test_un_releve_ampute_est_signale(self):
        maintenant = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)

        problemes = vigie.juger(self._releves([400] + [940] * 10), maintenant)

        self.assertEqual(len(problemes), 1)
        self.assertIn("400", problemes[0])
        self.assertIn("940", problemes[0])

    def test_une_valeur_aberrante_dans_l_historique_ne_fausse_pas_le_verdict(self):
        """Mediane et non moyenne : le 2026-09-09 porte 1727 lignes (deux
        releves reunis dans un seul commit)."""
        maintenant = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)

        volumes = [900, 1727, 940, 940, 940, 940, 940, 940, 940, 940, 940]

        self.assertEqual(vigie.juger(self._releves(volumes), maintenant), [])

    def test_sans_historique_suffisant_le_volume_n_est_pas_juge(self):
        """Deux relevés ne disent pas ce qui est normal : alerter la-dessus
        serait du bruit, pas une panne."""
        maintenant = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)

        self.assertEqual(vigie.juger(self._releves([100, 940]), maintenant), [])

    def test_aucun_releve_du_tout_est_signale(self):
        """Branche vide ou git muet : c'est une panne, pas un silence sain."""
        maintenant = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)

        problemes = vigie.juger([], maintenant)

        self.assertEqual(len(problemes), 1)
        self.assertIn("aucune sauvegarde", problemes[0].lower())

    def test_les_deux_problemes_peuvent_se_cumuler(self):
        maintenant = datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)

        problemes = vigie.juger(self._releves([300] + [940] * 10), maintenant)

        self.assertEqual(len(problemes), 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_vigie.py -k Juger`
Expected: FAIL — `AttributeError: module 'vigie' has no attribute 'juger'`

- [ ] **Step 3: Write minimal implementation**

Ajouter en tête du module : `from statistics import median`, et `MIN_HISTORIQUE = 5`.

```python
AGE_MAX_H = 26          # spec : un releve lent (45 min mesurees) ne doit pas alerter
FRACTION_MIN = 0.5      # spec : moitie de la mediane des 10 precedents
MIN_HISTORIQUE = 5      # en dessous, « l'habitude » n'a pas de sens


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_vigie.py`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add vigie.py tests/test_vigie.py
git commit -m "Vigie : regles de jugement (releve manquant, releve ampute)"
```

---

### Task 3: Le bilan du lundi

**Files:**
- Modify: `vigie.py`
- Test: `tests/test_vigie.py`

**Interfaces:**
- Consumes: la forme `{"date": datetime, "lignes": int}`.
- Produces: `bilan_hebdo(releves: list, maintenant: datetime) -> str | None` — texte le lundi, `None` les autres jours.

- [ ] **Step 1: Write the failing test**

```python
class TestBilanHebdo(unittest.TestCase):
    """Sans ce signe de vie, une vigie MORTE serait indiscernable d'une
    vigie qui n'a rien a dire -- exactement l'angle mort qu'elle corrige."""

    def _releves(self, n, base=None):
        base = base or datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)
        return [{"date": base - timedelta(days=i), "lignes": 940}
                for i in range(n)]

    def test_le_lundi_un_bilan_est_produit(self):
        lundi = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(lundi.weekday(), 0)

        texte = vigie.bilan_hebdo(self._releves(7), lundi)

        self.assertIsNotNone(texte)
        self.assertIn("7", texte)

    def test_les_autres_jours_rien(self):
        mardi = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)

        self.assertIsNone(vigie.bilan_hebdo(self._releves(7), mardi))

    def test_le_bilan_ne_compte_que_les_sept_derniers_jours(self):
        lundi = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)
        vieux = [{"date": datetime(2026, 8, 1, 13, 0, tzinfo=timezone.utc),
                  "lignes": 940}]

        texte = vigie.bilan_hebdo(self._releves(3) + vieux, lundi)

        self.assertIn("3", texte)

    def test_un_lundi_sans_aucun_releve_le_dit(self):
        lundi = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)

        texte = vigie.bilan_hebdo([], lundi)

        self.assertIsNotNone(texte)
        self.assertIn("0", texte)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_vigie.py -k Bilan`
Expected: FAIL — `AttributeError: module 'vigie' has no attribute 'bilan_hebdo'`

- [ ] **Step 3: Write minimal implementation**

```python
def bilan_hebdo(releves: list, maintenant: datetime):
    """Une ligne, le lundi : le signe de vie de la vigie elle-meme.

    Rend None les autres jours. Un message quotidien de bonne sante
    cesserait d'etre lu en une semaine (meme raisonnement que pour
    l'alerte de sauvegarde).
    """
    if maintenant.weekday() != 0:
        return None
    recents = [r for r in releves
               if (maintenant - r["date"]).days < 7]
    if not recents:
        return ("<b>Vigie : 0 relevé la semaine passée</b>\n\n"
                "À vérifier : la tâche « Traqueur de vols ».")
    volumes = median([r["lignes"] for r in recents])
    return (f"<b>Vigie : {len(recents)} relevé(s) la semaine passée</b>\n\n"
            f"Volume médian {volumes:.0f} lignes. Rien à signaler.")
```

Note : `NB_RELEVES_LUS` vaut 11, donc le bilan porte au plus sur les 11 derniers relevés lus — assez pour une semaine à un ou deux relevés par jour. Ne pas augmenter la limite pour ce seul besoin.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_vigie.py`
Expected: PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
git add vigie.py tests/test_vigie.py
git commit -m "Vigie : bilan du lundi, signe de vie de la vigie"
```

---

### Task 4: `main()` — assembler et envoyer

**Files:**
- Modify: `vigie.py`
- Test: `tests/test_vigie.py`

**Interfaces:**
- Consumes: `lire_releves()`, `juger()`, `bilan_hebdo()`, `hub_deals_db.envoyer_telegram()`.
- Produces: `main(argv: list | None = None, releves=None, envoyer=None, maintenant=None) -> int` — code de retour du processus (0 = la vigie a fait son travail, même si elle a signalé une panne).

- [ ] **Step 1: Write the failing test**

```python
class TestMain(unittest.TestCase):
    """L'envoi et la lecture sont injectes : aucun test ne doit toucher
    Telegram ni git (lecon du 2026-08-16 : une fonction modifiee fait
    partir de vrais appels reseau depuis les tests d'a cote)."""

    def setUp(self):
        self.envoyes = []

    def _envoyer(self, message):
        self.envoyes.append(message)
        return True

    def _releves(self, volumes, base):
        return [{"date": base - timedelta(hours=24 * i), "lignes": v}
                for i, v in enumerate(volumes)]

    def test_rien_a_signaler_rien_envoye(self):
        mardi = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)

        code = vigie.main(argv=[], releves=self._releves([940] * 11, mardi),
                          envoyer=self._envoyer, maintenant=mardi)

        self.assertEqual(code, 0)
        self.assertEqual(self.envoyes, [])

    def test_un_probleme_part_sur_telegram(self):
        mardi = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        vieux = self._releves([940] * 11,
                              datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc))

        code = vigie.main(argv=[], releves=vieux, envoyer=self._envoyer,
                          maintenant=mardi)

        self.assertEqual(code, 0)
        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("relevé", self.envoyes[0])

    def test_sans_envoi_rien_ne_part(self):
        """Mode d'essai, pour la verification en conditions reelles."""
        mardi = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        vieux = self._releves([940] * 11,
                              datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc))

        code = vigie.main(argv=["--sans-envoi"], releves=vieux,
                          envoyer=self._envoyer, maintenant=mardi)

        self.assertEqual(code, 0)
        self.assertEqual(self.envoyes, [])

    def test_le_bilan_du_lundi_part_aussi(self):
        lundi = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)

        vigie.main(argv=[], releves=self._releves([940] * 11, lundi),
                   envoyer=self._envoyer, maintenant=lundi)

        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("semaine", self.envoyes[0])

    def test_un_echec_d_envoi_fait_echouer_la_tache(self):
        """Sinon la panne serait doublement silencieuse. Un code non nul
        declenche le courriel d'echec de GitHub."""
        mardi = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
        vieux = self._releves([940] * 11,
                              datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc))

        code = vigie.main(argv=[], releves=vieux, envoyer=lambda m: False,
                          maintenant=mardi)

        self.assertEqual(code, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_vigie.py -k Main`
Expected: FAIL — `AttributeError: module 'vigie' has no attribute 'main'`

- [ ] **Step 3: Write minimal implementation**

```python
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
        import hub_deals_db
        envoyer = hub_deals_db.envoyer_telegram

    messages = juger(releves, maintenant)
    bilan = bilan_hebdo(releves, maintenant) if not messages else None
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
```

Ajouter `import sys` et `from datetime import datetime, timezone` en tête du module (`timezone` est nécessaire à `main()`).

Choix à respecter : le bilan du lundi n'est PAS envoyé quand un problème est déjà signalé — un « tout va bien » collé à une alerte serait contradictoire.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests`
Expected: PASS — toute la suite du dépôt, aucun test ne doit ralentir (une suite qui s'allonge trahit un appel réseau, leçon du 2026-08-16).

- [ ] **Step 5: Commit**

```bash
git add vigie.py tests/test_vigie.py
git commit -m "Vigie : assemblage, envoi Telegram et mode --sans-envoi"
```

---

### Task 5: Le workflow GitHub et la vérification réelle

**Files:**
- Create: `.github/workflows/vigie.yml`
- Modify: `README.md` (section décrivant l'exploitation), `CHANGELOG.md`

**Interfaces:**
- Consumes: `vigie.main()` par `python vigie.py`.
- Produces: rien pour le code ; une tâche planifiée côté GitHub.

- [ ] **Step 1: Écrire le workflow**

```yaml
name: Vigie du releve quotidien

on:
  schedule:
    - cron: "0 15 * * *"   # 15h UTC ; le releve du portable tourne a 13h UTC
  workflow_dispatch:        # declenchement a la main, pour verifier

permissions:
  contents: read

jobs:
  vigie:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0    # il faut l'historique, pas seulement la pointe

      - name: Recuperer la branche des sauvegardes
        run: git fetch origin sauvegardes:refs/remotes/origin/sauvegardes

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Installer les dependances
        run: pip install -r requirements.txt

      - name: Verifier l'etat du releve
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python vigie.py
```

- [ ] **Step 2: Poser les secrets du dépôt**

À faire par le user (ou lui donner les instructions exactes) : `Settings > Secrets and variables > Actions > New repository secret`, deux secrets nommés `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID`, avec les valeurs déjà présentes dans `HKCU\Environment` sur le portable.

**Ne jamais afficher ces valeurs dans une session d'outil** (leçon du 2026-08-16 : un jeton affiché en clair a dû être régénéré). Le user les copie depuis sa propre fenêtre.

- [ ] **Step 3: Prouver le jugement en local, sans rien envoyer**

```bash
python vigie.py --sans-envoi
```

Attendu : sur l'état sain du jour, aucun message imprimé. Vérifier ensuite un cas de panne avec un historique tronqué :

```bash
python -c "import vigie, datetime as d; print(vigie.juger(vigie.lire_releves()[:1], d.datetime.now(d.timezone.utc) + d.timedelta(days=2)))"
```

Attendu : un message « plus de relevé depuis … h ».

- [ ] **Step 4: Déclencher le workflow à la main et vérifier**

Après le push : onglet `Actions` > `Vigie du releve quotidien` > `Run workflow`.

À vérifier, dans cet ordre :
1. la tâche finit en succès ;
2. sur un état sain, **aucun message Telegram** n'arrive ;
3. les traces d'exécution ne contiennent **ni jeton ni identifiant de conversation** ;
4. le lendemain, la tâche s'est déclenchée seule (le cron GitHub peut glisser de plusieurs dizaines de minutes).

Pour prouver l'envoi sans attendre une vraie panne, lancer une fois en local avec les vraies variables d'environnement et un `maintenant` décalé :

```bash
python -c "import vigie, datetime as d; vigie.main(argv=[], maintenant=d.datetime.now(d.timezone.utc) + d.timedelta(days=2))"
```

Attendu : un message reçu sur Telegram, disant « plus de relevé depuis … h ».

- [ ] **Step 5: Documenter et commiter**

Ajouter au `README.md`, dans la partie exploitation : ce que la vigie surveille, quand elle parle, et le fait que son silence est normal. Ajouter une entrée `CHANGELOG.md` datée du jour, section « Ajouté ».

```bash
git add .github/workflows/vigie.yml README.md CHANGELOG.md
git commit -m "Vigie : tache GitHub Actions quotidienne"
```

---

## Notes d'exécution

- **Le dépôt est privé** : les minutes GitHub Actions sont limitées mais une tâche quotidienne de moins d'une minute reste très loin du plafond gratuit.
- **`hub_deals_db` est importé dans `main()`, pas en tête de module** : l'import est lourd (il lit l'environnement Travelpayouts) et les tests du cœur ne doivent pas en dépendre.
- **`hub_deals_db.log()` écrit `flight_deals_log.txt` dans le dossier courant.** Sur GitHub c'est un fichier jetable, détruit avec la machine : sans effet, mais à savoir en lisant les traces.
- **Ne pas faire écrire la vigie dans le dépôt** (pas de journal commité) : les droits sont en lecture seule, et un commit de la vigie polluerait l'historique que le relevé utilise comme source de vérité.
