# Héberger l'écoute et sortir les abonnés du portable — design

## Contexte

Le but est **de recruter des abonnés à Paris** (diaspora). Côté fonctionnalité, rien ne
manque : Paris est ville résidente depuis le 2026-09-19 et sert 30 routes au départ de
CDG dans le dernier relevé (Milan 38 €, Londres 56 €, … Dakar 400 €, Abidjan 474 €,
Kinshasa 589 €, São Paulo 750 €). Le parcours d'inscription est complet : `/start <CODE>`
→ clavier des 13 villes → confirmation → alertes, plafond 50.

Deux obstacles, découverts en cadrant ce recrutement le 2026-09-20, l'interdisent tant
qu'ils tiennent.

### a. Le bot ne répond que si le portable est allumé

`bot_ecoute.py` fait du long polling depuis la machine de collecte. Le journal montre un
démarrage par ouverture de session — 08:50, 09:28, 08:51 les jours précédents — et aucun
arrêt propre : la machine s'éteint, le bot avec elle. Telegram conserve les messages 24 h,
donc l'inscription finit par aboutir, mais avec des heures de retard.

Pour le compte test du propriétaire, c'est invisible. Pour un inconnu qui clique le lien
à 23 h et ne reçoit le clavier des villes que le lendemain matin, c'est une inscription
perdue.

### b. Les abonnés partaient sur un dépôt public (corrigé, avec une dette)

`conn.iterdump()` dumpe toute la base : `chat_id` Telegram et prénom de chaque abonné
étaient poussés sur le dépôt **public** deux fois par jour. Recruter, c'était publier.

Corrigé le 2026-09-20 (`6a8dc74`) : `generer_dump` copie la base en mémoire et **vide**
`abonnes` et `etat_bot` avant de dumper ; l'historique de la branche `sauvegardes` a été
réécrit (9 commits sur 63). Mais la correction laisse une dette :

> **Les abonnés ne sont plus sauvegardés nulle part.** Le dump public en était la seule
> copie hors machine.

C'est la seconde raison d'être de ce chantier, et elle est aussi contraignante que la
première : **ne recruter personne avant qu'il soit livré.**

## Objectif

Une inscription qui aboutit en moins d'une minute à toute heure, et des abonnés stockés
hors du portable, dans une base sauvegardée par son hébergeur.

## Décisions de cadrage (2026-09-20)

| Question | Décision | Pourquoi |
|---|---|---|
| Où héberger l'écoute | **Render, web service gratuit, en webhook** | Render n'a pas de worker gratuit (7 $/mois) ; le web service gratuit suffit pour quelques messages par jour |
| Base des abonnés | **Neon Postgres**, projet dédié | Le Postgres gratuit de Render expire à 30 jours ; Neon est déjà en service sur agent-autonome |
| Dialecte SQL | **Testé contre un vrai Postgres en CI** | Un dialecte non testé est l'angle mort habituel du projet (voir « Risques ») |
| Portée de la migration | **`abonnes` et `etat_bot` seulement** | Les 85 885 lignes d'`offres` servent une détection locale ; les déplacer coûte sans rien rapporter |
| Qui envoie les alertes | **Le portable, inchangé** | Lui seul a les prix ; le service Render ignore tout des affaires |

Écarté : conserver le long polling sur un worker payant (seule option à coût récurrent) ;
un cron GitHub Actions toutes les 5 min (latence réelle de 5 à 15 min, trop long après un
clic) ; ne déplacer que la base en laissant l'écoute sur le portable (ne lève pas le
frein) ; faire passer le portable par une API HTTP sur Render (une API à sécuriser, et le
relevé de 13h dépendrait du réveil de Render).

## Modèle

Trois acteurs, chacun avec une seule raison d'exister :

| | Rôle | Ce qu'il ignore |
|---|---|---|
| **Render** (web gratuit) | reçoit les webhooks, traite `/start`, `/ville`, `/stop`, répond à l'abonné | tout des prix et des affaires |
| **Neon** (Postgres) | source de vérité des abonnés : `abonnes`, `etat_bot` | — |
| **Le portable** | collecte, détection, liens courts, **envoi** des alertes | plus rien des inscriptions |

```
Inscription :  Telegram ──► POST /telegram ──► Render ──► Neon
                                   │                        ▲
Alerte 13h :   Portable ───────────┼────────────────────────┘  (lecture + désactivations)
               └──► Telegram (envoi des messages)
```

Ne bougent pas : collecte, détection, liens courts, envoi, sauvegarde publique, vigie.

## Changements

### 1. Couche d'accès — un adaptateur, et `abonnes.py` intact

psycopg3 expose `Connection.execute()` rendant un curseur, donc `conn.execute(…).fetchone()`
fonctionne à l'identique ; mais il exige `%s` et ne supporte pas `?`.

Un nouveau module `magasin.py` porte cette couche. Il expose une seule fonction,
`ouvrir(url)` : sans URL, une connexion SQLite (portable, tests) ; avec une URL Postgres,
une connexion psycopg enveloppée dans un adaptateur (~20 lignes) qui traduit `?` en `%s`
et expose `execute` / `commit`. Dans les deux cas, `ouvrir` crée les tables si besoin.

**`abonnes.py` n'est pas modifié** : ni ses requêtes, ni sa forme `conn` en paramètre.

La traduction doit ignorer ce qui se trouve entre quotes : un `?` dans un littéral ne doit
pas être remplacé. Aucune requête actuelle n'est dans ce cas, ce qui rend le piège d'autant
plus facile à introduire plus tard sans s'en apercevoir — d'où un test dédié.

Le SQL existant est déjà portable pour le reste : `ON CONFLICT (…) DO UPDATE SET …
excluded.x` et `CREATE TABLE IF NOT EXISTS` ont la même syntaxe dans les deux moteurs.

### 2. Schéma — `BIGINT`, et le DDL sort d'`abonnes.py`

`INTEGER` vaut 32 bits en Postgres, plafonné à 2 147 483 647. Le `chat_id` du compte test
(≈ 8,6 × 10⁸) passerait, mais Telegram attribue des identifiants au-delà : ce sera
**`BIGINT`**. La panne n'arriverait pas au test, elle arriverait au premier inconnu.

Le DDL diverge donc entre moteurs : il quitte `abonnes.py` pour `magasin.py`, une version
par dialecte. Le DML reste partagé — c'est lui que le job Postgres vérifie.

`etat_bot` migre elle aussi, bien qu'elle perde son rôle de garde-fou (§5) : le service
peut continuer d'y noter la dernière réception, ce qui aide au diagnostic. Elle n'est plus
lue par aucune alerte.

Inchangés : `actif` reste un entier (`bool()` s'en charge côté Python), les horodatages
restent du texte ISO, donc `datetime.fromisoformat` continue de fonctionner.

### 3. Service webhook

Un seul endpoint, `POST /telegram`, en Flask + gunicorn — ce que Render attend, et
`http.server` n'est pas fait pour du trafic public. Le service appelle `traiter_update()`,
déjà pure et idempotente, puis exécute les actions rendues.

Sécurité : `setWebhook` accepte un `secret_token` que Telegram renvoie dans l'en-tête
`X-Telegram-Bot-Api-Secret-Token`. Toute requête sans ce jeton repart en 403 **sans
toucher à la base**. Le journal garde la règle existante : jamais le texte reçu, jamais le
code d'invitation (`masquer()`).

Deux comportements qui comptent :

- **Neon injoignable → 500**, jamais 200. Telegram réessaiera. Répondre 200 sur un message
  non traité, c'est perdre une inscription en silence.
- **Le réveil prend ~1 minute** (le service gratuit s'endort après 15 min d'inactivité) et
  Telegram peut réessayer entre-temps : le même update peut arriver deux fois.
  `traiter_update()` est idempotente et les `ON CONFLICT` absorbent le doublon — au pire un
  message d'accueil envoyé deux fois.

### 4. Le relevé lit les abonnés dans Neon

`notifier_abonnes_sans_risque` conserve son contrat : **ne lève jamais**. Si Neon est
injoignable au moment du relevé, le propriétaire reçoit quand même son message, les
abonnés ne sont pas servis, et le journal le dit explicitement. Un relevé complet ne doit
pas échouer parce que la base des abonnés est en panne.

Les désactivations (abonné ayant bloqué le bot, HTTP 403) s'écrivent dans Neon.

### 5. Garde-fou — `getWebhookInfo` remplace `ecoute_muette`

`ecoute_muette()` compare l'instant présent au témoin `derniere_ecoute`, rafraîchi toutes
les ~50 s par le long polling. Avec un webhook il n'y a plus de boucle : le service dort,
c'est normal, et l'absence de message ne signale aucune panne. Le témoin deviendrait une
alarme permanente, donc ignorée.

À la place, la sonde que Telegram fournit pour ça : `getWebhookInfo` rend l'URL
enregistrée, `pending_update_count`, `last_error_date` et `last_error_message`. Alerte si
l'URL est vide, si une erreur est récente, ou si les messages s'accumulent. Ce contrôle
teste que **Telegram sait où nous joindre** — pas qu'une boucle tourne.

Elle va dans la **vigie**, pas dans le relevé : le webhook est désormais un service
extérieur au portable, et un portable éteint ne peut pas signaler qu'un service distant
est tombé. La vigie tourne déjà hors de la machine, une fois par jour, et détient déjà le
jeton Telegram dans les secrets du dépôt — c'est sa raison d'être.

### 6. Dépendances

`requirements.txt` (portable et service) gagne **psycopg** : les deux lisent Neon. Flask et
gunicorn ne servent qu'au service et vivent dans un `requirements-web.txt` séparé — le
portable n'a aucune raison de les installer, et le relevé ne doit pas dépendre d'un serveur
web pour tourner.

Render déploie ce dépôt tel quel : le service importe `abonnes.py` et `traiter_update()`
de `bot_ecoute.py`, qui restent la référence unique de la logique d'inscription.

### 7. Droits Neon

Un rôle dédié, limité à `SELECT / INSERT / UPDATE` sur `abonnes` et `etat_bot`. La leçon du
2026-09-13 sur agent-autonome — où les rôles se sont révélés être des `neon_superuser` —
vaut ici : le rôle est créé restreint dès le départ, pas « resserré plus tard ».

### 8. Migration

Script idempotent (`INSERT … ON CONFLICT DO NOTHING`) reprenant les abonnés existants. Un
seul aujourd'hui, mais il doit exister et être **rejoué** pour être cru : la seconde
exécution doit reprendre 0 ligne.

### 9. Anti-veille — une dépendance cachée à recréer

`bot_ecoute.py` pose un verrou `SetThreadExecutionState` qui empêche le portable de dormir
tant qu'il tourne (ajouté le 2026-09-18, après 12 h de veille imprévue). Le retirer rend la
veille de nouveau possible en journée, et le relevé de 13h en dépend sans que ce soit
écrit nulle part : endormie, la tâche ne part pas, et `StartWhenAvailable` ne la rattrape
qu'au réveil.

Correctif retenu : cocher **« Réveiller l'ordinateur pour exécuter cette tâche »** dans le
planificateur. C'est le mécanisme prévu pour ça, et il fonctionne même après des heures de
veille — contrairement à un verrou qu'il faudrait maintenir depuis un script qui, lui, ne
tourne que pendant le relevé.

## Bascule

L'ordre n'est pas négociable : Telegram refuse webhook et long polling simultanés (409).

1. Neon : projet, rôle restreint, tables, migration de l'abonné, vérification.
2. Déploiement du service **sans** enregistrer le webhook, puis contrôle positif : un
   update fabriqué envoyé à la main avec le bon secret, et la base doit bouger.
3. Arrêt de `bot_ecoute.py` **et désactivation de sa tâche planifiée** — sinon la prochaine
   ouverture de session le relance et Telegram renvoie des 409.
4. `setWebhook`, puis `getWebhookInfo`, puis un vrai `/start` depuis le compte test.
5. Le relevé lit les abonnés dans Neon — prouvé sur un relevé réel, pas en test.
6. Réveil de la machine pour la tâche de 13h.

**Retour arrière** : `deleteWebhook`, relancer le bot local, repointer le relevé sur SQLite.

## Tests

Les mêmes tests de persistance tournent **deux fois, un dialecte chacun** : sur SQLite dans
le job Windows actuel, sur un vrai Postgres dans un nouveau job Linux (`services: postgres`,
impossible sur le runner Windows).

S'y ajoutent :

- l'adaptateur : `?` traduits, `?` dans un littéral **non** traduit ;
- le webhook : secret absent → 403, secret faux → 403, update inconnu → 200 sans effet,
  base en panne → 500, update rejoué → un seul abonné ;
- la migration : rejouée, 0 ligne la seconde fois ;
- `getWebhookInfo` : URL vide, erreur récente, messages en attente → alerte ; nominal →
  silence.

### Le piège à fermer d'avance

Ces tests Postgres seront naturellement écrits pour se sauter faute de base — commode en
local. Mais si le job CI perd sa variable de connexion, ils se sauteraient **aussi**, et la
suite passerait au vert sans avoir rien vérifié du dialecte qui tourne en production.

Donc : saut autorisé en local, **échec** si la variable manque alors que `CI` est défini.

C'est exactement l'angle mort qui a déjà coûté trois fois à ce projet : le test « pas de
fenêtre console » qui passait sans le correctif tant qu'il ne passait pas par `pythonw` ;
les liens courts créés puis jamais retrouvés faute d'étiquette commune ; la vigie qui
rendait `[]` sur un dump illisible, donc « aucune ville effondrée ».

## Risques et incertitudes

| Risque | Traitement |
|---|---|
| Réveil Render ~1 min au premier message | Telegram réessaie ; doublon absorbé par l'idempotence |
| Neon free : suspension après inactivité | Réveil de l'ordre de la seconde ; à mesurer une fois en service |
| Double traitement d'un update | `traiter_update()` idempotente + `ON CONFLICT` |
| Perte de la variable de connexion en CI | Échec forcé quand `CI` est défini |
| Secret du webhook exposé | Jeton en en-tête, jamais journalisé ; rotation possible par `setWebhook` |

Non mesuré à ce jour : le temps de réveil réel de Render **et** de Neon bout en bout depuis
le clic d'un abonné. L'estimation d'une minute vient de la documentation Render, pas d'une
mesure sur ce service. À vérifier à l'étape 4 de la bascule.

## Hors scope

- Déplacer `offres` vers Postgres.
- Déplacer la collecte ou la détection hors du portable.
- Toucher aux seuils, aux villes, aux liens courts.
- Le contenu du message d'invitation et le recrutement lui-même — c'est la suite, une fois
  ce chantier livré.
