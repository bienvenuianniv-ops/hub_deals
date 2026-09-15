# Bot Telegram multi-abonnés (test privé) — plan d'implémentation

> **Pour les agents :** SOUS-COMPÉTENCE REQUISE — utiliser `superpowers:subagent-driven-development`
> (recommandé) ou `superpowers:executing-plans` pour exécuter ce plan tâche par tâche. Les étapes
> utilisent des cases à cocher (`- [ ]`) pour le suivi.

**Objectif :** permettre à des invités de s'abonner au bot `@ianniv_vols_bot` avec un code
d'invitation, de choisir leur ville de départ, et de recevoir chaque jour les affaires de cette
ville, avec des liens affiliés Travelpayouts.

**Architecture :** trois unités. `abonnes.py` porte les données des abonnés, le filtrage par
ville, la composition du message d'abonné, la boucle d'envoi et le témoin d'écoute — sans réseau
(l'envoi est injecté). `bot_ecoute.py` est un programme permanent qui lit `getUpdates` et traite
les commandes (`traiter_update` est pur hors base, la boucle réseau est mince). `hub_deals_db.py`
gagne des liens affiliés, un pied de message dans `decouper_message`, un envoi à un `chat_id`
quelconque avec statut détaillé, et appelle `abonnes.py` **après** le message du propriétaire.

**Pile technique :** Python 3.14, `requests`, `sqlite3`, `unittest` (bibliothèque standard),
Planificateur de tâches Windows. Aucune nouvelle dépendance.

**Spec :** `docs/superpowers/specs/2026-09-15-bot-abonnes-design.md`

## Contraintes globales

- **Répertoire de travail : `C:\Users\Dell\hub_deals_bot`, branche `bot-abonnes`.** Avant chaque
  commit, exécuter `git branch --show-current` (doit afficher `bot-abonnes`) et
  `git rev-parse --show-toplevel` (doit afficher `C:/Users/Dell/hub_deals_bot`). Ne **jamais**
  modifier ni commiter dans `C:\Users\Dell\hub_deals` : le relevé quotidien de 13h y tourne.
- **Aucun appel réseau dans les tests.** Envois, `getUpdates` et `mesurer_rabattements` sont
  remplacés par des faux. Suite actuelle : **174 tests en ~3 s** ; si la durée bondit, chercher
  l'appel réseau involontaire.
- **Toute fonction qui appelle `log()` voit `log` neutralisé dans ses tests** (modèle :
  `hub_deals_db.log = self.lignes.append` dans `setUp`, restauré dans `tearDown`). Sinon le
  journal d'exploitation reçoit des lignes simulées.
- **Les tests ne dépendent pas de l'environnement de la machine :** toute variable lue au
  chargement (`TRAVELPAYOUTS_MARKER`, `TELEGRAM_CHAT_ID`, `HUB_DEALS_CODE_INVITATION`…) est
  fixée explicitement dans le test qui en dépend.
- **Ne jamais recopier une valeur de `RABATTEMENT`** ni la liste des villes dans un test : les
  lire depuis `hub_deals_db.RABATTEMENT` / `abonnes.NOMS_AFFICHES`.
- **`anomaly_detection.py` et la table `RABATTEMENT` ne sont pas modifiés.**
- **Code Python :** commentaires et messages de journal en français **sans accents**, comme le
  reste du projet. **Exception :** les textes envoyés aux abonnés dans `abonnes.py` et
  `bot_ecoute.py` portent leurs accents et le signe `€` **en littéral UTF-8**. Dans
  `hub_deals_db.py`, garder la forme existante `\u20ac`.
- **Les secrets ne doivent atteindre aucune sortie.** `hub_deals_db.log()` masque les tokens ;
  `bot_ecoute.log()` masque en plus le code d'invitation. Le texte brut des messages reçus n'est
  jamais journalisé (données personnelles, et `/start CODE` contient le code).
- **Le message du propriétaire part toujours en premier**, et aucune erreur côté abonnés ne peut
  l'empêcher ni interrompre le relevé.
- Suite verte à chaque commit : `python -m unittest discover -s tests`.
- Fins de ligne : le dépôt normalise en LF (`.gitattributes`). Après tout remplacement de texte
  par script, vérifier par `assert` que le remplacement a eu lieu.

## Fichiers

| Fichier | Rôle |
|---|---|
| `hub_deals_db.py` (modifié) | `TRAVELPAYOUTS_MARKER`, `url_aviasales()`, `pied` dans `decouper_message()`, `envoyer_telegram_a()`, appel des abonnés, contrôle de l'écoute, `timeout=30` |
| `abonnes.py` (créé) | tables, inscriptions, filtrage, message d'abonné, boucle d'envoi, témoin d'écoute |
| `bot_ecoute.py` (créé) | commandes Telegram, boucle `getUpdates`, point d'entrée sous `pythonw` |
| `tests/test_liens_affilies.py` (créé) | tâches 1 et 2 |
| `tests/test_envoi_abonne.py` (créé) | tâche 3 |
| `tests/test_abonnes.py` (créé) | tâches 4 à 8 |
| `tests/test_bot_ecoute.py` (créé) | tâches 9 et 10 |
| `taches/bot_ecoute.xml`, `taches/installer_bot_ecoute.ps1` (créés) | tâche 11 |
| `.gitignore`, `README.md`, `CHANGELOG.md` (modifiés) | tâche 11 |

---

### Tâche 1 : liens affiliés

**Fichiers :**
- Modifier : `hub_deals_db.py` (constantes en tête, `construire_bloc`, `verifier_et_notifier_anomalies`)
- Créer : `tests/test_liens_affilies.py`

**Interfaces :**
- Produit : `hub_deals_db.TRAVELPAYOUTS_MARKER: str | None` ;
  `hub_deals_db.url_aviasales(chemin: str, etiquette: str) -> str`.

- [ ] **Étape 1 : établir la syntaxe du paramètre affilié**

La spec signale que la syntaxe n'est pas vérifiée. Chercher dans l'aide Travelpayouts
(« SubID », « marker », lien Aviasales) la forme attendue d'un lien de recherche
`https://www.aviasales.com/search/...` portant l'identifiant d'affilié et une étiquette.
Hypothèse de départ : `?marker=<MARKER>.<etiquette>`. Si la documentation indique une autre
forme, l'appliquer **uniquement** dans `url_aviasales()` et dans le test de l'étape 2.
Consigner la source consultée dans la docstring. Si aucune source n'est lisible, garder
l'hypothèse et le signaler dans la docstring : la tâche 12 la tranchera par un clic réel.

- [ ] **Étape 2 : écrire les tests qui échouent**

```python
"""Liens affiliés Travelpayouts et pied de message."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db


class TestUrlAviasales(unittest.TestCase):
    """Sans identifiant d'affilie, aucun clic ne rapporte ni ne se mesure
    (constat du 2026-09-15 : aucun lien envoye n'en portait)."""

    def setUp(self):
        self._marker = hub_deals_db.TRAVELPAYOUTS_MARKER

    def tearDown(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = self._marker

    def test_le_lien_porte_le_marker_et_l_etiquette(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = "123456"
        url = hub_deals_db.url_aviasales("/search/ABJ0302SAO1", "dakar")
        self.assertEqual(
            url, "https://www.aviasales.com/search/ABJ0302SAO1?marker=123456.dakar")

    def test_sans_marker_le_lien_reste_celui_d_aujourd_hui(self):
        """L'absence de marker ne doit jamais bloquer une alerte."""
        hub_deals_db.TRAVELPAYOUTS_MARKER = None
        url = hub_deals_db.url_aviasales("/search/ABJ0302SAO1", "dakar")
        self.assertEqual(url, "https://www.aviasales.com/search/ABJ0302SAO1")

    def test_le_bloc_du_proprietaire_porte_l_etiquette_proprietaire(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = "123456"
        a = {"destination": "Rome", "destination_code": "ROM", "hub": "Abidjan",
             "ville_depart": "Dakar", "prix_actuel": 975.0,
             "moyenne_historique": 1419.0, "baisse_pct": 31.3, "economie": 444.0,
             "rabattement_mesure": None, "lien": "/search/ABJ0511ROM1"}
        for groupe in ([a], [a, dict(a, ville_depart="Lome")]):
            bloc = hub_deals_db.construire_bloc(groupe)
            self.assertIn("?marker=123456.proprietaire", bloc)


class TestMarkerAbsentJournalise(unittest.TestCase):
    def setUp(self):
        self._marker = hub_deals_db.TRAVELPAYOUTS_MARKER
        self._log = hub_deals_db.log
        self._envoyer = hub_deals_db.envoyer_telegram
        self._mesurer = hub_deals_db.mesurer_rabattements
        self._detecter = hub_deals_db.detecter_anomalies
        self.lignes = []
        hub_deals_db.log = self.lignes.append
        hub_deals_db.envoyer_telegram = lambda msg: True
        hub_deals_db.mesurer_rabattements = lambda couples: {}
        hub_deals_db.detecter_anomalies = lambda conn, date_collecte=None: [{
            "destination": "Rome", "hub": "Abidjan", "ville_depart": "Dakar",
            "prix_actuel": 900.0, "moyenne_historique": 1000.0, "baisse_pct": 10.0,
            "economie": 100.0, "rabattement_mesure": None, "lien": "/search/X1"}]

    def tearDown(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = self._marker
        hub_deals_db.log = self._log
        hub_deals_db.envoyer_telegram = self._envoyer
        hub_deals_db.mesurer_rabattements = self._mesurer
        hub_deals_db.detecter_anomalies = self._detecter

    def test_un_marker_absent_est_signale_au_journal(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = None
        hub_deals_db.verifier_et_notifier_anomalies(None, "2026-09-15")
        self.assertIn("TRAVELPAYOUTS_MARKER", "\n".join(self.lignes))

    def test_un_marker_present_n_est_pas_signale(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = "123456"
        hub_deals_db.verifier_et_notifier_anomalies(None, "2026-09-15")
        self.assertNotIn("TRAVELPAYOUTS_MARKER", "\n".join(self.lignes))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 3 : vérifier l'échec**

Run: `python -m unittest tests.test_liens_affilies -v`
Expected: ERROR `AttributeError: module 'hub_deals_db' has no attribute 'TRAVELPAYOUTS_MARKER'` /
`url_aviasales`.

- [ ] **Étape 4 : implémenter**

Sous `TELEGRAM_CHAT_ID = ...` :

```python
# Identifiant d'affilie Travelpayouts. Absent : liens sans parametres
# affilies (comportement d'avant le 2026-09-15), jamais de blocage.
TRAVELPAYOUTS_MARKER = os.environ.get("TRAVELPAYOUTS_MARKER")
```

Juste après `construire_lien()` :

```python
def url_aviasales(chemin: str, etiquette: str) -> str:
    """
    Lien complet vers Aviasales, avec l'identifiant d'affilie et une
    etiquette (sub_id) qui separe les clics par destinataire dans le
    tableau de bord Travelpayouts : une ville par abonne, 'proprietaire'
    pour le message complet.

    Source de la syntaxe : <renseigner a l'etape 1>.
    """
    url = f"https://www.aviasales.com{chemin}"
    if not TRAVELPAYOUTS_MARKER:
        return url
    return f"{url}?marker={TRAVELPAYOUTS_MARKER}.{etiquette}"
```

L'étape 1 remplace `<renseigner a l'etape 1>` par l'URL ou le titre de la page consultée (ou
« non verifiee, tranchee par clic reel en tache 12 »). Ce n'est pas un espace réservé laissé
au code final : la docstring doit être complétée avant le commit.

Dans `construire_bloc`, remplacer :

```python
    lien = f"https://www.aviasales.com{premier['lien']}"
```

par :

```python
    lien = url_aviasales(premier["lien"], "proprietaire")
```

Dans `verifier_et_notifier_anomalies`, juste après `groupes = grouper_anomalies(anomalies)` et
son `log(...)` :

```python
    if not TRAVELPAYOUTS_MARKER:
        log("   -> liens sans identifiant d'affilie (TRAVELPAYOUTS_MARKER absent)")
```

- [ ] **Étape 5 : vérifier le vert**

Run: `python -m unittest discover -s tests`
Expected: `OK`, 179 tests.

- [ ] **Étape 6 : commit**

```bash
git branch --show-current && git rev-parse --show-toplevel
git add hub_deals_db.py tests/test_liens_affilies.py
git commit -m "Liens Aviasales avec identifiant d'affilie et etiquette de destinataire"
```

---

### Tâche 2 : pied de message dans `decouper_message`

La mention « prix repéré, à vérifier » doit figurer dans **chaque** morceau d'un message
d'abonné, sans faire dépasser la limite de 4096 caractères.

**Fichiers :**
- Modifier : `hub_deals_db.py` (`decouper_message`)
- Modifier : `tests/test_liens_affilies.py`

**Interfaces :**
- Produit : `hub_deals_db.decouper_message(blocs: list, entete: str, pied: str = "") -> list[str]`.
  Sans `pied`, comportement strictement identique à aujourd'hui.

- [ ] **Étape 1 : écrire les tests qui échouent** (ajouter avant `if __name__`)

```python
class TestPiedDeMessage(unittest.TestCase):
    PIED = "<i>Prix repéré aujourd'hui, il peut avoir changé : vérifie avant de réserver.</i>"

    def _blocs(self, n=72, taille=140):
        return ["<b>destination %d</b>\n%s" % (i, "x" * taille) for i in range(n)]

    def test_chaque_morceau_se_termine_par_le_pied(self):
        morceaux = hub_deals_db.decouper_message(self._blocs(), "<b>entete</b>", self.PIED)
        self.assertGreater(len(morceaux), 1)
        for m in morceaux:
            self.assertTrue(m.endswith("\n" + self.PIED))

    def test_le_pied_ne_fait_pas_depasser_la_limite(self):
        """Blocs calibres pour remplir le budget d'origine au caractere pres :
        sans reserve pour le pied, au moins un morceau deborderait."""
        entete = "<b>entete</b>"
        budget = hub_deals_db.LIMITE_TELEGRAM - len(entete) - 16
        blocs = ["y" * 99] * (budget // 100) * 3
        morceaux = hub_deals_db.decouper_message(blocs, entete, self.PIED)
        for m in morceaux:
            self.assertLessEqual(len(m), hub_deals_db.LIMITE_TELEGRAM)

    def test_sans_pied_rien_ne_change(self):
        blocs = self._blocs()
        self.assertEqual(hub_deals_db.decouper_message(blocs, "<b>e</b>"),
                         hub_deals_db.decouper_message(blocs, "<b>e</b>", ""))
```

- [ ] **Étape 2 : vérifier l'échec**

Run: `python -m unittest tests.test_liens_affilies.TestPiedDeMessage -v`
Expected: ERROR `TypeError: decouper_message() takes 2 positional arguments but 3 were given`.

- [ ] **Étape 3 : implémenter**

Signature : `def decouper_message(blocs: list, entete: str, pied: str = "") -> list:`

Ajouter à la docstring :

```
    Un pied (mention a repeter sous chaque morceau) peut etre fourni : sa
    place est retiree du budget avant la repartition, sinon il ferait
    deborder les morceaux pleins.
```

Remplacer la ligne du budget par :

```python
    budget = LIMITE_TELEGRAM - len(entete) - RESERVE_NUMEROTATION
    if pied:
        budget -= len(pied) + 1  # +1 pour le "\n" qui le precede
```

Remplacer le `return` final par :

```python
    suffixe = f"\n{pied}" if pied else ""
    return [
        "\n".join([entete if nb == 1 else f"{entete} ({i}/{nb})"] + groupe) + suffixe
        for i, groupe in enumerate(groupes, start=1)
    ]
```

- [ ] **Étape 4 : vérifier le vert**

Run: `python -m unittest discover -s tests`
Expected: `OK`, 182 tests.

- [ ] **Étape 5 : commit**

```bash
git branch --show-current && git rev-parse --show-toplevel
git add hub_deals_db.py tests/test_liens_affilies.py
git commit -m "decouper_message : pied repete sous chaque morceau, dans la limite Telegram"
```

---

### Tâche 3 : envoi à un `chat_id` avec statut détaillé

**Fichiers :**
- Modifier : `hub_deals_db.py` (`envoyer_telegram`, nouvelle `envoyer_telegram_a`)
- Créer : `tests/test_envoi_abonne.py`

**Interfaces :**
- Produit : `hub_deals_db.envoyer_telegram_a(chat_id, message: str, journaliser: bool = True) -> tuple[str, object]`.
  Premier élément parmi `"ok"`, `"bloque"` (HTTP 403), `"trop_vite"` (HTTP 429, second élément =
  `retry_after` en secondes, entier), `"echec"` (second élément = texte court : `"HTTP 400"`,
  `"reseau"`, `"non configure"`). Pour `"ok"` et `"bloque"`, second élément `None`.
- Conserve : `hub_deals_db.envoyer_telegram(message: str) -> bool`, même contrat, mêmes lignes de
  journal qu'aujourd'hui.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
"""Envoi Telegram a un destinataire quelconque, avec statut exploitable."""

import os
import sys
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db


class _Reponse:
    def __init__(self, status_code=200, text='{"ok":true}'):
        self.status_code = status_code
        self.text = text


class TestEnvoyerTelegramA(unittest.TestCase):
    def setUp(self):
        self._log = hub_deals_db.log
        self._post = hub_deals_db.requests.post
        self._bot = hub_deals_db.TELEGRAM_BOT_TOKEN
        self.lignes = []
        self.appels = []
        hub_deals_db.log = self.lignes.append
        hub_deals_db.TELEGRAM_BOT_TOKEN = "bot-factice"

    def tearDown(self):
        hub_deals_db.log = self._log
        hub_deals_db.requests.post = self._post
        hub_deals_db.TELEGRAM_BOT_TOKEN = self._bot

    def _repondre(self, reponse):
        def poster(url, data=None, timeout=None):
            self.appels.append(data)
            return reponse
        hub_deals_db.requests.post = poster

    def test_succes(self):
        self._repondre(_Reponse())
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"), ("ok", None))
        self.assertEqual(self.appels[0]["chat_id"], 777)

    def test_bot_bloque_par_l_abonne(self):
        self._repondre(_Reponse(403, '{"ok":false,"error_code":403,'
                                     '"description":"Forbidden: bot was blocked by the user"}'))
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"), ("bloque", None))

    def test_trop_de_requetes_renvoie_le_delai(self):
        self._repondre(_Reponse(429, '{"ok":false,"error_code":429,'
                                     '"parameters":{"retry_after":7}}'))
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"), ("trop_vite", 7))

    def test_429_sans_delai_lisible_attend_une_seconde(self):
        self._repondre(_Reponse(429, "pas du json"))
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"), ("trop_vite", 1))

    def test_autre_refus(self):
        self._repondre(_Reponse(400, '{"ok":false}'))
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"), ("echec", "HTTP 400"))

    def test_erreur_reseau(self):
        def poster(*a, **k):
            raise requests.exceptions.RequestException("coupure")
        hub_deals_db.requests.post = poster
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"), ("echec", "reseau"))

    def test_bot_non_configure(self):
        hub_deals_db.TELEGRAM_BOT_TOKEN = None
        self.assertEqual(hub_deals_db.envoyer_telegram_a(777, "x"),
                         ("echec", "non configure"))

    def test_sans_journalisation_le_corps_n_est_pas_ecrit(self):
        """Les messages d'abonnes ne sont pas recopies au journal : 30
        abonnes par jour le feraient enfler sans rien apprendre de plus
        que le compte rendu."""
        self._repondre(_Reponse())
        hub_deals_db.envoyer_telegram_a(777, "CORPS-UNIQUE", journaliser=False)
        self.assertNotIn("CORPS-UNIQUE", "\n".join(self.lignes))

    def test_envoyer_telegram_vise_toujours_le_proprietaire(self):
        chat = hub_deals_db.TELEGRAM_CHAT_ID
        hub_deals_db.TELEGRAM_CHAT_ID = "12345"
        try:
            self._repondre(_Reponse())
            self.assertIs(hub_deals_db.envoyer_telegram("x"), True)
            self.assertEqual(self.appels[0]["chat_id"], "12345")
        finally:
            hub_deals_db.TELEGRAM_CHAT_ID = chat


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 2 : vérifier l'échec**

Run: `python -m unittest tests.test_envoi_abonne -v`
Expected: ERROR `AttributeError: ... no attribute 'envoyer_telegram_a'`.

- [ ] **Étape 3 : implémenter**

Remplacer **tout le corps** de `envoyer_telegram` (docstring conservée) et ajouter
`envoyer_telegram_a` juste au-dessus :

```python
def envoyer_telegram_a(chat_id, message: str, journaliser: bool = True) -> tuple:
    """Envoie un message a un destinataire quelconque et renvoie un statut
    que l'appelant peut exploiter : ('ok', None), ('bloque', None) sur 403
    (l'abonne a bloque le bot), ('trop_vite', secondes) sur 429, ou
    ('echec', raison courte).

    journaliser=False : le corps n'est pas recopie au journal (messages
    d'abonnes). Les erreurs, elles, sont toujours journalisees.
    """
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        if journaliser:
            journaliser_message(
                message, "message NON envoye (Telegram non configure)")
        return ("echec", "non configure")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        reponse = requests.post(url, data={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=15)
    except requests.exceptions.RequestException as e:
        log(f"   -> ERREUR envoi Telegram : {e}")
        if journaliser:
            journaliser_message(message, "message Telegram NON parti (erreur reseau)")
        return ("echec", "reseau")

    if reponse.status_code == 403:
        return ("bloque", None)

    if reponse.status_code == 429:
        try:
            delai = int(json.loads(reponse.text)["parameters"]["retry_after"])
        except (ValueError, KeyError, TypeError):
            delai = 1
        return ("trop_vite", delai)

    if reponse.status_code != 200:
        log(f"   -> ECHEC Telegram : HTTP {reponse.status_code} {reponse.text[:200]}")
        if journaliser:
            journaliser_message(message, "message Telegram REFUSE")
        return ("echec", f"HTTP {reponse.status_code}")

    if journaliser:
        journaliser_message(message, "message Telegram envoye")
    return ("ok", None)
```

Corps de `envoyer_telegram` (sous sa docstring actuelle, inchangée) :

```python
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        journaliser_message(
            message, "message NON envoye (Telegram non configure)")
        return False
    statut, _ = envoyer_telegram_a(TELEGRAM_CHAT_ID, message)
    if statut in ("bloque", "trop_vite"):
        # le proprietaire ne bloque pas son propre bot : ces cas restent
        # des echecs, et doivent se lire comme tels au journal
        log(f"   -> ECHEC Telegram : {statut}")
        journaliser_message(message, "message Telegram REFUSE")
    return statut == "ok"
```

- [ ] **Étape 4 : vérifier le vert**

Run: `python -m unittest discover -s tests`
Expected: `OK`, 191 tests — dont les tests existants de `TestEnvoiRendCompteDeSonResultat` et
`TestJournalisationDuMessageTelegram`, inchangés.

- [ ] **Étape 5 : commit**

```bash
git branch --show-current && git rev-parse --show-toplevel
git add hub_deals_db.py tests/test_envoi_abonne.py
git commit -m "envoyer_telegram_a : envoi a un chat_id avec statut (403, 429, echec)"
```

---

### Tâche 4 : données des abonnés

**Fichiers :**
- Créer : `abonnes.py`
- Créer : `tests/test_abonnes.py`

**Interfaces :**
- Produit (module `abonnes`) :
  - `NOMS_AFFICHES: dict[str, str]` — clé de `RABATTEMENT` → nom affiché.
  - `PLAFOND_ABONNES = 50`
  - `maintenant() -> str` — UTC ISO, `"2026-09-15T12:00:00+00:00"`.
  - `init_abonnes(conn) -> None`
  - `trouver(conn, chat_id) -> dict | None` — clés `chat_id, prenom, ville_depart, actif (bool), inscrit_le, modifie_le, motif_inactif`.
  - `nb_actifs(conn) -> int`
  - `inscrire(conn, chat_id, prenom, quand: str) -> None` — crée, ou réactive en gardant la ville.
  - `choisir_ville(conn, chat_id, ville: str, quand: str) -> None` — lève `ValueError` si ville inconnue.
  - `desactiver(conn, chat_id, motif: str, quand: str) -> None`
  - `abonnes_a_servir(conn, exclure_chat_id=None) -> list[dict]` — actifs avec ville, triés par `chat_id`.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
"""Abonnes au bot : donnees, filtrage, message, envoi, temoin d'ecoute."""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import hub_deals_db

T0 = "2026-09-15T12:00:00+00:00"
T1 = "2026-09-15T12:05:00+00:00"


def _base():
    conn = sqlite3.connect(":memory:")
    abonnes.init_abonnes(conn)
    return conn


class TestStructure(unittest.TestCase):
    def test_les_villes_proposees_sont_exactement_celles_de_rabattement(self):
        """Une ville proposee sans entree dans RABATTEMENT ne recevrait
        jamais rien ; une ville de RABATTEMENT absente serait inaccessible."""
        self.assertEqual(set(abonnes.NOMS_AFFICHES), set(hub_deals_db.RABATTEMENT))


class TestDonneesAbonnes(unittest.TestCase):
    def setUp(self):
        self.conn = _base()

    def tearDown(self):
        self.conn.close()

    def test_init_est_idempotent(self):
        abonnes.init_abonnes(self.conn)
        abonnes.init_abonnes(self.conn)

    def test_inscription_puis_lecture(self):
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        a = abonnes.trouver(self.conn, 111)
        self.assertEqual(a["prenom"], "Awa")
        self.assertIs(a["actif"], True)
        self.assertIsNone(a["ville_depart"])
        self.assertEqual(a["inscrit_le"], T0)

    def test_inconnu(self):
        self.assertIsNone(abonnes.trouver(self.conn, 999))

    def test_choix_de_ville(self):
        ville = sorted(abonnes.NOMS_AFFICHES)[0]
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        abonnes.choisir_ville(self.conn, 111, ville, T1)
        a = abonnes.trouver(self.conn, 111)
        self.assertEqual(a["ville_depart"], ville)
        self.assertEqual(a["modifie_le"], T1)

    def test_ville_inconnue_refusee(self):
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        with self.assertRaises(ValueError):
            abonnes.choisir_ville(self.conn, 111, "Atlantide", T1)

    def test_desactivation_conserve_la_ligne(self):
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        abonnes.desactiver(self.conn, 111, "stop", T1)
        a = abonnes.trouver(self.conn, 111)
        self.assertIs(a["actif"], False)
        self.assertEqual(a["motif_inactif"], "stop")

    def test_reinscription_reactive_et_garde_la_ville(self):
        ville = sorted(abonnes.NOMS_AFFICHES)[0]
        abonnes.inscrire(self.conn, 111, "Awa", T0)
        abonnes.choisir_ville(self.conn, 111, ville, T0)
        abonnes.desactiver(self.conn, 111, "bloque", T0)
        abonnes.inscrire(self.conn, 111, "Awa", T1)
        a = abonnes.trouver(self.conn, 111)
        self.assertIs(a["actif"], True)
        self.assertIsNone(a["motif_inactif"])
        self.assertEqual(a["ville_depart"], ville)
        self.assertEqual(a["inscrit_le"], T0)

    def test_nb_actifs(self):
        abonnes.inscrire(self.conn, 1, "a", T0)
        abonnes.inscrire(self.conn, 2, "b", T0)
        abonnes.desactiver(self.conn, 2, "stop", T0)
        self.assertEqual(abonnes.nb_actifs(self.conn), 1)

    def test_abonnes_a_servir_ignore_inactifs_et_sans_ville(self):
        ville = sorted(abonnes.NOMS_AFFICHES)[0]
        for chat_id in (1, 2, 3):
            abonnes.inscrire(self.conn, chat_id, "x", T0)
        abonnes.choisir_ville(self.conn, 1, ville, T0)
        abonnes.choisir_ville(self.conn, 2, ville, T0)
        abonnes.desactiver(self.conn, 2, "stop", T0)
        # 3 n'a pas choisi de ville
        self.assertEqual([a["chat_id"] for a in abonnes.abonnes_a_servir(self.conn)], [1])

    def test_abonnes_a_servir_exclut_le_proprietaire(self):
        """Le proprietaire recoit deja le message complet : pas de doublon.
        TELEGRAM_CHAT_ID est une chaine, chat_id un entier."""
        ville = sorted(abonnes.NOMS_AFFICHES)[0]
        for chat_id in (1, 2):
            abonnes.inscrire(self.conn, chat_id, "x", T0)
            abonnes.choisir_ville(self.conn, chat_id, ville, T0)
        servis = abonnes.abonnes_a_servir(self.conn, exclure_chat_id="2")
        self.assertEqual([a["chat_id"] for a in servis], [1])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 2 : vérifier l'échec**

Run: `python -m unittest tests.test_abonnes -v`
Expected: ERROR `ModuleNotFoundError: No module named 'abonnes'`.

- [ ] **Étape 3 : implémenter `abonnes.py`**

```python
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
```

- [ ] **Étape 4 : vérifier le vert**

Run: `python -m unittest discover -s tests`
Expected: `OK`, 202 tests.

- [ ] **Étape 5 : commit**

```bash
git branch --show-current && git rev-parse --show-toplevel
git add abonnes.py tests/test_abonnes.py
git commit -m "abonnes.py : tables et operations sur les abonnes du bot"
```

---

### Tâche 5 : filtrage par ville et message d'abonné

**Fichiers :**
- Modifier : `abonnes.py`
- Modifier : `tests/test_abonnes.py`

**Interfaces :**
- Consomme : `hub_deals_db.url_aviasales`, `hub_deals_db.decouper_message(blocs, entete, pied)`.
- Produit :
  - `MENTION_PRIX = "<i>Prix repéré aujourd'hui, il peut avoir changé : vérifie avant de réserver.</i>"`
  - `filtrer_groupes(groupes: list[list[dict]], ville: str) -> list[list[dict]]` — chaque groupe réduit aux anomalies de `ville`, groupes vides retirés, ordre conservé.
  - `messages_abonne(groupes: list[list[dict]], ville: str) -> list[str]` — morceaux prêts à envoyer ; `[]` si aucune affaire.

Rappel du contenu d'une anomalie (produite par `corriger_anomalies` puis regroupée par
`grouper_anomalies`) : `destination`, `hub`, `ville_depart`, `prix_actuel`, `baisse_pct`,
`economie`, `rabattement_mesure`, `lien` (chemin `/search/...`).

- [ ] **Étape 1 : écrire les tests qui échouent** (ajouter avant `if __name__`)

```python
def _anomalie(ville, dest="Sao Paulo", hub="Abidjan", prix=1716.0, baisse=23.4,
              economie=500.0, lien="/search/ABJ0302SAO1", mesure=385.0):
    return {"destination": dest, "hub": hub, "ville_depart": ville,
            "prix_actuel": prix, "moyenne_historique": prix + economie,
            "baisse_pct": baisse, "economie": economie,
            "rabattement_mesure": mesure, "lien": lien}


class TestMessageAbonne(unittest.TestCase):
    def setUp(self):
        self._marker = hub_deals_db.TRAVELPAYOUTS_MARKER
        hub_deals_db.TRAVELPAYOUTS_MARKER = "123456"
        villes = sorted(abonnes.NOMS_AFFICHES)
        self.v1, self.v2 = villes[0], villes[1]

    def tearDown(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = self._marker

    def test_filtre_un_groupe_multi_villes_a_la_bonne_ligne(self):
        groupes = [[_anomalie(self.v1, prix=1716), _anomalie(self.v2, prix=2327)]]
        filtres = abonnes.filtrer_groupes(groupes, self.v2)
        self.assertEqual(len(filtres), 1)
        self.assertEqual([a["ville_depart"] for a in filtres[0]], [self.v2])

    def test_retire_les_groupes_sans_la_ville(self):
        groupes = [[_anomalie(self.v1)], [_anomalie(self.v2, dest="Rome", lien="/r")]]
        filtres = abonnes.filtrer_groupes(groupes, self.v1)
        self.assertEqual([g[0]["destination"] for g in filtres], ["Sao Paulo"])

    def test_aucune_affaire_aucun_message(self):
        self.assertEqual(abonnes.messages_abonne([[_anomalie(self.v1)]], self.v2), [])

    def test_contenu_du_message(self):
        [m] = abonnes.messages_abonne([[_anomalie(self.v1)]], self.v1)
        self.assertIn(f"<b>1 bonne affaire au départ de {abonnes.NOMS_AFFICHES[self.v1]}</b>", m)
        self.assertIn("<b>Sao Paulo</b> via Abidjan", m)
        self.assertIn("1716€ (-23%)", m)
        self.assertIn("500€ de moins que d'habitude", m)
        self.assertIn(f"?marker=123456.{self.v1.lower()}", m)
        self.assertTrue(m.endswith(abonnes.MENTION_PRIX))

    def test_pluriel(self):
        groupes = [[_anomalie(self.v1)], [_anomalie(self.v1, dest="Rome", lien="/r")]]
        [m] = abonnes.messages_abonne(groupes, self.v1)
        self.assertIn("<b>2 bonnes affaires au départ de", m)

    def test_aucun_detail_de_rabattement(self):
        """Information technique reservee au proprietaire."""
        for mesure in (385.0, None):
            [m] = abonnes.messages_abonne([[_anomalie(self.v1, mesure=mesure)]], self.v1)
            self.assertNotIn("rabattement", m.lower())

    def test_la_mention_est_dans_chaque_morceau(self):
        groupes = [[_anomalie(self.v1, dest="Destination %d" % i, lien="/s%d" % i)]
                   for i in range(80)]
        morceaux = abonnes.messages_abonne(groupes, self.v1)
        self.assertGreater(len(morceaux), 1)
        for m in morceaux:
            self.assertIn(abonnes.MENTION_PRIX, m)
            self.assertLessEqual(len(m), hub_deals_db.LIMITE_TELEGRAM)
```

- [ ] **Étape 2 : vérifier l'échec**

Run: `python -m unittest tests.test_abonnes.TestMessageAbonne -v`
Expected: ERROR `AttributeError: module 'abonnes' has no attribute 'filtrer_groupes'`.

- [ ] **Étape 3 : implémenter** (ajouter à `abonnes.py` ; `import hub_deals_db` en tête, sous
l'import de `datetime`)

```python
# Les prix viennent d'un cache Aviasales (jusqu'a 7 jours) : les regles
# Travelpayouts interdisent de presenter une remise comme garantie.
MENTION_PRIX = "<i>Prix repéré aujourd'hui, il peut avoir changé : vérifie avant de réserver.</i>"


def filtrer_groupes(groupes: list, ville: str) -> list:
    """Reduit chaque groupe (une affaire hub -> destination) aux lignes de
    la ville de l'abonne ; les groupes qui n'en ont pas disparaissent."""
    filtres = []
    for groupe in groupes:
        garde = [a for a in groupe if a["ville_depart"] == ville]
        if garde:
            filtres.append(garde)
    return filtres


def _bloc_abonne(a: dict, ville: str) -> str:
    lien = hub_deals_db.url_aviasales(a["lien"], ville.lower())
    return (
        f"\n<b>{a['destination']}</b> via {a['hub']}\n"
        f"{a['prix_actuel']:.0f}€ (-{a['baisse_pct']:.0f}%) — "
        f"{a['economie']:.0f}€ de moins que d'habitude\n"
        f"{lien}"
    )


def messages_abonne(groupes: list, ville: str) -> list:
    """Morceaux de message a envoyer a un abonne de 'ville' ; [] si aucune
    affaire -- on n'envoie pas de « rien aujourd'hui », qui deviendrait un
    bruit qu'on cesse de lire."""
    filtres = filtrer_groupes(groupes, ville)
    if not filtres:
        return []
    n = len(filtres)
    affaires = "1 bonne affaire" if n == 1 else f"{n} bonnes affaires"
    entete = f"<b>{affaires} au départ de {NOMS_AFFICHES[ville]}</b>"
    # une ville n'a qu'une ligne par groupe : le premier element suffit
    blocs = [_bloc_abonne(g[0], ville) for g in filtres]
    return hub_deals_db.decouper_message(blocs, entete, "\n" + MENTION_PRIX)
```

Note : le pied passé est `"\n" + MENTION_PRIX` pour laisser une ligne vide avant la mention ;
le test `endswith(MENTION_PRIX)` reste vrai.

- [ ] **Étape 4 : vérifier le vert**

Run: `python -m unittest discover -s tests`
Expected: `OK`, 209 tests.

- [ ] **Étape 5 : commit**

```bash
git branch --show-current && git rev-parse --show-toplevel
git add abonnes.py tests/test_abonnes.py
git commit -m "abonnes.py : filtrage par ville et message d'abonne avec mention obligatoire"
```

---

### Tâche 6 : boucle d'envoi aux abonnés

**Fichiers :**
- Modifier : `abonnes.py`
- Modifier : `tests/test_abonnes.py`

**Interfaces :**
- Consomme : `abonnes_a_servir`, `messages_abonne`, `desactiver`, `maintenant` ; un `envoyer(chat_id, message) -> tuple[str, object]` au contrat de `hub_deals_db.envoyer_telegram_a` (appelé avec `journaliser=False` par l'appelant réel, voir tâche 7).
- Produit : `notifier_abonnes(conn, groupes, envoyer, log, exclure_chat_id=None, dormir=time.sleep, pause: float = 0.05) -> dict`
  avec les clés `envoyes: int`, `bloques: int`, `echecs: list[str]`, `sans_affaire: int`, `servis: int`.
  Écrit **une** ligne de compte rendu via `log`.

- [ ] **Étape 1 : écrire les tests qui échouent** (ajouter avant `if __name__`)

```python
class TestNotifierAbonnes(unittest.TestCase):
    def setUp(self):
        self.conn = _base()
        self._marker = hub_deals_db.TRAVELPAYOUTS_MARKER
        hub_deals_db.TRAVELPAYOUTS_MARKER = None
        villes = sorted(abonnes.NOMS_AFFICHES)
        self.v1, self.v2 = villes[0], villes[1]
        self.lignes = []
        self.envois = []
        self.pauses = []
        self.groupes = [[_anomalie(self.v1)]]

    def tearDown(self):
        hub_deals_db.TRAVELPAYOUTS_MARKER = self._marker
        self.conn.close()

    def _abonne(self, chat_id, ville):
        abonnes.inscrire(self.conn, chat_id, "x", T0)
        abonnes.choisir_ville(self.conn, chat_id, ville, T0)

    def _notifier(self, reponses=None, **kw):
        """reponses : chat_id -> liste de statuts renvoyes dans l'ordre."""
        reponses = reponses or {}

        def envoyer(chat_id, message):
            self.envois.append((chat_id, message))
            file = reponses.get(chat_id)
            return file.pop(0) if file else ("ok", None)

        return abonnes.notifier_abonnes(
            self.conn, self.groupes, envoyer, self.lignes.append,
            dormir=self.pauses.append, **kw)

    def test_seuls_les_abonnes_de_la_ville_recoivent(self):
        self._abonne(1, self.v1)
        self._abonne(2, self.v2)
        compte = self._notifier()
        self.assertEqual([c for c, _ in self.envois], [1])
        self.assertEqual(compte["envoyes"], 1)
        self.assertEqual(compte["sans_affaire"], 1)

    def test_le_proprietaire_n_est_pas_servi_en_double(self):
        self._abonne(1, self.v1)
        self._abonne(2, self.v1)
        self._notifier(exclure_chat_id="2")
        self.assertEqual([c for c, _ in self.envois], [1])

    def test_403_desactive_l_abonne(self):
        self._abonne(1, self.v1)
        compte = self._notifier({1: [("bloque", None)]})
        self.assertEqual(compte["bloques"], 1)
        a = abonnes.trouver(self.conn, 1)
        self.assertIs(a["actif"], False)
        self.assertEqual(a["motif_inactif"], "bloque")

    def test_429_attend_puis_un_seul_nouvel_essai(self):
        self._abonne(1, self.v1)
        compte = self._notifier({1: [("trop_vite", 7), ("trop_vite", 7)]})
        self.assertEqual(len(self.envois), 2)
        self.assertIn(7, self.pauses)
        self.assertEqual(compte["echecs"], ["trop de requetes"])

    def test_429_puis_succes(self):
        self._abonne(1, self.v1)
        compte = self._notifier({1: [("trop_vite", 3), ("ok", None)]})
        self.assertEqual(compte["envoyes"], 1)
        self.assertEqual(compte["echecs"], [])

    def test_un_echec_n_arrete_pas_les_suivants(self):
        self._abonne(1, self.v1)
        self._abonne(2, self.v1)
        compte = self._notifier({1: [("echec", "HTTP 400")]})
        self.assertEqual([c for c, _ in self.envois], [1, 2])
        self.assertEqual(compte["envoyes"], 1)
        self.assertEqual(compte["echecs"], ["HTTP 400"])

    def test_une_exception_inattendue_n_arrete_pas_les_suivants(self):
        self._abonne(1, self.v1)
        self._abonne(2, self.v1)

        def envoyer(chat_id, message):
            if chat_id == 1:
                raise RuntimeError("panne")
            self.envois.append((chat_id, message))
            return ("ok", None)

        compte = abonnes.notifier_abonnes(self.conn, self.groupes, envoyer,
                                          self.lignes.append, dormir=self.pauses.append)
        self.assertEqual([c for c, _ in self.envois], [2])
        self.assertEqual(len(compte["echecs"]), 1)

    def test_pause_entre_deux_envois(self):
        self._abonne(1, self.v1)
        self._abonne(2, self.v1)
        self._notifier()
        self.assertIn(0.05, self.pauses)

    def test_compte_rendu_exact(self):
        self._abonne(1, self.v1)
        self._abonne(2, self.v1)
        self._abonne(3, self.v1)
        self._abonne(4, self.v2)
        self._notifier({2: [("bloque", None)], 3: [("echec", "HTTP 400")]})
        self.assertEqual(
            self.lignes,
            ["Abonnes : 1/3 envoye(s), 1 bloque(s), 1 echec(s) (HTTP 400), 1 sans affaire."])

    def test_compte_rendu_sans_abonne(self):
        self._notifier()
        self.assertEqual(self.lignes, ["Abonnes : aucun abonne a servir."])
```

- [ ] **Étape 2 : vérifier l'échec**

Run: `python -m unittest tests.test_abonnes.TestNotifierAbonnes -v`
Expected: ERROR `AttributeError: module 'abonnes' has no attribute 'notifier_abonnes'`.

- [ ] **Étape 3 : implémenter** (ajouter `import time` en tête de `abonnes.py`)

```python
def _envoyer_un_morceau(envoyer, chat_id, message, dormir):
    """Un seul nouvel essai sur 429, apres le delai impose par Telegram."""
    statut, detail = envoyer(chat_id, message)
    if statut == "trop_vite":
        dormir(detail)
        statut, detail = envoyer(chat_id, message)
        if statut == "trop_vite":
            return ("echec", "trop de requetes")
    return (statut, detail)


def notifier_abonnes(conn, groupes, envoyer, log, exclure_chat_id=None,
                     dormir=time.sleep, pause: float = 0.05) -> dict:
    """Envoie a chaque abonne actif les affaires de sa ville.

    Ne leve jamais pour un abonne : une panne sur l'un n'empeche pas les
    suivants. Le compte rendu ne dit QUE ce qui a ete verifie (lecon du
    2026-09-11 : un « envoye » inconditionnel a masque 24 jours de refus).
    """
    compte = {"envoyes": 0, "bloques": 0, "echecs": [], "sans_affaire": 0, "servis": 0}
    servis = abonnes_a_servir(conn, exclure_chat_id)
    if not servis:
        log("Abonnes : aucun abonne a servir.")
        return compte

    premier = True
    for abonne in servis:
        morceaux = messages_abonne(groupes, abonne["ville_depart"])
        if not morceaux:
            compte["sans_affaire"] += 1
            continue
        compte["servis"] += 1
        try:
            resultat = ("ok", None)
            for morceau in morceaux:
                if not premier:
                    dormir(pause)  # limite Telegram : ~30 messages/seconde
                premier = False
                resultat = _envoyer_un_morceau(envoyer, abonne["chat_id"], morceau, dormir)
                if resultat[0] != "ok":
                    break
        except Exception as e:
            resultat = ("echec", f"erreur inattendue : {e}")

        statut, detail = resultat
        if statut == "ok":
            compte["envoyes"] += 1
        elif statut == "bloque":
            compte["bloques"] += 1
            desactiver(conn, abonne["chat_id"], "bloque", maintenant())
        else:
            compte["echecs"].append(str(detail))

    details = f" ({', '.join(compte['echecs'])})" if compte["echecs"] else ""
    log(f"Abonnes : {compte['envoyes']}/{compte['servis']} envoye(s), "
        f"{compte['bloques']} bloque(s), {len(compte['echecs'])} echec(s){details}, "
        f"{compte['sans_affaire']} sans affaire.")
    return compte
```

- [ ] **Étape 4 : vérifier le vert**

Run: `python -m unittest discover -s tests`
Expected: `OK`, 219 tests.

- [ ] **Étape 5 : commit**

```bash
git branch --show-current && git rev-parse --show-toplevel
git add abonnes.py tests/test_abonnes.py
git commit -m "abonnes.py : envoi aux abonnes (403 desactive, 429 un essai, compte rendu exact)"
```

---

### Tâche 7 : brancher l'envoi aux abonnés dans le relevé

**Fichiers :**
- Modifier : `hub_deals_db.py` (`verifier_et_notifier_anomalies`, nouvelle `notifier_abonnes_sans_risque`, `sqlite3.connect` du bloc principal)
- Modifier : `tests/test_abonnes.py`

**Interfaces :**
- Consomme : `abonnes.init_abonnes`, `abonnes.notifier_abonnes`, `hub_deals_db.envoyer_telegram_a`.
- Produit : `hub_deals_db.notifier_abonnes_sans_risque(conn, groupes) -> None` (ne lève jamais).

- [ ] **Étape 1 : écrire les tests qui échouent** (ajouter avant `if __name__`)

```python
class TestReleveNotifieLesAbonnes(unittest.TestCase):
    """Le message du proprietaire part en premier, et rien cote abonnes ne
    peut l'empecher ni interrompre le releve."""

    def setUp(self):
        self.ordre = []
        self.lignes = []
        self._sauve = {n: getattr(hub_deals_db, n) for n in (
            "log", "envoyer_telegram", "envoyer_telegram_a", "mesurer_rabattements",
            "detecter_anomalies", "TELEGRAM_CHAT_ID", "TRAVELPAYOUTS_MARKER")}
        hub_deals_db.log = self.lignes.append
        hub_deals_db.TELEGRAM_CHAT_ID = "999"
        hub_deals_db.TRAVELPAYOUTS_MARKER = None
        hub_deals_db.mesurer_rabattements = lambda couples: {}
        self.ville = sorted(abonnes.NOMS_AFFICHES)[0]
        hub_deals_db.detecter_anomalies = (
            lambda conn, date_collecte=None: [_anomalie(self.ville, mesure=None)])
        hub_deals_db.envoyer_telegram = lambda msg: self.ordre.append("proprietaire") or True

        def envoyer_a(chat_id, message, journaliser=True):
            self.ordre.append(chat_id)
            return ("ok", None)
        hub_deals_db.envoyer_telegram_a = envoyer_a

        self.conn = _base()
        for chat_id in (1, 999):
            abonnes.inscrire(self.conn, chat_id, "x", T0)
            abonnes.choisir_ville(self.conn, chat_id, self.ville, T0)

    def tearDown(self):
        for n, v in self._sauve.items():
            setattr(hub_deals_db, n, v)
        self.conn.close()

    def test_proprietaire_d_abord_puis_abonnes_sans_doublon(self):
        hub_deals_db.verifier_et_notifier_anomalies(self.conn, "2026-09-15")
        self.assertEqual(self.ordre, ["proprietaire", 1])

    def test_une_panne_cote_abonnes_n_interrompt_rien(self):
        def exploser(*a, **k):
            raise RuntimeError("panne abonnes")
        hub_deals_db.envoyer_telegram_a = exploser
        import abonnes as module
        original = module.abonnes_a_servir
        module.abonnes_a_servir = exploser
        try:
            hub_deals_db.verifier_et_notifier_anomalies(self.conn, "2026-09-15")
        finally:
            module.abonnes_a_servir = original
        self.assertEqual(self.ordre, ["proprietaire"])
        self.assertIn("envoi aux abonnes impossible", "\n".join(self.lignes))

    def test_une_base_sans_table_abonnes_ne_casse_pas(self):
        conn = sqlite3.connect(":memory:")
        hub_deals_db.verifier_et_notifier_anomalies(conn, "2026-09-15")
        self.assertEqual(self.ordre, ["proprietaire"])
        self.assertIn("Abonnes : aucun abonne a servir.", self.lignes)

    def test_le_releve_attend_la_base_occupee_par_l_ecoute(self):
        import inspect
        bloc = inspect.getsource(hub_deals_db).split('if __name__ == "__main__":')[1]
        self.assertIn("sqlite3.connect(DB_PATH, timeout=30)", bloc)
```

- [ ] **Étape 2 : vérifier l'échec**

Run: `python -m unittest tests.test_abonnes.TestReleveNotifieLesAbonnes -v`
Expected: FAIL — `self.ordre == ["proprietaire"]` au lieu de `["proprietaire", 1]`, et la
chaîne `timeout=30` absente.

- [ ] **Étape 3 : implémenter**

Ajouter au-dessus de `verifier_et_notifier_anomalies` :

```python
def notifier_abonnes_sans_risque(conn, groupes: list) -> None:
    """Envoie aux abonnes du bot les affaires de leur ville.

    Appelee APRES le message du proprietaire. Ne leve jamais : import DANS
    le try, comme pour la sauvegarde -- un abonnes.py absent ou casse ne
    doit pas faire echouer la fin du releve.
    """
    try:
        import abonnes
        abonnes.init_abonnes(conn)
        abonnes.notifier_abonnes(
            conn, groupes,
            envoyer=lambda chat_id, message: envoyer_telegram_a(
                chat_id, message, journaliser=False),
            log=log,
            exclure_chat_id=TELEGRAM_CHAT_ID,
        )
    except Exception as e:
        log(f"   -> envoi aux abonnes impossible : {e}")
```

À la **fin** de `verifier_et_notifier_anomalies`, après le bloc `if/elif/else` du compte rendu
du propriétaire :

```python
    notifier_abonnes_sans_risque(conn, groupes)
```

Dans le bloc principal, remplacer `conn = sqlite3.connect(DB_PATH)` par :

```python
    # timeout : bot_ecoute.py ecrit dans la meme base (inscriptions, temoin)
    conn = sqlite3.connect(DB_PATH, timeout=30)
```

- [ ] **Étape 4 : vérifier le vert**

Run: `python -m unittest discover -s tests`
Expected: `OK`, 223 tests. Vérifier que `TestLeJournalNeMentPlus` (qui passe `conn=None`) est
toujours vert : la ligne « envoi aux abonnes impossible » s'ajoute à son journal sans casser
ses assertions. Vérifier aussi que la durée de la suite n'a pas bondi.

- [ ] **Étape 5 : commit**

```bash
git branch --show-current && git rev-parse --show-toplevel
git add hub_deals_db.py tests/test_abonnes.py
git commit -m "Releve : envoi aux abonnes apres le proprietaire, sans risque pour le releve"
```

---

### Tâche 8 : témoin d'écoute et alerte « écoute arrêtée »

**Fichiers :**
- Modifier : `abonnes.py`, `hub_deals_db.py` (nouvelle `verifier_ecoute_et_alerter`, bloc principal)
- Modifier : `tests/test_abonnes.py`

**Interfaces :**
- Produit :
  - `abonnes.SEUIL_ECOUTE_MUETTE_S = 600`
  - `abonnes.noter_ecoute(conn, quand: str) -> None`
  - `abonnes.ecoute_muette(conn, quand: str) -> bool` — vrai si témoin plus vieux que le seuil,
    ou absent alors qu'au moins un abonné existe (actif ou non).
  - `hub_deals_db.verifier_ecoute_et_alerter(conn) -> None` (ne lève jamais).

- [ ] **Étape 1 : écrire les tests qui échouent** (ajouter avant `if __name__`)

```python
class TestTemoinEcoute(unittest.TestCase):
    def setUp(self):
        self.conn = _base()

    def tearDown(self):
        self.conn.close()

    def test_temoin_recent(self):
        abonnes.noter_ecoute(self.conn, "2026-09-15T12:55:00+00:00")
        self.assertFalse(abonnes.ecoute_muette(self.conn, "2026-09-15T13:04:00+00:00"))

    def test_temoin_ancien(self):
        abonnes.noter_ecoute(self.conn, "2026-09-15T12:50:00+00:00")
        self.assertTrue(abonnes.ecoute_muette(self.conn, "2026-09-15T13:04:00+00:00"))

    def test_temoin_mis_a_jour(self):
        abonnes.noter_ecoute(self.conn, "2026-09-15T08:00:00+00:00")
        abonnes.noter_ecoute(self.conn, "2026-09-15T13:00:00+00:00")
        self.assertFalse(abonnes.ecoute_muette(self.conn, "2026-09-15T13:04:00+00:00"))

    def test_temoin_absent_sans_abonne_n_alerte_pas(self):
        """Bot jamais installe : rien a surveiller."""
        self.assertFalse(abonnes.ecoute_muette(self.conn, T0))

    def test_temoin_absent_avec_abonnes_alerte(self):
        abonnes.inscrire(self.conn, 1, "x", T0)
        self.assertTrue(abonnes.ecoute_muette(self.conn, T1))


class TestAlerteEcouteArretee(unittest.TestCase):
    def setUp(self):
        self.envois = []
        self.lignes = []
        self._log = hub_deals_db.log
        self._envoyer = hub_deals_db.envoyer_telegram
        hub_deals_db.log = self.lignes.append
        hub_deals_db.envoyer_telegram = lambda msg: self.envois.append(msg) or True
        self.conn = _base()

    def tearDown(self):
        hub_deals_db.log = self._log
        hub_deals_db.envoyer_telegram = self._envoyer
        self.conn.close()

    def test_alerte_si_ecoute_muette(self):
        abonnes.noter_ecoute(self.conn, "2020-01-01T00:00:00+00:00")
        hub_deals_db.verifier_ecoute_et_alerter(self.conn)
        self.assertEqual(len(self.envois), 1)
        self.assertIn("ecoute du bot est arretee", self.envois[0])

    def test_rien_si_ecoute_vivante(self):
        abonnes.noter_ecoute(self.conn, abonnes.maintenant())
        hub_deals_db.verifier_ecoute_et_alerter(self.conn)
        self.assertEqual(self.envois, [])

    def test_ne_leve_jamais(self):
        hub_deals_db.verifier_ecoute_et_alerter(None)
        self.assertEqual(self.envois, [])

    def test_appele_avant_la_fin_du_releve(self):
        import inspect
        bloc = inspect.getsource(hub_deals_db).split('if __name__ == "__main__":')[1]
        self.assertLess(bloc.index("verifier_ecoute_et_alerter(conn)"),
                        bloc.index("=== Fin d'execution ==="))
```

- [ ] **Étape 2 : vérifier l'échec**

Run: `python -m unittest tests.test_abonnes.TestTemoinEcoute tests.test_abonnes.TestAlerteEcouteArretee -v`
Expected: ERROR `AttributeError: module 'abonnes' has no attribute 'noter_ecoute'`.

- [ ] **Étape 3 : implémenter**

Dans `abonnes.py` :

```python
# Le long polling rafraichit le temoin au moins toutes les ~50 s. Mesure en
# FIN de releve (~5 min apres son demarrage), 10 min laissent a l'ecoute
# lancee a la meme ouverture de session, ou reveillee de veille, le temps de
# faire son premier appel -- un seuil de 2 h aurait ete fausse par la veille.
SEUIL_ECOUTE_MUETTE_S = 600


def noter_ecoute(conn, quand: str) -> None:
    conn.execute("""
        INSERT INTO etat_bot (cle, valeur) VALUES ('derniere_ecoute', ?)
        ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur
    """, (quand,))
    conn.commit()


def ecoute_muette(conn, quand: str) -> bool:
    ligne = conn.execute(
        "SELECT valeur FROM etat_bot WHERE cle = 'derniere_ecoute'").fetchone()
    if ligne is None:
        # jamais d'ecoute : anormal seulement si des invites existent
        return conn.execute("SELECT COUNT(*) FROM abonnes").fetchone()[0] > 0
    ecart = datetime.fromisoformat(quand) - datetime.fromisoformat(ligne[0])
    return ecart.total_seconds() > SEUIL_ECOUTE_MUETTE_S
```

Dans `hub_deals_db.py`, sous `notifier_abonnes_sans_risque` :

```python
def verifier_ecoute_et_alerter(conn) -> None:
    """Previent le proprietaire si bot_ecoute.py ne tourne plus : sinon un
    invite tape /start dans le vide pendant des jours sans que personne le
    sache. Ne leve jamais."""
    try:
        import abonnes
        abonnes.init_abonnes(conn)
        if abonnes.ecoute_muette(conn, abonnes.maintenant()):
            envoyer_telegram(
                "<b>Probleme technique -- l'ecoute du bot est arretee</b>\n\n"
                "Les invites qui tapent /start n'ont pas de reponse.\n\n"
                "A verifier : tache planifiee « Bot vols - ecoute » et "
                "bot_ecoute_log.txt"
            )
            log("   -> ALERTE ecoute du bot arretee envoyee")
    except Exception as e:
        log(f"   -> controle de l'ecoute du bot impossible : {e}")
```

Dans le bloc principal, juste après `sauvegarder_et_alerter(conn)` et avant
`log("=== Fin d'execution ===")` :

```python
    # en fin de releve : l'ecoute lancee a la meme ouverture de session a eu
    # le temps de rafraichir son temoin
    verifier_ecoute_et_alerter(conn)
```

- [ ] **Étape 4 : vérifier le vert**

Run: `python -m unittest discover -s tests`
Expected: `OK`, 232 tests.

- [ ] **Étape 5 : commit**

```bash
git branch --show-current && git rev-parse --show-toplevel
git add abonnes.py hub_deals_db.py tests/test_abonnes.py
git commit -m "Temoin d'ecoute du bot et alerte au proprietaire si l'ecoute est arretee"
```

---

### Tâche 9 : commandes du bot (`traiter_update`)

**Fichiers :**
- Créer : `bot_ecoute.py`
- Créer : `tests/test_bot_ecoute.py`

**Interfaces :**
- Consomme : `abonnes.trouver`, `inscrire`, `choisir_ville`, `desactiver`, `nb_actifs`, `NOMS_AFFICHES`, `PLAFOND_ABONNES`.
- Produit (module `bot_ecoute`) :
  - `traiter_update(conn, update: dict, code: str | None, quand: str) -> tuple[list[dict], str | None]`
    — actions à exécuter et résumé pour le journal (sans texte brut du message).
  - Action d'envoi : `{"methode": "sendMessage", "chat_id": int, "text": str}` + `"reply_markup"` éventuel.
  - Action de bouton : `{"methode": "answerCallbackQuery", "callback_query_id": str}`.
  - `code_valide(code: str | None) -> str | None` — `None` si absent ou hors `^[A-Za-z0-9_-]{12,64}$`.
  - Textes : `MSG_INVITATION`, `MSG_COMPLET`, `MSG_BIENVENUE`, `MSG_MENU`, `MSG_STOP`, `MSG_AIDE`, `msg_confirmation(ville)`.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
"""Ecoute du bot : commandes, boucle getUpdates, execution sous pythonw."""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import bot_ecoute

CODE = "invitation_test_2026"
T0 = "2026-09-15T12:00:00+00:00"


def _message(texte, chat_id=111, prenom="Awa", type_chat="private", update_id=1):
    return {"update_id": update_id, "message": {
        "chat": {"id": chat_id, "type": type_chat},
        "from": {"id": chat_id, "first_name": prenom}, "text": texte}}


def _bouton(data, chat_id=111, update_id=1):
    return {"update_id": update_id, "callback_query": {
        "id": "cb1", "data": data, "from": {"id": chat_id},
        "message": {"chat": {"id": chat_id, "type": "private"}}}}


def _textes(actions):
    return [a["text"] for a in actions if a["methode"] == "sendMessage"]


class TestCodeValide(unittest.TestCase):
    def test_code_correct(self):
        self.assertEqual(bot_ecoute.code_valide(CODE), CODE)

    def test_absent_trop_court_ou_caracteres_interdits(self):
        """Fail closed : un code inutilisable ferme les inscriptions. Un code
        court serait aussi masque a tort dans des lignes de journal."""
        for code in (None, "", "court", "espace interdit 12", "x" * 65):
            self.assertIsNone(bot_ecoute.code_valide(code))


class TestCommandes(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        abonnes.init_abonnes(self.conn)
        self.ville = sorted(abonnes.NOMS_AFFICHES)[0]

    def tearDown(self):
        self.conn.close()

    def _traiter(self, update, code=CODE):
        return bot_ecoute.traiter_update(self.conn, update, code, T0)

    def test_start_avec_le_bon_code_inscrit_et_propose_les_villes(self):
        actions, _ = self._traiter(_message(f"/start {CODE}"))
        self.assertIsNotNone(abonnes.trouver(self.conn, 111))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_BIENVENUE])
        boutons = actions[0]["reply_markup"]["inline_keyboard"]
        self.assertEqual({ligne[0]["callback_data"] for ligne in boutons},
                         {f"ville:{v}" for v in abonnes.NOMS_AFFICHES})

    def test_start_avec_un_code_faux_ou_absent(self):
        for texte in ("/start mauvais_code_2026", "/start"):
            actions, _ = self._traiter(_message(texte))
            self.assertEqual(_textes(actions), [bot_ecoute.MSG_INVITATION])
        self.assertIsNone(abonnes.trouver(self.conn, 111))

    def test_inscriptions_fermees_sans_code_configure(self):
        actions, _ = self._traiter(_message(f"/start {CODE}"), code=None)
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_INVITATION])
        self.assertIsNone(abonnes.trouver(self.conn, 111))

    def test_start_avec_le_nom_du_bot_accole(self):
        """Telegram peut envoyer /start@ianniv_vols_bot dans certains clients."""
        self._traiter(_message(f"/start@ianniv_vols_bot {CODE}"))
        self.assertIsNotNone(abonnes.trouver(self.conn, 111))

    def test_plafond_atteint(self):
        for chat_id in range(abonnes.PLAFOND_ABONNES):
            abonnes.inscrire(self.conn, 10_000 + chat_id, "x", T0)
        actions, _ = self._traiter(_message(f"/start {CODE}"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_COMPLET])
        self.assertIsNone(abonnes.trouver(self.conn, 111))

    def test_retour_sans_code_apres_stop(self):
        self._traiter(_message(f"/start {CODE}"))
        self._traiter(_message("/stop"))
        actions, _ = self._traiter(_message("/start"))
        self.assertIs(abonnes.trouver(self.conn, 111)["actif"], True)
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_BIENVENUE])

    def test_retour_refuse_si_plafond_atteint(self):
        self._traiter(_message(f"/start {CODE}"))
        self._traiter(_message("/stop"))
        for chat_id in range(abonnes.PLAFOND_ABONNES):
            abonnes.inscrire(self.conn, 10_000 + chat_id, "x", T0)
        actions, _ = self._traiter(_message("/start"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_COMPLET])
        self.assertIs(abonnes.trouver(self.conn, 111)["actif"], False)

    def test_un_abonne_actif_n_est_pas_bloque_par_le_plafond(self):
        self._traiter(_message(f"/start {CODE}"))
        for chat_id in range(abonnes.PLAFOND_ABONNES):
            abonnes.inscrire(self.conn, 10_000 + chat_id, "x", T0)
        actions, _ = self._traiter(_message("/start"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_BIENVENUE])

    def test_bouton_de_ville(self):
        self._traiter(_message(f"/start {CODE}"))
        actions, _ = self._traiter(_bouton(f"ville:{self.ville}"))
        self.assertEqual(abonnes.trouver(self.conn, 111)["ville_depart"], self.ville)
        self.assertEqual(actions[0], {"methode": "answerCallbackQuery",
                                      "callback_query_id": "cb1"})
        self.assertEqual(_textes(actions), [bot_ecoute.msg_confirmation(self.ville)])
        self.assertIn(abonnes.NOMS_AFFICHES[self.ville],
                      bot_ecoute.msg_confirmation(self.ville))

    def test_bouton_idempotent(self):
        """Un redemarrage peut rejouer une mise a jour deja traitee."""
        self._traiter(_message(f"/start {CODE}"))
        self._traiter(_bouton(f"ville:{self.ville}"))
        self._traiter(_bouton(f"ville:{self.ville}"))
        self.assertEqual(abonnes.trouver(self.conn, 111)["ville_depart"], self.ville)
        self.assertEqual(abonnes.nb_actifs(self.conn), 1)

    def test_bouton_d_un_inconnu_ou_d_un_desabonne(self):
        actions, _ = self._traiter(_bouton(f"ville:{self.ville}"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_INVITATION])
        self._traiter(_message(f"/start {CODE}"))
        self._traiter(_message("/stop"))
        actions, _ = self._traiter(_bouton(f"ville:{self.ville}"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_STOP])
        self.assertIsNone(abonnes.trouver(self.conn, 111)["ville_depart"])

    def test_bouton_de_ville_inconnue(self):
        self._traiter(_message(f"/start {CODE}"))
        actions, _ = self._traiter(_bouton("ville:Atlantide"))
        self.assertEqual(_textes(actions), [])
        self.assertIsNone(abonnes.trouver(self.conn, 111)["ville_depart"])

    def test_ville(self):
        self._traiter(_message(f"/start {CODE}"))
        actions, _ = self._traiter(_message("/ville"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_MENU])
        self.assertIn("reply_markup", actions[0])

    def test_stop(self):
        self._traiter(_message(f"/start {CODE}"))
        actions, _ = self._traiter(_message("/stop"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_STOP])
        a = abonnes.trouver(self.conn, 111)
        self.assertIs(a["actif"], False)
        self.assertEqual(a["motif_inactif"], "stop")

    def test_commandes_d_un_inconnu(self):
        for texte in ("/ville", "/stop", "bonjour"):
            actions, _ = self._traiter(_message(texte))
            self.assertEqual(_textes(actions), [bot_ecoute.MSG_INVITATION])

    def test_message_libre_d_un_abonne(self):
        self._traiter(_message(f"/start {CODE}"))
        actions, _ = self._traiter(_message("bonjour"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_AIDE])

    def test_groupes_et_messages_sans_texte_ignores(self):
        actions, _ = self._traiter(_message(f"/start {CODE}", type_chat="group"))
        self.assertEqual(actions, [])
        actions, resume = self._traiter({"update_id": 1, "message": {
            "chat": {"id": 111, "type": "private"}}})
        self.assertEqual(actions, [])
        self.assertIsNone(abonnes.trouver(self.conn, 111))

    def test_le_resume_ne_contient_ni_le_code_ni_le_texte(self):
        for update in (_message(f"/start {CODE}"), _message("mon numero 0612"),
                       _message("/start mauvais_code_2026")):
            _, resume = self._traiter(update)
            self.assertNotIn(CODE, resume or "")
            self.assertNotIn("0612", resume or "")
            self.assertNotIn("mauvais_code_2026", resume or "")
            self.assertIn("111", resume)
```

- [ ] **Étape 2 : vérifier l'échec**

Run: `python -m unittest tests.test_bot_ecoute -v`
Expected: ERROR `ModuleNotFoundError: No module named 'bot_ecoute'`.

- [ ] **Étape 3 : implémenter `bot_ecoute.py` (partie commandes)**

```python
"""
Ecoute du bot Telegram @ianniv_vols_bot : inscriptions au test prive.

Programme permanent, lance a l'ouverture de session par la tache planifiee
« Bot vols - ecoute » (pythonw, sans fenetre). SEUL lecteur de getUpdates :
le releve (hub_deals_db.py) ne fait qu'envoyer -- deux lecteurs simultanes
provoquent un HTTP 409 cote Telegram.

N'appelle jamais l'API des prix. Les messages recus ne sont jamais recopies
au journal : ils contiennent le code d'invitation ou des donnees
personnelles des invites.

Spec : docs/superpowers/specs/2026-09-15-bot-abonnes-design.md
"""

import hmac
import os
import re

import abonnes

MSG_INVITATION = "Ce bot est pour l'instant sur invitation."
MSG_COMPLET = "Le test est complet pour le moment."
MSG_BIENVENUE = "Bienvenue ! Choisis ta ville de départ :"
MSG_MENU = "Choisis ta ville de départ :"
MSG_STOP = "Tu es désabonné. /start pour revenir."
MSG_AIDE = "Commandes : /ville pour changer de ville, /stop pour arrêter."


def msg_confirmation(ville: str) -> str:
    nom = abonnes.NOMS_AFFICHES[ville]
    return (f"C'est noté : {nom}. Tu recevras les bonnes affaires au départ de "
            f"{nom}, au plus une fois par jour. /ville pour changer, /stop pour arrêter.")


def code_valide(code):
    """Le code d'invitation utilisable, ou None (inscriptions fermees).
    Au moins 12 caracteres : le code est masque dans le journal par simple
    remplacement, un code court y mutilerait des lignes legitimes."""
    if code and re.fullmatch(r"[A-Za-z0-9_-]{12,64}", code):
        return code
    return None


def _envoi(chat_id, texte, clavier=False) -> dict:
    action = {"methode": "sendMessage", "chat_id": chat_id, "text": texte}
    if clavier:
        action["reply_markup"] = {"inline_keyboard": [
            [{"text": nom, "callback_data": f"ville:{cle}"}]
            for cle, nom in abonnes.NOMS_AFFICHES.items()]}
    return action


def _traiter_bouton(conn, cq: dict, quand: str):
    chat_id = cq["message"]["chat"]["id"]
    actions = [{"methode": "answerCallbackQuery", "callback_query_id": cq["id"]}]
    abonne = abonnes.trouver(conn, chat_id)
    if abonne is None:
        return actions + [_envoi(chat_id, MSG_INVITATION)], f"bouton refuse (inconnu) chat_id={chat_id}"
    if not abonne["actif"]:
        return actions + [_envoi(chat_id, MSG_STOP)], f"bouton refuse (desabonne) chat_id={chat_id}"
    data = cq.get("data") or ""
    ville = data[len("ville:"):] if data.startswith("ville:") else None
    if ville not in abonnes.NOMS_AFFICHES:
        return actions, f"bouton inconnu chat_id={chat_id}"
    abonnes.choisir_ville(conn, chat_id, ville, quand)
    return actions + [_envoi(chat_id, msg_confirmation(ville))], f"ville {ville} chat_id={chat_id}"


def traiter_update(conn, update: dict, code, quand: str):
    """Traduit une mise a jour Telegram en actions a executer. Aucun appel
    reseau. Idempotent : rejouer une mise a jour ne change rien de plus."""
    if "callback_query" in update:
        return _traiter_bouton(conn, update["callback_query"], quand)

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or "text" not in message:
        return [], None
    if chat.get("type") != "private":
        return [], f"ignore (conversation non privee) chat_id={chat_id}"

    commande, _, argument = message["text"].strip().partition(" ")
    commande = commande.split("@")[0]
    argument = argument.strip()
    prenom = (message.get("from") or {}).get("first_name")
    abonne = abonnes.trouver(conn, chat_id)
    complet = abonnes.nb_actifs(conn) >= abonnes.PLAFOND_ABONNES

    if commande == "/start":
        if abonne is not None and abonne["actif"]:
            return [_envoi(chat_id, MSG_BIENVENUE, clavier=True)], f"start (deja abonne) chat_id={chat_id}"
        if abonne is not None:
            if complet:
                return [_envoi(chat_id, MSG_COMPLET)], f"start refuse (complet) chat_id={chat_id}"
            abonnes.inscrire(conn, chat_id, prenom, quand)
            return [_envoi(chat_id, MSG_BIENVENUE, clavier=True)], f"start (retour) chat_id={chat_id}"
        if not code or not hmac.compare_digest(argument, code):
            return [_envoi(chat_id, MSG_INVITATION)], f"start refuse (code absent ou faux) chat_id={chat_id}"
        if complet:
            return [_envoi(chat_id, MSG_COMPLET)], f"start refuse (complet) chat_id={chat_id}"
        abonnes.inscrire(conn, chat_id, prenom, quand)
        return [_envoi(chat_id, MSG_BIENVENUE, clavier=True)], f"start (nouvel abonne) chat_id={chat_id}"

    if abonne is None:
        return [_envoi(chat_id, MSG_INVITATION)], f"refuse (inconnu) chat_id={chat_id}"

    if commande == "/ville":
        if not abonne["actif"]:
            return [_envoi(chat_id, MSG_STOP)], f"ville refuse (desabonne) chat_id={chat_id}"
        return [_envoi(chat_id, MSG_MENU, clavier=True)], f"ville (menu) chat_id={chat_id}"

    if commande == "/stop":
        abonnes.desactiver(conn, chat_id, "stop", quand)
        return [_envoi(chat_id, MSG_STOP)], f"stop chat_id={chat_id}"

    return [_envoi(chat_id, MSG_AIDE)], f"message libre chat_id={chat_id}"
```

Note : `hmac.compare_digest` exige deux `str` ASCII ; un argument non ASCII lève `TypeError`.
Garder la comparaison ainsi mais ajouter, avant elle :

```python
        if not argument.isascii():
            return [_envoi(chat_id, MSG_INVITATION)], f"start refuse (code absent ou faux) chat_id={chat_id}"
```

et un test :

```python
    def test_code_non_ascii_refuse_sans_planter(self):
        actions, _ = self._traiter(_message("/start clé_non_ascii_2026"))
        self.assertEqual(_textes(actions), [bot_ecoute.MSG_INVITATION])
```

- [ ] **Étape 4 : vérifier le vert**

Run: `python -m unittest discover -s tests`
Expected: `OK`, 253 tests.

- [ ] **Étape 5 : commit**

```bash
git branch --show-current && git rev-parse --show-toplevel
git add bot_ecoute.py tests/test_bot_ecoute.py
git commit -m "bot_ecoute.py : commandes /start CODE, /ville, /stop et boutons de ville"
```

---

### Tâche 10 : boucle d'écoute, journal et point d'entrée

**Fichiers :**
- Modifier : `bot_ecoute.py`
- Modifier : `tests/test_bot_ecoute.py`

**Interfaces :**
- Consomme : `traiter_update`, `code_valide`, `abonnes.noter_ecoute`, `abonnes.maintenant`, `abonnes.init_abonnes`, `hub_deals_db.masquer_secrets`, `hub_deals_db.TELEGRAM_BOT_TOKEN`, `hub_deals_db.DB_PATH`.
- Produit :
  - `LOG_PATH = "bot_ecoute_log.txt"`, `CODE_INVITATION = code_valide(os.environ.get("HUB_DEALS_CODE_INVITATION"))`
  - `masquer(message: str) -> str`, `log(message: str) -> None`, `journaliser_plantage(type_exc, valeur, trace) -> None`
  - `appeler(token, methode, params, timeout=15) -> tuple[int, dict]` — seule fonction réseau.
  - `executer_actions(token, actions, appeler_fn) -> None`
  - `boucle(conn, token, code, appeler_fn=appeler, dormir=time.sleep, quand_fn=abonnes.maintenant, tours=None) -> None`

- [ ] **Étape 1 : écrire les tests qui échouent** (ajouter avant `if __name__`)

```python
class _FauxTelegram:
    """Remplace appeler() : reponses getUpdates programmees, appels notes."""

    def __init__(self, reponses_get_updates, statut_envoi=200):
        self.file = list(reponses_get_updates)
        self.appels = []
        self.statut_envoi = statut_envoi

    def __call__(self, token, methode, params, timeout=15):
        self.appels.append((methode, dict(params)))
        if methode == "getUpdates":
            reponse = self.file.pop(0)
            if isinstance(reponse, Exception):
                raise reponse
            return reponse
        return (self.statut_envoi, {"ok": self.statut_envoi == 200})


class TestBoucle(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        abonnes.init_abonnes(self.conn)
        self.lignes = []
        self.pauses = []
        self._log = bot_ecoute.log
        bot_ecoute.log = self.lignes.append

    def tearDown(self):
        bot_ecoute.log = self._log
        self.conn.close()

    def _boucle(self, faux, tours):
        bot_ecoute.boucle(self.conn, "bot-factice", CODE, appeler_fn=faux,
                          dormir=self.pauses.append, quand_fn=lambda: T0, tours=tours)

    def _derniere_ecoute(self):
        ligne = self.conn.execute(
            "SELECT valeur FROM etat_bot WHERE cle='derniere_ecoute'").fetchone()
        return ligne[0] if ligne else None

    def test_traite_les_mises_a_jour_et_avance_l_offset(self):
        faux = _FauxTelegram([
            (200, {"ok": True, "result": [_message(f"/start {CODE}", update_id=41)]}),
            (200, {"ok": True, "result": []})])
        self._boucle(faux, tours=2)
        self.assertIsNotNone(abonnes.trouver(self.conn, 111))
        methodes = [m for m, _ in faux.appels]
        self.assertEqual(methodes, ["getUpdates", "sendMessage", "getUpdates"])
        self.assertNotIn("offset", faux.appels[0][1])
        self.assertEqual(faux.appels[2][1]["offset"], 42)
        self.assertEqual(faux.appels[0][1]["allowed_updates"], ["message", "callback_query"])

    def test_le_temoin_est_rafraichi_meme_sans_message(self):
        faux = _FauxTelegram([(200, {"ok": True, "result": []})])
        self._boucle(faux, tours=1)
        self.assertEqual(self._derniere_ecoute(), T0)

    def test_erreur_reseau_attente_croissante_plafonnee(self):
        import requests
        faux = _FauxTelegram([requests.exceptions.ConnectionError("coupure")] * 9)
        self._boucle(faux, tours=9)
        self.assertEqual(self.pauses, [5, 10, 20, 40, 80, 160, 300, 300, 300])
        self.assertIsNone(self._derniere_ecoute())

    def test_conflit_409_journalise(self):
        faux = _FauxTelegram([(409, {"ok": False, "description": "Conflict"})])
        self._boucle(faux, tours=1)
        self.assertIn("409", "\n".join(self.lignes))
        self.assertEqual(self.pauses, [30])
        self.assertIsNone(self._derniere_ecoute())

    def test_autre_refus_attente_croissante(self):
        faux = _FauxTelegram([(401, {"ok": False, "description": "Unauthorized"})] * 2)
        self._boucle(faux, tours=2)
        self.assertEqual(self.pauses, [5, 10])
        self.assertIn("401", "\n".join(self.lignes))

    def test_une_mise_a_jour_qui_plante_est_sautee(self):
        """Sinon une seule mise a jour defectueuse bloquerait l'ecoute."""
        faux = _FauxTelegram([
            (200, {"ok": True, "result": [{"update_id": 7, "callback_query": {}}]}),
            (200, {"ok": True, "result": []})])
        self._boucle(faux, tours=2)
        self.assertEqual(faux.appels[1][1]["offset"], 8)
        self.assertIn("ERREUR", "\n".join(self.lignes))

    def test_base_verrouillee_la_mise_a_jour_est_rejouee(self):
        """Le releve tient la base ~25 s par hub : la mise a jour ne doit
        pas etre perdue, on la redemande au tour suivant."""
        original = bot_ecoute.traiter_update

        def verrouille(*a, **k):
            raise sqlite3.OperationalError("database is locked")
        bot_ecoute.traiter_update = verrouille
        try:
            faux = _FauxTelegram([
                (200, {"ok": True, "result": [_message("/stop", update_id=7)]}),
                (200, {"ok": True, "result": []})])
            self._boucle(faux, tours=2)
        finally:
            bot_ecoute.traiter_update = original
        self.assertNotIn("offset", faux.appels[1][1])

    def test_un_envoi_refuse_est_journalise_sans_arreter(self):
        faux = _FauxTelegram([
            (200, {"ok": True, "result": [_message("bonjour", update_id=1)]})],
            statut_envoi=400)
        self._boucle(faux, tours=1)
        self.assertIn("HTTP 400", "\n".join(self.lignes))

    def test_le_journal_ne_contient_pas_le_texte_recu(self):
        faux = _FauxTelegram([(200, {"ok": True, "result": [
            _message(f"/start {CODE}", update_id=1),
            _message("mon numero 0612", update_id=2)]})])
        self._boucle(faux, tours=1)
        journal = "\n".join(self.lignes)
        self.assertNotIn(CODE, journal)
        self.assertNotIn("0612", journal)


class TestMasquage(unittest.TestCase):
    def test_token_et_code_masques(self):
        import hub_deals_db
        bot, code = hub_deals_db.TELEGRAM_BOT_TOKEN, bot_ecoute.CODE_INVITATION
        hub_deals_db.TELEGRAM_BOT_TOKEN = "123:secret_bot_token"
        bot_ecoute.CODE_INVITATION = CODE
        try:
            texte = bot_ecoute.masquer(
                f"https://api.telegram.org/bot123:secret_bot_token/getUpdates {CODE}")
        finally:
            hub_deals_db.TELEGRAM_BOT_TOKEN, bot_ecoute.CODE_INVITATION = bot, code
        self.assertNotIn("secret_bot_token", texte)
        self.assertNotIn(CODE, texte)

    def test_le_journal_ecrit_est_masque(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "bot.txt")
            with mock.patch.object(bot_ecoute, "LOG_PATH", chemin), \
                 mock.patch.object(bot_ecoute, "CODE_INVITATION", CODE), \
                 mock.patch("builtins.print"):
                bot_ecoute.log(f"essai {CODE}")
            with open(chemin, encoding="utf-8") as f:
                contenu = f.read()
        self.assertIn("essai ***", contenu)


class TestSousPythonw(unittest.TestCase):
    def test_sans_token_l_arret_est_journalise_sous_pythonw(self):
        """Sous pythonw, stdout et stderr valent None : sans journal, un
        demarrage rate serait totalement muet. Le test passe lui-meme par
        pythonw (lecon du 2026-09-13 : depuis le runner, il ne prouvait rien)."""
        import subprocess
        import tempfile
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            self.skipTest("pythonw.exe introuvable")
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "bot_ecoute.py")
        env = {k: v for k, v in os.environ.items()
               if k not in ("TELEGRAM_BOT_TOKEN", "HUB_DEALS_CODE_INVITATION")}
        with tempfile.TemporaryDirectory() as dossier:
            code_retour = subprocess.run([pythonw, script], cwd=dossier, env=env,
                                         timeout=60).returncode
            with open(os.path.join(dossier, "bot_ecoute_log.txt"), encoding="utf-8") as f:
                journal = f.read()
        self.assertEqual(code_retour, 1)
        self.assertIn("ARRET", journal)
        self.assertIn("TELEGRAM_BOT_TOKEN", journal)

    def test_le_bloc_principal_installe_la_journalisation(self):
        import inspect
        bloc = inspect.getsource(bot_ecoute).split('if __name__ == "__main__":')[1]
        self.assertIn("sys.excepthook = journaliser_plantage", bloc)
        self.assertIn("timeout=60", bloc)
```

- [ ] **Étape 2 : vérifier l'échec**

Run: `python -m unittest tests.test_bot_ecoute.TestBoucle tests.test_bot_ecoute.TestMasquage tests.test_bot_ecoute.TestSousPythonw -v`
Expected: ERROR `AttributeError: module 'bot_ecoute' has no attribute 'boucle'` (et `log`,
`masquer`) ; le test pythonw échoue faute de journal.

- [ ] **Étape 3 : implémenter**

En tête de `bot_ecoute.py`, compléter les imports :

```python
import hmac
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests

import abonnes
import hub_deals_db
```

Sous les imports :

```python
LOG_PATH = "bot_ecoute_log.txt"
DELAI_LONG_POLLING = 50   # secondes : Telegram garde la requete ouverte
ATTENTE_MAX = 300         # plafond de l'attente croissante apres erreur
ATTENTE_CONFLIT = 30      # HTTP 409 : un autre lecteur de getUpdates
```

Sous `code_valide` (la constante dépend de la fonction) :

```python
CODE_INVITATION = code_valide(os.environ.get("HUB_DEALS_CODE_INVITATION"))
```

Journal et réseau :

```python
def masquer(message: str) -> str:
    """Tokens (via hub_deals_db) et code d'invitation remplaces par ***."""
    message = hub_deals_db.masquer_secrets(message)
    if CODE_INVITATION:
        message = message.replace(CODE_INVITATION, "***")
    return message


def log(message: str) -> None:
    """Journal separe de celui du releve : deux processus, deux fichiers."""
    horodatage = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ligne = f"[{horodatage}] {masquer(message)}"
    print(ligne)  # silencieux sous pythonw (stdout None)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(ligne + "\n")


def journaliser_plantage(type_exc, valeur, trace) -> None:
    """Crochet sys.excepthook : sous pythonw, stderr vaut None."""
    import traceback
    log("=== PLANTAGE de l'ecoute ===")
    log("".join(traceback.format_exception(type_exc, valeur, trace)).rstrip())


def appeler(token, methode, params, timeout=15):
    """Seule fonction reseau du module. Renvoie (code HTTP, corps JSON)."""
    reponse = requests.post(f"https://api.telegram.org/bot{token}/{methode}",
                            json=params, timeout=timeout)
    try:
        corps = reponse.json()
    except ValueError:
        corps = {}
    return reponse.status_code, corps


def executer_actions(token, actions, appeler_fn) -> None:
    for action in actions:
        params = {k: v for k, v in action.items() if k != "methode"}
        try:
            statut, corps = appeler_fn(token, action["methode"], params)
        except requests.exceptions.RequestException as e:
            log(f"ERREUR reseau {action['methode']} : {e}")
            continue
        if statut != 200:
            log(f"ECHEC {action['methode']} HTTP {statut} "
                f"{(corps or {}).get('description', '')} chat_id={params.get('chat_id')}")
```

Boucle :

```python
def boucle(conn, token, code, appeler_fn=appeler, dormir=time.sleep,
           quand_fn=abonnes.maintenant, tours=None) -> None:
    """Long polling getUpdates. tours=None : sans fin (production)."""
    offset = None
    echecs = 0
    tour = 0
    while tours is None or tour < tours:
        tour += 1
        params = {"timeout": DELAI_LONG_POLLING,
                  "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        try:
            statut, corps = appeler_fn(token, "getUpdates", params,
                                       timeout=DELAI_LONG_POLLING + 10)
        except requests.exceptions.RequestException as e:
            echecs += 1
            attente = min(ATTENTE_MAX, 5 * 2 ** (echecs - 1))
            log(f"ERREUR reseau getUpdates ({e}) : nouvel essai dans {attente} s")
            dormir(attente)
            continue

        if statut == 409:
            log(f"CONFLIT HTTP 409 : un autre programme lit getUpdates, "
                f"nouvel essai dans {ATTENTE_CONFLIT} s")
            dormir(ATTENTE_CONFLIT)
            continue
        if statut != 200:
            echecs += 1
            attente = min(ATTENTE_MAX, 5 * 2 ** (echecs - 1))
            log(f"ECHEC getUpdates HTTP {statut} {(corps or {}).get('description', '')} : "
                f"nouvel essai dans {attente} s")
            dormir(attente)
            continue

        echecs = 0
        try:
            abonnes.noter_ecoute(conn, quand_fn())
        except sqlite3.OperationalError as e:
            log(f"ERREUR temoin d'ecoute : {e}")

        for update in corps.get("result", []):
            try:
                actions, resume = traiter_update(conn, update, code, quand_fn())
            except sqlite3.OperationalError as e:
                # base occupee par le releve : on ne confirme pas cette mise
                # a jour, Telegram la renverra au tour suivant
                log(f"Base occupee, mise a jour {update.get('update_id')} rejouee : {e}")
                break
            except Exception as e:
                log(f"ERREUR traitement mise a jour {update.get('update_id')} : {e}")
                offset = update["update_id"] + 1
                continue
            offset = update["update_id"] + 1
            if resume:
                log(resume)
            executer_actions(token, actions, appeler_fn)
```

Point d'entrée, en fin de fichier :

```python
if __name__ == "__main__":
    # sous pythonw.exe, rien ne s'affiche : tout plantage doit aller au journal
    sys.excepthook = journaliser_plantage

    token = hub_deals_db.TELEGRAM_BOT_TOKEN
    if not token:
        log("ARRET : il manque TELEGRAM_BOT_TOKEN dans l'environnement.")
        sys.exit(1)
    if CODE_INVITATION is None:
        log("Inscriptions FERMEES : HUB_DEALS_CODE_INVITATION absent ou invalide "
            "(12 a 64 caracteres parmi A-Z a-z 0-9 _ -).")

    # timeout > duree d'une transaction du releve (~25 s par hub)
    conn = sqlite3.connect(hub_deals_db.DB_PATH, timeout=60)
    abonnes.init_abonnes(conn)
    log("=== Demarrage de l'ecoute ===")
    boucle(conn, token, CODE_INVITATION)
```

- [ ] **Étape 4 : vérifier le vert**

Run: `python -m unittest discover -s tests`
Expected: `OK`, 266 tests. Durée : le test pythonw ajoute quelques secondes (lancement d'un
interpréteur) ; tout autre bond signale un appel réseau.

- [ ] **Étape 5 : commit**

```bash
git branch --show-current && git rev-parse --show-toplevel
git add bot_ecoute.py tests/test_bot_ecoute.py
git commit -m "bot_ecoute.py : boucle getUpdates, journal masque, demarrage sous pythonw"
```

---

### Tâche 11 : tâche planifiée, documentation

**Fichiers :**
- Créer : `taches/bot_ecoute.xml`, `taches/installer_bot_ecoute.ps1`
- Modifier : `.gitignore`, `README.md`, `CHANGELOG.md`

- [ ] **Étape 1 : `.gitignore`** — sous `flight_deals_log.txt` :

```
# journal du programme d'ecoute du bot (bot_ecoute.py)
bot_ecoute_log.txt
```

- [ ] **Étape 2 : `taches/bot_ecoute.xml`** (sans déclaration d'encodage ; SID et chemin
Python recopiés de la tâche « Traqueur de vols »)

```xml
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Ecoute du bot Telegram hub_deals : inscriptions au test prive (bot_ecoute.py)</Description>
    <URI>\Bot vols - ecoute</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-21-1904889933-992648809-436972831-1002</UserId>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT5M</Interval>
      <Count>10</Count>
    </RestartOnFailure>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
  </Settings>
  <Triggers>
    <LogonTrigger />
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>C:\Users\Dell\AppData\Local\Programs\Python\Python314\pythonw.exe</Command>
      <Arguments>bot_ecoute.py</Arguments>
      <WorkingDirectory>C:\Users\Dell\hub_deals</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
```

`ExecutionTimeLimit PT0S` est **indispensable** : sans lui, le planificateur arrête la tâche
au bout de 72 heures par défaut, et l'écoute mourrait en silence tous les trois jours.

- [ ] **Étape 3 : `taches/installer_bot_ecoute.ps1`**

La modification du planificateur exige une session **élevée** sur cette machine (les sessions
d'outil ne le sont pas : « Accès refusé » 0x80070005). Le script est lancé par
`Start-Process powershell -Verb RunAs` ; l'utilisateur valide l'UAC ; le résultat est écrit
dans un fichier à relire.

```powershell
# Enregistre la tache « Bot vols - ecoute ». A lancer en session elevee :
#   Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Users\Dell\hub_deals\taches\installer_bot_ecoute.ps1'
$resultat = Join-Path $env:TEMP 'installer_bot_ecoute.txt'
try {
    $xml = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot 'bot_ecoute.xml')
    Register-ScheduledTask -TaskName 'Bot vols - ecoute' -Xml $xml -Force -ErrorAction Stop | Out-Null
    "OK $(Get-Date -Format s)" | Out-File -Encoding utf8 $resultat
} catch {
    "ECHEC $(Get-Date -Format s) : $_" | Out-File -Encoding utf8 $resultat
}
```

- [ ] **Étape 4 : `README.md`** — ajouter une section `## Bot multi-abonnés (test privé)`
avant `## Sauvegardes`, contenant :

```markdown
## Bot multi-abonnés (test privé)

Des invités s'abonnent à `@ianniv_vols_bot` avec un lien
`https://t.me/ianniv_vols_bot?start=<CODE>`, choisissent leur ville de départ et reçoivent
chaque jour les affaires de cette ville. Le propriétaire (`TELEGRAM_CHAT_ID`) continue de
recevoir le message complet, envoyé en premier.

| Variable (portée User) | Rôle |
|---|---|
| `HUB_DEALS_CODE_INVITATION` | code du lien d'invitation, 12 à 64 caractères `A-Z a-z 0-9 _ -`. Absent : inscriptions fermées |
| `TRAVELPAYOUTS_MARKER` | identifiant d'affilié ajouté aux liens. Absent : liens sans affiliation |

Commandes : `/start` (avec le code la première fois), `/ville`, `/stop`. Plafond :
`abonnes.PLAFOND_ABONNES` abonnés actifs.

`bot_ecoute.py` tourne en permanence (tâche « Bot vols - ecoute », ouverture de session,
`pythonw`) et journalise dans `bot_ecoute_log.txt`. C'est le **seul** lecteur de `getUpdates`.
S'il ne tourne plus, le relevé suivant envoie une alerte au propriétaire.

Installation de la tâche (UAC à valider) :
`Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Users\Dell\hub_deals\taches\installer_bot_ecoute.ps1'`
puis lire `%TEMP%\installer_bot_ecoute.txt`.
```

Mettre aussi à jour la ligne de la section `## Fichiers` pour citer `abonnes.py`,
`bot_ecoute.py` et `taches/`.

- [ ] **Étape 5 : `CHANGELOG.md`** — nouvelle entrée en tête :

```markdown
## 2026-09-15

### Ajouté
- **Bot multi-abonnés, en test privé.** Inscription par lien d'invitation, choix de la ville de
  départ par boutons, `/ville`, `/stop`. Chaque abonné reçoit uniquement les affaires de sa
  ville, avec la mention « prix repéré, à vérifier » (règle Travelpayouts contre les remises
  trompeuses) ; le propriétaire reçoit toujours le message complet, en premier. Bot bloqué →
  abonné désactivé ; HTTP 429 → un seul nouvel essai ; compte rendu exact au journal.
  Programme d'écoute permanent `bot_ecoute.py` (tâche « Bot vols - ecoute »), alerte au
  propriétaire s'il ne tourne plus.
- **Liens affiliés.** Les liens Aviasales portent `TRAVELPAYOUTS_MARKER` et une étiquette par
  destinataire (ville ou `proprietaire`). Aucun lien n'en portait jusqu'ici.
```

- [ ] **Étape 6 : vérifier et commiter**

Run: `python -m unittest discover -s tests`
Expected: `OK`, 266 tests.

```bash
git branch --show-current && git rev-parse --show-toplevel
git add .gitignore taches/bot_ecoute.xml taches/installer_bot_ecoute.ps1 README.md CHANGELOG.md
git commit -m "Tache planifiee de l'ecoute du bot, documentation"
```

---

### Tâche 12 : vérification en conditions réelles, fusion, installation

Cette tâche se fait **avec l'utilisateur** : elle demande un 2e compte Telegram, des
variables d'environnement qu'il pose lui-même, un clic dans le tableau de bord Travelpayouts
et la validation d'un UAC. Ne rien déclarer vérifié sans la preuve décrite.

**Pourquoi en deux temps :** la tâche planifiée pointe vers le checkout principal, qui ne
contient le nouveau code qu'après fusion. Les points 1 à 4 se vérifient avant fusion, depuis le
worktree, sur une **copie** de la base ; les points 5 et 6 après fusion.

- [ ] **Étape 1 : variables posées par l'utilisateur**

Lui demander de taper lui-même (les valeurs ne doivent pas transiter par la conversation) :

```
! setx HUB_DEALS_CODE_INVITATION "<code de 12 a 64 caracteres>"
! setx TRAVELPAYOUTS_MARKER "<son identifiant d'affilie>"
```

Vérifier leur présence **sans afficher les valeurs**, en lisant `HKCU\Environment` (l'environnement
de la session est antérieur au `setx`) :

```bash
python -c "import winreg,re; k=winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment'); c=winreg.QueryValueEx(k,'HUB_DEALS_CODE_INVITATION')[0]; m=winreg.QueryValueEx(k,'TRAVELPAYOUTS_MARKER')[0]; print('code conforme' if re.fullmatch(r'[A-Za-z0-9_-]{12,64}',c) else 'code NON conforme', '| marker', len(m), 'car.')"
```

- [ ] **Étape 2 : copie de la base et lancement de l'écoute depuis le worktree**

```bash
cp /c/Users/Dell/hub_deals/flight_deals.db /c/Users/Dell/hub_deals_bot/flight_deals.db
```

Ne pas le faire entre 12h55 et 13h10 (relevé en cours dans le checkout principal). Lancer
l'écoute avec les variables relues du registre, via `Start-Process` (pas `ProcessStartInfo`,
qui ferait hériter pythonw des handles) :

```powershell
foreach ($n in 'TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','TRAVELPAYOUTS_TOKEN','HUB_DEALS_CODE_INVITATION','TRAVELPAYOUTS_MARKER') { Set-Item "env:$n" ([Environment]::GetEnvironmentVariable($n,'User')) }
Start-Process -FilePath 'C:\Users\Dell\AppData\Local\Programs\Python\Python314\pythonw.exe' -ArgumentList 'bot_ecoute.py' -WorkingDirectory 'C:\Users\Dell\hub_deals_bot'
```

Preuve : `bot_ecoute_log.txt` du worktree contient `=== Demarrage de l'ecoute ===` et **pas**
« Inscriptions FERMEES » ; un processus `pythonw` est présent.

- [ ] **Étape 3 : parcours réel de l'abonné** (point 1 de la spec)

L'utilisateur, depuis un 2e compte Telegram : `/start mauvais_code_2026` → refus ; lien
d'invitation → boutons ; choisir une ville **qui a une affaire dans le dernier relevé** (la
lire dans le journal du relevé de 13h du jour) ; `/ville` → boutons ; `/stop` → désabonné ;
`/start` sans code → boutons, puis rechoisir la ville.

Preuve : le journal de l'écoute montre la séquence `start refuse` / `start (nouvel abonne)` /
`ville ...` / `ville (menu)` / `stop` / `start (retour)` avec le `chat_id` du 2e compte, **sans**
le code ni aucun texte brut ; la table `abonnes` de la copie contient la ligne active avec la
ville.

- [ ] **Étape 4 : envoi réel sur la copie** (point 2)

Rejouer la notification du dernier relevé de la copie, sans collecte ni sauvegarde (qui
échouerait faute de `.sauvegardes/` dans le worktree et enverrait une fausse alerte) :

Écrire dans le scratchpad un script `rejeu_releve.py` :

```python
import sqlite3, sys
sys.path.insert(0, r"C:\Users\Dell\hub_deals_bot")
import hub_deals_db
conn = sqlite3.connect(hub_deals_db.DB_PATH, timeout=30)
date = conn.execute("SELECT MAX(date_collecte) FROM offres").fetchone()[0]
hub_deals_db.log("=== VERIFICATION manuelle bot-abonnes (rejeu du releve " + date + ") ===")
if "--ecoute-seule" not in sys.argv:
    hub_deals_db.verifier_et_notifier_anomalies(conn, date)
hub_deals_db.verifier_ecoute_et_alerter(conn)
```

Le lancer **depuis PowerShell**, dans le même appel que la relecture des variables du registre
(le `foreach` de l'étape 2 : l'environnement d'un appel d'outil ne persiste pas), avec
`Set-Location C:\Users\Dell\hub_deals_bot` pour que `DB_PATH` et le journal visent la copie :

```powershell
foreach ($n in 'TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','TRAVELPAYOUTS_TOKEN','HUB_DEALS_CODE_INVITATION','TRAVELPAYOUTS_MARKER') { Set-Item "env:$n" ([Environment]::GetEnvironmentVariable($n,'User')) }
Set-Location C:\Users\Dell\hub_deals_bot; python <scratchpad>\rejeu_releve.py
```
Prévenir l'utilisateur qu'il recevra une seconde fois le message complet du jour.

Preuve : le propriétaire reçoit le message complet ; le 2e compte reçoit **uniquement** sa
ville, avec la mention « Prix repéré… » et sans « rabattement » ; le journal du worktree
contient `Abonnes : 1/1 envoye(s), 0 bloque(s), 0 echec(s), 0 sans affaire.` ; aucune alerte
« écoute arrêtée ».

- [ ] **Étape 5 : clic réel sur le lien affilié** (point 3)

L'utilisateur clique sur le lien reçu par le 2e compte, puis consulte son tableau de bord
Travelpayouts (le délai d'apparition d'un clic peut aller jusqu'à quelques heures).

Preuve : le clic apparaît **avec l'étiquette de la ville**. Si l'étiquette n'apparaît pas ou si
le clic n'est pas compté, corriger `url_aviasales()` et son test (tâche 1), recommiter, refaire
cette étape. Tant que ce point n'est pas prouvé, le signaler comme **non vérifié** — il ne bloque
pas la fusion, mais doit être tranché avant d'inviter d'autres personnes.

- [ ] **Étape 6 : écoute arrêtée** (point 4)

Arrêter le `pythonw` de l'écoute (`Stop-Process` sur son PID, repéré par sa ligne de commande
`bot_ecoute.py`), attendre 11 minutes, puis rejouer uniquement le contrôle, exactement comme à
l'étape 4 mais avec `python <scratchpad>\rejeu_releve.py --ecoute-seule`.

Preuve : l'alerte « l'ecoute du bot est arretee » est reçue et journalisée.

- [ ] **Étape 7 : nettoyage du worktree et fusion**

```bash
rm /c/Users/Dell/hub_deals_bot/flight_deals.db
cd /c/Users/Dell/hub_deals_bot && python -m unittest discover -s tests
```

Puis présenter à l'utilisateur les options de fin de branche
(`superpowers:finishing-a-development-branch`). La fusion recommandée est `--no-ff` dans
`master` **en dehors de 12h55–13h10**, poussée après accord, la branche supprimée ensuite
(usage du dépôt depuis le 2026-09-11).

- [ ] **Étape 8 : installation de la tâche et redémarrage de session** (points 5 et 6, après fusion)

Lancer l'installation élevée, faire valider l'UAC, relire le résultat :

```powershell
Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Users\Dell\hub_deals\taches\installer_bot_ecoute.ps1'
Get-Content "$env:TEMP\installer_bot_ecoute.txt"
Export-ScheduledTask -TaskName 'Bot vols - ecoute'
```

Preuve : `OK`, et l'export montre `ExecutionTimeLimit PT0S`, `LogonTrigger`, `pythonw.exe`,
`WorkingDirectory C:\Users\Dell\hub_deals`. Démarrer la tâche (`Start-ScheduledTask`), vérifier
le processus et `bot_ecoute_log.txt` du checkout principal. **L'abonné de test n'existe pas dans
la vraie base** : lui faire refaire `/start` avec le lien d'invitation et choisir sa ville.

L'utilisateur ferme puis rouvre sa session. Preuve : un seul processus `pythonw` pour
`bot_ecoute.py`, nouvelle ligne `=== Demarrage de l'ecoute ===`, témoin `derniere_ecoute`
récent dans `flight_deals.db`.

Au relevé automatique suivant (13h) : le journal du relevé contient la ligne `Abonnes : ...`
exacte, aucune alerte « écoute arrêtée », et les contrôles habituels restent sains (code 0,
9/9 hubs, sauvegarde = distant, aucun processus résiduel **autre que l'écoute**).
