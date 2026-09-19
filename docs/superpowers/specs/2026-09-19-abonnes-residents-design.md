# Abonnés résidents d'un hub — design

## Contexte

Le modèle de `hub_deals` suppose depuis l'origine que l'abonné est **loin** des hubs :
il part d'une ville africaine, rejoint un hub de correspondance à un coût forfaitaire
(`RABATTEMENT`), puis prend son vol long-courrier. D'où le principe :

```
total_estime = prix_vol_depuis_le_hub + cout_rabattement_ville_hub
```

Deux angles morts en découlent, et ce chantier les traite ensemble parce que c'est le
même trou :

1. **Une ville qui est aussi un hub ne voit jamais ses propres vols directs.** Abidjan est
   à la fois ville de départ et hub `ABJ` ; la base contient **0 ligne**
   `ville_depart=Abidjan, hub_origine=Abidjan`. Le commentaire de `RABATTEMENT` l'assume
   (« pas d'entrée ABJ->ABJ, Abidjan étant déjà un hub ») — mais la conséquence est qu'un
   abonné d'Abidjan ne peut pas être alerté sur un Abidjan → Dubaï direct.
2. **Aucune des 5 villes actuelles ne voit ses vols directs**, pour une raison différente :
   Dakar, Kinshasa, Brazzaville et Lomé ne sont pas des hubs, donc leurs prix au départ
   ne sont jamais interrogés. Le seul abonné inscrit à ce jour est à Dakar.

S'y ajoute la piste ouverte le 2026-09-16 : servir des abonnés **déjà installés dans un
hub** (« abonnés depuis Paris »), pour qui le rabattement est nul.

### Ce que les mesures ont établi (2026-09-19)

Toutes les simulations ci-dessous rejouent le détecteur réel (`anomaly_detection.py`,
seuils du 2026-09-12) sur une copie de la base, chaque relevé n'étant jugé **qu'avec son
passé** (`DELETE FROM offres WHERE date_collecte > d` dans une transaction annulée
ensuite). Les 5 villes réelles servent de **cas témoin** : elles retrouvent bien leurs
chiffres connus, ce qui prouve que la sonde mesure le vrai détecteur et non une
ré-implémentation approximative.

**a. L'intuition « Paris » est fondée, et pour la bonne raison.** Paris est le 3ᵉ hub le
moins cher sur le tronçon hub → destination (358 € de moyenne, derrière Istanbul 327 € et
Le Caire 333 €) et sort dans 50 des 200 meilleurs prix du dernier relevé. Ce qui l'exclut
aujourd'hui, c'est uniquement le coût pour l'atteindre : 486 € depuis Abidjan, 496 €
depuis Dakar, 708 € depuis Kinshasa, 1 306 € depuis Brazzaville. Pour un résident, ce
coût est nul.

**b. Paris n'a produit aucune alerte depuis le recalibrage du 2026-09-12**, alors qu'il en
a produit 289 auparavant. Les deux faits sont cohérents : les 289 datent d'avant la
correction de `RABATTEMENT` du 2026-08-16, qui sous-estimait le trajet vers Paris de +65 %
à +209 % et biaisait structurellement le classement en sa faveur. Depuis que la table est
juste, Paris ne sort plus. Ce n'est pas une régression.

**c. Le plancher de 80 € éteint les résidents.** Sur 17 relevés (12 → 19/09), en donnant à
chaque hub un jumeau « résident » à rabattement nul :

| Résident | Billet médian | Candidats (% ou z) | Retenus à 80 € |
|---|---|---|---|
| Lagos | 672 € | 7 | 7 |
| Le Caire | 317 € | 20 | 2 |
| Paris | 397 € | 19 | **0** |
| Istanbul | 269 € | 39 | **0** |
| Casablanca | 363 € | 7 | **0** |
| *villes réelles* | *1 127 €* | *132* | *59* |

`ECONOMIE_MINIMALE` est calibré pour des itinéraires à 1 127 € de médiane, où 80 €
représentent 7 % du billet. Chez un résident dont le billet médian vaut 270 à 400 €, le
même seuil exige 20 à 30 % de baisse. Les rejets ne sont pas absurdes pour autant : le
meilleur raté d'Istanbul est −42 % sur Barcelone, soit 58 € sur un billet à 80 €.

**d. La densité de trafic est un mauvais critère de choix de marché.** Sur 52 relevés
(20/08 → 19/09, soit depuis la correction du rabattement), avec un plancher relatif :

| Résident | Affaires | Par relevé | Économie médiane | Max |
|---|---|---|---|---|
| Abidjan | 26 | 0,5 | **188 €** | 570 € |
| Lagos | 28 | 0,5 | 152 € | 509 € |
| Nairobi | 21 | 0,4 | 117 € | 381 € |
| Addis-Abeba | 29 | 0,6 | 112 € | 416 € |
| Johannesburg | 11 | 0,2 | 94 € | 151 € |
| Le Caire | 48 | 0,9 | 78 € | 569 € |
| Casablanca | 48 | 0,9 | 64 € | 282 € |
| Istanbul | 65 | **1,2** | 42 € | 93 € |
| Paris | 42 | 0,8 | **22 €** | 137 € |

*(témoin : les 5 villes actuelles, règle inchangée à 80 €, donnent 686 affaires,
13,2/relevé, médiane 143 €.)*

Paris est **bon dernier en montant**, et c'est une conséquence de sa densité : un marché
concurrentiel est bon marché et stable, donc pauvre en anomalies qui valent de l'argent.
La densité est un bon critère pour choisir un **hub de correspondance** ; elle ne dit rien
de la qualité d'un **marché de départ**.

**e. Les correspondances sont à écarter pour un résident.** Simulé pour Paris via les 8
autres hubs — gratuit, puisque les 9 hubs sont aussi des destinations : le prix
Paris → hub est déjà relevé chaque jour, donc mesuré et jamais vieilli. Le résultat est
géographiquement absurde :

```
via Abidjan  -> Sao Paulo   1814 EUR  -23.9%  eco 571
via Abidjan  -> Rome        1048 EUR  -30.9%  eco 469
via Lagos    -> Bruxelles   1110 EUR  -28.4%  eco 440
```

Paris → Abidjan → Rome à 1 048 € quand notre propre base contient le Paris → Rome direct à
88 €. Le modèle hub tient quand le voyageur est loin des hubs — le hub est alors vraiment
sur le chemin. La détection compare chaque route à sa seule histoire et ne sait pas qu'un
vol direct existe.

## Objectif

Permettre à un abonné **résidant dans une ville que nous relevons** d'être alerté sur les
vols directs au départ de chez lui, avec un critère de déclenchement adapté au niveau de
prix de son marché.

## Décisions de cadrage (brainstorming du 2026-09-19)

| Question | Décision | Motif |
|---|---|---|
| Public visé | Résidents des hubs déjà suivis | Coût API nul, 111 relevés d'historique déjà en base |
| Critère d'alerte | Plancher relatif **réservé aux résidents** | Le % n'est déformé que par la correction de rabattement, inexistante à rabattement nul |
| Périmètre | Vols directs uniquement | Les correspondances produisent des itinéraires absurdes (e) |
| Villes ouvertes | Les 9 hubs | Coût marginal quasi nul ; le seuil trie de lui-même |
| Angle mort des 5 villes | Traité dans le même chantier | Même mécanique, et c'est ce qui sert l'abonné existant |

## Modèle

**Un résident n'est pas un nouveau concept : c'est une ville dont le rabattement vers son
propre hub vaut 0.** Le principe du projet reste littéralement vrai, avec 0 comme valeur
sincère et non comme code d'exception. Tout l'aval — insertion, regroupement des affaires
par (hub, destination), filtrage par ville, liens Aviasales, découpage des messages —
fonctionne sans modification.

Deux représentations ont été écartées :

- **un drapeau `resident: True` sur la ville** : ajoute un chemin de code parallèle pour
  exprimer ce que 0 dit déjà ;
- **une table `VILLES_RESIDENTES` séparée** : duplique la liste des villes, donc invite à
  la désynchronisation que les tests structurels existants cherchent justement à éviter.

## Changements

### 1. `HUBS` — quatre entrées nouvelles

`DKR` (Dakar), `FIH` (Kinshasa), `BZV` (Brazzaville), `LFW` (Lomé).

Coût : 125 appels API de plus par relevé (4 hubs x 32 destinations, moins `DKR`, `BZV` et
`FIH` qui sont aussi des destinations), soit 279 → 404 — ~2,7 min au lieu de ~1,9 min à
`PAUSE_ENTRE_APPELS = 0.4`.

Ces hubs ne servent que leur propre ville. Aucune autre ville n'ayant d'entrée de
rabattement vers eux, la boucle d'insertion les saute déjà d'elle-même
(`if rabattement is None: continue`) — aucun garde-fou supplémentaire n'est nécessaire.

### 2. `RABATTEMENT` — huit villes nouvelles, cinq entrées ajoutées

Villes nouvelles, chacune réduite à une seule entrée (son hub, à 0) : Paris (`CDG`),
Istanbul (`IST`), Casablanca (`CMN`), Le Caire (`CAI`), Lagos (`LOS`), Nairobi (`NBO`),
Addis-Abeba (`ADD`), Johannesburg (`JNB`).

Villes existantes, une entrée à 0 ajoutée vers leur propre hub : Dakar (`DKR`), Abidjan
(`ABJ`), Kinshasa (`FIH`), Brazzaville (`BZV`), Lomé (`LFW`).

Total : 13 villes de départ.

**Aucune valeur existante n'est modifiée.** Le piège documenté du projet — « ne jamais
changer `RABATTEMENT` sans recalculer `total_estime` », qui avait produit 7 semaines de
détection muette sur 43 % des lignes — ne se déclenche donc pas : on ajoute des clés, on
n'en corrige aucune.

`VILLE_IATA` et `NOMS_AFFICHES` reçoivent les 8 villes nouvelles ; les tests structurels
existants vérifient déjà que ces ensembles de clés coïncident.

### 3. Seuil — une fonction plutôt qu'une constante

```
plancher_economie(rabattement, moyenne_historique):
    si rabattement == 0 : max(25 EUR, 12 % de la moyenne)
    sinon               : ECONOMIE_MINIMALE (80 EUR, inchangé)
```

Le 0 est un discriminant sûr : aucun rabattement réel ne vaut 0, et un test structurel le
garantit. Il est malgré tout isolé dans une fonction nommée et testée plutôt que laissé en
`if rabattement == 0` au milieu du détecteur.

Le double plancher est nécessaire dans les deux sens : 12 % seuls laisseraient passer des
alertes à 10 € sur les billets intra-européens à 80 € d'Istanbul ; 25 € seuls
reproduiraient le biais du seuil absolu à plus petite échelle. Sur les 52 relevés mesurés,
`max(25 €, 12 %)` retient 288 affaires réparties sur 9 villes, soit 5,5 par relevé au
total — mais chaque abonné ne voit que la sienne, de 0,2/relevé (Johannesburg) à
1,2/relevé (Istanbul).

Les 5 villes actuelles gardent exactement leur comportement **sur leurs routes
existantes** : aucune d'elles n'a de rabattement nul. Seule leur route directe nouvelle,
celle vers leur propre hub, relève du plancher relatif.

### 4. Mesure du rabattement — un court-circuit

`mesurer_rabattements` doit ignorer les couples à rabattement nul : la mesure
interrogerait l'API sur une ville vers elle-même, ce qui renvoie une erreur 400
(le cas est déjà documenté dans `EQUIVALENCES`). Aucun appel ne doit partir, et le
rabattement reste 0.

### 5. Message — une ligne conditionnelle

`_bloc_abonne` affiche aujourd'hui `<destination> via <hub>`, ce qui donnerait
« Paris via Paris » pour un résident. À rabattement nul, il affiche **« vol direct »**.
Le reste du bloc (prix, baisse, économie, lien) est inchangé.

### 6. Reprise d'historique — migration ponctuelle et idempotente

Pour les 9 villes adossées à un hub déjà suivi, l'historique existe déjà sous une autre
clé : `total_estime = prix_vol_hub + 0`, et `prix_vol_hub` est stocké depuis le
2026-07-21. Le report est **arithmétiquement exact, et non une estimation**.

Un script de migration insère ces lignes — **16 156 mesurées** sur une base de 68 561,
soit +24 % — en excluant
pour chaque ville sa propre destination (`EQUIVALENCES` comprises : Paris exclut `PAR`
*et* `CDG`). Sans lui, ces villes resteraient muettes 3 relevés
(`MIN_RELEVES_HISTORIQUE = 2` relevés antérieurs) ; avec lui, elles sont vivantes dès le
premier.

Les 4 villes promues en hubs (Dakar, Kinshasa, Brazzaville, Lomé) n'ont, elles, aucun
historique : leurs 3 premiers relevés seront muets. C'est incompressible.

## Ce qui reste incertain

**Le rendement des vols directs depuis Dakar, Kinshasa, Brazzaville et Lomé est
inconnu.** Ces prix n'ont jamais été relevés ; aucune extrapolation n'est possible depuis
les données existantes. C'est le pari de ce chantier, et c'est aussi la partie qui sert
l'unique abonné actuel. Les chiffres du tableau (d) ne couvrent que les 9 hubs déjà
suivis.

Les mesures portent sur 52 relevés d'un seul mois, sur un marché saisonnier. Elles
donnent un ordre de grandeur, pas une prévision.

## Ce qui est hors scope

- **Les correspondances pour résidents** — écartées sur preuve (e).
- **Le garde-fou inter-routes** (« n'alerter sur un trajet avec correspondance que s'il
  bat le meilleur vol direct connu pour la même destination »). Il corrigerait une
  faiblesse réelle du produit entier, pas seulement du mode résident, et demande au
  détecteur une comparaison entre routes qu'il ne sait pas faire. Chantier distinct.
- **Les villes qui ne sont pas des hubs** (Bruxelles, Londres, Montréal, New York), là où
  vit la diaspora. À reprendre quand ce modèle aura fait ses preuves.
- **Le rafraîchissement de la table `RABATTEMENT`** pour les villes existantes.

## Tests

**Structurels** (le projet en a déjà de cette famille, à étendre)

- chaque ville de `RABATTEMENT` a un code dans `VILLE_IATA` et un nom dans `NOMS_AFFICHES`
- chaque hub cité dans `RABATTEMENT` existe dans `HUBS`
- pour chaque ville résidente, l'entrée à 0 pointe bien vers le hub de sa propre ville
- aucune entrée de rabattement autre que celle d'une ville vers son propre hub ne vaut 0
  (c'est ce qui rend le discriminant du seuil fiable)

**Seuil**

- `plancher_economie` aux bornes exactes : 24,99 € rejeté / 25 € retenu à rabattement nul ;
  11,9 % rejeté / 12 % retenu quand 12 % dépasse 25 €
- une route à rabattement non nul garde le plancher de 80 €, inchangé
- une économie de 58 € sur un billet à 138 € de moyenne déclenche pour un résident et ne
  déclenche pas pour une ville classique

**Collecte**

- une ville résidente ne reçoit de lignes que pour son propre hub
- un résident de Paris ne reçoit jamais `PAR` ni `CDG` en destination
- une ville existante conserve exactement ses routes d'avant, plus celle de son hub

**Rabattement mesuré**

- `mesurer_rabattements` ne lance aucun appel API pour un couple à rabattement nul
- le rabattement rendu vaut 0 et est marqué comme non mesuré

**Message**

- un bloc à rabattement nul affiche « vol direct », jamais « via <ville> »
- un bloc à rabattement non nul est inchangé (non-régression)

**Migration**

- rejouée deux fois, elle n'ajoute aucune ligne la seconde fois
- chaque ligne reportée vérifie `total_estime == prix_vol_hub` et `rabattement == 0`
- elle n'exclut ni n'altère aucune ligne existante
