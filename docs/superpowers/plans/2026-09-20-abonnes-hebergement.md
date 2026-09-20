# Hébergement de l'écoute et abonnés hors du portable — plan d'implémentation

> **Pour les agents :** SOUS-COMPÉTENCE REQUISE : utiliser superpowers:subagent-driven-development (recommandé) ou superpowers:executing-plans pour exécuter ce plan tâche par tâche. Les étapes utilisent des cases à cocher (`- [ ]`).

**But :** une inscription Telegram qui aboutit en moins d'une minute à toute heure, et des abonnés stockés hors du portable dans une base sauvegardée par son hébergeur.

**Architecture :** l'écoute passe du long polling sur le portable à un webhook servi par un web service Render gratuit. Les tables `abonnes` et `etat_bot` quittent SQLite pour Neon Postgres, que le service et le relevé lisent tous deux. `abonnes.py` et `traiter_update()` ne changent pas : un adaptateur traduit les placeholders.

**Pile :** Python 3.14, psycopg 3, Flask + gunicorn, Neon Postgres, Render, GitHub Actions.

**Spec :** `docs/superpowers/specs/2026-09-20-abonnes-hebergement-design.md`

## Contraintes globales

- **Python 3.14** en local et en CI ; runner `windows-latest` pour la suite existante (des tests appellent `tasklist` et `pythonw.exe`), `ubuntu-latest` pour le seul job Postgres.
- **Le français sans accents dans le code** (docstrings, commentaires, journaux) ; les accents sont réservés aux textes envoyés à l'utilisateur.
- **Aucun test ne touche le réseau ni la vraie base** : tout est injecté (`poster`, `executer`, `envoyer`, `appeler_fn`).
- **Aucune exception avalée dans une sonde** : une erreur se voit ou lève. « Pas de données » et « requête fausse » ne doivent jamais se ressembler.
- **Jamais dans un journal** : le texte reçu d'un utilisateur, le code d'invitation, un jeton.
- `chat_id` est un **BIGINT** côté Postgres, jamais INTEGER.
- Variables d'environnement introduites : `HUB_DEALS_ABONNES_URL` (chaîne Neon), `TELEGRAM_WEBHOOK_SECRET`, `HUB_DEALS_TEST_PG_URL` (tests seulement).
- Après chaque tâche : `python -m pytest tests -q` doit être vert avant de committer.

---

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `magasin.py` *(créé)* | ouvrir une connexion abonnés (SQLite ou Postgres), créer les tables, traduire les placeholders |
| `web_bot.py` *(créé)* | service webhook Flask : vérifier le secret, appeler `traiter_update()`, exécuter les actions |
| `migrer_abonnes.py` *(créé)* | reprise idempotente des abonnés SQLite → Postgres |
| `render.yaml` *(créé)* | déclaration du service Render |
| `requirements-web.txt` *(créé)* | Flask + gunicorn, pour le service seul |
| `requirements.txt` *(modifié)* | ajout de `psycopg[binary]` |
| `abonnes.py` | **inchangé** — sauf retrait de `ecoute_muette` et `SEUIL_ECOUTE_MUETTE_S` (tâche 7) |
| `hub_deals_db.py` *(modifié)* | lit les abonnés via `magasin.ouvrir()` ; perd `verifier_ecoute_et_alerter` |
| `bot_ecoute.py` *(modifié)* | garde `traiter_update()` et `_envoi()` ; perd `boucle()`, `empecher_la_veille()` et son `__main__` |
| `vigie.py` *(modifié)* | nouvelle sonde `juger_webhook()` |
| `.github/workflows/tests.yml` *(modifié)* | second job `postgres` sur Linux |

---

### Tâche 1 : `magasin.py` — ouvrir une connexion abonnés

**Fichiers :**
- Créer : `magasin.py`
- Test : `tests/test_magasin.py`

**Interfaces :**
- Consomme : rien.
- Produit : `magasin.ouvrir(url=None, chemin="flight_deals.db")` → connexion (SQLite ou adaptateur Postgres) avec tables créées ; `magasin.traduire(sql: str) -> str` ; `magasin.URL_ENV = "HUB_DEALS_ABONNES_URL"`.

- [ ] **Étape 1 : écrire les tests de traduction**

```python
# tests/test_magasin.py
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import magasin


class TestTraduction(unittest.TestCase):
    """psycopg3 exige %s et ne connait pas ?. abonnes.py ecrit en ?, il
    n'est pas modifie : c'est la couche qui traduit."""

    def test_remplace_les_placeholders(self):
        self.assertEqual(
            magasin.traduire("SELECT x FROM t WHERE a = ? AND b = ?"),
            "SELECT x FROM t WHERE a = %s AND b = %s")

    def test_ne_touche_pas_a_un_point_d_interrogation_entre_quotes(self):
        """Aucune requete actuelle n'en contient -- ce qui rend le piege
        d'autant plus facile a introduire plus tard sans s'en apercevoir."""
        self.assertEqual(
            magasin.traduire("UPDATE t SET texte = 'ou ca ?' WHERE a = ?"),
            "UPDATE t SET texte = 'ou ca ?' WHERE a = %s")

    def test_ne_touche_pas_a_un_pourcent_existant(self):
        """Un % litteral doit etre double pour psycopg, sinon il le prend
        pour un placeholder."""
        self.assertEqual(
            magasin.traduire("SELECT x FROM t WHERE nom LIKE 'a%' AND b = ?"),
            "SELECT x FROM t WHERE nom LIKE 'a%%' AND b = %s")
```

- [ ] **Étape 2 : lancer les tests et vérifier qu'ils échouent**

Lancer : `python -m pytest tests/test_magasin.py -q`
Attendu : ÉCHEC, `ModuleNotFoundError: No module named 'magasin'`

- [ ] **Étape 3 : écrire `traduire`**

```python
# magasin.py
"""
Ouvre la connexion aux tables d'abonnes, en SQLite ou en Postgres.

Les abonnes ont quitte flight_deals.db le 2026-09-20 : le releve tourne
sur le portable, l'ecoute sur Render, et les deux doivent voir les memes
inscriptions. Voir docs/superpowers/specs/2026-09-20-abonnes-hebergement-design.md

abonnes.py n'a PAS ete modifie : ses requetes restent ecrites avec des
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
```

- [ ] **Étape 4 : lancer les tests et vérifier qu'ils passent**

Lancer : `python -m pytest tests/test_magasin.py -q`
Attendu : 3 passed

- [ ] **Étape 5 : écrire les tests d'ouverture SQLite**

```python
class TestOuvertureSQLite(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dossier = tempfile.TemporaryDirectory()
        self.chemin = os.path.join(self.dossier.name, "essai.db")

    def tearDown(self):
        self.dossier.cleanup()

    def test_cree_les_tables(self):
        conn = magasin.ouvrir(chemin=self.chemin)

        tables = {l[0] for l in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}

        self.assertIn("abonnes", tables)
        self.assertIn("etat_bot", tables)

    def test_est_idempotent(self):
        magasin.ouvrir(chemin=self.chemin)
        conn = magasin.ouvrir(chemin=self.chemin)

        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM abonnes").fetchone()[0], 0)

    def test_accepte_les_requetes_d_abonnes(self):
        """Le contrat qui compte : abonnes.py doit fonctionner tel quel."""
        import abonnes
        conn = magasin.ouvrir(chemin=self.chemin)

        abonnes.inscrire(conn, 862000001, "Awa", abonnes.maintenant())

        self.assertEqual(abonnes.nb_actifs(conn), 1)
```

- [ ] **Étape 6 : lancer et vérifier l'échec**

Lancer : `python -m pytest tests/test_magasin.py -q`
Attendu : ÉCHEC, `module 'magasin' has no attribute 'ouvrir'`

- [ ] **Étape 7 : écrire `ouvrir` et le DDL**

```python
# magasin.py (suite)

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

    `url` absente : SQLite dans `chemin` (tests, et secours si Neon tombe
    pendant une mise au point locale). `url` presente : Postgres.
    L'environnement (HUB_DEALS_ABONNES_URL) sert de valeur par defaut.
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
```

- [ ] **Étape 8 : lancer la suite complète**

Lancer : `python -m pytest tests -q`
Attendu : tout vert, 6 tests de plus qu'avant

- [ ] **Étape 9 : committer**

```bash
git add magasin.py tests/test_magasin.py
git commit -m "Magasin : ouvrir les abonnes en SQLite ou en Postgres

abonnes.py n'est pas modifie : ses requetes gardent les placeholders
« ? », traduits ici pour psycopg. Un « ? » entre quotes n'est pas
traduit, et les pourcents litteraux sont doubles -- sans quoi psycopg
les prendrait pour des placeholders.

chat_id est un BIGINT en Postgres : INTEGER plafonne a 2 147 483 647,
or Telegram attribue des identifiants au-dela.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Tâche 2 : les mêmes tests contre un vrai Postgres, en CI

**Fichiers :**
- Modifier : `tests/test_magasin.py`
- Modifier : `.github/workflows/tests.yml`

**Interfaces :**
- Consomme : `magasin.ouvrir(url=...)` (tâche 1).
- Produit : la classe de tests `TestOuverturePostgres`, réutilisable ; la variable `HUB_DEALS_TEST_PG_URL`.

- [ ] **Étape 1 : écrire le test gardien et les tests Postgres**

```python
class TestGardeCI(unittest.TestCase):
    """Le piege que cette tache existe pour fermer.

    Les tests Postgres se sautent faute de base -- commode en local. Mais
    si le job CI perdait sa variable de connexion, ils se sauteraient
    AUSSI, et la suite passerait au vert sans avoir rien verifie du
    dialecte qui tourne en production. Meme famille que la vigie qui
    rendait [] sur un dump illisible.
    """

    def test_en_ci_la_base_postgres_est_obligatoire(self):
        if not os.environ.get("CI"):
            self.skipTest("hors CI : la base Postgres est facultative")
        self.assertTrue(
            os.environ.get("HUB_DEALS_TEST_PG_URL"),
            "HUB_DEALS_TEST_PG_URL manque : les tests Postgres se seraient "
            "sautes en silence")


@unittest.skipUnless(os.environ.get("HUB_DEALS_TEST_PG_URL"),
                     "HUB_DEALS_TEST_PG_URL absente")
class TestOuverturePostgres(unittest.TestCase):
    """Les MEMES operations que TestOuvertureSQLite, contre le moteur qui
    tourne reellement en production."""

    def setUp(self):
        self.url = os.environ["HUB_DEALS_TEST_PG_URL"]
        self.conn = magasin.ouvrir(url=self.url)
        self.conn.execute("DELETE FROM abonnes")
        self.conn.execute("DELETE FROM etat_bot")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_cree_les_tables(self):
        nb = self.conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name IN ('abonnes', 'etat_bot')").fetchone()[0]

        self.assertEqual(nb, 2)

    def test_un_chat_id_au_dela_de_deux_milliards_passe(self):
        """INTEGER aurait leve ici, et seulement ici : le compte test du
        proprietaire (8,6e8) ne revele pas le defaut."""
        import abonnes
        grand = 7_000_000_000

        abonnes.inscrire(self.conn, grand, "Grand", abonnes.maintenant())

        self.assertIsNotNone(abonnes.trouver(self.conn, grand))

    def test_le_cycle_complet_d_un_abonne(self):
        import abonnes
        quand = abonnes.maintenant()

        abonnes.inscrire(self.conn, 862000001, "Awa", quand)
        abonnes.choisir_ville(self.conn, 862000001, "Paris", quand)
        servis = abonnes.abonnes_a_servir(self.conn)
        abonnes.desactiver(self.conn, 862000001, "stop", quand)

        self.assertEqual([a["ville_depart"] for a in servis], ["Paris"])
        self.assertEqual(abonnes.nb_actifs(self.conn), 0)

    def test_la_reinscription_conserve_la_ville(self):
        """ON CONFLICT DO UPDATE : meme syntaxe dans les deux moteurs, mais
        c'est ici qu'on le prouve."""
        import abonnes
        quand = abonnes.maintenant()
        abonnes.inscrire(self.conn, 862000001, "Awa", quand)
        abonnes.choisir_ville(self.conn, 862000001, "Paris", quand)
        abonnes.desactiver(self.conn, 862000001, "stop", quand)

        abonnes.inscrire(self.conn, 862000001, "Awa", quand)

        abonne = abonnes.trouver(self.conn, 862000001)
        self.assertEqual(abonne["ville_depart"], "Paris")
        self.assertTrue(abonne["actif"])

    def test_le_temoin_d_etat_s_ecrase(self):
        import abonnes
        abonnes.noter_ecoute(self.conn, "2026-09-20T10:00:00+00:00")
        abonnes.noter_ecoute(self.conn, "2026-09-20T11:00:00+00:00")

        valeur = self.conn.execute(
            "SELECT valeur FROM etat_bot WHERE cle = 'derniere_ecoute'").fetchone()[0]

        self.assertEqual(valeur, "2026-09-20T11:00:00+00:00")
```

- [ ] **Étape 2 : lancer en local et vérifier le saut**

Lancer : `python -m pytest tests/test_magasin.py -q -rs`
Attendu : les tests `TestOuverturePostgres` sont **sautés** avec « HUB_DEALS_TEST_PG_URL absente », `TestGardeCI` sauté aussi (hors CI). Aucun échec.

- [ ] **Étape 3 : vérifier que le gardien mord**

Lancer : `CI=true python -m pytest tests/test_magasin.py::TestGardeCI -q`
Attendu : ÉCHEC, « HUB_DEALS_TEST_PG_URL manque : les tests Postgres se seraient sautes en silence »

C'est le contrôle positif du gardien : sans cette exécution, on ne saurait pas s'il protège quoi que ce soit.

- [ ] **Étape 4 : ajouter le job Postgres au workflow**

```yaml
# .github/workflows/tests.yml — a ajouter sous le job « tests »
  postgres:
    # Linux et non Windows : les services de conteneurs n'existent pas sur
    # les runners Windows. Ce job ne lance QUE les tests de persistance ;
    # la suite complete reste sur Windows, ou vivent tasklist et pythonw.
    runs-on: ubuntu-latest
    timeout-minutes: 10
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: essai
          POSTGRES_DB: hub_deals_test
        options: >-
          --health-cmd pg_isready --health-interval 5s
          --health-timeout 5s --health-retries 10
        ports:
          - 5432:5432
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-python@v7
        with:
          python-version: "3.14"

      - name: Installer les dependances
        run: pip install -r requirements-dev.txt

      - name: Lancer les tests de persistance
        env:
          HUB_DEALS_TEST_PG_URL: postgresql://postgres:essai@localhost:5432/hub_deals_test
        # seulement ce qui touche a la persistance : lancer toute la suite
        # ici ne prouverait rien de plus sur le dialecte, et masquerait
        # l'echec du job Windows derriere un second vert.
        run: python -m pytest tests/test_magasin.py tests/test_migrer_abonnes.py -q -rs
```

- [ ] **Étape 5 : ajouter psycopg aux dépendances**

```
# requirements.txt
requests>=2.34,<3
psycopg[binary]>=3.2,<4
```

- [ ] **Étape 6 : lancer la suite complète**

Lancer : `python -m pytest tests -q`
Attendu : tout vert

- [ ] **Étape 7 : committer et vérifier le CI**

```bash
git add tests/test_magasin.py .github/workflows/tests.yml requirements.txt
git commit -m "CI : jouer les tests de persistance contre un vrai Postgres

Les memes operations, deux moteurs : SQLite dans le job Windows,
Postgres dans un job Linux (les services de conteneurs n'existent pas
sur les runners Windows).

Un test gardien echoue si la variable de connexion manque alors que CI
est defini : sans lui, un job qui perdrait sa base sauterait les tests
en silence et la suite passerait au vert sans avoir rien verifie du
dialecte de production.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push origin master
```

Puis : `gh run watch <id> --exit-status` et lire la sortie du job `postgres`.
Attendu : **aucun test sauté** dans ce job (`-rs` l'afficherait), et `test_un_chat_id_au_dela_de_deux_milliards_passe` vert.

---

### Tâche 3 : migration des abonnés vers Postgres

**Fichiers :**
- Créer : `migrer_abonnes.py`
- Test : `tests/test_migrer_abonnes.py`

**Interfaces :**
- Consomme : `magasin.ouvrir` (tâche 1).
- Produit : `migrer_abonnes.migrer(source, cible) -> dict` avec les clés `abonnes` et `etat_bot` (nombre de lignes **reprises**, pas lues).

- [ ] **Étape 1 : écrire les tests**

```python
# tests/test_migrer_abonnes.py
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import magasin
import migrer_abonnes


class TestMigration(unittest.TestCase):
    """Cible en SQLite : la migration ne doit rien connaitre du moteur.
    Le job Postgres rejoue la meme classe avec une cible Postgres."""

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.source = magasin.ouvrir(
            chemin=os.path.join(self.dossier.name, "source.db"))
        self.cible = magasin.ouvrir(
            chemin=os.path.join(self.dossier.name, "cible.db"))
        quand = abonnes.maintenant()
        abonnes.inscrire(self.source, 862000001, "Awa", quand)
        abonnes.choisir_ville(self.source, 862000001, "Paris", quand)
        abonnes.noter_ecoute(self.source, quand)

    def tearDown(self):
        self.dossier.cleanup()

    def test_reprend_les_abonnes_avec_leur_ville(self):
        compte = migrer_abonnes.migrer(self.source, self.cible)

        abonne = abonnes.trouver(self.cible, 862000001)
        self.assertEqual(compte["abonnes"], 1)
        self.assertEqual(abonne["ville_depart"], "Paris")
        self.assertTrue(abonne["actif"])

    def test_rejouee_ne_reprend_rien(self):
        """Une migration qu'on n'a pas rejouee n'est pas idempotente,
        c'est une supposition."""
        migrer_abonnes.migrer(self.source, self.cible)

        compte = migrer_abonnes.migrer(self.source, self.cible)

        self.assertEqual(compte["abonnes"], 0)
        self.assertEqual(abonnes.nb_actifs(self.cible), 1)

    def test_ne_touche_pas_a_un_abonne_deja_present(self):
        """La cible fait foi : si quelqu'un s'est inscrit par le webhook
        pendant la bascule, la migration ne doit pas l'ecraser avec une
        vieille ligne."""
        quand = abonnes.maintenant()
        abonnes.inscrire(self.cible, 862000001, "Awa", quand)
        abonnes.choisir_ville(self.cible, 862000001, "Dakar", quand)

        migrer_abonnes.migrer(self.source, self.cible)

        self.assertEqual(
            abonnes.trouver(self.cible, 862000001)["ville_depart"], "Dakar")

    def test_reprend_le_temoin_d_etat(self):
        compte = migrer_abonnes.migrer(self.source, self.cible)

        self.assertEqual(compte["etat_bot"], 1)

    def test_reprend_toutes_les_colonnes_d_abonnes(self):
        """migrer_abonnes reecrit la liste des colonnes. Si abonnes.py en
        gagne une sans qu'elle soit ajoutee ici, la migration l'oublierait
        en silence."""
        self.assertEqual(migrer_abonnes._COLONNES, abonnes._COLONNES)
```

- [ ] **Étape 2 : lancer et vérifier l'échec**

Lancer : `python -m pytest tests/test_migrer_abonnes.py -q`
Attendu : ÉCHEC, `ModuleNotFoundError: No module named 'migrer_abonnes'`

- [ ] **Étape 3 : écrire la migration**

```python
# migrer_abonnes.py
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
        reprises["abonnes"] += curseur.rowcount if curseur.rowcount > 0 else 0

    for cle, valeur in source.execute("SELECT cle, valeur FROM etat_bot").fetchall():
        curseur = cible.execute(
            """INSERT INTO etat_bot (cle, valeur) VALUES (?, ?)
               ON CONFLICT (cle) DO NOTHING""", (cle, valeur))
        reprises["etat_bot"] += curseur.rowcount if curseur.rowcount > 0 else 0

    cible.commit()
    return reprises


def main(argv=None) -> int:
    analyseur = argparse.ArgumentParser(description="Reprise des abonnes")
    analyseur.add_argument("--a-blanc", action="store_true",
                           help="montrer ce qui serait repris, sans ecrire")
    options = analyseur.parse_args(sys.argv[1:] if argv is None else argv)

    source = magasin.ouvrir(url=None)          # SQLite locale
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
```

- [ ] **Étape 4 : lancer les tests**

Lancer : `python -m pytest tests/test_migrer_abonnes.py -q`
Attendu : 4 passed

- [ ] **Étape 5 : committer**

```bash
git add migrer_abonnes.py tests/test_migrer_abonnes.py
git commit -m "Migration : reprendre les abonnes vers la base distante

Idempotente et rejouee par un test : la seconde execution reprend 0
ligne. La cible fait foi -- une inscription arrivee par le webhook
pendant la bascule n'est pas ecrasee par la vieille copie locale.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Tâche 4 : le service webhook

**Fichiers :**
- Créer : `web_bot.py`, `requirements-web.txt`, `render.yaml`
- Test : `tests/test_web_bot.py`

**Interfaces :**
- Consomme : `magasin.ouvrir()` (tâche 1), `bot_ecoute.traiter_update(conn, update, code, quand)` et `bot_ecoute.executer_actions(token, actions, appeler_fn)` (existants, inchangés).
- Produit : `web_bot.app` (application Flask), `web_bot.creer_app(ouvrir=..., appeler=...)` pour les tests.

- [ ] **Étape 1 : écrire les tests**

```python
# tests/test_web_bot.py
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import magasin
import web_bot

SECRET = "secret_de_test_0123456789"
CODE = "code_invitation_essai"


def _update(texte, chat_id=862000001):
    return {"update_id": 1, "message": {
        "message_id": 7, "text": texte,
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": chat_id, "first_name": "Awa"}}}


class TestWebhook(unittest.TestCase):
    """Aucun appel reseau : l'appelant Telegram est injecte."""

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.chemin = os.path.join(self.dossier.name, "essai.db")
        self.appels = []

        def appeler(token, methode, params):
            self.appels.append((methode, params))
            return 200, {"ok": True}

        self.app = web_bot.creer_app(
            ouvrir=lambda: magasin.ouvrir(chemin=self.chemin),
            appeler=appeler, token="jeton", code=CODE, secret=SECRET)
        self.client = self.app.test_client()

    def tearDown(self):
        self.dossier.cleanup()

    def _poster(self, update, secret=SECRET):
        entetes = {}
        if secret is not None:
            entetes["X-Telegram-Bot-Api-Secret-Token"] = secret
        return self.client.post("/telegram", json=update, headers=entetes)

    def test_sans_secret_rien_n_est_traite(self):
        reponse = self._poster(_update(f"/start {CODE}"), secret=None)

        self.assertEqual(reponse.status_code, 403)
        self.assertEqual(self.appels, [])

    def test_avec_un_mauvais_secret_rien_n_est_traite(self):
        reponse = self._poster(_update(f"/start {CODE}"), secret="faux")

        self.assertEqual(reponse.status_code, 403)
        self.assertEqual(self.appels, [])

    def test_un_start_valide_inscrit_et_repond(self):
        reponse = self._poster(_update(f"/start {CODE}"))

        conn = magasin.ouvrir(chemin=self.chemin)
        self.assertEqual(reponse.status_code, 200)
        self.assertIsNotNone(abonnes.trouver(conn, 862000001))
        self.assertEqual(self.appels[0][0], "sendMessage")

    def test_le_meme_update_rejoue_ne_cree_qu_un_abonne(self):
        """Render s'endort : Telegram peut reessayer pendant le reveil et
        livrer deux fois le meme update."""
        self._poster(_update(f"/start {CODE}"))
        self._poster(_update(f"/start {CODE}"))

        conn = magasin.ouvrir(chemin=self.chemin)
        self.assertEqual(abonnes.nb_actifs(conn), 1)

    def test_un_update_sans_message_repond_200_sans_rien_faire(self):
        reponse = self._poster({"update_id": 2})

        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(self.appels, [])

    def test_si_la_base_est_injoignable_la_reponse_est_500(self):
        """Repondre 200 sur un message non traite, c'est perdre une
        inscription en silence : Telegram ne reessaierait jamais."""
        def ouvrir_casse():
            raise RuntimeError("connexion refusee")

        app = web_bot.creer_app(ouvrir=ouvrir_casse, appeler=lambda *a: None,
                                token="jeton", code=CODE, secret=SECRET)

        reponse = app.test_client().post(
            "/telegram", json=_update("/start"),
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})

        self.assertEqual(reponse.status_code, 500)

    def test_la_page_de_sante_repond_sans_secret(self):
        """Render pingue le service ; ce chemin ne doit rien exposer."""
        reponse = self.client.get("/sante")

        self.assertEqual(reponse.status_code, 200)
        self.assertNotIn(CODE, reponse.get_data(as_text=True))
```

- [ ] **Étape 2 : lancer et vérifier l'échec**

Lancer : `python -m pytest tests/test_web_bot.py -q`
Attendu : ÉCHEC, `ModuleNotFoundError: No module named 'web_bot'` (installer d'abord Flask : `pip install -r requirements-web.txt`)

- [ ] **Étape 3 : écrire `requirements-web.txt`**

```
# Dependances du SEUL service webhook. Le releve ne doit pas dependre
# d'un serveur web pour tourner : il garde requirements.txt.
-r requirements.txt
flask>=3,<4
gunicorn>=23,<24
```

- [ ] **Étape 4 : écrire le service**

```python
# web_bot.py
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


app = creer_app() if os.environ.get("HUB_DEALS_ABONNES_URL") else None

if __name__ == "__main__":
    if app is None:
        sys.exit("ARRET : HUB_DEALS_ABONNES_URL absente de l'environnement")
    app.run(port=int(os.environ.get("PORT", 5000)))
```

- [ ] **Étape 5 : lancer les tests**

Lancer : `python -m pytest tests/test_web_bot.py -q`
Attendu : 7 passed

- [ ] **Étape 6 : écrire `render.yaml`**

```yaml
# render.yaml
services:
  - type: web
    name: hub-deals-bot
    runtime: python
    plan: free          # s'endort apres 15 min ; Telegram reessaie au reveil
    buildCommand: pip install -r requirements-web.txt
    startCommand: gunicorn web_bot:app --bind 0.0.0.0:$PORT --workers 1 --timeout 30
    healthCheckPath: /sante
    envVars:
      - key: HUB_DEALS_ABONNES_URL
        sync: false     # renseignee a la main dans le tableau de bord
      - key: TELEGRAM_BOT_TOKEN
        sync: false
      - key: HUB_DEALS_CODE_INVITATION
        sync: false
      - key: TELEGRAM_WEBHOOK_SECRET
        sync: false
```

- [ ] **Étape 7 : lancer la suite complète et committer**

Lancer : `python -m pytest tests -q`

```bash
git add web_bot.py requirements-web.txt render.yaml tests/test_web_bot.py
git commit -m "Webhook : servir l'ecoute Telegram hors du portable

Le long polling ne repondait que si le portable etait allume. Le
service recoit desormais les updates par webhook et appelle
traiter_update() telle quelle -- pure et idempotente, elle absorbe le
doublon que Telegram peut livrer pendant le reveil du service.

Une requete sans le secret repart en 403 sans toucher a la base. Une
base injoignable rend 500 et non 200 : Telegram reessaiera, alors
qu'un 200 perdrait l'inscription en silence.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Tâche 5 : le relevé lit les abonnés dans la base distante

**Fichiers :**
- Modifier : `hub_deals_db.py` (`notifier_abonnes_sans_risque`, suppression de `verifier_ecoute_et_alerter`)
- Modifier : `abonnes.py` (suppression de `ecoute_muette` et `SEUIL_ECOUTE_MUETTE_S`)
- Modifier : `tests/test_abonnes.py`, `tests/test_envoi_abonne.py`

**Interfaces :**
- Consomme : `magasin.ouvrir()` (tâche 1).
- Produit : `notifier_abonnes_sans_risque(groupes)` — **la signature perd `conn`** : la connexion aux abonnés n'est plus celle du relevé.

- [ ] **Étape 1 : écrire le test de bascule**

```python
# tests/test_envoi_abonne.py — a ajouter
class TestSourceDesAbonnes(unittest.TestCase):
    def test_les_abonnes_viennent_du_magasin_pas_de_la_base_des_offres(self):
        """Le releve tourne sur le portable, l'ecoute sur Render : les
        abonnes ne sont plus dans flight_deals.db."""
        appels = []
        conn_abonnes = magasin.ouvrir(chemin=self.chemin_abonnes)
        abonnes.inscrire(conn_abonnes, 862000001, "Awa", abonnes.maintenant())
        abonnes.choisir_ville(conn_abonnes, 862000001, "Dakar",
                              abonnes.maintenant())

        # patcher magasin.ouvrir et NON hub_deals_db.magasin.ouvrir :
        # l'import est fait DANS la fonction (pour qu'un module casse ne
        # fasse jamais echouer le releve), donc hub_deals_db n'a pas
        # d'attribut « magasin » a patcher.
        with mock.patch.object(magasin, "ouvrir",
                               lambda: magasin_sqlite(self.chemin_abonnes)):
            hub_deals_db.envoyer_telegram_a = lambda cid, msg, journaliser=False: (
                appels.append(cid) or True)
            hub_deals_db.notifier_abonnes_sans_risque(self.groupes)

        self.assertEqual(appels, [862000001])

    def test_une_base_injoignable_n_interrompt_pas_le_releve(self):
        """Contrat inchange : ne leve jamais. Le proprietaire a deja recu
        son message, le releve doit finir."""
        def ouvrir_casse():
            raise RuntimeError("connexion refusee")

        with mock.patch.object(magasin, "ouvrir", ouvrir_casse):
            hub_deals_db.notifier_abonnes_sans_risque(self.groupes)   # ne leve pas
```

- [ ] **Étape 2 : lancer et vérifier l'échec**

Lancer : `python -m pytest tests/test_envoi_abonne.py -q`
Attendu : ÉCHEC — `notifier_abonnes_sans_risque` prend encore `conn`

- [ ] **Étape 3 : modifier `hub_deals_db.py`**

```python
def notifier_abonnes_sans_risque(groupes: list) -> None:
    """Envoie aux abonnes du bot les affaires de leur ville.

    Les abonnes vivent desormais hors de flight_deals.db : le releve
    tourne sur le portable, l'ecoute sur Render, et les deux voient la
    meme base distante.

    Ne leve jamais : le proprietaire a deja recu son message, et un
    releve complet ne doit pas echouer parce que la base des abonnes est
    injoignable.
    """
    conn = None
    try:
        import abonnes
        import magasin
        conn = magasin.ouvrir()
        abonnes.notifier_abonnes(
            conn, groupes,
            envoyer=lambda chat_id, message: envoyer_telegram_a(
                chat_id, message, journaliser=False),
            log=log,
            exclure_chat_id=TELEGRAM_CHAT_ID,
        )
    except Exception as e:
        log(f"   -> envoi aux abonnes impossible : {e}")
    finally:
        if conn is not None:
            conn.close()
```

Supprimer entièrement `verifier_ecoute_et_alerter` et son appel (la sonde passe dans la vigie, tâche 6). Supprimer `ecoute_muette` et `SEUIL_ECOUTE_MUETTE_S` d'`abonnes.py`, ainsi que leurs tests dans `tests/test_abonnes.py`.

- [ ] **Étape 4 : ajouter l'import et corriger l'appelant**

Dans `hub_deals_db.py`, remplacer l'appel `notifier_abonnes_sans_risque(conn, groupes)` par `notifier_abonnes_sans_risque(groupes)`.

Vérifier qu'il n'en reste aucun : `grep -n "notifier_abonnes_sans_risque\|verifier_ecoute_et_alerter\|ecoute_muette" *.py tests/*.py`
Attendu : seulement la définition et l'appel à un argument.

- [ ] **Étape 5 : lancer la suite**

Lancer : `python -m pytest tests -q`
Attendu : tout vert

- [ ] **Étape 6 : committer**

```bash
git add hub_deals_db.py abonnes.py tests/
git commit -m "Releve : lire les abonnes dans la base distante

notifier_abonnes_sans_risque perd son parametre conn : la connexion aux
abonnes n'est plus celle des offres. Contrat inchange -- ne leve
jamais, et une base injoignable laisse le releve se terminer.

ecoute_muette disparait : elle comparait l'heure au temoin rafraichi
toutes les 50 s par le long polling. Avec un webhook il n'y a plus de
boucle, le service dort, et l'absence de message ne signale aucune
panne. La sonde getWebhookInfo la remplace, dans la vigie.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Tâche 6 : la vigie surveille le webhook

**Fichiers :**
- Modifier : `vigie.py`, `tests/test_vigie.py`

**Interfaces :**
- Consomme : rien des tâches précédentes.
- Produit : `vigie.juger_webhook(infos: dict, maintenant: datetime) -> list[str]` ; `vigie.lire_webhook(appeler=None) -> dict`.

- [ ] **Étape 1 : écrire les tests**

```python
# tests/test_vigie.py — a ajouter
class TestJugerWebhook(unittest.TestCase):
    """Le webhook est un service EXTERIEUR au portable : un portable
    eteint ne peut pas signaler qu'un service distant est tombe. C'est
    donc la vigie qui le surveille."""

    def _infos(self, **champs):
        base = {"url": "https://hub-deals-bot.onrender.com/telegram",
                "pending_update_count": 0}
        base.update(champs)
        return base

    def test_un_webhook_sain_ne_dit_rien(self):
        maintenant = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)

        self.assertEqual(vigie.juger_webhook(self._infos(), maintenant), [])

    def test_une_url_vide_est_signalee(self):
        """Cas le plus grave : Telegram ne sait plus ou nous joindre, et
        chaque /start part dans le vide."""
        maintenant = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)

        problemes = vigie.juger_webhook(self._infos(url=""), maintenant)

        self.assertEqual(len(problemes), 1)
        self.assertIn("aucune adresse", problemes[0].lower())

    def test_une_erreur_recente_est_signalee(self):
        maintenant = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)
        hier = int(datetime(2026, 9, 21, 14, tzinfo=timezone.utc).timestamp())

        problemes = vigie.juger_webhook(
            self._infos(last_error_date=hier,
                        last_error_message="Connection timed out"), maintenant)

        self.assertEqual(len(problemes), 1)
        self.assertIn("Connection timed out", problemes[0])

    def test_une_vieille_erreur_ne_dit_rien(self):
        """Le service s'endort : une erreur d'il y a trois jours a ete
        rattrapee depuis. N'alerter que sur ce qui dure."""
        maintenant = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)
        vieux = int(datetime(2026, 9, 18, 9, tzinfo=timezone.utc).timestamp())

        self.assertEqual(
            vigie.juger_webhook(self._infos(last_error_date=vieux,
                                            last_error_message="Bad Gateway"),
                                maintenant), [])

    def test_des_messages_qui_s_accumulent_sont_signales(self):
        maintenant = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)

        problemes = vigie.juger_webhook(
            self._infos(pending_update_count=12), maintenant)

        self.assertEqual(len(problemes), 1)
        self.assertIn("12", problemes[0])

    def test_sans_infos_rien_n_est_juge(self):
        """getWebhookInfo injoignable : c'est le critere d'age du releve
        qui parle, pas celui-ci."""
        maintenant = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)

        self.assertEqual(vigie.juger_webhook({}, maintenant), [])
```

- [ ] **Étape 2 : lancer et vérifier l'échec**

Lancer : `python -m pytest tests/test_vigie.py -q`
Attendu : ÉCHEC, `module 'vigie' has no attribute 'juger_webhook'`

- [ ] **Étape 3 : implémenter**

```python
# vigie.py — constantes
AGE_ERREUR_MAX_H = 24    # au-dela, l'erreur a ete rattrapee depuis
EN_ATTENTE_MAX = 5       # quelques messages en vol sont normaux


def lire_webhook(appeler=None) -> dict:
    """Etat du webhook vu par Telegram. Rend {} si l'appel echoue : c'est
    le critere d'age du releve qui portera l'alerte."""
    if appeler is None:
        import hub_deals_db
        import requests

        def appeler():
            r = requests.get(
                f"https://api.telegram.org/bot{hub_deals_db.TELEGRAM_BOT_TOKEN}"
                "/getWebhookInfo", timeout=20)
            return r.json().get("result", {}) if r.status_code == 200 else {}

    try:
        return appeler() or {}
    except Exception as erreur:
        print(f"getWebhookInfo indisponible : {erreur}")
        return {}


def juger_webhook(infos: dict, maintenant: datetime) -> list:
    """Le webhook remplace le long polling : ce n'est plus « une boucle
    tourne-t-elle » qu'il faut verifier, mais « Telegram sait-il ou nous
    joindre ». Un service endormi est normal et ne dit rien ici."""
    if not infos:
        return []

    if not infos.get("url"):
        return ["<b>Vigie : le bot n'a plus d'adresse</b>\n\n"
                "Telegram ne sait plus où livrer les messages : chaque "
                "/start part dans le vide.\n\n"
                "À vérifier : relancer setWebhook."]

    problemes = []
    erreur_le = infos.get("last_error_date")
    if erreur_le:
        age_h = (maintenant.timestamp() - erreur_le) / 3600
        if age_h <= AGE_ERREUR_MAX_H:
            problemes.append(
                f"<b>Vigie : le webhook a échoué il y a {age_h:.0f} h</b>\n\n"
                f"{infos.get('last_error_message', 'sans message')}\n\n"
                "À vérifier : le service sur Render (journal, déploiement).")

    en_attente = infos.get("pending_update_count", 0)
    if en_attente > EN_ATTENTE_MAX:
        problemes.append(
            f"<b>Vigie : {en_attente} message(s) non délivrés</b>\n\n"
            "Ils s'accumulent chez Telegram : le service ne les prend plus.\n\n"
            "À vérifier : le service sur Render.")
    return problemes
```

Puis, dans `main()`, ajouter la sonde aux messages :

```python
    messages = (juger(releves, maintenant)
                + juger_par_ville(volumes)
                + juger_webhook(lire_webhook(), maintenant))
```

- [ ] **Étape 4 : ajouter le test de `main`**

```python
    def test_un_webhook_muet_part_sur_telegram(self):
        mardi = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)

        with mock.patch.object(vigie, "lire_webhook", lambda: {"url": ""}):
            code = vigie.main(argv=[], releves=self._releves([940] * 11, mardi),
                              volumes=[], envoyer=self._envoyer,
                              maintenant=mardi)

        self.assertEqual(code, 0)
        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("adresse", self.envoyes[0])
```

Ajouter `from unittest import mock` en tête de `tests/test_vigie.py` s'il n'y est pas.

⚠️ Les tests existants de `TestMain` n'injectent pas `lire_webhook` : ils appelleraient le vrai `getWebhookInfo`. Leur ajouter `mock.patch.object(vigie, "lire_webhook", lambda: {})`, comme `volumes=[]` l'avait été.

- [ ] **Étape 5 : lancer la suite**

Lancer : `python -m pytest tests -q`
Attendu : tout vert. Vérifier la durée : si elle grimpe de plusieurs secondes, un test appelle le réseau — le corriger avant de committer.

- [ ] **Étape 6 : committer**

```bash
git add vigie.py tests/test_vigie.py
git commit -m "Vigie : surveiller le webhook plutot qu'une boucle

Le webhook est un service exterieur au portable, et un portable eteint
ne peut pas signaler qu'un service distant est tombe. La vigie tourne
deja hors machine et detient deja le jeton : c'est sa place.

getWebhookInfo dit si Telegram sait ou nous joindre. Une erreur de plus
de 24 h ne dit rien : le service s'endort, elle a ete rattrapee depuis.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Tâche 7 : retirer le long polling du portable

**Fichiers :**
- Modifier : `bot_ecoute.py` (suppression de `boucle`, `empecher_la_veille`, `_attente_apres_echec`, `__main__`)
- Modifier : `tests/test_bot_ecoute.py`
- Supprimer : `taches/bot_ecoute.xml`, `taches/installer_bot_ecoute.ps1`

**Interfaces :**
- Produit : `bot_ecoute.py` réduit à la logique pure — `traiter_update`, `_envoi`, `executer_actions`, `appeler`, `masquer`, `log`, `code_valide` — importée par `web_bot.py`.

- [ ] **Étape 1 : vérifier ce qui devient inutilisé**

Lancer : `grep -rn "boucle\|empecher_la_veille\|_attente_apres_echec" *.py tests/*.py`
Noter chaque occurrence : tout ce qui n'est appelé que par le `__main__` supprimé part avec lui.

- [ ] **Étape 2 : supprimer le code et ses tests**

Retirer de `bot_ecoute.py` : `boucle()`, `_attente_apres_echec()`, `empecher_la_veille()`, `ATTENTE_MAX`, le bloc `if __name__ == "__main__"`, et les imports devenus inutiles (`time`, `sqlite3`, `ctypes`). Retirer de `tests/test_bot_ecoute.py` les classes qui les testaient, **y compris `TestSousPythonw`** — le bot ne tourne plus sous `pythonw`.

En tête du module, remplacer le docstring par :

```python
"""
Logique d'inscription au bot Telegram : traduit une mise a jour en
actions, sans aucun appel reseau.

Le long polling a ete retire le 2026-09-20 : l'ecoute est servie par
web_bot.py (webhook, hors du portable). Ce module reste la reference
unique de la logique d'inscription, appelee par le service.

Spec : docs/superpowers/specs/2026-09-20-abonnes-hebergement-design.md
"""
```

- [ ] **Étape 3 : lancer la suite**

Lancer : `python -m pytest tests -q`
Attendu : tout vert, avec moins de tests qu'avant (les tests `pythonw` de `bot_ecoute` sont partis).

⚠️ Vérifier que `tests/test_sauvegarde.py::TestExecutionDesCommandes` **reste** : lui teste `sauvegarde._executer`, toujours utilisé par le relevé.

- [ ] **Étape 4 : committer**

```bash
git rm taches/bot_ecoute.xml taches/installer_bot_ecoute.ps1
git add bot_ecoute.py tests/test_bot_ecoute.py
git commit -m "Ecoute : retirer le long polling du portable

Telegram refuse webhook et long polling simultanes : garder la boucle
aurait produit des 409 a chaque ouverture de session. bot_ecoute.py
reste la reference unique de la logique d'inscription, appelee par le
service.

empecher_la_veille part avec elle -- le verrou anti-veille est repris
par le planificateur (option « Reveiller l'ordinateur »).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Tâche 8 : la bascule (manuelle, dans cet ordre)

**Fichiers :** aucun. Cette tâche produit des **preuves**, à coller dans le CHANGELOG à la tâche 9.

L'ordre n'est pas négociable : Telegram refuse webhook et long polling simultanés (409).

- [ ] **Étape 1 : créer la base Neon**

Projet Neon dédié `hub-deals`. Créer un rôle restreint (**pas** `neon_superuser` — leçon du 2026-09-13 sur agent-autonome) :

```sql
CREATE ROLE hub_deals_bot LOGIN PASSWORD '<mot de passe genere>';
GRANT CONNECT ON DATABASE hub_deals TO hub_deals_bot;
GRANT USAGE ON SCHEMA public TO hub_deals_bot;
-- les tables sont creees par magasin.ouvrir(), lance une premiere fois
-- avec un role proprietaire, puis :
GRANT SELECT, INSERT, UPDATE ON abonnes, etat_bot TO hub_deals_bot;
```

Vérifier que le rôle **ne peut pas** supprimer :

```sql
SET ROLE hub_deals_bot;
DELETE FROM abonnes;   -- attendu : ERROR: permission denied for table abonnes
RESET ROLE;
```

- [ ] **Étape 2 : poser la variable sur le portable, puis migrer**

Faire taper par l'utilisateur dans **une fenêtre PowerShell séparée** (jamais dans la conversation : `setx` afficherait la chaîne de connexion) :

```powershell
setx HUB_DEALS_ABONNES_URL "postgresql://hub_deals_bot:...@...neon.tech/hub_deals?sslmode=require"
```

Vérifier sa présence sans l'afficher — via `HKCU\Environment`, **jamais** via l'environnement du processus courant, obsolète après `setx` :

```powershell
[bool](Get-ItemProperty -Path HKCU:\Environment -Name HUB_DEALS_ABONNES_URL -ErrorAction SilentlyContinue)
```

Puis, dans un **nouveau** terminal :

```bash
python migrer_abonnes.py --a-blanc   # attendu : « Source : 1 abonne(s) »
python migrer_abonnes.py             # attendu : « Reprises : 1 abonne(s) »
python migrer_abonnes.py             # attendu : « Reprises : 0 abonne(s) »
```

La troisième ligne est la preuve d'idempotence en réel, pas en test.

- [ ] **Étape 3 : déployer le service, webhook NON enregistré**

Créer le service Render depuis `render.yaml`, renseigner les quatre variables. `TELEGRAM_WEBHOOK_SECRET` : 32 caractères aléatoires, généré et collé dans Render sans passer par la conversation.

Contrôle positif, avec un update fabriqué :

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  https://hub-deals-bot.onrender.com/telegram \
  -H "Content-Type: application/json" -d '{"update_id":1}'
# attendu : 403 (pas de secret)
```

Puis un vrai update de test avec le bon secret, **avec le chat_id du propriétaire** :

```bash
curl -s -X POST https://hub-deals-bot.onrender.com/telegram \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: <secret>" \
  -d '{"update_id":2,"message":{"message_id":1,"text":"/aide","chat":{"id":<chat_id>,"type":"private"},"from":{"id":<chat_id>,"first_name":"Test"}}}'
# attendu : {"ok":true} ET le message d'aide recu sur Telegram
```

**Mesurer le réveil** (la spec l'annonce à ~1 min d'après la documentation Render, sans mesure) : attendre 20 min que le service s'endorme, puis chronométrer le même appel.

```bash
time curl -s -o /dev/null https://hub-deals-bot.onrender.com/sante
```

Noter la valeur réelle : elle corrige la spec.

- [ ] **Étape 4 : arrêter le bot local et enregistrer le webhook**

```powershell
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process
Get-ScheduledTask -TaskName "Bot vols - ecoute" | Disable-ScheduledTask
```

Vérifier qu'aucun `pythonw` ne subsiste avant d'enregistrer le webhook, sinon 409.

```bash
curl -s "https://api.telegram.org/bot<token>/setWebhook" \
  -d "url=https://hub-deals-bot.onrender.com/telegram" \
  -d "secret_token=<secret>" -d "drop_pending_updates=false"
curl -s "https://api.telegram.org/bot<token>/getWebhookInfo"
```

Attendu : `"url"` renseignée, `"pending_update_count":0`, pas de `last_error_message`.

Puis un vrai `/start` depuis le **compte test**, et vérifier que le clavier des villes arrive.

- [ ] **Étape 5 : prouver le relevé en réel**

Déclencher un relevé à la main (`Start-ScheduledTask "Traqueur de vols"`) et lire `flight_deals_log.txt` :

- « Abonnes : 1/1 envoye(s) » (ou 0 sans affaire, ce qui est normal) ;
- aucune ligne « envoi aux abonnes impossible » ;
- le message reçu sur le compte test.

- [ ] **Étape 6 : réveiller la machine pour la tâche de 13h**

```powershell
$t = Get-ScheduledTask -TaskName "Traqueur de vols"
$t.Settings.WakeToRun = $true
Set-ScheduledTask -InputObject $t
(Get-ScheduledTask -TaskName "Traqueur de vols").Settings.WakeToRun   # attendu : True
```

Sans cela, le verrou anti-veille disparu avec `bot_ecoute.py` laisse la machine s'endormir et le relevé de 13h ne part pas.

- [ ] **Étape 7 : retour arrière, si besoin**

À garder sous la main : `deleteWebhook`, réactiver la tâche « Bot vols - ecoute », `git revert` de la tâche 5 pour repointer le relevé sur SQLite.

---

### Tâche 9 : documentation

**Fichiers :**
- Modifier : `README.md`, `CHANGELOG.md`

- [ ] **Étape 1 : README**

Remplacer la section décrivant l'écoute par le nouveau fonctionnement : webhook sur Render, abonnés dans Neon, variables d'environnement attendues des deux côtés, sonde `getWebhookInfo` dans la vigie, et la procédure de retour arrière.

Ajouter dans la section « Sauvegarde » : **les abonnés ne sont plus dans le dump** parce qu'ils ne sont plus dans cette base ; c'est Neon qui les sauvegarde.

- [ ] **Étape 2 : CHANGELOG**

Une entrée datée reprenant : le frein levé (inscription à toute heure), la dette remboursée (abonnés de nouveau sauvegardés), le temps de réveil **mesuré** à l'étape 3 de la tâche 8, et les preuves de la bascule.

- [ ] **Étape 3 : committer et vérifier le CI**

```bash
git add README.md CHANGELOG.md
git commit -m "Doc : ecoute en webhook, abonnes chez Neon

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push origin master
```

Vérifier que **les deux** jobs sont verts, et que le job `postgres` n'affiche aucun test sauté.

---

## Ce que ce plan ne fait pas

- Déplacer `offres` vers Postgres.
- Toucher aux seuils, aux villes, aux liens courts.
- Rédiger le message d'invitation ni recruter — c'est la suite, une fois ce plan livré.
