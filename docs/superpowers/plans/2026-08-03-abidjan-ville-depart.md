# Abidjan comme 2e ville de depart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter `"Abidjan"` comme deuxieme ville de depart active dans `RABATTEMENT`, avec des couts de rabattement obtenus par requete directe a l'API Travelpayouts (pas de nouveau code d'architecture, la structure generalisee gere deja nativement une ville avec un sous-ensemble de hubs).

**Architecture:** Ajout de donnees pur dans `hub_deals_db.py` : une nouvelle entree `RABATTEMENT["Abidjan"]` avec 4 hubs (`CMN`, `CDG`, `IST`, `NBO` — `ADD` omis faute de donnees API, `ABJ` omis car c'est deja le hub). Le mecanisme existant (`couts_ville.get(hub_iata)` -> `continue` si absent dans `enregistrer_offres`) traite deja ce sous-ensemble sans modification. Un test de garde-fou verifie le contenu de `RABATTEMENT["Abidjan"]` sur la vraie config (pas de monkeypatch), pour attraper une regression future (hub ajoute par erreur, prix nul ou negatif).

**Tech Stack:** Python 3.14, `unittest` (stdlib), aucune nouvelle dependance.

## Global Constraints

- Pas de changement au nombre ou a la liste des hubs surveilles (`HUBS` reste CMN, CDG, IST, ADD, NBO, ABJ).
- Pas de rabattement `ABJ -> ABJ` (Abidjan est deja le hub).
- `ADD` reste absent de `RABATTEMENT["Abidjan"]` — aucune source de donnees fiable actuellement (v1/prices/cheap, v2/prices/latest et v3/prices_for_dates renvoient tous `data: []` pour cette route).
- Valeurs de prix gardees exactes (pas arrondies), contrairement aux valeurs Dakar (estimation manuelle arrondie).
- Dakar reste inchangee (memes valeurs qu'avant).
- Toute migration/logique existante (init_db, anomaly_detection.py) reste intacte — aucune modification de code au-dela de la config `RABATTEMENT` et de la doc.

---

## Task 1: Donnee Abidjan + test de garde-fou

**Files:**
- Modify: `hub_deals_db.py:50-63` (config `RABATTEMENT` + commentaire)
- Modify: `tests/test_hub_deals_db.py` (nouvelle classe de test)

**Interfaces:**
- Consumes: rien (config pure, aucune fonction en amont)
- Produces: `RABATTEMENT["Abidjan"]: dict[str, dict]` — cle IATA (`"CMN"`, `"CDG"`, `"IST"`, `"NBO"`) -> `{"prix": float, "duree_h": float}`. Consomme ensuite tel quel par `enregistrer_offres` (deja generique, aucun changement necessaire).

- [ ] **Step 1: Ecrire le test qui echoue**

Ajouter a `tests/test_hub_deals_db.py`, apres la classe `TestNotificationMentionneLaVille` et avant le bloc `if __name__ == "__main__":` :

```python
class TestRabattementAbidjan(unittest.TestCase):
    def test_contient_exactement_les_hubs_attendus(self):
        self.assertIn("Abidjan", hub_deals_db.RABATTEMENT)
        self.assertEqual(
            set(hub_deals_db.RABATTEMENT["Abidjan"].keys()),
            {"CMN", "CDG", "IST", "NBO"},
        )

    def test_chaque_entree_a_un_prix_et_une_duree_positifs(self):
        for hub_iata, cout in hub_deals_db.RABATTEMENT["Abidjan"].items():
            self.assertGreater(cout["prix"], 0, msg=f"prix invalide pour {hub_iata}")
            self.assertGreater(cout["duree_h"], 0, msg=f"duree_h invalide pour {hub_iata}")
```

- [ ] **Step 2: Lancer les tests et verifier qu'ils echouent**

Run: `python -m unittest tests.test_hub_deals_db.TestRabattementAbidjan -v` (depuis `C:\Users\Dell\hub_deals`)
Expected: `test_contient_exactement_les_hubs_attendus` echoue avec `AssertionError` (`"Abidjan"` absent de `RABATTEMENT`, qui ne contient encore que `"Dakar"`).

- [ ] **Step 3: Ajouter l'entree Abidjan dans `RABATTEMENT`**

Remplacer les lignes 50-63 de `hub_deals_db.py` :

```python
# Cout de rabattement par ville de depart -> chaque hub. Dakar : estimation
# manuelle arrondie (prix reels de juillet 2026). Abidjan : valeurs exactes
# obtenues par requete directe a l'API Travelpayouts le 2026-08-03
# (v1/prices/cheap, complete par v3/prices_for_dates pour NBO) -- ADD omis,
# aucune donnee disponible sur ces trois endpoints pour cette route ; pas
# d'entree ABJ->ABJ, Abidjan etant deja le hub. Ajouter une ville = ajouter
# une entree ici, meme structure -- aucun autre changement de code necessaire.
RABATTEMENT = {
    "Dakar": {
        "CMN": {"prix": 400, "duree_h": 4},
        "CDG": {"prix": 300, "duree_h": 6},
        "IST": {"prix": 400, "duree_h": 7},
        "ADD": {"prix": 500, "duree_h": 6},
        "NBO": {"prix": 500, "duree_h": 8},
        "ABJ": {"prix": 200, "duree_h": 2},
    },
    "Abidjan": {
        "CMN": {"prix": 563, "duree_h": 3},
        "CDG": {"prix": 511, "duree_h": 8},
        "IST": {"prix": 700, "duree_h": 9},
        "NBO": {"prix": 374, "duree_h": 8},
    },
}
```

- [ ] **Step 4: Lancer les tests et verifier qu'ils passent**

Run: `python -m unittest tests.test_hub_deals_db.TestRabattementAbidjan -v`
Expected: 2 tests, tous PASS

- [ ] **Step 5: Lancer toute la suite de tests**

Run: `python -m unittest discover -s tests -v`
Expected: 13 tests au total (11 existants + 2 nouveaux), tous PASS

- [ ] **Step 6: Verifier la syntaxe de l'ensemble du fichier**

Run: `python -m py_compile hub_deals_db.py`
Expected: aucune sortie, code de retour 0

- [ ] **Step 7: Commit**

```bash
git add hub_deals_db.py tests/test_hub_deals_db.py
git commit -m "Ajoute Abidjan comme 2e ville de depart"
```

---

## Task 2: Documentation + verification en conditions reelles

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `hub_deals_AUDIT.md`

**Interfaces:**
- Consumes: Task 1 (config `RABATTEMENT` complete, tests verts)
- Produces: rien de nouveau cote code — documentation a jour + confirmation manuelle que le comportement fonctionne contre la vraie base et la vraie tache planifiee.

- [ ] **Step 1: Mettre a jour `README.md`**

Dans la section "Principe", remplacer la phrase :

```
Seule **Dakar** est active pour l'instant (une seule ville dans `RABATTEMENT`) ; ajouter une nouvelle ville de départ se fait en ajoutant une entrée à ce dictionnaire, sans autre changement de code.
```

par :

```
**Dakar** et **Abidjan** sont actives (deux villes dans `RABATTEMENT`) ; ajouter une nouvelle ville de départ se fait en ajoutant une entrée à ce dictionnaire, sans autre changement de code. Abidjan étant elle-même l'un des 6 hubs surveillés, elle n'a pas d'entrée de rabattement vers elle-même (`ABJ`), et son entrée `ADD` est omise faute de données API disponibles pour cette route.
```

- [ ] **Step 2: Mettre a jour `CHANGELOG.md`**

Ajouter en haut du fichier, avant la section `## 2026-08-03` existante, une nouvelle section datee (utiliser la date du jour reelle au moment de l'execution) :

```markdown
## 2026-08-03 (Abidjan)

### Ajouté
- `RABATTEMENT["Abidjan"]` : deuxième ville de départ active, 4 hubs (CMN, CDG, IST, NBO) — coûts obtenus par requête directe à l'API Travelpayouts (`v1/prices/cheap`, complété par `v3/prices_for_dates` pour NBO), contrairement à Dakar (estimation manuelle). `ADD` omis (aucune donnée API disponible pour cette route), pas d'entrée `ABJ` (Abidjan est déjà le hub)
- `tests/test_hub_deals_db.py` : test de garde-fou vérifiant les clés et les valeurs de `RABATTEMENT["Abidjan"]`

```

(Note : si une entree `## 2026-08-03` datee du meme jour existe deja plus bas dans le fichier au moment de l'execution, adapter le titre pour eviter l'ambiguite, par exemple en fusionnant dans la section existante plutot qu'en creant un doublon de date.)

- [ ] **Step 3: Ajouter une section a `hub_deals_AUDIT.md`**

Ajouter a la fin du fichier une section documentant :
- Les 3 endpoints Travelpayouts testes (`v1/prices/cheap`, `v2/prices/latest`, `v3/prices_for_dates`) et pourquoi `v1/prices/cheap` a ete retenu comme methode principale (une requete par hub, cible sur une paire origine/destination), avec `v3/prices_for_dates` en repli pour NBO.
- Le tableau des valeurs obtenues (CMN 563€/3h, CDG 511€/8h, IST 700€/9h, NBO 374€/8h) et la date de la requete (2026-08-03).
- La decision d'omettre ADD (aucune donnee sur les 3 endpoints testes) et ABJ (rabattement vers soi-meme n'a pas de sens).
- Le resultat de la verification en conditions reelles du Step 5 ci-dessous (nombre de lignes avant/apres, `ville_depart` distinctes, resultat de la tache planifiee).

- [ ] **Step 4: Lancer le script contre la vraie base**

Run (depuis `C:\Users\Dell\hub_deals`, avec `TRAVELPAYOUTS_TOKEN` deja en variable d'environnement utilisateur) :
```bash
python hub_deals_db.py
```
Expected: le log se termine par `=== Fin d'execution ===` sans ligne `ERREUR`.

Verifier avec :
```bash
python -c "import sqlite3; c = sqlite3.connect('flight_deals.db'); print(c.execute(\"SELECT DISTINCT ville_depart FROM offres\").fetchall())"
```
Expected: `[('Dakar',), ('Abidjan',)]` (les deux villes desormais presentes, dans un ordre quelconque).

- [ ] **Step 5: Verifier `detect_anomalies.py` sans erreur**

Run:
```bash
python detect_anomalies.py
```
Expected: s'execute sans erreur ; si des entrees sont affichees (mode diagnostic ou anomalies), certaines portent `"ville_depart": "Abidjan"`.

- [ ] **Step 6: Redeclencher la tache planifiee "Traqueur de vols"**

Utiliser PowerShell :
```powershell
Start-ScheduledTask -TaskName "Traqueur de vols"
```
Attendre ~60 secondes (delai de 30s au demarrage du script + appels API), puis :
```powershell
Get-ScheduledTaskInfo -TaskName "Traqueur de vols" | Format-List LastTaskResult
```
Expected: `LastTaskResult : 0`

- [ ] **Step 7: Completer la section de `hub_deals_AUDIT.md` avec les resultats reels**

Reprendre la section ecrite au Step 3 et y ajouter les chiffres reels obtenus aux Steps 4-6 (nombre de lignes avant/apres, resultat `LastTaskResult`).

- [ ] **Step 8: Commit et push final**

```bash
git add README.md CHANGELOG.md hub_deals_AUDIT.md
git commit -m "Documente Abidjan comme 2e ville de depart"
git push
```
