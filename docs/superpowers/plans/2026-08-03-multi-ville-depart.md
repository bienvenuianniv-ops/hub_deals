# Support multi-villes de départ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Généraliser `hub_deals` pour supporter plusieurs villes de départ (chacune avec sa propre table de coûts de rabattement vers les mêmes hubs), sans appel API supplémentaire, en gardant Dakar comme unique ville active pour cette itération.

**Architecture:** `RABATTEMENT` passe d'un dict plat `hub -> cout` à un dict `ville -> hub -> cout`, avec les hubs (nom d'affichage) extraits dans un dict `HUBS` séparé et partagé. La boucle de récupération API reste indexée sur `HUBS` (un seul appel par hub) ; `enregistrer_offres()` boucle en interne sur les villes de `RABATTEMENT` pour insérer une ligne par (ville, offre). La table SQLite `offres` gagne une colonne `ville_depart`, migrée de façon idempotente avec un défaut `'Dakar'` pour l'historique existant. `anomaly_detection.py` regroupe désormais les moyennes historiques par `(ville_depart, hub_origine, destination_code)`.

**Tech Stack:** Python 3.14, sqlite3 (stdlib), `requests` (déjà en dépendance), `unittest` (stdlib, pas de nouvelle dépendance de test) avec base SQLite en mémoire (`sqlite3.connect(":memory:")`).

## Global Constraints

- Pas de fichier de config externe (JSON/YAML) — `HUBS` et `RABATTEMENT` restent des dicts Python en dur dans `hub_deals_db.py`.
- Pas de liste de hubs personnalisable par ville — tous les hubs de `HUBS` sont potentiellement partagés par toutes les villes de `RABATTEMENT`.
- Pas de nouvel appel API par ville de départ — un seul appel `get_special_offers(hub)` par hub, réutilisé pour toutes les villes.
- Aucune nouvelle dépendance de test — utiliser `unittest` (stdlib) plutôt que `pytest`.
- Seule `"Dakar"` reste une ville active à l'issue de ce plan, avec ses valeurs de coût actuelles inchangées.
- Toute migration de schéma doit être idempotente (sûre à ré-exécuter à chaque lancement du script).

---

## Task 1: Migration DB idempotente pour `ville_depart`

**Files:**
- Modify: `hub_deals_db.py:55-71` (fonction `init_db`)
- Create: `tests/test_hub_deals_db.py`

**Interfaces:**
- Consumes: rien (première tâche)
- Produces: `init_db(conn: sqlite3.Connection) -> None` — crée la table `offres` si absente, et garantit que la colonne `ville_depart TEXT NOT NULL DEFAULT 'Dakar'` existe (l'ajoute si nécessaire, ne fait rien si déjà présente).

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/test_hub_deals_db.py` :

```python
import sqlite3
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db


class TestInitDbMigration(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def _colonnes(self):
        return [row[1] for row in self.conn.execute("PRAGMA table_info(offres)")]

    def test_cree_la_table_avec_colonne_ville_depart(self):
        hub_deals_db.init_db(self.conn)
        self.assertIn("ville_depart", self._colonnes())

    def test_idempotent_sur_une_base_deja_a_jour(self):
        hub_deals_db.init_db(self.conn)
        hub_deals_db.init_db(self.conn)  # ne doit pas lever d'erreur
        self.assertIn("ville_depart", self._colonnes())

    def test_migre_une_table_existante_sans_ville_depart(self):
        # simule l'ancien schema (avant la colonne ville_depart)
        self.conn.execute("""
            CREATE TABLE offres (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date_collecte TEXT NOT NULL,
                hub_origine TEXT NOT NULL,
                destination_code TEXT,
                destination_nom TEXT,
                prix_vol_hub REAL,
                rabattement REAL,
                total_estime REAL,
                date_depart TEXT,
                lien TEXT
            )
        """)
        self.conn.execute("""
            INSERT INTO offres (date_collecte, hub_origine, destination_code, total_estime)
            VALUES ('2026-07-21 10:00:00', 'Casablanca', 'SID', 612)
        """)
        self.conn.commit()

        hub_deals_db.init_db(self.conn)

        self.assertIn("ville_depart", self._colonnes())
        ville = self.conn.execute("SELECT ville_depart FROM offres WHERE destination_code = 'SID'").fetchone()[0]
        self.assertEqual(ville, "Dakar")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Lancer les tests et vérifier qu'ils échouent**

Run: `python -m unittest tests.test_hub_deals_db -v` (depuis `C:\Users\Dell\hub_deals`)
Expected: `test_cree_la_table_avec_colonne_ville_depart` et `test_migre_une_table_existante_sans_ville_depart` échouent avec `AssertionError` (colonne absente) — `test_idempotent_sur_une_base_deja_a_jour` peut passer par hasard (comportement encore trivial), c'est attendu.

- [ ] **Step 3: Implémenter la migration**

Dans `hub_deals_db.py`, remplacer la fonction `init_db` (lignes 55-71) :

```python
def init_db(conn: sqlite3.Connection) -> None:
    """Cree la table si elle n'existe pas encore, et applique les
    migrations de schema necessaires. Idempotent -- sans danger a
    re-executer a chaque lancement du script."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS offres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_collecte TEXT NOT NULL,
            hub_origine TEXT NOT NULL,
            destination_code TEXT,
            destination_nom TEXT,
            prix_vol_hub REAL,
            rabattement REAL,
            total_estime REAL,
            date_depart TEXT,
            lien TEXT
        )
    """)
    colonnes = [row[1] for row in conn.execute("PRAGMA table_info(offres)")]
    if "ville_depart" not in colonnes:
        conn.execute("ALTER TABLE offres ADD COLUMN ville_depart TEXT NOT NULL DEFAULT 'Dakar'")
    conn.commit()
```

- [ ] **Step 4: Lancer les tests et vérifier qu'ils passent**

Run: `python -m unittest tests.test_hub_deals_db -v`
Expected: 3 tests, tous PASS

- [ ] **Step 5: Commit**

```bash
git add hub_deals_db.py tests/test_hub_deals_db.py
git commit -m "Ajoute la migration idempotente de la colonne ville_depart"
```

---

## Task 2: Config `HUBS`/`RABATTEMENT` + insertion multi-villes

**Files:**
- Modify: `hub_deals_db.py:41-52` (config `RABATTEMENT`)
- Modify: `hub_deals_db.py:83-105` (fonction `enregistrer_offres`)
- Modify: `hub_deals_db.py:190-199` (boucle principale, dans `if __name__ == "__main__":`)
- Modify: `tests/test_hub_deals_db.py` (ajouter la classe de tests)

**Interfaces:**
- Consumes: `init_db` de la Task 1 (les tests de cette tâche appellent `init_db(conn)` avant d'insérer)
- Produces: `HUBS: dict[str, dict]` (clé IATA -> `{"nom": str}`), `RABATTEMENT: dict[str, dict[str, dict]]` (ville -> hub IATA -> `{"prix": float, "duree_h": float}`), `enregistrer_offres(conn, hub_iata: str, offres: list, date_collecte: str) -> None` — insère une ligne par (ville de `RABATTEMENT` ayant une entrée pour ce hub) x (offre).

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `tests/test_hub_deals_db.py` :

```python
class TestEnregistrerOffresMultiVilles(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)

        # sauvegarde la config reelle, remplacee par une config de test
        self._hubs_original = hub_deals_db.HUBS
        self._rabattement_original = hub_deals_db.RABATTEMENT
        hub_deals_db.HUBS = {"CMN": {"nom": "Casablanca"}}
        hub_deals_db.RABATTEMENT = {
            "Dakar": {"CMN": {"prix": 400, "duree_h": 4}},
            "Abidjan": {"CMN": {"prix": 150, "duree_h": 2}},
        }

    def tearDown(self):
        hub_deals_db.HUBS = self._hubs_original
        hub_deals_db.RABATTEMENT = self._rabattement_original
        self.conn.close()

    def _offre_test(self, prix=100):
        return {
            "destination": "SID",
            "destination_name": "Sal",
            "price": prix,
            "departure_at": "2026-09-01T10:00:00+00:00",
            "link": "/search/CMN0109SID1",
        }

    def test_insere_une_ligne_par_ville_ayant_un_cout_pour_ce_hub(self):
        hub_deals_db.enregistrer_offres(self.conn, "CMN", [self._offre_test()], "2026-08-03 12:00:00")

        lignes = self.conn.execute(
            "SELECT ville_depart, total_estime FROM offres ORDER BY ville_depart"
        ).fetchall()

        self.assertEqual(len(lignes), 2)
        self.assertEqual(lignes, [("Abidjan", 250.0), ("Dakar", 500.0)])

    def test_ignore_une_ville_sans_cout_defini_pour_ce_hub(self):
        hub_deals_db.RABATTEMENT["Nairobi"] = {}  # aucune entree pour CMN

        hub_deals_db.enregistrer_offres(self.conn, "CMN", [self._offre_test()], "2026-08-03 12:00:00")

        villes = [row[0] for row in self.conn.execute("SELECT ville_depart FROM offres")]
        self.assertNotIn("Nairobi", villes)
        self.assertEqual(len(villes), 2)
```

- [ ] **Step 2: Lancer les tests et vérifier qu'ils échouent**

Run: `python -m unittest tests.test_hub_deals_db -v`
Expected: les deux nouveaux tests échouent — soit `AttributeError` (si `HUBS` n'existe pas encore), soit un mauvais nombre de lignes / mauvais `total_estime` (l'implémentation actuelle utilise encore l'ancien `RABATTEMENT` plat indexé par hub).

- [ ] **Step 3: Remplacer la config plate par `HUBS`/`RABATTEMENT` imbriqué**

Dans `hub_deals_db.py`, remplacer les lignes 41-52 :

```python
HUBS = {
    "CMN": {"nom": "Casablanca"},
    "CDG": {"nom": "Paris"},
    "IST": {"nom": "Istanbul"},
    "ADD": {"nom": "Addis-Abeba"},
    "NBO": {"nom": "Nairobi"},
    "ABJ": {"nom": "Abidjan"},
}

# Cout de rabattement par ville de depart -> chaque hub, base sur des prix
# reels (juillet 2026 pour Dakar). Ajouter une ville = ajouter une entree
# ici, meme structure -- aucun autre changement de code necessaire.
RABATTEMENT = {
    "Dakar": {
        "CMN": {"prix": 400, "duree_h": 4},
        "CDG": {"prix": 300, "duree_h": 6},
        "IST": {"prix": 400, "duree_h": 7},
        "ADD": {"prix": 500, "duree_h": 6},
        "NBO": {"prix": 500, "duree_h": 8},
        "ABJ": {"prix": 200, "duree_h": 2},
    },
    # "Abidjan": { ... },  # a ajouter plus tard, meme structure
}
```

- [ ] **Step 4: Réécrire `enregistrer_offres` pour boucler sur les villes**

Remplacer la fonction `enregistrer_offres` (lignes 83-105, après le déplacement dû au Step 3 — chercher par nom de fonction) :

```python
def enregistrer_offres(conn: sqlite3.Connection, hub_iata: str, offres: list, date_collecte: str) -> None:
    """Insere chaque offre du jour dans la base, une fois par ville de
    depart ayant un cout de rabattement defini pour ce hub."""
    hub_nom = HUBS[hub_iata]["nom"]
    for ville, couts_ville in RABATTEMENT.items():
        rabattement = couts_ville.get(hub_iata)
        if rabattement is None:
            continue
        for offre in offres:
            prix_vol = offre.get("price", 0)
            total_estime = prix_vol + rabattement["prix"]
            conn.execute("""
                INSERT INTO offres (
                    date_collecte, ville_depart, hub_origine, destination_code, destination_nom,
                    prix_vol_hub, rabattement, total_estime, date_depart, lien
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date_collecte,
                ville,
                hub_nom,
                offre.get("destination"),
                offre.get("destination_name"),
                prix_vol,
                rabattement["prix"],
                total_estime,
                offre.get("departure_at"),
                offre.get("link"),
            ))
    conn.commit()
```

- [ ] **Step 5: Mettre à jour la boucle principale pour itérer sur `HUBS`**

Dans le bloc `if __name__ == "__main__":`, remplacer :

```python
    for hub_iata in RABATTEMENT:
        log(f"Recuperation des offres depuis {RABATTEMENT[hub_iata]['nom']} ({hub_iata})...")
```

par :

```python
    for hub_iata, hub_info in HUBS.items():
        log(f"Recuperation des offres depuis {hub_info['nom']} ({hub_iata})...")
```

(le reste du bloc `try/except` en dessous ne change pas — `enregistrer_offres(conn, hub_iata, offres, date_collecte)` est déjà appelé avec la bonne signature)

- [ ] **Step 6: Lancer les tests et vérifier qu'ils passent**

Run: `python -m unittest tests.test_hub_deals_db -v`
Expected: 5 tests (3 de la Task 1 + 2 nouveaux), tous PASS

- [ ] **Step 7: Vérifier la syntaxe de l'ensemble du fichier**

Run: `python -m py_compile hub_deals_db.py`
Expected: aucune sortie, code de retour 0

- [ ] **Step 8: Commit**

```bash
git add hub_deals_db.py tests/test_hub_deals_db.py
git commit -m "Generalise RABATTEMENT en ville -> hub, insertion multi-villes"
```

---

## Task 3: `anomaly_detection.py` — regroupement par (ville, hub, destination)

**Files:**
- Modify: `anomaly_detection.py:27-41` (fonction `calculer_moyennes_historiques`)
- Modify: `anomaly_detection.py:44-94` (fonction `detecter_anomalies`)
- Create: `tests/test_anomaly_detection.py`

**Interfaces:**
- Consumes: `init_db` (Task 1) pour préparer la base de test avec la colonne `ville_depart`
- Produces: `calculer_moyennes_historiques(conn) -> dict[tuple[str, str, str], dict]` (clé `(ville_depart, hub_origine, destination_code)`), `detecter_anomalies(conn, date_collecte=None, mode_diagnostic=False) -> list[dict]` où chaque dict contient désormais la clé `"ville_depart"`.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/test_anomaly_detection.py` :

```python
import sqlite3
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db
import anomaly_detection


def _inserer_offre(conn, ville, hub, dest_code, dest_nom, total_estime, date_collecte):
    conn.execute("""
        INSERT INTO offres (
            date_collecte, ville_depart, hub_origine, destination_code,
            destination_nom, prix_vol_hub, rabattement, total_estime, date_depart, lien
        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, '2026-09-01T10:00:00+00:00', '/search/x')
    """, (date_collecte, ville, hub, dest_code, dest_nom, total_estime, total_estime))
    conn.commit()


class TestCalculerMoyennesHistoriques(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_ne_mixe_pas_les_moyennes_de_deux_villes(self):
        # Meme hub/destination, mais couts de rabattement (donc total_estime)
        # tres differents selon la ville de depart.
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Abidjan", "Casablanca", "SID", "Sal", 250, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Abidjan", "Casablanca", "SID", "Sal", 250, "2026-08-02 10:00:00")

        moyennes = anomaly_detection.calculer_moyennes_historiques(self.conn)

        self.assertEqual(moyennes[("Dakar", "Casablanca", "SID")]["moyenne"], 600)
        self.assertEqual(moyennes[("Abidjan", "Casablanca", "SID")]["moyenne"], 250)


class TestDetecterAnomalies(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_resultat_contient_la_ville_depart(self):
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 500, "2026-08-03 10:00:00")  # -16.7%

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-03 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["ville_depart"], "Dakar")

    def test_baisse_dans_une_ville_ne_declenche_pas_d_anomalie_dans_une_autre(self):
        # Abidjan baisse fortement, mais Dakar (meme hub/destination) reste stable :
        # seule l'anomalie Abidjan doit remonter.
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Dakar", "Casablanca", "SID", "Sal", 600, "2026-08-03 10:00:00")
        _inserer_offre(self.conn, "Abidjan", "Casablanca", "SID", "Sal", 250, "2026-08-01 10:00:00")
        _inserer_offre(self.conn, "Abidjan", "Casablanca", "SID", "Sal", 250, "2026-08-02 10:00:00")
        _inserer_offre(self.conn, "Abidjan", "Casablanca", "SID", "Sal", 150, "2026-08-03 10:00:00")  # -40%

        anomalies = anomaly_detection.detecter_anomalies(self.conn, date_collecte="2026-08-03 10:00:00")

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["ville_depart"], "Abidjan")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Lancer les tests et vérifier qu'ils échouent**

Run: `python -m unittest tests.test_anomaly_detection -v`
Expected: `test_ne_mixe_pas_les_moyennes_de_deux_villes` échoue (`KeyError`, la clé actuelle est `(hub, dest_code)` sans ville) ; les tests de `TestDetecterAnomalies` échouent aussi (`KeyError: 'ville_depart'` ou moyennes incorrectement mélangées).

- [ ] **Step 3: Mettre à jour `calculer_moyennes_historiques`**

Remplacer les lignes 27-41 de `anomaly_detection.py` :

```python
def calculer_moyennes_historiques(conn: sqlite3.Connection) -> dict:
    """
    Calcule la moyenne du total_estime par (ville de depart, destination),
    sur tout l'historique (tous les releves confondus).
    Cle : (ville_depart, hub_origine, destination_code)
    """
    cur = conn.execute("""
        SELECT ville_depart, hub_origine, destination_code, AVG(total_estime), COUNT(*)
        FROM offres
        GROUP BY ville_depart, hub_origine, destination_code
    """)
    moyennes = {}
    for ville, hub, dest_code, moyenne, nb_releves in cur.fetchall():
        moyennes[(ville, hub, dest_code)] = {"moyenne": moyenne, "nb_releves": nb_releves}
    return moyennes
```

- [ ] **Step 4: Mettre à jour `detecter_anomalies`**

Remplacer les lignes 44-94 de `anomaly_detection.py` :

```python
def detecter_anomalies(
    conn: sqlite3.Connection,
    date_collecte: str = None,
    mode_diagnostic: bool = False,
) -> list:
    """
    Compare chaque offre du releve donne (par defaut le plus recent dans
    la base) a la moyenne historique de sa destination, pour la meme ville
    de depart. Renvoie la liste des anomalies detectees, triees par
    pourcentage de baisse (la meilleure affaire en premier).

    Si mode_diagnostic=True, renvoie TOUTES les comparaisons (meme celles
    sous le seuil), pour voir ce qui se passe reellement.
    """
    if date_collecte is None:
        date_collecte = get_dernier_releve(conn)
    moyennes = calculer_moyennes_historiques(conn)

    cur = conn.execute("""
        SELECT ville_depart, hub_origine, destination_code, destination_nom,
               total_estime, date_depart, lien
        FROM offres
        WHERE date_collecte = ?
    """, (date_collecte,))

    resultats = []
    for ville, hub, dest_code, dest_nom, total_estime, date_depart, lien in cur.fetchall():
        cle = (ville, hub, dest_code)
        info_historique = moyennes.get(cle)

        if not info_historique or info_historique["nb_releves"] < 2:
            continue

        moyenne = info_historique["moyenne"]
        baisse = (moyenne - total_estime) / moyenne

        if mode_diagnostic or baisse >= SEUIL_BAISSE:
            resultats.append({
                "destination": dest_nom,
                "destination_code": dest_code,
                "hub": hub,
                "ville_depart": ville,
                "prix_actuel": total_estime,
                "moyenne_historique": round(moyenne, 2),
                "baisse_pct": round(baisse * 100, 1),
                "nb_releves_historique": info_historique["nb_releves"],
                "date_depart": date_depart,
                "lien": lien,
            })

    resultats.sort(key=lambda x: x["baisse_pct"], reverse=True)
    return resultats
```

- [ ] **Step 5: Lancer les tests et vérifier qu'ils passent**

Run: `python -m unittest tests.test_anomaly_detection -v`
Expected: 3 tests, tous PASS

- [ ] **Step 6: Lancer toute la suite de tests**

Run: `python -m unittest discover -s tests -v`
Expected: 8 tests au total (5 de `test_hub_deals_db` + 3 de `test_anomaly_detection`), tous PASS

- [ ] **Step 7: Commit**

```bash
git add anomaly_detection.py tests/test_anomaly_detection.py
git commit -m "Regroupe les moyennes historiques par ville de depart"
```

---

## Task 4: Message Telegram — mentionner la ville de départ

**Files:**
- Modify: `hub_deals_db.py:150-171` (fonction `verifier_et_notifier_anomalies`)
- Modify: `tests/test_hub_deals_db.py` (ajouter la classe de tests)

**Interfaces:**
- Consumes: `detecter_anomalies` (Task 3, retourne désormais `ville_depart`), `envoyer_telegram(message: str) -> None` (existant, inchangé)
- Produces: `verifier_et_notifier_anomalies(conn, date_collecte) -> None` — comportement inchangé pour l'appelant, message HTML enrichi de la ville de départ.

- [ ] **Step 1: Écrire le test qui échoue**

Ajouter à `tests/test_hub_deals_db.py` :

```python
class TestNotificationMentionneLaVille(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)
        self._envoyer_telegram_original = hub_deals_db.envoyer_telegram
        self.messages_envoyes = []
        hub_deals_db.envoyer_telegram = self.messages_envoyes.append

    def tearDown(self):
        hub_deals_db.envoyer_telegram = self._envoyer_telegram_original
        self.conn.close()

    def test_le_message_mentionne_la_ville_de_depart(self):
        for total, date in [(600, "2026-08-01 10:00:00"), (600, "2026-08-02 10:00:00"), (500, "2026-08-03 10:00:00")]:
            self.conn.execute("""
                INSERT INTO offres (
                    date_collecte, ville_depart, hub_origine, destination_code,
                    destination_nom, prix_vol_hub, rabattement, total_estime, date_depart, lien
                ) VALUES (?, 'Dakar', 'Casablanca', 'SID', 'Sal', 0, 0, ?, '2026-09-01T10:00:00+00:00', '/search/x')
            """, (date, total))
        self.conn.commit()

        hub_deals_db.verifier_et_notifier_anomalies(self.conn, "2026-08-03 10:00:00")

        self.assertEqual(len(self.messages_envoyes), 1)
        self.assertIn("Dakar", self.messages_envoyes[0])
```

- [ ] **Step 2: Lancer le test et vérifier qu'il échoue**

Run: `python -m unittest tests.test_hub_deals_db.TestNotificationMentionneLaVille -v`
Expected: FAIL — soit le message ne contient pas "Dakar" à l'endroit attendu (le format actuel ne l'inclut pas), soit `KeyError: 'ville_depart'` selon l'état d'avancement.

- [ ] **Step 3: Mettre à jour le message dans `verifier_et_notifier_anomalies`**

Remplacer les lignes 150-171 de `hub_deals_db.py` :

```python
def verifier_et_notifier_anomalies(conn: sqlite3.Connection, date_collecte: str) -> None:
    """Compare le releve du jour a la moyenne historique de chaque
    destination (logique centralisee dans anomaly_detection.py), et
    envoie une notification Telegram pour toute baisse superieure au
    seuil."""
    anomalies = detecter_anomalies(conn, date_collecte=date_collecte)

    if not anomalies:
        log("Aucune anomalie a notifier pour ce releve.")
        return

    lignes = [f"<b>{len(anomalies)} bonne(s) affaire(s) detectee(s) !</b>\n"]
    for a in anomalies:
        lignes.append(
            f"\n<b>{a['destination']}</b> (depuis {a['hub']}, au depart de {a['ville_depart']})\n"
            f"{a['prix_actuel']:.0f}€ (moyenne habituelle : {a['moyenne_historique']:.0f}€, "
            f"-{a['baisse_pct']:.0f}%)\n"
            f"https://www.aviasales.com{a['lien']}"
        )
    message = "\n".join(lignes)
    envoyer_telegram(message)
    log(f"Notification Telegram envoyee pour {len(anomalies)} anomalie(s).")
```

- [ ] **Step 4: Lancer le test et vérifier qu'il passe**

Run: `python -m unittest tests.test_hub_deals_db.TestNotificationMentionneLaVille -v`
Expected: PASS

- [ ] **Step 5: Lancer toute la suite de tests**

Run: `python -m unittest discover -s tests -v`
Expected: 9 tests au total, tous PASS

- [ ] **Step 6: Commit**

```bash
git add hub_deals_db.py tests/test_hub_deals_db.py
git commit -m "Le message Telegram mentionne la ville de depart"
```

---

## Task 5: Vérification end-to-end réelle + documentation

**Files:**
- Modify: `hub_deals_AUDIT.md` (nouvelle section)
- Modify: `README.md` (mention multi-villes)
- Modify: `CHANGELOG.md` (nouvelle entrée datée)

**Interfaces:**
- Consumes: l'intégralité des tâches précédentes (script complet, prêt à s'exécuter contre l'API réelle et la vraie base `flight_deals.db`)
- Produces: rien de nouveau côté code — validation manuelle + documentation à jour.

- [ ] **Step 1: Sauvegarder la base réelle avant le premier lancement post-migration**

```bash
cp flight_deals.db flight_deals.db.backup-avant-multi-villes
```

- [ ] **Step 2: Lancer le script contre la vraie base**

Run (depuis `C:\Users\Dell\hub_deals`, avec `TRAVELPAYOUTS_TOKEN` déjà en variable d'environnement utilisateur) :
```bash
python hub_deals_db.py
```
Expected: le log se termine par `=== Fin d'execution ===` sans ligne `ERREUR` ; `PRAGMA table_info(offres)` inclut `ville_depart` ; toutes les nouvelles lignes ont `ville_depart = 'Dakar'`.

Vérifier avec :
```bash
python -c "import sqlite3; c = sqlite3.connect('flight_deals.db'); print(c.execute(\"SELECT DISTINCT ville_depart FROM offres\").fetchall())"
```
Expected: `[('Dakar',)]` (une seule ville, puisque `RABATTEMENT` n'en contient qu'une pour l'instant)

- [ ] **Step 3: Vérifier que `detect_anomalies.py` reste cohérent avec le nouveau champ**

Run:
```bash
python detect_anomalies.py
```
Expected: s'exécute sans erreur ; si des anomalies (ou le mode diagnostic) sont affichées, chaque entrée JSON contient bien la clé `"ville_depart": "Dakar"`.

- [ ] **Step 4: Supprimer la sauvegarde une fois la vérification confirmée**

```bash
rm flight_deals.db.backup-avant-multi-villes
```

- [ ] **Step 5: Redéclencher la tâche planifiée "Traqueur de vols" et vérifier le résultat**

Utiliser PowerShell :
```powershell
Start-ScheduledTask -TaskName "Traqueur de vols"
```
Attendre ~60 secondes (délai de 30s au démarrage du script + appels API), puis :
```powershell
Get-ScheduledTaskInfo -TaskName "Traqueur de vols" | Format-List LastTaskResult
```
Expected: `LastTaskResult : 0`

- [ ] **Step 6: Mettre à jour `hub_deals_AUDIT.md`**

Ajouter une section décrivant : la généralisation multi-villes, la migration `ville_depart` (idempotente, backfill `'Dakar'`), l'absence de coût API supplémentaire, et la vérification end-to-end du Step 5 (résultat, date).

- [ ] **Step 7: Mettre à jour `README.md`**

Dans la section "Principe", mentionner que le coût de rabattement dépend désormais de la ville de départ (`RABATTEMENT[ville][hub]`), et que seule Dakar est active pour l'instant. Ajouter une ligne sur `tests/` dans la section "Installation" ou une nouvelle section "Tests" :

```
## Tests

python -m unittest discover -s tests -v
```

- [ ] **Step 8: Mettre à jour `CHANGELOG.md`**

Ajouter une entrée datée du jour, section "Ajouté" : généralisation multi-villes de départ (`HUBS`/`RABATTEMENT` imbriqué, colonne `ville_depart`, regroupement des moyennes par ville, tests `unittest`), section "Modifié" : message Telegram enrichi de la ville de départ.

- [ ] **Step 9: Commit et push final**

```bash
git add hub_deals_AUDIT.md README.md CHANGELOG.md
git commit -m "Documente la generalisation multi-villes de depart"
git push
```
