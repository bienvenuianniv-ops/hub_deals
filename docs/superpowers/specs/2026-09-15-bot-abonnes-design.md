# Bot Telegram multi-abonnés (test privé) — design

## Contexte

`hub_deals` détecte chaque jour des baisses de prix sur les vols au départ de 5 villes
(Dakar, Abidjan, Lomé, Kinshasa, Brazzaville) et les envoie par Telegram à **un seul
destinataire**, le propriétaire (`TELEGRAM_CHAT_ID`). Le bot `@ianniv_vols_bot` ne fait
qu'envoyer : il ne lit jamais les messages qu'on lui adresse.

L'objectif est de tester l'intérêt du service auprès d'autres personnes avant tout
investissement plus lourd (serveur, app mobile). Ce chantier est le premier morceau d'un
découpage en quatre, établi au brainstorming :

| # | Morceau | Statut |
|---|---|---|
| A | Liens affiliés Travelpayouts | **inclus ici** (indispensable à la mesure du test) |
| B | Plusieurs abonnés | **ce chantier** |
| C | Collecte sur serveur | hors scope |
| D | Vitrine / app mobile | hors scope |

### Constat à l'origine du morceau A

Les liens envoyés aujourd'hui (`construire_lien`, `construire_bloc`) sont des liens
Aviasales **sans identifiant d'affilié** : aucun clic ne rapporte rien et aucun n'est
mesurable dans le tableau de bord Travelpayouts.

### Règles Travelpayouts à respecter (recherche du 2026-09-15)

- Les messageries (Telegram) sont une source de trafic **autorisée**.
- **Spam interdit** → abonnement volontaire uniquement, désabonnement immédiat.
- **Pas d'allégation de remise trompeuse** → les prix viennent d'un cache (jusqu'à 7 jours) :
  chaque message d'abonné porte la mention « prix repéré, à vérifier ».
- **Pas de marque Aviasales** dans le nom du bot ou une publicité.
- Non vérifié (page officielle des conditions en 403) : l'acceptation du projet dans le
  programme Aviasales et la méthode de paiement vers le pays du propriétaire. **À confirmer
  par le propriétaire dans son tableau de bord**, indépendamment de ce chantier.

## Décisions actées (brainstorming du 2026-09-15)

1. **Canal : bot Telegram** (le bot existe, pas de store, pas de validation Apple/Google).
2. **L'abonné choisit sa ville de départ, et rien d'autre.** Destinations et budget viendront
   plus tard si le test le justifie ; le modèle ne doit pas l'empêcher.
3. **Test privé** (10 à 30 personnes), tout sur le portable, base SQLite actuelle.
4. **Accès par code d'invitation** via lien profond `t.me/ianniv_vols_bot?start=CODE`.
5. **Approche A : un programme d'écoute permanent sur le portable**, plutôt que le traitement
   des commandes au moment du relevé (confirmation jusqu'à 24 h plus tard) ou un bot sur
   Render + Neon (travail du lancement public, écarté).

## Architecture

```
                 Telegram
        getUpdates │   ▲ sendMessage
                   ▼   │
   ┌────────────────────────┐        ┌────────────────────────────┐
   │ bot_ecoute.py          │        │ hub_deals_db.py (relevé)   │
   │ tâche « à l'ouverture  │        │ tâche quotidienne 13h      │
   │ de session », pythonw  │        │ + ouverture de session     │
   └──────────┬─────────────┘        └──────────────┬─────────────┘
              │ écrit                               │ lit
              ▼                                     ▼
        ┌──────────────────── flight_deals.db ────────────────────┐
        │ abonnes   etat_bot   (+ tables existantes inchangées)    │
        └──────────────────────────────────────────────────────────┘
```

**Règle de séparation :** seul `bot_ecoute.py` appelle `getUpdates`. Le relevé n'appelle
que `sendMessage`. Deux lecteurs simultanés de `getUpdates` provoquent un HTTP 409
(« Conflict ») côté Telegram.

### 1. `bot_ecoute.py` — programme d'écoute

- Boucle de *long polling* sur `getUpdates` (`timeout` de l'ordre de 50 s).
- Commandes :
  - `/start CODE` : code valide → affiche les 5 villes en boutons (clavier *inline*).
  - `/start` sans code, par un abonné **déjà connu** (actif ou inactif) → réactive et
    réaffiche les boutons. Par un inconnu → « Ce bot est pour l'instant sur invitation. »
  - `/start` avec un code faux → même réponse que sans code (on ne dit pas que le code est faux).
  - `/ville` → réaffiche les boutons (abonné connu uniquement).
  - `/stop` → statut inactif, « Tu es désabonné. /start pour revenir. »
  - Appui sur un bouton → enregistre la ville, `answerCallbackQuery`, message de confirmation.
  - Tout autre message → rappel des commandes disponibles.
- Les traitements sont **idempotents** (choisir deux fois la même ville ne change rien) : un
  redémarrage entre traitement et confirmation de l'`offset` peut rejouer une mise à jour
  sans dommage. L'`offset` n'est donc pas persisté.
- N'appelle **jamais** l'API des prix.
- Écrit l'horodatage de chaque retour de `getUpdates` (avec ou sans message) dans `etat_bot`.
- Lancé par une **deuxième tâche planifiée** : déclencheur à l'ouverture de session,
  `pythonw.exe`, instance unique (`MultipleInstancesPolicy = IgnoreNew`), redémarrage en cas
  d'échec, sans restriction batterie (même réglage que « Traqueur de vols »).

### 2. Données

```sql
CREATE TABLE IF NOT EXISTS abonnes (
    chat_id       INTEGER PRIMARY KEY,
    prenom        TEXT,
    ville_depart  TEXT,          -- clé de RABATTEMENT ("Lome", sans accent) ; NULL tant que non choisie
    actif         INTEGER NOT NULL DEFAULT 1,
    inscrit_le    TEXT NOT NULL, -- UTC ISO
    modifie_le    TEXT NOT NULL, -- UTC ISO
    motif_inactif TEXT           -- 'stop' | 'bloque' | NULL
);

CREATE TABLE IF NOT EXISTS etat_bot (
    cle    TEXT PRIMARY KEY,     -- 'derniere_ecoute'
    valeur TEXT NOT NULL
);
```

- Un désabonnement ne supprime jamais la ligne (historique du test).
- La ville est stockée sous sa **clé de `RABATTEMENT`** ; l'affichage (« Lomé ») est dérivé.
  Un test structurel vérifie que chaque bouton correspond à une clé de `RABATTEMENT`.
- Les deux programmes ouvrent la base avec `timeout=30` et gardent des transactions courtes.

### 3. Relevé de 13h — envoi aux abonnés

Dans `verifier_et_notifier_anomalies`, **après** l'envoi du message complet au propriétaire
(inchangé, y compris le détail « rabattement mesuré / estimé ») :

1. Charger les abonnés `actif = 1` avec une `ville_depart` non nulle, **en excluant
   `TELEGRAM_CHAT_ID`** (le propriétaire reçoit déjà tout, pas de doublon).
2. Pour chaque abonné, filtrer les groupes d'anomalies (issus de `grouper_anomalies`) aux
   lignes dont `ville_depart` est la sienne. Groupe vide → ignoré.
3. Aucun groupe restant → **aucun message** (pas de « rien aujourd'hui »).
4. Sinon, construire un message d'abonné (format ci-dessous), le découper avec
   `decouper_message` si nécessaire, l'envoyer.

`envoyer_telegram` reçoit un paramètre `chat_id` optionnel (défaut : `TELEGRAM_CHAT_ID`) et
doit pouvoir distinguer, pour l'appelant, succès / bloqué (403) / trop de requêtes (429 +
`retry_after`) / autre échec. La forme exacte (booléen enrichi ou objet de résultat) est
laissée au plan ; l'existant qui attend un booléen ne doit pas casser.

### 4. Format du message d'abonné

```
<b>1 bonne affaire au départ de Dakar</b>

<b>Sao Paulo</b> via Abidjan
1716€ (-23%) — 500€ de moins que d'habitude
https://www.aviasales.com/search/ABJ0302SAO1?<paramètres affiliés>

<i>Prix repéré aujourd'hui, il peut avoir changé : vérifie avant de réserver.</i>
```

- Pas de détail de rabattement (information technique réservée au propriétaire).
- La mention « prix repéré… » est **obligatoire** et figure dans chaque morceau si le
  message est découpé.
- Prix et pourcentage = valeurs après `corriger_anomalies` (identiques à celles du message
  du propriétaire pour cette ville).

### 5. Liens affiliés

- Variables d'environnement : `TRAVELPAYOUTS_MARKER` (identifiant d'affilié).
- Étiquette de ville en minuscules sans accent (`dakar`, `abidjan`, `lome`, `kinshasa`,
  `brazzaville`) pour les abonnés ; étiquette `proprietaire` pour le message complet.
- ⚠️ **Syntaxe exacte du marker et de l'étiquette (sub_id) chez Aviasales non vérifiée.**
  Le plan commence par la déterminer ; elle n'est tenue pour acquise qu'après qu'un clic réel
  apparaît dans le tableau de bord Travelpayouts **avec** son étiquette.
- `TRAVELPAYOUTS_MARKER` absent → liens sans paramètres affiliés (comportement actuel) et
  une ligne de journal par relevé ; l'envoi n'est jamais bloqué pour ça.

### 6. Code d'invitation et plafond

- `HUB_DEALS_CODE_INVITATION` (variable d'environnement, portée User). Contraintes Telegram
  sur le paramètre de lien profond : `[A-Za-z0-9_-]`, 64 caractères maximum.
- Variable absente → **aucune nouvelle inscription possible** (fail closed) ; les abonnés
  existants continuent de fonctionner. Journalisé au démarrage de l'écoute.
- Changer le code coupe les nouvelles inscriptions sans toucher aux abonnés existants.
- `PLAFOND_ABONNES = 50` (constante) : compte les abonnés **actifs**. Atteint → « Le test
  est complet pour le moment. » Un abonné connu **inactif** qui se réactive est refusé de la
  même façon si le plafond est atteint ; un abonné **actif** n'est jamais concerné (`/start`
  lui réaffiche simplement les boutons).

## Gestion des erreurs

Principe hérité du 2026-09-11 (24 jours d'alertes perdues derrière un journal qui affirmait
un envoi non vérifié) : **aucun échec silencieux, aucun « envoyé » non vérifié.**

### Envoi aux abonnés

| Situation | Comportement |
|---|---|
| Échec quelconque sur les abonnés | **n'affecte jamais** le message du propriétaire, envoyé avant |
| HTTP 403 (bot bloqué) | `actif = 0`, `motif_inactif = 'bloque'`, journalisé, pas de nouvel essai |
| HTTP 429 | attendre `retry_after`, **un** nouvel essai, puis échec journalisé |
| Autre erreur (réseau, 400…) | journalisée, abonné suivant |
| Cadence | pause courte entre deux envois (limite Telegram ≈ 30 msg/s) |
| Exception inattendue dans la boucle | rattrapée par abonné ; le relevé continue (sauvegarde, fin d'exécution) |

Compte rendu unique et exact, par exemple :
`Abonnes : 12/14 envoye(s), 1 bloque, 1 echec (HTTP 400), 3 sans affaire.`

### Programme d'écoute

- **Réseau indisponible** : attente croissante (plafonnée à quelques minutes) puis reprise,
  sans plantage.
- **HTTP 409** (autre lecteur `getUpdates`) : journalisé explicitement, attente, reprise.
- **Plantage** : `sys.excepthook = journaliser_plantage` (même mécanisme que le relevé) ;
  la tâche planifiée relance.
- **Journal séparé** `bot_ecoute_log.txt` (gitignoré), avec `masquer_secrets` — le token du
  bot figure dans chaque URL d'appel. Le code d'invitation est aussi masqué.
- Les messages reçus ne sont **pas** journalisés en entier (données personnelles des
  invités) : on journalise la commande et le `chat_id`.

### Écoute arrêtée sans que personne le sache

À la **fin** du relevé (≈ 5 min après son démarrage, ce qui laisse à l'écoute lancée à la
même ouverture de session le temps de faire son premier appel), lire
`etat_bot.derniere_ecoute`. Si elle a **plus de 10 minutes** (ou n'existe pas alors que des
abonnés existent), envoyer au propriétaire :
« Problème technique — l'écoute du bot est arrêtée. Les invités qui tapent /start n'ont
pas de réponse. »

> Écart avec la présentation orale (« plus de 2 heures ») : le long polling rafraîchit
> l'horodatage au moins toutes les ~50 s, donc 10 minutes suffisent. Un seuil de 2 heures
> aurait été faussé par une sortie de veille : la tâche rattrapée par `StartWhenAvailable`
> aurait vu un horodatage ancien d'avant la veille. Mesuré en fin de relevé, l'horodatage a
> eu le temps d'être rafraîchi après le réveil.

## Tests

TDD : chaque test écrit et **vu échouer** avant le code.

- **Aucun appel réseau** : fausse couche Telegram injectée ; `log()` neutralisé dans toute
  fonction qui l'appelle. Surveiller la durée de la suite (0,36 s → 2,56 s avait trahi un
  appel réseau involontaire le 2026-08-16).
- **Ne jamais recopier une valeur de `RABATTEMENT`** ni la liste des villes dans un test : la
  lire depuis la table.

Cas couverts :

- filtrage des groupes par ville ; groupe multi-villes réduit à la bonne ligne ;
- abonné sans affaire → aucun envoi ;
- message d'abonné : mention « à vérifier » présente (y compris dans chaque morceau découpé),
  paramètres affiliés présents, **aucune** mention de rabattement ;
- message du propriétaire envoyé même si tous les envois abonnés échouent ;
- propriétaire abonné → pas de doublon ;
- 403 → inactif/`bloque` ; 429 → exactement un nouvel essai ;
- compte rendu du journal exact dans chaque combinaison ;
- `/start` : bon code, code faux, sans code (inconnu / connu inactif), plafond atteint,
  variable de code absente ;
- `/ville`, `/stop`, appui bouton, message libre ;
- idempotence d'une mise à jour rejouée ;
- alerte « écoute arrêtée » : horodatage récent / ancien / absent avec et sans abonnés ;
- `MARKER` absent → lien sans paramètres, envoi non bloqué ;
- test structurel : boutons de ville ⊂ clés de `RABATTEMENT` ;
- masquage du token et du code d'invitation dans `bot_ecoute_log.txt` ;
- **« aucune fenêtre » lancé via `pythonw` lui-même** (depuis le runner, le test passait sans
  correctif sur le relevé).

## Vérification en conditions réelles (condition de fusion)

1. Depuis un 2e compte Telegram : code faux → refus ; bon code → boutons → ville ;
   `/ville` ; `/stop` ; `/start` sans code → réactivation.
2. Relevé lancé à la main (`Start-ScheduledTask`) : message complet reçu par le
   propriétaire, message filtré reçu par l'abonné de test ; compte rendu exact au journal.
3. **Clic réel** sur un lien d'abonné → visible dans le tableau de bord Travelpayouts avec
   l'étiquette de ville.
4. Écoute arrêtée volontairement → alerte reçue au relevé suivant.
5. Fermeture/réouverture de session → l'écoute redémarre (processus `pythonw` présent,
   horodatage rafraîchi).
6. Aucun processus `pythonw`/`git` résiduel en trop, token absent des deux journaux.

## Hors scope

Choix des destinations, budget maximum, serveur Render/Neon, WhatsApp, app mobile, codes
d'invitation multiples, commandes d'administration dans le bot, message « rien
aujourd'hui ». **`anomaly_detection.py` et la table `RABATTEMENT` ne sont pas modifiés.**

## Travail

Branche `bot-abonnes` dans un worktree séparé (`C:\Users\Dell\hub_deals_bot`) : le relevé
quotidien tourne depuis le checkout principal et ne doit pas exécuter de code en cours.
Fusion `--no-ff` dans `master` uniquement après la vérification réelle.
