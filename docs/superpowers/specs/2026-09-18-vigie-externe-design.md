# Vigie externe du relevé quotidien — design

## Contexte

Toute la collecte de `hub_deals` tourne sur un portable Windows : le relevé quotidien
(tâche « Traqueur de vols », 13h) et l'écoute du bot (`bot_ecoute.py`). Trois pannes
observées en deux jours montrent que la machine est le point faible :

| Date | Panne | Découverte |
|---|---|---|
| 2026-09-17 09:29 et 13:42 | Envoi de la sauvegarde vers GitHub impossible (réseau) | par hasard, en séance |
| 2026-09-17 19:02 → 18/09 07:37 | Mise en veille de 12 h, bot muet | le lendemain, en séance |
| depuis 2026-09-16 | 25 à 35 délais de connexion Telegram par heure | en séance |

Aucune de ces pannes ne s'est signalée d'elle-même. Le relevé prévient déjà quand *il*
échoue (`sauvegarder_et_alerter`), mais un relevé qui **ne tourne pas du tout** ne
prévient personne : le silence est ambigu, c'est le même angle mort que le `NextRunTime`
vide de la tâche planifiée (2026-08-15) et que les 7 semaines de détection muette.

Une vigie qui tournerait sur le portable se tairait en même temps que lui. Elle doit donc
vivre ailleurs.

### Décision de cadrage (brainstorming du 2026-09-18)

Trois options d'hébergement ont été comparées : GitHub Actions, un service de « bouton du
mort » (healthchecks.io), une tâche planifiée Render. **GitHub Actions retenu** : gratuit,
aucun compte supplémentaire, le code et ses tests restent dans le dépôt.

Le user a par ailleurs choisi de commencer par la surveillance (« B ») avant d'envisager
le passage de la collecte dans le nuage (« A »). La vigie sert aussi à **mesurer la
fréquence réelle des pannes** : c'est la donnée qui manque pour décider si l'hébergement
distant vaut son coût.

## Objectif

Être prévenu sur Telegram, sans intervention, quand le relevé quotidien ne tourne plus ou
tourne mal — même si le portable est éteint, endormi ou privé de réseau.

## Ce qui est hors scope

- **La surveillance de l'écoute du bot.** Elle demanderait que `bot_ecoute.py` publie un
  signe de vie hors de la machine (code des deux côtés). Le seul abonné actif aujourd'hui
  est le compte test du user : la valeur ne justifie pas encore le coût. À reprendre quand
  de vrais abonnés écriront au bot.
- **Les pannes de moins de 24 h.** La vigie passe une fois par jour.
- **Le déplacement de la collecte dans le nuage** (chantier « A », plus tard).

## Source de vérité : la branche `sauvegardes`

Chaque relevé pousse un dump SQL sur la branche `sauvegardes` du dépôt
(`sauvegarde.py`, un commit par relevé). Ces commits sont lisibles de l'extérieur et
portent tout ce qu'il faut, **sans rien modifier sur le portable** :

- la **date du commit** → le relevé a-t-il eu lieu, et quand ;
- le **nombre de lignes ajoutées** au dump (`git log --numstat`) → le volume du relevé.

Mesure sur les 20 derniers relevés : **900 à 971 lignes ajoutées**, très régulier (un
commit à 1727 correspond à deux relevés d'une même journée réunis). Un relevé amputé —
réseau coupé au milieu, hubs manquants — se voit donc dans ce seul chiffre.

Conséquence : la vigie n'a **pas besoin de télécharger la base** ni de la lire. Deux
signaux suffisent, et ils sont gratuits.

## Règles de jugement

Une fonction pure reçoit les derniers relevés (date, lignes ajoutées) et l'heure courante,
et rend la liste des problèmes constatés. Trois règles :

1. **Relevé manquant** — aucune sauvegarde depuis plus de **26 h**. Marge volontaire : le
   relevé de 13h peut prendre 45 min un mauvais jour (mesuré le 17/09), et le déclenchement
   d'une tâche GitHub peut glisser de plusieurs dizaines de minutes.
2. **Relevé maigre** — le dernier relevé compte moins de **la moitié de la médiane** des 10
   précédents. La médiane, pas la moyenne : elle ne bouge pas si une valeur est aberrante.
Deux règles, pas trois : une règle sur les rattrapages (plusieurs relevés poussés dans la
même minute après un blocage) a été écartée au cadrage. Le cas du 17/09 est déjà couvert
par la règle 1, et une règle de plus, c'est un faux positif de plus.

Chaque problème est un texte prêt à envoyer, en français, disant **ce qui est constaté** et
**ce qu'il faut vérifier** — jamais un code d'erreur seul.

## Ce que la vigie envoie

- **Problème constaté** → un message Telegram au propriétaire, via le bot existant. Envoyer
  ne perturbe pas l'écoute : seule la **lecture** (`getUpdates`) est réservée à un seul
  programme, et c'est `bot_ecoute.py` qui la détient.
- **Tout va bien** → **rien**. Un message quotidien de bonne santé cesserait d'être lu en
  une semaine, et son absence passerait inaperçue le jour où elle compterait (même
  raisonnement que `sauvegarder_et_alerter`).
- **Le lundi** → un bilan d'une ligne (« 7 relevés sur 7 la semaine passée, volume médian
  N »). C'est le signe de vie de la vigie elle-même, à une fréquence qui reste lisible.
- **La vigie plante** → GitHub envoie un courriel d'échec au propriétaire du dépôt. Une
  panne de la vigie ne peut donc pas être silencieuse. *(Comportement par défaut de GitHub,
  à vérifier une fois dans les réglages du compte.)*

## Architecture

```
GitHub Actions (cron quotidien)
  └── récupère l'historique de la branche `sauvegardes`
  └── vigie.py
        ├── lire_releves()   — seule fonction qui appelle git
        ├── juger()          — PURE : (relevés, maintenant) -> [problèmes]
        ├── bilan_hebdo()    — PURE : (relevés, maintenant) -> texte ou None
        └── main()           — assemble, puis envoie via hub_deals_db.envoyer_telegram()
```

- `vigie.py` est un **nouveau module à la racine**, comme `recherche.py` : il ne touche ni
  au relevé ni au bot, et ne peut donc rien casser dans la collecte.
- L'envoi Telegram réutilise `hub_deals_db.envoyer_telegram()` : masquage des secrets au
  journal, découpage à 4096 caractères et lecture du code HTTP sont déjà traités là.
- **Aucun appel à l'API des prix.** La vigie ne consomme aucun quota Travelpayouts.

## Secrets et sécurité

- `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` sont posés en **secrets du dépôt** (dépôt
  privé), jamais dans le code ni dans le fichier du workflow.
- Le workflow n'écrit rien dans le dépôt : **droits en lecture seule**.
- Le jeton ne doit jamais apparaître dans les traces d'exécution, qui sont conservées par
  GitHub. `masquer_secrets()` de `hub_deals_db` couvre déjà ce chemin, et GitHub masque en
  plus ses propres secrets.

## Tests

Le cœur est pur, donc testable sans réseau ni git :

- relevé d'il y a 2 h → aucun problème ;
- relevé d'il y a 30 h → « relevé manquant » ;
- dernier relevé à 400 lignes contre une médiane de 940 → « relevé maigre » ;
- un relevé aberrant dans l'historique ne fausse pas le verdict (médiane) ;
- historique vide ou d'un seul relevé → pas de plantage, pas de fausse alerte ;
- lundi → bilan ; les autres jours → rien quand tout va bien ;
- le texte des messages est en français et nomme ce qu'il faut vérifier.

`lire_releves()` est vérifiée à part, sur le vrai dépôt, et la chaîne complète est prouvée
une fois en conditions réelles (exécution manuelle du workflow, message reçu).

## Vérification en conditions réelles (à faire à la livraison)

1. Le workflow se déclenche à la main et rend « aucun problème » sur l'état sain du jour.
2. Un cas de panne simulé (historique tronqué) produit bien le message attendu.
3. Un message de test arrive réellement sur Telegram depuis GitHub, sans jeton visible dans
   les traces.
4. Le cron se déclenche tout seul le lendemain, et le bilan du lundi part.

## Effet attendu, une fois en place

Une panne du portable se signale d'elle-même en moins de 24 h, et le journal des alertes de
la vigie donne, sur quelques semaines, la **fréquence réelle des pannes** — la donnée qui
manque pour décider du passage de la collecte dans le nuage.
