# Page publique quotidienne — design

## Contexte

Le but reste **d'élargir le programme à des abonnés utiles**. Le cadrage du 2026-09-22 a
fait apparaître deux obstacles que le bot Telegram, seul, ne peut pas franchir.

### a. Certaines personnes n'ont pas Telegram

Contrainte donnée par le propriétaire en cadrant l'acquisition. Le bot est aujourd'hui le
**seul** accès au produit : qui n'a pas Telegram n'a rien.

Automatiser WhatsApp n'est pas une réponse. L'API officielle exige un compte entreprise
Meta vérifié, un prestataire payant et des modèles de messages validés à l'avance ; les
bibliothèques non officielles violent les conditions d'usage et font bannir le numéro
personnel. **Décision : ne pas construire de bot WhatsApp.** Construire ce qui *voyage
dans* WhatsApp — un lien.

### b. Le bot se tait, et le silence chasse les abonnés

Mesure du 2026-09-22, 114 relevés rejoués avec le vrai détecteur (chaque date ne voyant
que les données antérieures) :

| famille | affaires / relevé | jours muets |
|---|---:|---:|
| villes rabattues vers plusieurs hubs (Kinshasa, Lomé, Dakar, Abidjan, Brazzaville) | 1,6 à 2,6 | 21 à 38 % |
| villes **résidentes**, leur propre et unique hub (Casablanca → Johannesburg) | 0,2 à 0,7 | 54 à 82 % |

Ce n'est pas un problème de calibrage : en retirant tout le plancher en euros, les
résidentes ne passent que de 71 % à 65 % de jours muets, pendant que les témoins ne
bougent pas (1,98 → 1,99 affaires par relevé). Elles n'ont que 82 routes à comparer contre
585. **Aucun seuil ne crée des routes.**

Le clavier a donc été restreint aux 5 bonnes villes le 2026-09-22 (`a7fd8a1`). Mais un
abonné de Dakar reste sans message 38 % des jours, et les 8 villes retirées n'ont plus
aucun accès au produit.

**Une page n'a pas de jour muet.** Elle montre ce qu'il y a, toutes villes confondues. Un
visiteur voit immédiatement s'il y a quelque chose pour lui, au lieu de s'abonner et
d'attendre dans le vide.

## Objectif

Une **adresse unique, valable tous les jours**, qu'on colle dans un groupe WhatsApp, qui
affiche les affaires du jour sans exiger aucune application, conserve les liens
d'affiliation, et sert d'entonnoir vers le bot pour ceux qui ont Telegram.

## Décisions de cadrage (2026-09-22)

| Question | Décision | Pourquoi |
|---|---|---|
| Hébergement | **GitHub Pages**, branche `gh-pages` | Gratuit et toujours disponible. Render Free s'endort après 15 min : un visiteur venu d'un groupe attendrait 30 s devant une page blanche, sur le chemin même du revenu |
| Qui fabrique la page | **Le relevé**, en fin d'exécution | Il calcule déjà les affaires. Une GitHub Action lisant le dump n'apporterait aucune indépendance — le dump vient du portable de toute façon — et ajoute une pièce mobile |
| Où publier | Branche **`gh-pages`** dédiée | `/docs` sur `master` ajouterait un commit de données par jour sur la branche de code, qui deviendrait illisible |
| Villes affichées | **Les 13**, groupées par ville | Personne n'est exclu. Un visiteur de Nairobi voit tout de suite s'il y a quelque chose pour lui |
| Villes sans affaire | **Affichées quand même**, avec une ligne honnête | Les masquer laisserait croire que la ville n'est pas couverte |
| Fraîcheur | **Le jour seul**, une page réécrite chaque matin | Le plus simple à construire et à comprendre. Une archive multiplierait les fichiers pour un bénéfice non démontré |
| Villes non abonnables | **Liste d'attente** | Enregistre la demande réelle sans promettre une alerte quotidienne qu'on ne tiendrait pas. C'est la donnée qui dira où ajouter des hubs |
| Code d'invitation | ~~La page l'ouvre, assumé~~ → **la page ne porte jamais le code** (décision du 2026-09-26, relecture) | L'historique public de `gh-pages` rendrait tout code publié irrévocable. Les villes servies mènent à « Demander une invitation » (liste d'attente) ; le propriétaire invite à la main |

## Architecture

Trois unités, séparées par ce qu'elles savent faire :

```
   relevé (hub_deals_db.py)
      │  groupes déjà calculés
      ▼
   page.py            ← PUR : des affaires en entrée, du HTML en sortie.
      │                 Aucun réseau, aucun git, aucune horloge implicite.
      ▼
   publier_page()     ← worktree git + push, patron de sauvegarde.py
      │
      ▼
   branche gh-pages ──► https://bienvenuianniv-ops.github.io/hub_deals/
```

### `page.py` — le rendu, pur

`rendre(affaires_par_ville, quand, age_heures) -> str`

Un seul fichier HTML, CSS en ligne, **conçu pour un téléphone d'abord** : le public arrive
depuis WhatsApp, sur mobile. Aucun fichier annexe, aucune police distante.

> **Amendement du 2026-09-22, trouvé en écrivant le plan.** Cette section disait
> « aucun script », ce qui contredit l'avertissement de fraîcheur ci-dessous : une
> page statique n'est pas régénérée quand le relevé ne tourne pas, donc elle ne
> peut pas s'avertir elle-même après coup. **La date du relevé est toujours
> écrite en clair** (lisible sans JavaScript) et un script en ligne de quelques
> lignes ajoute un bandeau au-delà de 24 h. Sans JavaScript, on voit la date sans
> le bandeau — jamais un prix présenté à tort comme frais.

Contenu :

- en-tête : date du relevé, nombre d'affaires ;
- une section par ville, les 13, dans l'ordre de qualité mesuré ;
- par affaire : destination, hub (ou « vol direct »), prix, baisse en %, économie en €,
  lien ;
- pour une ville sans affaire : « rien aujourd'hui au départ de X » ;
- `abonnes.MENTION_PRIX` — les règles Travelpayouts interdisent de présenter une remise
  comme garantie, les prix viennent d'un cache pouvant aller jusqu'à 7 jours ;
- **avertissement de fraîcheur** au-delà de 24 h : « ces prix datent du … ». Sans lui, un
  visiteur croirait que des prix de trois jours sont ceux d'aujourd'hui — le même silence
  ambigu que le journal qui affirmait « envoyée » sous « message NON parti » ;
- appel à l'action, selon la ville :
  - ville **abonnable** (les 5) → `t.me/<bot>?start=<CODE>` ;
  - ville **non abonnable** (les 8) → `t.me/<bot>?start=attente_<etiquette>`.

> ⚠️ **Le payload `start` de Telegram n'accepte que `A-Za-z0-9_-`.**
> `attente_Le Caire` serait rejeté par Telegram, et `attente_Addis-Abeba` est
> ambigu. L'étiquette passe donc par `etiquette_ville()` — `le_caire`,
> `addis_abeba` — et `traiter_update()` refait le chemin inverse par une
> correspondance construite à partir de `NOMS_AFFICHES`, **jamais par une
> table recopiée à la main**. C'est exactement le défaut déjà payé le
> 2026-09-16 : deux endroits fabriquaient l'étiquette chacun de son côté, les
> clés ont divergé dès la première ville à nom composé, et les abonnés ont
> reçu des liens non comptés pendant des jours.

Les données viennent des `groupes` du relevé, filtrés par `abonnes.filtrer_groupes()` —
**la même fonction que le bot**. La page et le bot ne peuvent donc pas se contredire.

### Nom du bot — une configuration qui manque

Vérifié : **aucune chaîne `t.me` ni nom d'utilisateur de bot n'existe dans le code.**
Tout passe par l'API avec le jeton, qui ne révèle pas le nom public. La page en a besoin
pour fabriquer ses liens.

Nouvelle variable d'environnement `HUB_DEALS_BOT_USERNAME` (par exemple
`ianniv_vols_bot`). **Absente, la page se publie quand même, sans ses appels à
l'action** : des affaires sans bouton valent mieux qu'aucune page, et un bouton vers
`t.me/None` serait pire que pas de bouton. L'absence est journalisée.

### Étiquettes d'affiliation

La page utilise `page_<ville>` là où le bot utilise `<ville>`. Le trafic des deux canaux
devient distinguable dans Travelpayouts — premier pas vers le critère « qui clique ».
Environ deux fois plus de liens courts créés par relevé (19 aujourd'hui), sans difficulté.

`etiquette_ville()` reste le point de passage unique : la page ne fabrique pas son
étiquette dans son coin, sinon les deux divergent dès qu'une ville a un nom composé — le
défaut déjà payé le 2026-09-16 avec « Le Caire » et « Addis-Abeba ».

### `publier_page()` — la publication

Reprend le patron de `sauvegarde.sauvegarder_distant()`, déjà durci : worktree git sur la
branche dédiée, `add` → `diff --cached --quiet` (pas de commit vide) → `commit` → `push`,
délais d'attente sur chaque commande, `GIT_TERMINAL_PROMPT` neutralisé.

- Alerte Telegram **si la publication échoue**. Jamais de « publication OK » quotidien :
  un succès répété devient un bruit qu'on cesse de lire, et son absence passe inaperçue.
- **Ne lève jamais** : le relevé est déjà enregistré à ce stade.
- Worktree `.pages/`, ignoré par git.

### Liste d'attente

Nouvelle table `villes_souhaitees (chat_id, ville, quand)` dans le magasin Postgres, créée
comme les autres par un rôle propriétaire (le rôle du bot n'a que SELECT/INSERT/UPDATE).

`traiter_update()` reconnaît le payload `attente_<Ville>` : il enregistre le souhait et
répond « c'est noté, je te préviens quand X sera couverte ». **Il n'abonne pas** aux
alertes quotidiennes, et ne compte pas dans `PLAFOND_ABONNES`.

Deux obligations, traitées dès le départ :

> ⚠️ `villes_souhaitees` **entre dans `sauvegarde.TABLES_PRIVEES`**, sinon elle part deux
> fois par jour sur le dépôt public — exactement la fuite du 2026-09-20.
>
> ⚠️ Elle **entre dans l'instantané local** (`abonnes.ecrire_instantane`), sinon elle n'est
> sauvegardée nulle part.

## Ce qui ne change pas

La collecte interroge toujours les 13 villes et les 13 hubs. Les villes retirées du
clavier restent des **hubs** précieux pour les autres. Le message Telegram du propriétaire
et celui des abonnés sont inchangés.

## Traitement des erreurs

| Panne | Comportement |
|---|---|
| Rendu HTML impossible | Journalisé, alerte Telegram, relevé poursuivi |
| `git push` en échec ou bloqué | Délai d'attente, journalisé, alerte, relevé poursuivi |
| Aucune affaire ce jour | Page publiée quand même, chaque ville portant sa ligne « rien aujourd'hui » |
| Relevé non passé depuis > 24 h | La page en place affiche son avertissement de fraîcheur |
| Base des souhaits injoignable | Le bot répond une excuse explicite ; aucune inscription silencieusement perdue |

## Tests

Le rendu étant pur, tout se teste sans réseau ni git.

- une ville sans affaire produit sa ligne honnête, et n'est pas masquée ;
- les 13 villes sont présentes ;
- `MENTION_PRIX` est dans la page ;
- l'avertissement de fraîcheur apparaît au-delà de 24 h **et pas avant** ;
- les étiquettes de la page diffèrent de celles du bot, et passent par
  `etiquette_ville()` ;
- une ville abonnable porte le lien d'inscription, une ville non abonnable le lien de
  liste d'attente ;
- **la page ne contient aucune donnée personnelle** — ni `chat_id`, ni prénom ;
- `villes_souhaitees` figure dans `TABLES_PRIVEES` et dans l'instantané local ;
- `attente_<etiquette>` enregistre un souhait sans créer d'abonné ni consommer le
  plafond, **y compris pour une ville à nom composé** (`le_caire`, `addis_abeba`) ;
- l'étiquette écrite par la page et celle attendue par le bot proviennent du même
  `etiquette_ville()` — un test les confronte pour les 13 villes ;
- `attente_<inconnue>` est refusé ;
- sans `HUB_DEALS_BOT_USERNAME`, la page se rend sans appel à l'action et ne contient
  aucun lien `t.me` bancal ;
- la publication ne lève jamais, et alerte quand elle échoue.

## À faire à la main

1. **Activer GitHub Pages** : Settings → Pages → Source : branche `gh-pages`.
2. **Créer la table `villes_souhaitees`** dans Neon, avec le rôle propriétaire.
3. **Manual Deploy** du service `hub-deals-bot` sur Render (il ne se redéploie pas seul),
   pour que le bot connaisse le payload `attente_`.
4. **Poser `HUB_DEALS_BOT_USERNAME`** sur le portable (le relevé fabrique la page).

## Hors sujet, volontairement

Le parrainage (pas assez de monde pour amorcer une boucle), l'attribution des clics par
abonné (avec 5 abonnés elle n'apprendrait rien ; à reprendre quand la page aura du
trafic), le retrait de `PLAFOND_ABONNES`, et toute retouche des seuils de détection — la
mesure ci-dessus dit qu'elle ne servirait à rien.
