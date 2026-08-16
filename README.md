# hub_deals

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Détecteur de bonnes affaires vol au départ de Dakar, Abidjan, Brazzaville, Lomé et Kinshasa, via 9 hubs de correspondance (Casablanca, Paris, Istanbul, Addis-Abeba, Nairobi, Abidjan, Johannesburg, Le Caire, Lagos). Interroge l'API Travelpayouts sur une matrice imposée de 32 destinations, stocke l'historique en SQLite, détecte les anomalies de prix par rapport à l'historique de chaque route, et notifie les bonnes affaires par Telegram.

## Principe

Un rabattement ville de départ → hub a un coût forfaitaire connu (voir `RABATTEMENT` dans `hub_deals_db.py`, imbriqué par ville puis par hub : `RABATTEMENT[ville][hub]`). Le total estimé d'un trajet est donc :

```
total_estime = prix_vol_depuis_le_hub + cout_rabattement_ville_hub
```

Cinq villes de départ sont actives : **Dakar**, **Abidjan**, **Brazzaville**, **Lomé** et **Kinshasa**. Ajouter une ville se fait en ajoutant une entrée à `RABATTEMENT`, sans autre changement de code **et sans appel API supplémentaire** : le prix hub → destination n'est interrogé qu'une fois, puis réutilisé pour chaque ville de départ (279 appels par relevé, quel que soit le nombre de villes).

Une ville n'a pas d'entrée de rabattement vers un hub qui est elle-même (cas d'Abidjan, à la fois ville de départ et hub `ABJ`), ni vers un hub pour lequel l'API ne renvoie aucun prix — omis plutôt qu'estimé : `ADD` pour Abidjan, `ADD` et `JNB` pour Lomé.

### Détection d'anomalie

Chaque exécution enregistre les offres du jour dans `flight_deals.db`, avec la ville de départ (`ville_depart`). `anomaly_detection.py` compare ensuite le prix du jour à l'historique de sa route — clé (ville de départ, hub, destination), pour ne jamais mélanger deux villes.

Deux règles, selon la profondeur d'historique disponible :

| Historique de la route | Règle appliquée | Déclenche si |
|---|---|---|
| < 2 relevés antérieurs | *aucune* | la route n'est pas jugée |
| 2 ou 3 relevés, ou aucune dispersion | pourcentage | baisse ≥ 8 % sous la moyenne |
| ≥ 4 relevés, avec dispersion | z-score | prix ≥ 1,5 écart-type sous la moyenne **et** baisse ≥ 3 % |

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
| `test_travelpayouts.py` | Script de test brut de l'API Travelpayouts. |
| `hub_deals_AUDIT.md` | Journal d'audit détaillé du projet (historique des décisions et correctifs). |
| `flight_deals.db`, `flight_deals_log.txt`, `destinations_perso.json` | Générés à l'exécution — ignorés par git. |

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
python recherche.py --surveiller BKK   # +9 appels par relevé
python recherche.py --liste
python recherche.py --oublier BKK
```

Les destinations surveillées sont stockées dans `destinations_perso.json` (local, non versionné),
15 au maximum. Une recherche n'écrit jamais dans la base.

## Sauvegardes

Deux mécanismes, pour **deux risques différents** :

| | Protège de | Où |
|---|---|---|
| Copie locale | erreur logique, migration ratée | même disque, 5 copies gardées |
| Dump distant | perte de la machine, disque mort | branche `sauvegardes` du dépôt privé |

La copie locale ne protège **pas** d'une panne matérielle : elle vit sur le même disque. Le dump
distant est le seul qui survit à la perte du portable. Il est poussé automatiquement à la fin de
chaque relevé, et une panne de git ou de réseau n'interrompt jamais la collecte.

Le dump est un fichier SQL texte : git l'encode en deltas efficaces, et il se restaure sans
dépendre du format binaire de SQLite.

```bash
python sauvegarde.py --sauver                       # copie locale + dump distant
git show sauvegardes:flight_deals.sql > dump.sql    # récupérer le dump
python sauvegarde.py --restaurer dump.sql neuve.db  # restaurer
```

La restauration est **outillée et non documentée** : `sqlite3` n'existe pas en ligne de commande
sur toutes les machines — notamment pas sur celle-ci — et une procédure qu'on découvre
inexécutable le jour de la panne ne vaut rien. `--restaurer` refuse d'écraser un fichier existant.

## Tests

```
python -m unittest discover -s tests -v
```

## Automatisation

Tourne via la tâche planifiée Windows **« Traqueur de vols »**, configurée avec `C:\Users\Dell\hub_deals` comme répertoire de travail. Deux déclencheurs :

| Déclencheur | Quand |
|---|---|
| Ouverture de session | à chaque connexion (d'où plusieurs relevés certains jours) |
| Quotidien | tous les jours à 13h00 |

Le déclencheur quotidien a été ajouté le 2026-08-15 : jusque-là la collecte reposait **uniquement** sur l'ouverture de session, donc quelques jours sans allumer la machine suffisaient à créer un trou dans l'historique — or la détection d'anomalie a besoin d'un historique régulier. `StartWhenAvailable` est activé pour rattraper une exécution manquée si la machine était éteinte à 13h00.

Les restrictions batterie (`DisallowStartIfOnBatteries`, `StopIfGoingOnBatteries`) ont été levées le même jour : sur ce portable, une session ouverte sur batterie empêchait la tâche de démarrer, et un débranchement en cours de relevé la tuait en laissant des données partielles — sans le moindre avertissement.

> Modifier cette tâche demande une session PowerShell **élevée** (elle réside dans le dossier racine du planificateur) ; `schtasks /Change` fonctionne sans élévation mais n'expose ni les réglages batterie ni l'ajout de déclencheur.

## Licence

[MIT](LICENSE)
