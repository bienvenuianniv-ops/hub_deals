# Recherche de billet à la demande — design

## Contexte

`hub_deals` fonctionne aujourd'hui **en éventail** : la tâche planifiée interroge 9 hubs × 32
destinations imposées, calcule un total par ville de départ (`prix du vol` + `rabattement`), et
notifie par Telegram ce que la détection statistique juge anormalement bas. L'utilisateur **subit**
ce que le programme lui propose — il ne peut pas poser sa propre question.

Cette itération ajoute le chemin inverse : *je sais où je veux aller, dis-moi combien ça coûte et
comment y aller au mieux*. Elle ajoute aussi la possibilité de placer une destination choisie sous
la surveillance du relevé quotidien, pour bénéficier ensuite des alertes de baisse.

Origine du besoin : « je voulais dire si je peux faire une recherche de billet personnalisé au lieu
d'attendre que le programme m'en impose », puis « j'en veux un outil de recherche de billet et
bonnes affaires pour n'importe quelle destination ».

## Découvertes de faisabilité (sondes jetables du 2026-08-15)

Trois constats mesurés contre l'API réelle, qui déterminent la conception :

1. **Le vol direct n'est jamais interrogé.** Le collecteur passe toujours par un hub. Sonde :
   `DKR → BZV` en direct vaut **932 €**, alors que le relevé du jour annonce **1001 € via Paris**
   comme meilleure option. L'outil de recherche doit donc systématiquement inclure le vol direct —
   c'est un angle mort structurel, pas un réglage.

2. **La table `RABATTEMENT` est optimiste.** Les valeurs de Dakar sont des estimations manuelles
   arrondies (juillet 2026) :

   | Segment | API (2026-08-15) | Table | Écart |
   |---|---|---|---|
   | DKR → CMN | 468 € | 400 € | +17 % |
   | DKR → IST | 525 € | 400 € | +31 % |
   | DKR → CDG | *aucun prix en cache* | 300 € | — |

   Conséquence : les totaux affichés par le relevé sont **sous-estimés**. Une recherche à la demande
   doit interroger les segments réels plutôt que lire cette table. **La correction de la table
   elle-même est hors scope de cette spec** (voir « Problème connexe » plus bas).

3. **La couverture de l'API est inégale.** `v1/prices/cheap` est un cache de prix déjà observés,
   pas un moteur de recherche : `MRS → BKK` (613 €) et `JNB → BKK` (528 €) répondent, `LOS → BKK` et
   `NBO → BKK` ne renvoient rien. Une destination absente du cache n'est pas une erreur : c'est un
   trou à afficher honnêtement, jamais à combler par une valeur inventée.

## Décisions actées (brainstorming)

| Question | Décision |
|---|---|
| Interface | **Ligne de commande.** Un bot Telegram interactif exigerait un processus à l'écoute en permanence ; la machine est un portable et la collecte tourne par tâche planifiée. |
| Origines | **Les 5 villes de `RABATTEMENT` uniquement** (Dakar, Abidjan, Brazzaville, Lomé, Kinshasa). Une origine arbitraire imposerait de mesurer à la main ses coûts vers 9 hubs, ou un second mécanisme de collecte par routes explicites — écarté pour l'instant. |
| Destinations | **N'importe quel code IATA**, y compris hors des 32 destinations suivies. |
| Prix des segments | **API en priorité, repli sur `RABATTEMENT`** quand le cache ne répond pas, avec la provenance affichée. |
| « Bonne affaire » | **Les deux réponses** : classement immédiat des itinéraires entre eux, *et* possibilité de placer la destination sous surveillance pour obtenir de vraies alertes de baisse ensuite. |
| Stockage des destinations surveillées | **Fichier `destinations_perso.json`**, écrit par la recherche, lu et fusionné par le relevé. Pas de migration de schéma, éditable à la main, hors du code. |

## Architecture

Un nouveau module autonome, plus une greffe minimale sur le collecteur.

```
recherche.py                 (nouveau)  point d'entrée CLI + logique de recherche
destinations_perso.json      (nouveau, généré, gitignoré)  destinations sous surveillance
hub_deals_db.py              (modifié)  fusionne les destinations perso dans la boucle
tests/test_recherche.py      (nouveau)  suite unittest, aucun appel réseau
```

`recherche.py` **importe** `hub_deals_db` pour réutiliser `get_prix_route`, `construire_lien`,
`HUBS`, `DESTINATIONS`, `RABATTEMENT`, `VILLE_IATA` et `PAUSE_ENTRE_APPELS`. Il ne duplique aucune
constante. L'import est sans effet de bord : tout le code d'exécution du collecteur est sous
`if __name__ == "__main__"`.

`VILLE_IATA` — ajoutée le 2026-08-15 pour corriger le bug des routes ville → elle-même — sert ici
une seconde fois : elle donne le code d'origine du vol direct, et permet de refuser une recherche
dont la destination est la ville de départ.

## Interface en ligne de commande

```
python recherche.py <ville> <destination>     recherche un itinéraire
python recherche.py --surveiller <dest>       ajoute une destination au relevé quotidien
python recherche.py --oublier <dest>          la retire
python recherche.py --liste                   affiche les destinations surveillées et leur coût
```

**Résolution de la destination** : un argument de 3 lettres est traité comme un code IATA
(`BKK`) ; sinon il est cherché parmi les noms de `DESTINATIONS` sans tenir compte de la casse
(`brazzaville` → `BZV`). Un nom inconnu de plus de 3 lettres est refusé avec un message expliquant
qu'il faut le code IATA — le programme n'a pas d'annuaire de villes et ne doit pas deviner.

**Erreurs traitées explicitement**, chacune avec un message actionnable :

| Cas | Comportement |
|---|---|
| Ville inconnue | Refus + liste des 5 villes disponibles |
| Destination = ville de départ | Refus (« Dakar → Dakar n'a pas de sens ») |
| Nom de destination non résolu | Refus + invitation à donner le code IATA |
| Aucun itinéraire trouvé | Message clair : le cache ne connaît pas cette route, ce n'est pas une panne |
| Erreur réseau sur un segment | Segment ignoré, recherche poursuivie, mention en fin de sortie |

## Algorithme de recherche

Pour une ville V et une destination D :

1. Vol direct : `get_prix_route(VILLE_IATA[V], D)` — **1 appel**.
2. Pour chaque hub H de `RABATTEMENT[V]`, en sautant `H == D` (l'itinéraire « via D pour aller à
   D » est le vol direct, déjà couvert) :
   - segment aller `V → H` : `get_prix_route(VILLE_IATA[V], H)`. Si le cache ne répond pas, repli
     sur `RABATTEMENT[V][H]["prix"]`, marqué comme estimé.
   - segment principal `H → D` : `get_prix_route(H, D)`. **Sans prix, l'option est écartée** — il
     n'existe aucune valeur de repli honnête pour ce segment.
   - total = aller + principal.
3. Trier par total croissant, vol direct inclus dans le même classement.
4. Afficher chaque option avec sa décomposition, la provenance de chaque prix, la date de départ et
   le lien Aviasales (`construire_lien`).
5. Ajouter le contexte historique si la base connaît le couple (V, D) : nombre de relevés, meilleur
   prix jamais observé, écart du prix actuel à ce minimum.

**Biais du classement mixte — à traiter, pas à ignorer.** Un total qui repose sur un aller estimé
est comparé à des totaux entièrement mesurés, alors que les estimations se sont révélées optimistes
de 17 à 31 %. Une option estimée peut donc prendre la tête du classement sans le mériter. Trois
règles en découlent :

- toute option comportant un prix estimé est **signalée visuellement** (parenthèses) ;
- quand la meilleure option repose sur un aller estimé, la ligne « Meilleure option » porte un
  **avertissement explicite** indiquant que le total réel peut être plus élevé ;
- à total égal, une option entièrement mesurée passe **devant** une option estimée.

Aucune pondération corrective n'est appliquée : inventer un coefficient de rattrapage
(« +25 % sur les estimations ») remplacerait une valeur fausse par une autre, sans base mesurée.
La transparence est préférée à la correction.

**Coût** : `1 + 2 × nb_hubs(V)` appels au maximum, soit **19 pour Dakar** (9 hubs), ~10 s avec la
pause de 0,4 s. À comparer aux 279 appels du relevé automatique. `PAUSE_ENTRE_APPELS` est respectée
entre chaque appel, comme dans le collecteur.

**Aucune écriture en base.** Une recherche manuelle ne doit pas entrer dans l'historique qui nourrit
la détection statistique — sinon les recherches de l'utilisateur fausseraient ses propres alertes.
C'est la raison, et elle doit rester documentée dans le code.

## Format de sortie

```
Dakar → Bangkok (BKK)

  via Paris        (300)+ 540 =  840 €   départ 2026-09-05   [aller estimé]
  via Istanbul      525 + 410 =  935 €   départ 2026-09-12   [segments API]
  direct                        1120 €   départ 2026-10-03   [API]
  via Casablanca    468 + 690 = 1158 €   départ 2026-09-28   [segments API]

  Meilleure option : via Paris, 840 €
  Attention : son prix d'aller est estimé, pas mesuré — le total réel peut être plus élevé.
  https://www.aviasales.com/search/CDG0509BKK1

  Historique : route inconnue de ta base.
  → python recherche.py --surveiller BKK  (+9 appels par relevé)
```

Les parenthèses signalent un prix estimé issu de `RABATTEMENT`, jamais mélangé silencieusement avec
un prix réel. Quand la route est connue de la base, la dernière section devient :

```
  Historique : 46 relevés, meilleur prix vu 810 € le 2026-08-10.
  Prix actuel : +23 % au-dessus de ce minimum.
```

## Mise sous surveillance

`--surveiller BKK` écrit dans `destinations_perso.json`, de même forme que `DESTINATIONS` :

```json
{ "BKK": "Bangkok", "YMQ": "Montreal" }
```

Le nom est repris de `DESTINATIONS` s'il y figure, sinon le code sert de nom.

**Greffe sur `hub_deals_db.py`** — c'est la seule modification du collecteur :

- une fonction `charger_destinations_perso()` qui lit le fichier s'il existe, renvoie `{}` sinon, et
  ne plante jamais sur un fichier absent ou illisible (un JSON corrompu est signalé dans le log et
  ignoré : un relevé ne doit pas échouer à cause d'un fichier de confort) ;
- la boucle principale itère sur `{**DESTINATIONS, **charger_destinations_perso()}` au lieu de
  `DESTINATIONS` ;
- le log de démarrage indique le nombre de destinations personnelles actives.

Les destinations personnelles bénéficient alors de **tout le mécanisme existant** sans code
supplémentaire : lignes en base pour les 5 villes, détection d'anomalie, notification Telegram.

**Garde-fou de coût** : chaque destination surveillée ajoute 9 appels par relevé (un par hub).
`MAX_DESTINATIONS_PERSO = 15` (soit +135 appels, ~+54 s). Au-delà, `--surveiller` refuse et invite à
en retirer une. `--liste` affiche en permanence le coût courant en appels.

`destinations_perso.json` est ajouté au `.gitignore` : c'est une préférence locale, pas du code.

## Tests

`tests/test_recherche.py`, en `unittest` comme le reste de la suite, **sans aucun appel réseau** —
`get_prix_route` est remplacée par une fonction de test, sur le modèle du remplacement de
`envoyer_telegram` déjà pratiqué dans `test_hub_deals_db.py`.

Comportements couverts :

- les options sont triées par total croissant, vol direct compris ;
- un segment aller sans prix API retombe sur `RABATTEMENT` et **est marqué comme estimé** ;
- une option dont le segment principal `H → D` n'a pas de prix est **écartée**, pas valorisée à 0 ;
- à total égal, l'option entièrement mesurée est classée **avant** l'option comportant un estimé ;
- un hub égal à la destination est sauté (pas de doublon avec le vol direct) ;
- une destination égale à la ville de départ est refusée ;
- une ville absente de `RABATTEMENT` est refusée avec la liste des villes valides ;
- résolution d'un nom connu vers son code IATA, insensible à la casse ; refus d'un nom inconnu ;
- une erreur réseau sur un segment n'interrompt pas la recherche ;
- lecture/écriture de `destinations_perso.json` dans un dossier temporaire, y compris fichier
  absent et JSON corrompu ;
- `MAX_DESTINATIONS_PERSO` refuse la 16ᵉ destination ;
- la fusion produit bien l'union des destinations d'origine et personnelles, sans écraser les
  premières.

## Vérification en conditions réelles

Après implémentation, conformément à l'usage du projet :

1. `python recherche.py Dakar BZV` — vérifier que le vol direct à ~932 € apparaît et bat le
   « 1001 € via Paris » du relevé, et que les liens Aviasales s'ouvrent sur la bonne route.
2. `python recherche.py Dakar XXX` sur une destination sans prix — vérifier le message honnête.
3. `--surveiller` une destination, lancer un **vrai relevé**, puis vérifier en base que les lignes
   apparaissent pour les 5 villes et que le total d'appels a augmenté du montant attendu.
4. `--oublier` la même destination et confirmer le retour à l'état antérieur.
5. Confirmer qu'aucune recherche n'a écrit en base (`COUNT(*)` avant/après).

## Problème connexe, hors scope

**La table `RABATTEMENT` sous-estime les coûts de 17 à 31 %** sur les segments mesurés (Dakar). Cela
affecte le relevé automatique et donc les alertes Telegram, indépendamment de cette fonctionnalité.
Trois pistes envisageables plus tard : rafraîchir périodiquement la table via l'API, remplacer la
valeur figée par une moyenne glissante mesurée, ou afficher un intervalle. **À traiter dans une
itération dédiée** — le mélanger ici brouillerait deux sujets distincts.

## Hors scope (YAGNI)

- Choix de dates ou de période : `v1/prices/cheap` renvoie l'option la moins chère qu'il connaît,
  toutes dates confondues.
- Aller-retours et multi-destinations.
- Origines hors des 5 villes de `RABATTEMENT`.
- Cache des segments `ville → hub` entre deux recherches (une recherche est ponctuelle ; la
  complexité ne se justifie pas tant que l'usage ne le montre pas).
- Interface Telegram interactive.
- Correction de `RABATTEMENT` (voir « Problème connexe »).
- Historisation des recherches effectuées.

## Fichiers impactés

| Fichier | Nature |
|---|---|
| `recherche.py` | nouveau — CLI et logique de recherche |
| `tests/test_recherche.py` | nouveau — suite unittest sans réseau |
| `hub_deals_db.py` | modifié — `charger_destinations_perso()` + fusion dans la boucle + log |
| `.gitignore` | modifié — `destinations_perso.json` |
| `README.md` | modifié — section usage de la recherche |
| `CHANGELOG.md` | modifié — entrée datée |
