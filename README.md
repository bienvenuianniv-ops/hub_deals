# hub_deals

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Détecteur de bonnes affaires vol au départ de treize villes, via neuf hubs de correspondance (Casablanca, Paris, Istanbul, Addis-Abeba, Nairobi, Abidjan, Johannesburg, Le Caire, Lagos) et quatre villes interrogées uniquement pour que leurs propres habitants voient leur vol direct (Dakar, Kinshasa, Brazzaville, Lomé) — depuis le 2026-09-19, c'est exactement le même ensemble de treize villes des deux côtés. Interroge l'API Travelpayouts sur une matrice imposée de 32 destinations, stocke l'historique en SQLite, détecte les anomalies de prix par rapport à l'historique de chaque route, et notifie les bonnes affaires par Telegram.

## Principe

Un rabattement ville de départ → hub a un coût forfaitaire connu (voir `RABATTEMENT` dans `hub_deals_db.py`, imbriqué par ville puis par hub : `RABATTEMENT[ville][hub]`). Le total estimé d'un trajet est donc :

```
total_estime = prix_vol_depuis_le_hub + cout_rabattement_ville_hub
```

Une ville peut aussi être **sa propre origine** : le rabattement vaut alors 0 et
`total_estime` se réduit au prix du vol direct. C'est le cas des treize villes vers leur
propre hub — un Parisien ne paie rien pour rejoindre Paris.

Treize villes de départ sont actives : les cinq historiques — **Dakar**, **Abidjan**, **Brazzaville**, **Lomé**, **Kinshasa** — et les huit villes résidentes ajoutées le 2026-09-19 — **Paris**, **Istanbul**, **Casablanca**, **Le Caire**, **Lagos**, **Nairobi**, **Addis-Abeba**, **Johannesburg**. Ajouter une ville qui réutilise un hub existant se fait en ajoutant une entrée à `RABATTEMENT`, sans autre changement de code **et sans appel API supplémentaire** : le prix hub → destination n'est interrogé qu'une fois, puis réutilisé pour chaque ville de départ. Les huit villes résidentes sont dans ce cas : leur hub (Paris, Istanbul…) existait déjà. Seuls Dakar, Kinshasa, Brazzaville et Lomé ont dû devenir des hubs à part entière pour voir leur propre vol direct — ce sont ces quatre hubs, et non les treize villes, qui font passer le relevé de 279 à 404 appels par relevé.

Une ville a une entrée de rabattement à 0 vers un hub qui est elle-même — le cas résident, décrit plus haut. Avant le 2026-09-19, seule Abidjan cumulait les deux rôles (ville de départ et hub `ABJ`) et n'avait alors *aucune* entrée vers elle-même ; elle en a désormais une, à 0, comme les douze autres. Une ville n'a en revanche toujours aucune entrée vers un hub pour lequel l'API ne renvoie aucun prix — omis plutôt qu'estimé : `ADD` pour Abidjan, `ADD` et `JNB` pour Lomé.

### Détection d'anomalie

Chaque exécution enregistre les offres du jour dans `flight_deals.db`, avec la ville de départ (`ville_depart`). `anomaly_detection.py` compare ensuite le prix du jour à l'historique de sa route — clé (ville de départ, hub, destination), pour ne jamais mélanger deux villes.

Deux règles, selon la profondeur d'historique disponible :

| Historique de la route | Règle appliquée | Déclenche si |
|---|---|---|
| < 2 relevés antérieurs | *aucune* | la route n'est pas jugée |
| 2 ou 3 relevés, ou aucune dispersion | pourcentage | baisse ≥ 8 % sous la moyenne |
| ≥ 4 relevés, avec dispersion | z-score | prix ≥ 2 écarts-types sous la moyenne **et** baisse ≥ 6 % |

Un critère s'ajoute aux deux méthodes : la baisse doit représenter au moins **80 €** d'économie
(`ECONOMIE_MINIMALE`). C'est le seul seuil aveugle au prix du billet, et c'est voulu — il rattrape
les routes bon marché, où un joli pourcentage ne pèse que quelques dizaines d'euros.

Ce plancher de 80 € est calibré pour des itinéraires à 1 127 € de médiane, où il pèse 7 %
du billet. Sur un vol direct au départ de la ville de l'abonné — rabattement nul, billet
médian de 270 à 400 € — il exigerait 20 à 30 % de baisse et n'a rien laissé passer sur
52 relevés. Ces routes utilisent donc un plancher relatif : `max(25 €, 12 % de la
moyenne)`. Le pourcentage est légitime ici et nulle part ailleurs, parce qu'il n'est
déformé que par la correction de rabattement — inexistante quand le rabattement est nul.

Le message d'alerte affiche cette économie en euros. C'est volontaire : le rabattement mesuré du jour décale le prix *et* la moyenne du même montant, ce qui laisse l'économie absolue intacte mais **change le pourcentage affiché** — il peut donc passer sous le plancher de détection sans que l'affaire ait changé. L'économie en euros, elle, ne bouge pas.

Le minimum de deux relevés antérieurs (`MIN_RELEVES_HISTORIQUE`) évite de comparer le prix du jour à une observation unique : un billet d'avion bouge assez d'un jour à l'autre pour qu'une telle « référence » ne signale que du bruit.

Le z-score rend le seuil relatif à la volatilité propre de chaque route : une baisse de 6 % sur une route très stable peut être plus significative qu'une baisse de 15 % sur une route erratique. Mais il demande assez de points pour que l'écart-type veuille dire quelque chose — d'où le repli en pourcentage.

**Point critique :** le relevé jugé est exclu de sa propre référence. Sinon il tire la moyenne vers lui et se compare à une référence qu'il a lui-même déformée, ce qui plafonne mécaniquement le z-score à `(n-1)/√n` — 0,71 sur 2 relevés, 1,16 sur 3, 1,50 sur 4. Un seuil à 1,5 devient alors inatteignable en dessous de 4 relevés, quelle que soit l'ampleur de la baisse (voir CHANGELOG du 2026-08-15).

Toute anomalie déclenche une notification Telegram, qui mentionne la ville de départ.

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

## Fichiers

| Fichier | Rôle |
|---|---|
| `hub_deals_db.py` | Script principal : récupère les offres, les enregistre en base, notifie les anomalies. C'est lui qu'exécute la tâche planifiée. |
| `anomaly_detection.py` | Logique partagée de détection d'anomalie (moyenne et écart-type historiques, z-score avec repli en pourcentage), utilisée par les deux scripts ci-dessous. |
| `detect_anomalies.py` | Outil CLI d'analyse/diagnostic — relit la base et affiche les comparaisons, sans notifier. |
| `recherche.py` | Recherche de billet à la demande : interroger soi-même une route, et mettre une destination sous surveillance du relevé quotidien. |
| `reprise_residents.py` | Migration ponctuelle : reporte l'historique des vols directs des villes résidentes. Rejouable sans risque. |
| `abonnes.py` | Abonnés du bot (test privé) : inscriptions, filtrage des affaires par ville, message d'abonné, envoi, témoin d'écoute. Sans réseau. |
| `bot_ecoute.py` | Programme d'écoute permanent du bot : commandes `/start`, `/ville`, `/stop`. Seul lecteur de `getUpdates`. |
| `taches/` | Définition XML et script d'installation de la tâche planifiée « Bot vols - ecoute ». |
| `test_travelpayouts.py` | Script de test brut de l'API Travelpayouts. |
| `hub_deals_AUDIT.md` | Journal d'audit détaillé du projet (historique des décisions et correctifs). |
| `flight_deals.db`, `flight_deals_log.txt`, `bot_ecoute_log.txt`, `destinations_perso.json` | Générés à l'exécution — ignorés par git. |

## Installation

```
pip install -r requirements.txt
```

## Configuration

Variables d'environnement requises (aucun secret en dur dans le code) :

```
TRAVELPAYOUTS_TOKEN=...   # requis
TELEGRAM_BOT_TOKEN=...    # optionnel — sans lui, pas de notification
TELEGRAM_CHAT_ID=...      # optionnel — idem
```

## Usage

```
python hub_deals_db.py       # collecte + notification
python detect_anomalies.py   # analyse/diagnostic sans notifier
```

## Recherche à la demande

Le relevé quotidien propose ce qu'il juge intéressant. Pour poser sa propre question :

```bash
python recherche.py Dakar BZV          # par code IATA
python recherche.py Dakar Brazzaville  # par nom, pour les destinations connues
python recherche.py Kinshasa BKK
```

La recherche interroge le **vol direct** (que le relevé automatique n'interroge jamais) et chaque
hub disposant d'un rabattement pour cette ville, puis classe les itinéraires du moins cher au plus
cher. Les prix d'aller sont demandés à l'API ; quand elle ne répond pas, la valeur estimée de
`RABATTEMENT` sert de repli et est signalée entre parenthèses.

Pour suivre une destination dans le temps et recevoir les alertes Telegram dessus :

```bash
python recherche.py --surveiller BKK   # +13 appels par relevé (un par hub)
python recherche.py --liste
python recherche.py --oublier BKK
```

Les destinations surveillées sont stockées dans `destinations_perso.json` (local, non versionné),
15 au maximum. Une recherche n'écrit jamais dans la base.

## Bot multi-abonnés (test privé)

Des invités s'abonnent à `@ianniv_vols_bot` avec un lien
`https://t.me/ianniv_vols_bot?start=<CODE>`, choisissent leur ville de départ et reçoivent
chaque jour les affaires de cette ville. Le propriétaire (`TELEGRAM_CHAT_ID`) continue de
recevoir le message complet, envoyé en premier.

| Variable (portée User) | Rôle |
|---|---|
| `HUB_DEALS_CODE_INVITATION` | code du lien d'invitation, 12 à 64 caractères `A-Z a-z 0-9 _ -`. Absent : inscriptions fermées |
| `TRAVELPAYOUTS_MARKER` | identifiant d'affilié ajouté aux liens. Absent : liens sans affiliation |
| `TRAVELPAYOUTS_PROJET` | ID du projet Travelpayouts (`source=` dans l'adresse du tableau de bord) : liens courts, seuls comptés comme clics. Absent : liens directs |

Commandes : `/start` (avec le code la première fois), `/ville`, `/stop`. Plafond :
`abonnes.PLAFOND_ABONNES` abonnés actifs.

`bot_ecoute.py` tourne en permanence (tâche « Bot vols - ecoute », ouverture de session,
`pythonw`) et journalise dans `bot_ecoute_log.txt`. C'est le **seul** lecteur de `getUpdates`.
S'il ne tourne plus, le relevé suivant envoie une alerte au propriétaire.

Installation de la tâche (UAC à valider) :
`Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Users\Dell\hub_deals\taches\installer_bot_ecoute.ps1'`
puis lire `%TEMP%\installer_bot_ecoute.txt`.

## Sauvegardes

Deux mécanismes, pour **deux risques différents** :

| | Protège de | Où |
|---|---|---|
| Copie locale | erreur logique, migration ratée | même disque, 5 copies gardées |
| Dump distant | perte de la machine, disque mort | branche `sauvegardes` du dépôt — **public** |

La copie locale ne protège **pas** d'une panne matérielle : elle vit sur le même disque. Le dump
distant est le seul qui survit à la perte du portable. Il est poussé automatiquement à la fin de
chaque relevé, et une panne de git ou de réseau n'interrompt jamais la collecte.

Le dump est un fichier SQL texte : git l'encode en deltas efficaces, et il se restaure sans
dépendre du format binaire de SQLite.

**Le dépôt est public, donc le dump ne contient aucune donnée personnelle.** `generer_dump` copie
la base en mémoire et vide `abonnes` et `etat_bot` avant de dumper — le schéma reste, les lignes
partent. Ajouter une table qui contient des données d'abonnés impose de l'ajouter à
`TABLES_PRIVEES`. Conséquence à connaître : **les abonnés ne sont sauvegardés nulle part**, tant
qu'ils vivent dans cette base SQLite.

```bash
python sauvegarde.py --sauver                       # copie locale + dump distant
git show sauvegardes:flight_deals.sql > dump.sql    # récupérer le dump
python sauvegarde.py --restaurer dump.sql neuve.db  # restaurer
```

La restauration est **outillée et non documentée** : `sqlite3` n'existe pas en ligne de commande
sur toutes les machines — notamment pas sur celle-ci — et une procédure qu'on découvre
inexécutable le jour de la panne ne vaut rien. `--restaurer` refuse d'écraser un fichier existant.

## Vigie externe

Une tâche GitHub Actions (`.github/workflows/vigie.yml`) tourne chaque jour à 15h UTC et vérifie,
**depuis l'extérieur**, que le relevé du portable tourne toujours. Une vigie hébergée sur le
portable se tairait en même temps que lui : le 17/09, la machine a dormi 12 h sans que personne
ne le sache.

Elle ne lit que ce que le portable a déjà poussé : la date et le volume des commits de la branche
`sauvegardes`. **Rien n'est modifié côté portable**, et elle n'appelle jamais l'API des prix.

| Constat | Message |
|---|---|
| Plus de sauvegarde depuis 26 h | « plus de relevé depuis N h » |
| Dernier relevé sous la moitié de la médiane des 10 précédents | « relevé anormalement court » |
| Une ville sous la moitié de **sa propre** médiane | « N ville(s) au volume effondré », toutes nommées dans un seul message |
| Rien à signaler | **silence** — sauf le lundi, bilan d'une ligne |

Le critère par ville existe parce que le volume total ne voit pas la panne d'une partie du parc :
si les huit villes qui ne partent que de leur propre hub disparaissaient, le relevé garderait
76 % de son volume habituel et passerait pour normal. Chaque ville est jugée contre sa propre
médiane, les absences comptant pour zéro — une ville nouvelle ou intermittente a donc une médiane
basse et ne déclenche rien. En dessous de 10 lignes de médiane, une ville n'est pas jugée. Les
volumes sont lus dans le dump déjà poussé, chargé dans une base SQLite en mémoire (0,7 s). La
vigie imprime ce qu'elle a lu (`Volumes lus : 11 relevé(s), 13 ville(s)`) et rend un code non nul
si le dump est illisible : sans quoi « aucune ville effondrée » et « je n'ai rien pu lire »
seraient le même silence.

Le silence est donc normal. Le bilan du lundi est le signe de vie de la vigie elle-même, et si la
vigie plante, GitHub envoie un courriel d'échec : sa panne ne peut pas être silencieuse.

```bash
python vigie.py --sans-envoi   # juger l'état sans rien envoyer
```

Secrets attendus dans le dépôt (`Settings > Secrets and variables > Actions`) :
`TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID`.

## Clics affiliés

`clics.py` lit les statistiques Travelpayouts par Sous-ID et par jour, sans ouvrir le tableau de
bord et sans cliquer sur quoi que ce soit — ouvrir un vrai lien pour « vérifier » fausserait les
compteurs.

```bash
python clics.py                      # les 30 derniers jours
python clics.py --sub-id dakar --depuis 2026-09-16
```

Trois colonnes, parce que trois choses différentes se ressemblent dans un rapport :

| Colonne | Ce que c'est |
|---|---|
| **clics** | redirection par `aviasales.tpk.ro` non marquée robot — c'est ce que compte le tableau de bord |
| **robots** | la même chose, marquée `is_bot` : aperçus de liens générés par Telegram, sondes. Exclus des clics, montrés plutôt qu'effacés |
| **directs** | visite rattachée au marker déposé par un lien **direct** — en pratique nos messages Telegram d'avant le 16/09, qui restent cliquables indéfiniment. **Jamais** comptée comme clic, mais le Sous-ID est là : une réservation serait bien créditée |

Les trois se distinguent de façon nette, vérifié le 20/09 : un événement issu d'un lien court porte
`promo_id` 4114 et `traffic_source` 574520, et partage son `trace_id` avec sa redirection ; un
direct n'a ni l'un ni l'autre et son `trace_id` vaut son propre `action_id` — un UUIDv7 dont
l'horodatage encodé est celui de l'événement, donc rien ne le précède. Ce que les données ne
disent pas : si un direct est l'ouverture d'un ancien lien ou une nouvelle recherche dans une
session déjà attribuée. Les deux portent `sub_type: search` et `page_url` est vide.

Ce module est né de l'écart « `dakar` = 4 clics pour 1 attendu » du 16/09, resté inexpliqué
quatre jours : l'API donne chaque événement à la seconde, et les quatre étaient quatre vraies
ouvertures du même lien, avec quatre `trace_id` distincts.

## Tests

```
python -m unittest discover -s tests -v
```

Ils tournent aussi à chaque poussée, via `.github/workflows/tests.yml`. Le runner est
**Windows** : le relevé tourne sous Windows et plusieurs tests appellent `tasklist` ou
`pythonw.exe`, qui n'existent pas ailleurs — sur Linux ils échoueraient ou seraient sautés sans
rien prouver. Vérifier `0 skipped` dans la sortie fait partie du contrôle.

## Automatisation

Tourne via la tâche planifiée Windows **« Traqueur de vols »**, configurée avec `C:\Users\Dell\hub_deals` comme répertoire de travail. Deux déclencheurs :

| Déclencheur | Quand |
|---|---|
| Ouverture de session | à chaque connexion (d'où plusieurs relevés certains jours) |
| Quotidien | tous les jours à 13h00 |

Le déclencheur quotidien a été ajouté le 2026-08-15 : jusque-là la collecte reposait **uniquement** sur l'ouverture de session, donc quelques jours sans allumer la machine suffisaient à créer un trou dans l'historique — or la détection d'anomalie a besoin d'un historique régulier. `StartWhenAvailable` est activé pour rattraper une exécution manquée si la machine était éteinte à 13h00.

Les restrictions batterie (`DisallowStartIfOnBatteries`, `StopIfGoingOnBatteries`) ont été levées le même jour : sur ce portable, une session ouverte sur batterie empêchait la tâche de démarrer, et un débranchement en cours de relevé la tuait en laissant des données partielles — sans le moindre avertissement.

La tâche lance **`pythonw.exe`** (et non `python.exe`) depuis le 2026-09-13 : aucune fenêtre ne
s'ouvre, donc aucune qu'on puisse fermer par mégarde — 4 relevés sur 99 avaient été tués ainsi
(code de sortie `0xC000013A`). Contrepartie gérée dans le code : sans console, un plantage serait
invisible, d'où `sys.excepthook` qui écrit toute trace d'exception (masquée) dans
`flight_deals_log.txt` ; et les commandes git sont lancées avec `CREATE_NO_WINDOW`, sans quoi
chacune ouvrirait sa propre fenêtre.

> Modifier cette tâche demande une session PowerShell **élevée** (elle réside dans le dossier racine du planificateur) ; `schtasks /Change` fonctionne sans élévation mais n'expose ni les réglages batterie ni l'ajout de déclencheur.

## Licence

[MIT](LICENSE)
