# Changelog

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/). Projet personnel sans versionnage sémantique — entrées datées.

## 2026-09-13

### Modifié
- **Le relevé tourne sans fenêtre (`pythonw.exe`).** 4 relevés sur 99 avaient été tués par la
  fermeture de leur console (`0xC000013A` dans l'historique du planificateur, les 04, 07, 08 et
  09/09). Deux effets de bord de `pythonw` constatés puis traités, test à l'appui : chaque commande
  git ouvrait sa propre fenêtre (`CREATE_NO_WINDOW`), et `sys.stderr` valant `None`, un plantage
  aurait été totalement muet (`sys.excepthook` → journal, masqué ; l'absence de token est
  journalisée au lieu de passer par `SystemExit`). Vérifié sous `pythonw` sur une copie : token
  absent et plantage réel apparaissent bien dans le journal, sans le token.

### Corrigé
- **`recherche.py` mesure aussi l'aller vers Paris.** Le trajet ville → hub passe par
  `get_prix_segment()` (v3), le vol direct et le segment hub → destination restent sur v1. Vérifié
  en réel (`Dakar BKK`) : « via Paris » affiche désormais `486 [API]` au lieu de la valeur estimée ;
  la base n'est pas modifiée. Une fonction `get_prix` injectée sert toujours pour tous les segments,
  pour que les tests ne fassent aucun appel réseau.
- **Le rabattement vers Paris est enfin mesuré au moment de l'alerte.** `mesurer_rabattements()`
  passait par `v1/prices/cheap`, qui ne renvoie rien pour les segments vers CDG : dans le journal,
  Dakar→Paris n'avait été mesuré **aucun jour sur 22**. La mesure passe désormais par
  `v3/prices_for_dates` en aller-retour (`get_prix_segment()`). Sonde du jour sur les 40 segments,
  avec témoins positif et négatif : v1 en couvre 17, **v3 21 — les mêmes 17 au même prix, plus 4
  vers Paris**. Vérifié avec la vraie fonction contre l'API : 21/40, aucune erreur.
- Diagnostic du « Rabattement mesuré pour 1/10 » du 2026-09-12 : **pas une régression**. Sur tout
  l'historique, ~52 % des blocs sont mesurés ; ce relevé ne portait que sur des affaires via Lagos,
  dont aucun segment n'a de prix. La couverture de l'API **varie d'un jour à l'autre** (Lomé→Istanbul
  mesurable depuis le 29/08, Lomé→Nairobi plus depuis le 23/08) : les marques `[M]`/`[NM]` de la
  table sont un instantané du 2026-08-16.

- **Un `git push` sans identifiant valide ne peut plus bloquer le relevé indéfiniment.** Reproduit
  avec l'environnement de la tâche planifiée : le gestionnaire d'identifiants attendait une saisie
  que personne ne ferait. Le relevé ne se terminait jamais, **l'alerte d'échec ne partait pas** (elle
  est déclenchée après), et `MultipleInstances = IgnoreNew` faisait sauter les relevés suivants
  pendant jusqu'à 72 h. `_executer` interdit désormais toute saisie (`GIT_TERMINAL_PROMPT=0`,
  `GCM_INTERACTIVE=never`) — le même push échoue en 1,2 s avec un message explicite — et impose un
  délai de 300 s en dernier recours, en tuant **tout l'arbre** de processus : le gestionnaire
  d'identifiants hérite des tubes de sortie, et tant qu'il vit, lire la sortie de git bloque aussi.
- **La sortie de git est décodée en UTF-8** et non plus en cp1252, qui produisait du texte illisible
  et, sur un octet non défini en cp1252, **perdait toute la sortie sans lever d'erreur**. Le journal
  garde la **fin** de la sortie (où git met la cause, `fatal: Authentication failed`) au lieu des
  120 premiers caractères.
- `restaurer()` ne laisse plus de fichier vide après un échec, qui interdisait de réessayer sous le
  même nom.
- L'import du module de sauvegarde est passé dans le `try` de `sauvegarder_et_alerter()` : un
  `sauvegarde.py` absent ou cassé déclenche l'alerte au lieu de faire planter la fin du relevé.

### Écarté après vérification (revue du 2026-08-22)
- « La purge des copies locales ne tourne jamais » : par conception, la copie locale est un outil
  manuel, à lancer avant une opération destructive.
- « `print()` plante sur un stdout cp1252 » : la tâche lance `python.exe` avec une console, dont
  l'encodage est UTF-8.

## 2026-09-12

### Ajouté
- **Les alertes sont regroupées par affaire réelle (hub → destination), plus une par ville de
  départ.** La bonne affaire se joue sur le tronçon hub → destination ; la ville de départ n'ajoute
  qu'un rabattement constant, si bien que la même aubaine remontait autant de fois qu'il y a de
  villes rattachées au hub, avec une **économie identique à l'euro près**. Mesuré sur les 15 derniers
  relevés : **241 alertes ne recouvraient que 62 affaires distinctes**, et 28 de ces 62 remontaient
  avec les 5 villes au complet. Le relevé du 2026-09-12 à 10h43 envoyait 10 alertes pour 3 affaires.
- La clé de regroupement inclut le lien, qui **encode déjà la date de départ** : deux dates sont deux
  affaires, même sur le même couple hub/destination.
- Format : l'économie passe en en-tête de groupe (elle est commune et c'est le critère de
  déclenchement), le pourcentage reste par ville (son dénominateur change avec le rabattement), le
  lien n'apparaît qu'une fois. Les groupes sont triés par économie décroissante, les villes par prix
  croissant. Quand les économies d'un groupe diffèrent de quelques centimes — les historiques n'ont
  pas tous la même longueur selon la ville — c'est **la plus basse** qui est annoncée.
- **Le statut du rabattement est affiché ligne par ligne**, et non en note de bas de bloc : plusieurs
  villes d'un même groupe peuvent être mesurées avec des valeurs différentes, ce qu'une note unique
  ne saurait porter.
- **Repli sur le format plat pour un groupe d'une seule ville** : un en-tête de groupe et une liste
  d'une ligne ne mettraient rien en facteur commun. Cas jamais observé (0 groupe sur 62 en 15
  relevés) mais couvert par un test dédié.

### Corrigé
- **Le message d'alerte affiche désormais l'économie en euros, et n'est plus justifié par le seul
  pourcentage.** `corriger_anomalies()` recalcule le pourcentage sur l'échelle décalée par le
  rabattement mesuré : un `-5 %` pouvait donc s'afficher sous un plancher annoncé à 6 %. Mesuré sur
  le message du 2026-09-12 : la correction **abaisse** le pourcentage dans **27 cas sur 34** (0,9
  point en médiane, 1,8 au maximum) et le **remonte** dans les 7 autres ; **43 des 66 blocs envoyés**
  affichaient un pourcentage sous 6 %.
- **Choix délibéré : on ne refiltre pas sur le pourcentage corrigé.** Le rabattement n'est mesuré que
  pour les routes *déjà* détectées. Un refiltrage ne pourrait donc qu'en retirer, jamais rattraper
  celles que la même correction ferait repasser au-dessus du plancher : il serait unilatéral et
  couperait des affaires réelles pour un simple effet de dénominateur. L'économie en euros, elle, est
  exactement préservée par le décalage (l'écart absolu ne bouge pas) et c'est un critère de
  déclenchement depuis le recalibrage — c'est donc elle qui justifie l'alerte dans le message.
- Champ `economie` ajouté à chaque anomalie par `detecter_anomalies()`.

### Modifié
- **Recalibrage du détecteur : `z ≥ 2`, plancher de baisse à 6 %, et une économie minimale de
  80 €.** Le relevé du matin avait encore remonté 66 alertes. Mesuré sur les 15 derniers relevés,
  l'ancien réglage (`z ≥ 1,5`, plancher 3 %) en produisait **79 par relevé en médiane, jusqu'à
  112**, d'une économie médiane de **64 €** — du bruit de marché présenté comme de bonnes affaires.
  La cause tient en un chiffre : les routes suivies sont très stables, **coefficient de variation
  médian de 2,8 %**, si bien que 1,5 écart-type ne pesait qu'environ **4 % de baisse**. Le plancher
  à 3 % ne filtrait donc plus rien — il avait été fixé quand le parc était encore majoritairement
  jugé au pourcentage (voir l'entrée du 2026-08-20).
- **Le nouveau réglage donne 16 alertes par relevé en médiane (8 à 26), d'une économie médiane de
  146 €**, mesuré par backtest causal : la référence de chaque relevé n'utilise que les relevés
  *antérieurs*, donc exactement ce dont disposait le détecteur ce jour-là.
- **Monter le z-score seul ne suffisait pas — et dégradait la qualité.** À `z ≥ 2,5` sans toucher au
  plancher, le volume tombait de moitié (32 par relevé) mais l'économie médiane **baissait à 54 €** :
  on retenait des baisses statistiquement rares sur des routes ultra-stables, donc de tout petits
  montants. Ce sont le plancher en pourcentage et le seuil en euros qui portent la qualité, pas le
  z-score. `ECONOMIE_MINIMALE` est le critère le plus discriminant : à volume égal, passer de 50 €
  à 80 € fait monter l'économie médiane de 104 € à 146 €.

## 2026-09-11

### Corrigé
- **Les notifications d'anomalies sont découpées pour tenir dans la limite de Telegram.** L'API
  refuse tout `sendMessage` de plus de 4096 caractères avec un `HTTP 400 message is too long`, et
  **perd le message entier — elle ne le tronque pas**. Un relevé de 72 anomalies pèse ~13 000
  caractères. Résultat : **32 notifications refusées entre le 2026-08-17 et le 2026-09-08**, puis
  5 refus supplémentaires pour un motif différent (voir ci-dessous). Aucune alerte de prix n'est
  partie depuis le 2026-08-18. La coupe tombe toujours *entre* deux anomalies, jamais à
  l'intérieur : un bloc porte du HTML, et le trancher produirait un balisage mal fermé — donc un
  400 de plus, pour une autre raison. Chaque morceau reprend l'en-tête suivi de `(i/n)` ; un
  message unique n'est pas numéroté, `(1/1)` n'apprenant rien.
- **Le journal ne ment plus sur ce qui est parti.** `envoyer_telegram()` ne renvoyait rien, et
  l'appelant journalisait `Notification Telegram envoyee pour N anomalie(s)` de façon
  inconditionnelle, juste après l'appel. L'appelant ne *pouvait pas* savoir — c'est là qu'est la
  racine, pas dans la taille du message. Ce faux témoignage a masqué 37 refus consécutifs pendant
  trois semaines : le journal affirmait chaque jour qu'une notification était partie, le seul
  démenti étant l'absence de sonnerie sur le téléphone. La fonction renvoie désormais un booléen,
  et le compte rendu distingue trois états — succès complet, `ECHEC` total, et `ECHEC partiel :
  i/n message(s) partis`. Annoncer un succès complet quand 1 morceau sur 4 passe serait le même
  mensonge sous une autre forme.

### Su mais non corrigé
- **Le token du bot est révoqué** (`HTTP 401 Unauthorized` depuis le 2026-09-09, 5 occurrences).
  Indépendant du découpage : même réparé, le tuyau reste fermé tant que `TELEGRAM_BOT_TOKEN` ne
  porte pas un token valide. À régénérer côté BotFather.
- **Le détecteur s'est déréglé tout seul.** Le taux d'anomalies est passé de 4,2 % à 7,8 % des
  routes en onze jours, sans événement de marché correspondant. Cause : le basculement
  `pourcentage` → `z-score`. Tant qu'une route a moins de `MIN_RELEVES_ZSCORE` (4) relevés, elle
  est jugée au seuil de 8 % ; au-delà, à « 1,5 σ **et** ≥ 3 % ». L'historique s'accumulant, **100 %
  du parc est passé en z-score le 2026-08-20** — le seuil effectif a glissé de 8 % à ~3 % sans
  décision. Seules 13 des 72 alertes du 2026-09-11 passeraient l'ancienne règle. S'y ajoute que les
  deux queues de la distribution sont deux fois plus grasses qu'une gaussienne (14,7 % à z ≥ +1,5,
  10,0 % à z ≤ −1,5, contre 6,7 % attendus) : « 1,5 écart-type » ne vaut pas « événement rare » sur
  ces prix. Économie médiane des 72 alertes : **47 €**, dont 39 sous 50 €. Recalibrage à faire.

118 → 137 tests.

## 2026-08-16

### Ajouté
- **Une sauvegarde qui échoue notifie désormais sur Telegram.** Jusqu'ici l'échec n'allait que dans
  le journal : le relevé s'enregistrait, l'alerte de prix partait normalement, tout paraissait sain
  — et les données avaient cessé d'être protégées sans le moindre signe. C'est le même angle mort
  que le `NextRunTime` vide de la tâche planifiée en juillet : **le silence est ambigu**. Seul
  l'échec est notifié ; un message quotidien « sauvegarde OK » deviendrait un bruit qu'on cesse de
  lire en une semaine, et son absence passerait alors inaperçue. Le message est délibérément
  distinct des alertes de prix, pour qu'une panne technique ne se confonde pas avec une bonne
  affaire. 114 → 118 tests.

## 2026-08-16

### Ajouté
- **Sauvegarde hors machine** (`sauvegarde.py`). La base et ses copies vivaient toutes sur le même
  disque : une panne matérielle effaçait 21 relevés que l'API ne peut pas reconstituer. Un dump SQL
  texte est désormais poussé sur la branche `sauvegardes` du dépôt privé à la fin de chaque relevé,
  via un worktree git dédié — `master` reste exempt de commits automatiques, et git encode les
  deltas efficacement puisque la base est cumulative. Toute erreur est absorbée : une panne de git
  ou de réseau n'interrompt jamais une collecte.
- **Restauration outillée** (`--restaurer`), et non simplement documentée : `sqlite3` n'existe pas
  en ligne de commande sur la machine de l'utilisateur, donc la commande de README habituelle y
  serait inexécutable — précisément le jour où l'on en aurait besoin. La commande refuse d'écraser
  un fichier existant. Vérifiée de bout en bout : dump récupéré depuis la branche git puis restauré,
  10 996 lignes et somme des totaux identiques à la base en service.
- **Purge des copies locales** : elles s'accumulaient sans limite. Les 5 plus récentes sont
  conservées, et la purge ne touche aucun autre fichier. 101 → 114 tests.

## 2026-08-16

### Ajouté
- **Le message Telegram envoyé est désormais journalisé**, encadré par des marqueurs
  `--- message Telegram envoye ---` / `--- fin du message ---`. Un bot ne peut pas relire ses
  propres messages sortants — l'API Telegram n'expose que ce qu'il *reçoit* (`getUpdates`) — donc
  sans cette trace, vérifier après coup ce qui a été notifié supposait d'avoir le téléphone sous
  la main. Le corps est conservé tel quel, HTML compris, pour rester fidèle à ce qui part, et
  passe par `log()` donc par `masquer_secrets()`. Le message est aussi journalisé quand Telegram
  n'est **pas** configuré : ce cas était jusqu'ici totalement silencieux, on ne savait même pas ce
  qui aurait été notifié.

### Corrigé
- **Le code de réponse de Telegram n'était pas vérifié.** `requests.post` était appelé sans
  contrôle du statut : un message refusé — Telegram répond 400 sur du HTML mal fermé, par exemple —
  était compté comme envoyé, et le journal affichait « Notification Telegram envoyee ». Le statut
  est désormais vérifié et un refus est journalisé avec son code et le début de la réponse. 97 →
  101 tests.

## 2026-08-16

### Modifié
- **Les 5 rabattements vers Paris étaient les valeurs les plus fausses de la table, sur le hub le
  plus utilisé.** `v1/prices/cheap` ne renvoie rien pour `ville → CDG`, mais `v3/prices_for_dates`
  si. Mesure du 2026-08-16 : Lomé → Paris **279 € → 862 € (+209 %)**, Brazzaville → Paris
  600 € → 1 306 € (+118 %), Kinshasa → Paris 377 € → 708 € (+88 %), Dakar → Paris 300 € → 496 €
  (+65 %) ; Abidjan → Paris était au contraire sur-estimé (511 € → 486 €). Conséquence mesurée
  avant correction : **les 20 meilleurs prix d'un relevé passaient tous par Paris**, non par
  réalité du marché mais parce que Paris portait le rabattement le plus bas de la table. Après
  correction, Paris ne représente plus que 6 des 20 meilleurs prix et Le Caire en prend 11 ; le
  meilleur trajet au départ de Lomé passe de « Paris → Londres 335 € » à « Abidjan → Accra
  649 € ». 1 783 lignes recalculées rétroactivement, selon la même procédure que la mise à jour
  précédente.
- **Piège aller simple / aller-retour documenté dans le code.** `v2/prices/latest` et
  `v3/prices_for_dates` prennent un paramètre `one_way` qui, laissé à `true`, renvoie des prix
  environ 43 % plus bas que les aller-retour de `v1/prices/cheap`. Une première sonde a ainsi
  « récupéré » 15 segments avec des valeurs incomparables ; seul un contrôle sur un segment couvert
  par les trois endpoints (`DKR→CMN` : v1=468, v2=467, v3=468 en aller-retour) l'a révélé.
- 12 segments restent sans prix sur aucun des trois endpoints et gardent leur ancienne valeur,
  marquée `[NM]` dans la table.

## 2026-08-16

### Modifié
- **`RABATTEMENT` remis à jour et historique recalculé rétroactivement.** Les 23 segments pour
  lesquels l'API renvoie un prix ont été réécrits d'après une mesure du 2026-08-16 ; 15 valeurs
  changent, dont Brazzaville → Lagos (400 € → **1 083 €**), Abidjan → Nairobi (374 € → 883 €) et
  Dakar → Abidjan (200 € → 409 €). Deux valeurs étaient **sur**-estimées (Abidjan → Istanbul
  700 € → 672 €). Les 17 segments sans prix API sont conservés tels quels et marqués `[NM]` dans
  la table, avec l'âge de la valeur : aucune n'est inventée pour combler un trou. `CDG` est `[NM]`
  pour les cinq villes, ce qui reste la limite principale de cette table.
- **Le recalcul rétroactif était indispensable, pas cosmétique.** Changer la table sans toucher
  l'historique aurait fait bondir `total_estime` sur **4 383 lignes (43 % de la base)** tandis que
  les moyennes historiques seraient restées basses : sur ces routes, le prix du jour serait passé
  systématiquement au-dessus de sa propre moyenne, et **plus aucune anomalie n'aurait été détectée
  pendant environ 49 relevés, soit sept semaines**. `prix_vol_hub` et `rabattement` étant stockés
  séparément, `total_estime` a pu être recalculé sur toute la base (sauvegarde prise avant). Le
  décalage étant additif et appliqué à l'ensemble de l'historique d'une route, les écarts relatifs
  et les z-scores sont préservés.
- Vérification après migration : 10 081 lignes intactes, 0 violation de
  `total_estime = prix_vol_hub + rabattement`, 0 ligne portant un rabattement différent de la
  table, et la détection retrouve **les mêmes anomalies aux mêmes totaux** que ceux calculés par
  la correction d'affichage — les deux mécanismes convergent.

### Corrigé
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

### Ajouté
- **Recherche de billet à la demande** (`recherche.py`) : interroger soi-même une route depuis
  l'une des 5 villes vers n'importe quel code IATA, au lieu de subir les propositions du relevé.
  Inclut le **vol direct**, que le collecteur n'interroge jamais — mesuré le 2026-08-15 sur
  `Dakar → Brazzaville`, le direct à 932 € bat les 1001 € via Paris annoncés par le relevé. Les
  segments ville → hub sont demandés à l'API plutôt que lus dans `RABATTEMENT`, dont les valeurs
  estimées se sont révélées optimistes de 17 à 31 % ; le repli sur la table est signalé entre
  parenthèses et une option entièrement mesurée passe devant une option estimée à total égal.
  Coût : 19 appels pour Dakar.
- **Mise sous surveillance** (`--surveiller`) : ajoute une destination au relevé quotidien via
  `destinations_perso.json` (local, non versionné, 15 maximum, +9 appels par destination). Elle
  bénéficie alors de la détection d'anomalie et des notifications Telegram existantes.
- 37 → 79 tests.

## 2026-08-15

### Corrigé
- **Des routes ramenaient une ville de départ chez elle.** Quatre villes de `RABATTEMENT` figurent aussi dans `DESTINATIONS` (`DKR`, `ABJ`, `BZV`, `FIH`). Le code excluait bien `destination == hub`, mais jamais `destination == ville de départ` : la base contenait donc des lignes « Dakar → via Paris → Dakar » (381 € de vol + 300 € de rabattement = 681 € pour revenir chez soi). L'exclusion ne pouvait pas se faire dans la boucle d'appels API — la route `CDG→DKR` reste valable pour les quatre autres villes de départ — elle se fait donc à l'insertion, ville par ville, dans `enregistrer_prix()`, via une table `VILLE_IATA`. Mesuré sur la base réelle : 105 lignes parasites sur 7 414 (1,4 %), réparties sur 46 relevés, soit ~16 par relevé. Lomé n'en produisait aucune, mais par accident seulement : `LFW` n'est pas dans `DESTINATIONS`. Un test structurel vérifie désormais que toute ville de `RABATTEMENT` a son code IATA, pour qu'ajouter une ville sans le sien échoue au lieu de réintroduire le bug silencieusement. Les 105 lignes historiques ont été purgées (sauvegarde de la base prise avant suppression) ; vérifié après coup que les routes légitimes vers ces mêmes villes sont intactes — `DKR` conserve ses lignes au départ d'Abidjan, Brazzaville, Kinshasa et Lomé. 30 → 32 tests.
- **Le seuil d'anomalie était mathématiquement inatteignable.** Le passage au z-score calculait la moyenne et l'écart-type sur *tout* l'historique, relevé du jour inclus. Un point inclus dans sa propre référence ne peut pas s'en écarter librement : avec un écart-type d'échantillon (N-1), son z-score est plafonné à `(n-1)/√n`, soit 0,71 sur 2 relevés, 1,16 sur 3 et 1,50 sur 4. Avec `SEUIL_ZSCORE = 1.5`, aucune route de moins de 4 relevés ne pouvait déclencher d'alerte, même en cas d'effondrement du prix — soit 1 014 des 1 341 routes de la base. Vérifié sur la base réelle : l'ancienne logique remontait **0 anomalie** sur le relevé du 2026-08-15, avec un z-score maximum de 1,155, exactement le plafond théorique pour n=3. `calculer_stats_historiques()` accepte désormais `exclure_date` et la détection construit sa référence **sans** le relevé jugé ; le même relevé remonte 6 anomalies.
- Suite de tests remise au vert : elle référençait encore `calculer_moyennes_historiques()` et `enregistrer_offres()`, renommées lors du refactor précédent sans mise à jour des tests (3 erreurs + 4 échecs).

### Sécurité
- **Le token d'API s'écrivait en clair dans le journal.** Sur erreur réseau, `requests` place l'URL complète dans son exception — query string comprise, donc `token=…`. Le message partait tel quel dans `flight_deals_log.txt` (2 occurrences constatées). Ajout de `masquer_secrets()`, appliqué **dans `log()`** et non chez les appelants : c'est le point de passage unique de tout ce qui est journalisé, donc le seul endroit où l'oubli est impossible. Couvre aussi le token du bot Telegram, que l'URL de l'API porte dans son chemin (`/bot<token>/sendMessage`). Le `chat_id` n'est volontairement pas masqué : il ne circule que dans le corps du POST, donc n'apparaît jamais dans une exception, et c'est souvent un nombre court — le remplacer aveuglément mutilerait des messages légitimes contenant la même suite de chiffres. Les occurrences déjà écrites ont été retirées du fichier existant (sans copie de sauvegarde, volontairement : elle conserverait le secret qu'on cherche à effacer). Portée réelle du problème : le journal est dans `.gitignore` et n'a jamais été commité — le token n'a donc jamais atteint GitHub, il n'était exposé que localement. 32 → 37 tests.

### Ajouté

*Les quatre entrées suivantes documentent un chantier resté non commité dans l'arbre de travail, absent du changelog jusqu'ici.*

- **Matrice hubs × destinations imposée**, en remplacement de `get_special_offers`. Cet endpoint ne renvoyait que ce que contenait le cache Aviasales — majoritairement des routes CEI/Asie centrale, la base d'utilisateurs du service étant russophone. `v1/prices/cheap` impose origine **et** destination : la couverture est désormais choisie (`DESTINATIONS`, 32 villes sur 5 zones). Ajout de `construire_lien()`, l'endpoint ne renvoyant pas de lien direct, et de `EQUIVALENCES` (`CDG`/`PAR` désignent la même ville, l'API renvoie 400 si origine = destination).
- Hubs `JNB` (Johannesburg), `CAI` (Le Caire) et `LOS` (Lagos) : 6 → 9 hubs surveillés.
- `RABATTEMENT["Brazzaville"]` : 3e ville de départ (plusieurs valeurs estimées, signalées en commentaire, à confirmer).
- Détection par **z-score** en remplacement du seuil en pourcentage fixe : le seuil devient relatif à la volatilité propre de chaque route. Voir la section « Corrigé » — cette bascule était inopérante en l'état.

- `RABATTEMENT["Lome"]` et `RABATTEMENT["Kinshasa"]` : 4e et 5e villes de départ actives. Coûts obtenus par requête directe à l'API Travelpayouts le 2026-08-15 (`v1/prices/cheap`, complété par `v3/prices_for_dates`), même méthode que pour Abidjan. Kinshasa couvre les 9 hubs ; Lomé en couvre 7 — `ADD` et `JNB` omis, aucun prix renvoyé par aucun des deux endpoints. Ces routes avaient été jugées non couvertes le 2026-08-03, mais via `get_special_offers` uniquement ; les endpoints à origine/destination imposées les couvrent bien.
- Repli en pourcentage (`SEUIL_BAISSE`, 8 %) quand une route a moins de `MIN_RELEVES_ZSCORE` (4) relevés d'historique ou aucune dispersion — en dessous, l'écart-type n'est pas assez fiable pour arbitrer seul.
- Plancher `PLANCHER_BAISSE_ZSCORE` (3 %) en mode z-score : sur une route très stable, 1,5 écart-type peut ne représenter que quelques euros.
- Champ `methode` (`"z-score"` ou `"pourcentage"`) dans chaque résultat de `detecter_anomalies()`, pour savoir quelle règle a tranché.
- Tests : couverture des deux bugs ci-dessus (dont un test de régression sur l'atteignabilité du seuil), du repli en pourcentage, du tri, des hausses et des routes vues pour la première fois ; invariants structurels de `RABATTEMENT` valables pour toute ville présente ou future (hubs connus, prix et durées positifs, pas de rabattement vers soi-même). 13 → 28 tests.

### Modifié
- `MIN_RELEVES_HISTORIQUE` (2) : une route n'est plus jugée tant qu'elle n'a pas **deux relevés antérieurs**. La réécriture de la détection avait fait sauter le garde-fou `nb_releves >= 2` de l'ancien code — une route était jugée dès une seule observation passée, ce qui revient à signaler le bruit quotidien d'un prix de billet. Effet mesuré sur le relevé du 2026-08-15 13:00 : 11 anomalies → 8, les trois retirées étant exactement celles à `n=1` ; les 8 restantes ont 4 relevés d'historique et passent toutes par le z-score.
- Tâche planifiée « Traqueur de vols » : ajout d'un déclencheur **quotidien à 13h00** en plus du déclencheur d'ouverture de session, qui était jusqu'ici le seul — la collecte dépendait donc entièrement des connexions, et quelques jours sans allumer la machine créaient un trou dans l'historique dont la détection d'anomalie a besoin. `StartWhenAvailable` activé pour rattraper une exécution manquée. Restrictions batterie (`DisallowStartIfOnBatteries`, `StopIfGoingOnBatteries`) levées : sur ce portable, une session ouverte sur batterie empêchait le démarrage et un débranchement en cours de relevé tuait la tâche en laissant des données partielles, sans avertissement.
- Tri des anomalies par baisse décroissante plutôt que par z-score : critère lisible et commun aux deux méthodes de détection (le z-score est absent en mode pourcentage).
- `duree_h` documenté comme purement indicatif (il n'entre dans aucun calcul). Pour Lomé et Kinshasa, c'est la durée d'itinéraire renvoyée par l'API, escales comprises — d'où des valeurs plus élevées que les estimations « temps de vol » des premières villes.

## 2026-08-03

### Ajouté
- `anomaly_detection.py` : logique de détection d'anomalie mutualisée (moyenne historique, seuil `SEUIL_BAISSE`), utilisée par `hub_deals_db.py` et `detect_anomalies.py`
- `.gitignore`, dépôt git local puis distant (GitHub privé)
- `README.md`, `LICENSE` (MIT) et badge licence associé
- `requirements.txt` (dépendance `requests`)
- `.gitattributes` pour normaliser les fins de ligne en LF
- `hub_deals_AUDIT.md` : journal d'audit détaillé du projet
- Généralisation multi-villes de départ : `HUBS`/`RABATTEMENT` imbriqué (`RABATTEMENT[ville][hub]`), nouvelle colonne `ville_depart` dans `offres` (migration idempotente, backfill `'Dakar'` sur les lignes existantes), regroupement des moyennes historiques par (ville de départ, hub, destination) dans `anomaly_detection.py`, suite `tests/` (`unittest`, 13 tests) couvrant la migration et la non-contamination des moyennes entre villes
- `RABATTEMENT["Abidjan"]` : deuxième ville de départ active, 4 hubs (CMN, CDG, IST, NBO) — coûts obtenus par requête directe à l'API Travelpayouts (`v1/prices/cheap`, complété par `v3/prices_for_dates` pour NBO), contrairement à Dakar (estimation manuelle). `ADD` omis (aucune donnée API disponible pour cette route), pas d'entrée `ABJ` (Abidjan est déjà le hub)
- `tests/test_hub_deals_db.py` : test de garde-fou vérifiant les clés et les valeurs de `RABATTEMENT["Abidjan"]`

### Modifié
- Secrets (`TRAVELPAYOUTS_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) externalisés en variables d'environnement — plus aucune valeur en dur dans le code
- `detect_anomalies.py` réécrit en wrapper CLI fin autour de `anomaly_detection.py`
- Projet déplacé de `C:\Users\Dell\` (racine du profil) vers `C:\Users\Dell\hub_deals\` ; tâche planifiée "Traqueur de vols" mise à jour en conséquence
- Message de notification Telegram enrichi de la ville de départ (`depuis {hub}, au depart de {ville_depart}`)

### Supprimé
- `hub_deals.py` (script obsolète, remplacé par `hub_deals_db.py`)
- Hubs BZV (Brazzaville) et FIH (Kinshasa) retirés de `RABATTEMENT` — l'API Travelpayouts n'a aucune couverture "special offers" sur ces routes (confirmé par appel direct)

### Sécurité
- Token du bot Telegram régénéré via BotFather (l'ancien token, précédemment exposé en clair dans le code, a été révoqué)

## 2026-07-29

### Ajouté
- `detect_anomalies.py` : détection d'anomalie de prix par comparaison à la moyenne historique, avec mode diagnostic

## 2026-07-21

### Ajouté
- `hub_deals_db.py` : version avec stockage SQLite cumulatif (`flight_deals.db`), classement du jour et notification Telegram
- Tâche planifiée Windows "Traqueur de vols" pour l'exécution automatique quotidienne

## 2026-07-19

### Ajouté
- `test_travelpayouts.py` : script de test initial de l'API Travelpayouts
