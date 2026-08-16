# Rabattement mesuré au moment de l'alerte — plan d'implémentation

> **Pour les agents :** SOUS-COMPÉTENCE REQUISE — utiliser `superpowers:subagent-driven-development`
> (recommandé) ou `superpowers:executing-plans` pour exécuter ce plan tâche par tâche. Les étapes
> utilisent des cases à cocher (`- [x]`) pour le suivi.

**Objectif :** afficher dans les alertes Telegram un total fondé sur le rabattement réellement
mesuré au moment de l'envoi, au lieu de la valeur figée de `RABATTEMENT`.

**Architecture :** trois couches séparées dans `hub_deals_db.py` — `mesurer_rabattements()` fait
les appels réseau, `corriger_anomalies()` est une fonction pure qui applique le décalage et
re-trie, et `verifier_et_notifier_anomalies()` les enchaîne puis compose le message. Aucune
écriture en base : la correction est d'affichage uniquement, l'historique et la détection ne
bougent pas.

**Pile technique :** Python 3, `requests`, `sqlite3`, `unittest` (bibliothèque standard). Aucune
nouvelle dépendance.

**Spec :** `docs/superpowers/specs/2026-08-16-rabattement-mesure-alerte-design.md`

## Contraintes globales

- **Aucun appel réseau dans les tests.** La fonction de prix est injectée en paramètre. Modèle
  existant : `tests/test_recherche.py`.
- **Aucune écriture dans `flight_deals.db`.** Ni `total_estime`, ni l'historique, ni
  `anomaly_detection.py` ne sont modifiés.
- **`anomaly_detection.py` n'est pas touché** — il doit rester une logique pure sans réseau,
  puisque `detect_anomalies.py` s'en sert aussi.
- **Commentaires et messages en français sans accents** dans le code Python, comme le reste du
  projet. Les accents sont admis dans la documentation Markdown.
- **Les secrets ne doivent jamais atteindre une sortie.** Toute exception `requests` journalisée
  passe par `log()`, qui appelle `masquer_secrets()`. Ne jamais `print()` une exception réseau.
- **Une erreur de mesure ne doit jamais empêcher l'envoi de la notification.** Une alerte avec
  des totaux non corrigés vaut mieux qu'une alerte perdue.
- **`PAUSE_ENTRE_APPELS`** (0.4 s) est respectée entre deux appels réels, jamais dans les tests.
- Suite verte à chaque commit : `python -m unittest discover -s tests`.
- Suite actuelle : **80 tests**.

---

### Tâche 1 : Mesure des rabattements

Interroge l'API pour les couples (ville, hub) donnés, avec repli sur `RABATTEMENT`. C'est la
seule couche qui fait du réseau.

**Fichiers :**
- Modifier : `hub_deals_db.py`
- Modifier : `tests/test_hub_deals_db.py`

**Interfaces :**
- Consomme : `HUBS`, `RABATTEMENT`, `VILLE_IATA`, `get_prix_route`, `PAUSE_ENTRE_APPELS`, `log`
- Produit :
  - `mesurer_rabattements(couples, get_prix=None, pause=True) -> dict`
    — clé `(ville, hub_nom)`, valeur `{"prix": float, "table": float, "mesure": bool}`.
    `couples` est un itérable de `(ville, hub_nom)` où `hub_nom` est le **nom** du hub
    (« Paris »), tel que porté par `hub_origine` dans les anomalies — pas le code IATA.

- [x] **Étape 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_hub_deals_db.py` :

```python
class TestMesurerRabattements(unittest.TestCase):
    """La fonction de prix est injectee : aucun appel reseau ici."""

    def _prix(self, table):
        """Fabrique une fausse get_prix_route a partir d'un dict
        {(origine, destination): prix}."""
        def get_prix(origine, destination):
            valeur = table.get((origine, destination))
            if valeur is None:
                return {}
            return {"price": valeur, "departure_at": "2026-09-05T10:00:00+00:00"}
        return get_prix

    def test_renvoie_le_prix_api_quand_il_existe(self):
        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Casablanca")],
            get_prix=self._prix({("DKR", "CMN"): 468}), pause=False)

        self.assertEqual(mesures[("Dakar", "Casablanca")]["prix"], 468)
        self.assertEqual(mesures[("Dakar", "Casablanca")]["table"], 400)
        self.assertTrue(mesures[("Dakar", "Casablanca")]["mesure"])

    def test_replie_sur_la_table_quand_l_api_ne_repond_rien(self):
        """Cas le plus frequent : 17 des 40 segments n'ont aucun prix,
        dont CDG pour les cinq villes."""
        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Paris")], get_prix=self._prix({}), pause=False)

        self.assertEqual(mesures[("Dakar", "Paris")]["prix"], 300)
        self.assertEqual(mesures[("Dakar", "Paris")]["table"], 300)
        self.assertFalse(mesures[("Dakar", "Paris")]["mesure"])

    def test_replie_sur_la_table_en_cas_d_erreur_reseau(self):
        def get_prix(origine, destination):
            raise requests.exceptions.RequestException("coupure")

        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Casablanca")], get_prix=get_prix, pause=False)

        self.assertEqual(mesures[("Dakar", "Casablanca")]["prix"], 400)
        self.assertFalse(mesures[("Dakar", "Casablanca")]["mesure"])

    def test_dedoublonne_les_couples(self):
        """Plusieurs anomalies partagent souvent le meme (ville, hub) :
        un seul appel doit etre fait."""
        appels = []

        def get_prix(origine, destination):
            appels.append((origine, destination))
            return {"price": 468, "departure_at": ""}

        hub_deals_db.mesurer_rabattements(
            [("Dakar", "Casablanca"), ("Dakar", "Casablanca"),
             ("Dakar", "Casablanca")], get_prix=get_prix, pause=False)

        self.assertEqual(len(appels), 1)

    def test_ignore_un_nom_de_hub_inconnu_sans_lever(self):
        """Un nom absent de HUBS ne doit pas faire echouer une notification."""
        mesures = hub_deals_db.mesurer_rabattements(
            [("Dakar", "Atlantide")], get_prix=self._prix({}), pause=False)

        self.assertEqual(mesures, {})

    def test_ignore_un_couple_sans_rabattement_en_table(self):
        """Abidjan n'a pas d'entree pour le hub ADD."""
        mesures = hub_deals_db.mesurer_rabattements(
            [("Abidjan", "Addis-Abeba")], get_prix=self._prix({}), pause=False)

        self.assertEqual(mesures, {})

    def test_les_noms_de_hubs_sont_uniques(self):
        """L'inversion nom -> IATA perdrait silencieusement un hub si deux
        hubs portaient le meme nom."""
        noms = [info["nom"] for info in hub_deals_db.HUBS.values()]
        self.assertEqual(len(noms), len(set(noms)))
```

Ajouter `import requests` en tête de `tests/test_hub_deals_db.py` s'il n'y est pas déjà.

- [x] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

Commande : `python -m unittest tests.test_hub_deals_db -k TestMesurerRabattements`
Attendu : `AttributeError: module 'hub_deals_db' has no attribute 'mesurer_rabattements'`

- [x] **Étape 3 : Écrire l'implémentation minimale**

Ajouter à `hub_deals_db.py`, après `classement_du_jour()` :

```python
def mesurer_rabattements(couples, get_prix=None, pause: bool = True) -> dict:
    """
    Interroge l'API pour le cout reel de chaque trajet ville -> hub.

    La table RABATTEMENT vieillit : mesure du 2026-08-16, 23 des 40
    segments ont un prix API, avec des ecarts allant de -4 % a +171 %
    selon l'anciennete de la valeur. On mesure donc au moment de
    l'alerte, sans jamais toucher a ce qui est enregistre en base.

    `couples` porte le NOM du hub (« Paris »), comme la colonne
    hub_origine des anomalies -- pas le code IATA.

    Renvoie {(ville, hub_nom): {"prix", "table", "mesure"}}. `table` est
    rendue avec la mesure pour que le calcul du decalage reste une
    fonction pure de son entree.
    """
    if get_prix is None:
        get_prix = get_prix_route

    iata_par_nom = {info["nom"]: iata for iata, info in HUBS.items()}
    mesures = {}

    # dict.fromkeys dedoublonne en preservant l'ordre : plusieurs
    # anomalies partagent souvent le meme couple, un seul appel suffit
    for ville, hub_nom in dict.fromkeys(couples):
        hub_iata = iata_par_nom.get(hub_nom)
        if hub_iata is None:
            continue  # nom inconnu : on ignore plutot que de lever

        cout = RABATTEMENT.get(ville, {}).get(hub_iata)
        if cout is None:
            continue  # pas de rabattement connu pour ce couple

        repli = {"prix": cout["prix"], "table": cout["prix"], "mesure": False}
        origine = VILLE_IATA.get(ville)
        if origine is None:
            mesures[(ville, hub_nom)] = repli
            continue

        try:
            offre = get_prix(origine, hub_iata)
        except requests.exceptions.RequestException as e:
            # log() masque les secrets : l'URL de l'exception porte le token
            log(f"   -> rabattement {origine}->{hub_iata} non mesure : {e}")
            mesures[(ville, hub_nom)] = repli
            continue

        if pause:
            time.sleep(PAUSE_ENTRE_APPELS)

        if offre and offre.get("price"):
            mesures[(ville, hub_nom)] = {
                "prix": offre["price"],
                "table": cout["prix"],
                "mesure": True,
            }
        else:
            mesures[(ville, hub_nom)] = repli

    return mesures
```

- [x] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

Commande : `python -m unittest tests.test_hub_deals_db -k TestMesurerRabattements -v`
Attendu : 7 tests PASS

- [x] **Étape 5 : Vérifier que la suite complète reste verte**

Commande : `python -m unittest discover -s tests`
Attendu : OK — 80 + 7 = **87 tests**

- [x] **Étape 6 : Commit**

```bash
git add hub_deals_db.py tests/test_hub_deals_db.py
git commit -m "feat(alerte): mesure du rabattement reel par couple ville-hub"
```

---

### Tâche 2 : Décalage d'échelle et re-tri

Fonction pure : elle prend les anomalies et les mesures, et rend des anomalies corrigées. Aucun
réseau, aucune base.

**Fichiers :**
- Modifier : `hub_deals_db.py`
- Modifier : `tests/test_hub_deals_db.py`

**Interfaces :**
- Consomme : la sortie de `mesurer_rabattements()` (tâche 1) et celle de
  `anomaly_detection.detecter_anomalies()`
- Produit :
  - `corriger_anomalies(anomalies: list, mesures: dict) -> list`
    — chaque anomalie rendue gagne la clé `rabattement_mesure` (`float` si mesuré, `None`
    sinon) ; `prix_actuel`, `moyenne_historique` et `baisse_pct` sont décalés quand la mesure
    existe. Liste re-triée par `baisse_pct` décroissante.

- [x] **Étape 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_hub_deals_db.py` :

```python
class TestCorrigerAnomalies(unittest.TestCase):
    def _anomalie(self, ville="Dakar", hub="Abidjan", prix=810.0,
                  moyenne=900.0, baisse=10.0):
        return {
            "destination": "Nairobi", "destination_code": "NBO",
            "hub": hub, "ville_depart": ville,
            "prix_actuel": prix, "moyenne_historique": moyenne,
            "ecart_type": 50.0, "z_score": 1.8, "baisse_pct": baisse,
            "methode": "z-score", "nb_releves_historique": 6,
            "date_depart": "2026-09-05T10:00:00+00:00", "lien": "/search/x",
        }

    def test_decale_le_prix_du_jour_et_la_moyenne(self):
        """Le rabattement est une constante additive de tout l'historique :
        on decale les deux du meme montant."""
        mesures = {("Dakar", "Abidjan"): {"prix": 409, "table": 200,
                                          "mesure": True}}

        [a] = hub_deals_db.corriger_anomalies([self._anomalie()], mesures)

        self.assertEqual(a["prix_actuel"], 1019)        # 810 + 209
        self.assertEqual(a["moyenne_historique"], 1109)  # 900 + 209
        self.assertEqual(a["rabattement_mesure"], 409)

    def test_preserve_l_ecart_absolu(self):
        """Le decalage ne doit pas creer ni detruire d'ecart : c'est ce qui
        garantit que le z-score reste valable."""
        mesures = {("Dakar", "Abidjan"): {"prix": 409, "table": 200,
                                          "mesure": True}}

        [a] = hub_deals_db.corriger_anomalies([self._anomalie()], mesures)

        self.assertEqual(a["moyenne_historique"] - a["prix_actuel"], 90)

    def test_recalcule_le_pourcentage_sur_l_echelle_decalee(self):
        mesures = {("Dakar", "Abidjan"): {"prix": 409, "table": 200,
                                          "mesure": True}}

        [a] = hub_deals_db.corriger_anomalies([self._anomalie()], mesures)

        self.assertEqual(a["baisse_pct"], 8.1)   # 90 / 1109

    def test_ne_decale_pas_une_anomalie_non_mesuree(self):
        mesures = {("Dakar", "Paris"): {"prix": 300, "table": 300,
                                        "mesure": False}}

        [a] = hub_deals_db.corriger_anomalies(
            [self._anomalie(hub="Paris")], mesures)

        self.assertEqual(a["prix_actuel"], 810)
        self.assertEqual(a["moyenne_historique"], 900)
        self.assertEqual(a["baisse_pct"], 10.0)
        self.assertIsNone(a["rabattement_mesure"])

    def test_ne_decale_pas_une_anomalie_sans_mesure_du_tout(self):
        [a] = hub_deals_db.corriger_anomalies([self._anomalie()], {})

        self.assertEqual(a["prix_actuel"], 810)
        self.assertIsNone(a["rabattement_mesure"])

    def test_retrie_apres_correction(self):
        """Le decalage reduit le pourcentage : sans re-tri, l'ordre affiche
        ne correspondrait plus aux pourcentages affiches.

        Le cas est choisi pour que l'ordre s'INVERSE reellement -- un jeu
        de donnees ou l'ordre resterait le meme passerait ce test meme si
        le tri etait absent, et ne prouverait donc rien."""
        anomalies = [
            self._anomalie(hub="Abidjan", prix=810, moyenne=900, baisse=10.0),
            self._anomalie(hub="Paris", prix=920, moyenne=1000, baisse=8.0),
        ]
        mesures = {
            # +300 de decalage : 90/1200 = 7,5 %, sous les 8 % de Paris
            ("Dakar", "Abidjan"): {"prix": 500, "table": 200, "mesure": True},
            ("Dakar", "Paris"): {"prix": 300, "table": 300, "mesure": False},
        }

        corrigees = hub_deals_db.corriger_anomalies(anomalies, mesures)

        self.assertEqual([a["hub"] for a in corrigees], ["Paris", "Abidjan"])
        self.assertEqual(corrigees[0]["baisse_pct"], 8.0)
        self.assertEqual(corrigees[1]["baisse_pct"], 7.5)

    def test_ne_modifie_pas_les_anomalies_d_origine(self):
        """La fonction rend de nouveaux dictionnaires : muter l'entree
        rendrait le diagnostic incoherent avec ce que la base contient."""
        origine = self._anomalie()
        mesures = {("Dakar", "Abidjan"): {"prix": 409, "table": 200,
                                          "mesure": True}}

        hub_deals_db.corriger_anomalies([origine], mesures)

        self.assertEqual(origine["prix_actuel"], 810)
        self.assertNotIn("rabattement_mesure", origine)
```

- [x] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

Commande : `python -m unittest tests.test_hub_deals_db -k TestCorrigerAnomalies`
Attendu : `AttributeError: module 'hub_deals_db' has no attribute 'corriger_anomalies'`

- [x] **Étape 3 : Écrire l'implémentation minimale**

Ajouter à `hub_deals_db.py`, juste après `mesurer_rabattements()` :

```python
def corriger_anomalies(anomalies: list, mesures: dict) -> list:
    """
    Applique le rabattement mesure au prix du jour ET a la moyenne.

    Le rabattement est une constante additive de tout l'historique d'une
    route : la meme valeur entre dans chacune de ses lignes. Decaler les
    deux du meme montant preserve donc exactement l'ecart absolu et
    l'ecart-type, donc le z-score. Seul le pourcentage change, son
    denominateur ayant augmente.

    Hypothese assumee : on substitue une constante a une autre. Le total
    affiche est « ce que vaudrait cette route si le rabattement mesure
    aujourd'hui s'appliquait a tout l'historique ». C'est la seule
    transformation qui garde tous les chiffres du message coherents.

    Les anomalies d'origine ne sont pas modifiees.
    """
    corrigees = []
    for a in anomalies:
        b = dict(a)
        mesure = mesures.get((a["ville_depart"], a["hub"]))

        if mesure and mesure["mesure"]:
            delta = mesure["prix"] - mesure["table"]
            b["prix_actuel"] = a["prix_actuel"] + delta
            b["moyenne_historique"] = a["moyenne_historique"] + delta
            if b["moyenne_historique"] > 0:
                b["baisse_pct"] = round(
                    (b["moyenne_historique"] - b["prix_actuel"])
                    / b["moyenne_historique"] * 100, 1)
            b["rabattement_mesure"] = mesure["prix"]
        else:
            b["rabattement_mesure"] = None

        corrigees.append(b)

    # le decalage change les pourcentages : sans re-tri, l'ordre affiche
    # ne correspondrait plus aux pourcentages affiches
    corrigees.sort(key=lambda x: x["baisse_pct"], reverse=True)
    return corrigees
```

- [x] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

Commande : `python -m unittest tests.test_hub_deals_db -k TestCorrigerAnomalies -v`
Attendu : 7 tests PASS

- [x] **Étape 5 : Vérifier que la suite complète reste verte**

Commande : `python -m unittest discover -s tests`
Attendu : OK — 87 + 7 = **94 tests**

- [x] **Étape 6 : Commit**

```bash
git add hub_deals_db.py tests/test_hub_deals_db.py
git commit -m "feat(alerte): decalage d'echelle par le rabattement mesure"
```

---

### Tâche 3 : Greffe sur la notification et documentation

Enchaîne les deux couches et compose le message. Rien de nouveau côté logique.

**Fichiers :**
- Modifier : `hub_deals_db.py` (`verifier_et_notifier_anomalies`)
- Modifier : `tests/test_hub_deals_db.py`
- Modifier : `README.md`, `CHANGELOG.md`

**Interfaces :**
- Consomme : `mesurer_rabattements()` (tâche 1), `corriger_anomalies()` (tâche 2)
- Produit : aucune nouvelle interface publique

- [x] **Étape 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_hub_deals_db.py` :

```python
class TestNotificationAvecRabattementMesure(unittest.TestCase):
    """envoyer_telegram est remplace par un espion : aucun envoi reel."""

    def setUp(self):
        self.envois = []
        self._envoyer = hub_deals_db.envoyer_telegram
        self._mesurer = hub_deals_db.mesurer_rabattements
        hub_deals_db.envoyer_telegram = lambda msg: self.envois.append(msg)

        self.conn = sqlite3.connect(":memory:")
        hub_deals_db.init_db(self.conn)
        # une route jugee anormalement basse au dernier releve
        for date, total in (("2026-08-10 10:00:00", 900),
                            ("2026-08-11 10:00:00", 900),
                            ("2026-08-12 10:00:00", 900),
                            ("2026-08-13 10:00:00", 810)):
            self.conn.execute("""
                INSERT INTO offres (date_collecte, ville_depart, hub_origine,
                    destination_code, destination_nom, prix_vol_hub,
                    rabattement, total_estime, date_depart, lien)
                VALUES (?, 'Dakar', 'Abidjan', 'NBO', 'Nairobi', 0, 200, ?, '', '/x')
            """, (date, total))
        self.conn.commit()

    def tearDown(self):
        hub_deals_db.envoyer_telegram = self._envoyer
        hub_deals_db.mesurer_rabattements = self._mesurer
        self.conn.close()

    def test_le_message_affiche_le_rabattement_mesure(self):
        hub_deals_db.mesurer_rabattements = lambda couples, **kw: {
            ("Dakar", "Abidjan"): {"prix": 409, "table": 200, "mesure": True}}

        hub_deals_db.verifier_et_notifier_anomalies(
            self.conn, "2026-08-13 10:00:00")

        self.assertEqual(len(self.envois), 1)
        self.assertIn("Rabattement mesure ce jour", self.envois[0])
        self.assertIn("409", self.envois[0])
        self.assertIn("1019", self.envois[0])   # 810 + 209

    def test_le_message_signale_un_rabattement_non_mesure(self):
        hub_deals_db.mesurer_rabattements = lambda couples, **kw: {
            ("Dakar", "Abidjan"): {"prix": 200, "table": 200, "mesure": False}}

        hub_deals_db.verifier_et_notifier_anomalies(
            self.conn, "2026-08-13 10:00:00")

        self.assertIn("non mesure", self.envois[0])
        self.assertIn("810", self.envois[0])   # non decale

    def test_une_erreur_de_mesure_n_empeche_pas_la_notification(self):
        """Une alerte avec des totaux non corriges vaut infiniment mieux
        qu'une alerte perdue."""
        def exploser(couples, **kw):
            raise RuntimeError("panne inattendue")
        hub_deals_db.mesurer_rabattements = exploser

        hub_deals_db.verifier_et_notifier_anomalies(
            self.conn, "2026-08-13 10:00:00")

        self.assertEqual(len(self.envois), 1)
        self.assertIn("810", self.envois[0])
```

- [x] **Étape 2 : Lancer les tests pour vérifier qu'ils échouent**

Commande : `python -m unittest tests.test_hub_deals_db -k TestNotificationAvecRabattementMesure`
Attendu : échec sur `Rabattement mesure ce jour` absent du message

- [x] **Étape 3 : Écrire l'implémentation minimale**

Dans `hub_deals_db.py`, remplacer le corps de `verifier_et_notifier_anomalies()` situé après le
`if not anomalies:` par :

```python
    # cout reel du trajet vers le hub, mesure maintenant : la table
    # RABATTEMENT vieillit (jusqu'a +171 % d'ecart mesure le 2026-08-16)
    couples = [(a["ville_depart"], a["hub"]) for a in anomalies]
    try:
        mesures = mesurer_rabattements(couples)
    except Exception as e:
        # une alerte aux totaux non corriges vaut mieux qu'une alerte perdue
        log(f"   -> mesure des rabattements impossible : {e}")
        mesures = {}
    anomalies = corriger_anomalies(anomalies, mesures)

    nb_mesures = sum(1 for a in anomalies if a["rabattement_mesure"] is not None)
    log(f"Rabattement mesure pour {nb_mesures}/{len(anomalies)} anomalie(s).")

    lignes = [f"<b>{len(anomalies)} bonne(s) affaire(s) detectee(s) !</b>\n"]
    for a in anomalies:
        if a["rabattement_mesure"] is not None:
            note = f"Rabattement mesure ce jour : {a['rabattement_mesure']:.0f}€"
        else:
            note = "Rabattement estime, non mesure ce jour"
        lignes.append(
            f"\n<b>{a['destination']}</b> (depuis {a['hub']}, au depart de {a['ville_depart']})\n"
            f"{a['prix_actuel']:.0f}€ (moyenne habituelle : {a['moyenne_historique']:.0f}€, "
            f"-{a['baisse_pct']:.0f}%)\n"
            f"{note}\n"
            f"https://www.aviasales.com{a['lien']}"
        )
    message = "\n".join(lignes)
    envoyer_telegram(message)
    log(f"Notification Telegram envoyee pour {len(anomalies)} anomalie(s).")
```

- [x] **Étape 4 : Lancer les tests pour vérifier qu'ils passent**

Commande : `python -m unittest tests.test_hub_deals_db -k TestNotificationAvecRabattementMesure -v`
Attendu : 3 tests PASS

- [x] **Étape 5 : Vérifier que la suite complète reste verte**

Commande : `python -m unittest discover -s tests`
Attendu : OK — 94 + 3 = **97 tests**

- [x] **Étape 6 : Documenter dans `README.md`**

Dans la section « Détection d'anomalie », ajouter après la description du seuil :

```markdown
### Rabattement mesuré à l'alerte

Le total stocké en base utilise la table `RABATTEMENT`, qui vieillit : mesuré le 2026-08-16,
l'écart entre la table et l'API va de −4 % à +171 % selon l'ancienneté de la valeur, et 17 des
40 segments n'ont aucun prix API (dont `CDG` pour les cinq villes).

Au moment d'envoyer une alerte, le coût réel du trajet ville → hub est donc mesuré, et appliqué
**à la fois** au prix du jour et à la moyenne historique — le rabattement étant une constante
additive de tout l'historique d'une route, ce décalage préserve l'écart absolu et le z-score.
Chaque ligne d'alerte indique si le rabattement a été mesuré ou s'il vient de la table.

Cette correction est **d'affichage uniquement** : rien n'est réécrit en base, et la détection
travaille toujours sur les mêmes valeurs qu'avant.
```

- [x] **Étape 7 : Documenter dans `CHANGELOG.md`**

Ajouter en tête, sous une entrée `## 2026-08-16` existante ou nouvelle, section `### Corrigé` :

```markdown
- **Les alertes Telegram annonçaient des totaux faux.** `total_estime` additionne le prix du vol
  hub → destination et une valeur de `RABATTEMENT` écrite en dur. Mesure des 40 segments le
  2026-08-16 : le problème n'est pas un sous-dimensionnement uniforme mais du **vieillissement** —
  Kinshasa et Lomé, relevés la veille par API, collent à +0 %, tandis que Brazzaville → Lagos
  dérive de **+171 %**, Abidjan → Nairobi de +136 % et Dakar → Abidjan de +104 %. Trois segments
  sont au contraire **sur**-estimés. Le coût réel du trajet ville → hub est désormais mesuré au
  moment de l'alerte et appliqué au prix du jour **et** à la moyenne historique : le rabattement
  étant une constante additive de tout l'historique d'une route, ce décalage préserve l'écart
  absolu et le z-score, et seul le pourcentage change. Chaque ligne indique si le rabattement a
  été mesuré ou vient de la table — 17 des 40 segments n'ont aucun prix API, dont `CDG` pour les
  cinq villes. Correction d'affichage uniquement : la base et la détection sont inchangées, donc
  l'historique reste comparable. 80 → 97 tests.
```

- [x] **Étape 8 : Commit**

```bash
git add hub_deals_db.py tests/test_hub_deals_db.py README.md CHANGELOG.md
git commit -m "feat(alerte): affiche le rabattement mesure dans les notifications"
```

---

## Vérification en conditions réelles (après la tâche 3)

À faire manuellement, avec le vrai token et la vraie base — conformément à l'usage du projet.

- [x] Relever `SELECT COUNT(*) FROM offres` **avant** le relevé.
- [x] Lancer `python hub_deals_db.py` (vrai relevé, vraie notification Telegram).
- [x] Vérifier dans le journal la ligne `Rabattement mesure pour N/M anomalie(s).`
- [x] Sur le message Telegram reçu : vérifier qu'au moins une ligne porte
      `Rabattement mesure ce jour` et au moins une autre `Rabattement estime, non mesure ce jour`
      — les segments `CDG` garantissent le second cas.
- [x] Vérifier que les pourcentages affichés décroissent bien de haut en bas (preuve du re-tri).
- [x] Pour une ligne mesurée, recalculer à la main : `total affiché − total en base` doit égaler
      `rabattement mesuré − rabattement de la table`.
- [x] Vérifier que les `total_estime` stockés sont **identiques** à ce qu'ils auraient été sans
      ce changement (comparer une ligne du relevé à `prix_vol_hub + rabattement` de la table).
- [x] `grep` le journal pour le token : aucune occurrence en clair.
- [x] Confirmer que la tâche planifiée « Traqueur de vols » tourne toujours (`LastTaskResult = 0`).

## Après la vérification

- [ ] Fusionner dans `master` en `--no-ff` (usage du projet), pousser sur `origin`.
- [x] Mettre à jour `hub_deals_AUDIT.md` avec les mesures relevées.
