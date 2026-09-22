# Page publique quotidienne — plan d'implémentation

> **Pour un agent :** SOUS-COMPÉTENCE REQUISE — utiliser
> `superpowers:subagent-driven-development` (recommandé) ou
> `superpowers:executing-plans` pour dérouler ce plan tâche par tâche. Les
> étapes utilisent des cases à cocher (`- [ ]`).

**But :** publier chaque jour, à une adresse unique et gratuite, les bonnes
affaires du relevé — sans exiger d'application — et offrir une liste d'attente
aux villes que le bot ne peut pas servir.

**Architecture :** un module `page.py` **pur** (des affaires en entrée, du HTML
en sortie, aucun réseau, aucun git, aucune horloge implicite), publié par une
fonction `publier_page()` qui reprend le patron worktree déjà durci de
`sauvegarde.sauvegarder_distant()`, vers la branche `gh-pages` servie par GitHub
Pages.

**Outils :** Python 3.14, `unittest` + `pytest`, `subprocess` pour git, aucune
dépendance nouvelle.

**Spec :** `docs/superpowers/specs/2026-09-22-page-publique-design.md`

## Contraintes globales

- **Aucune dépendance nouvelle.** HTML et CSS écrits à la main, CSS en ligne,
  aucune police distante, aucun fichier annexe.
- **Mobile d'abord.** Le public arrive depuis WhatsApp, sur téléphone.
- **Zéro donnée personnelle dans la page.** Jamais de `chat_id`, jamais de
  prénom. La branche `gh-pages` est publique.
- **Échapper tout texte venant de l'API.** `destination_nom` vient de
  Travelpayouts : il passe par `html.escape()` avant d'entrer dans la page.
- **Étiquettes via `hub_deals_db.etiquette_ville()` uniquement.** Jamais de
  table recopiée : c'est le défaut payé le 2026-09-16, où deux endroits
  fabriquaient l'étiquette chacun de leur côté et où les abonnés ont reçu des
  liens non comptés pendant des jours.
- **Le payload `start` de Telegram n'accepte que `A-Za-z0-9_-`.**
  `attente_Le Caire` serait rejeté ; l'étiquette est `attente_le_caire`.
- **Rien ne doit casser le relevé.** Toute la chaîne de publication attrape ses
  exceptions ; le relevé est déjà enregistré à ce stade.
- **On n'alerte que les échecs.** Jamais de « publication OK » quotidien : un
  succès répété devient un bruit qu'on cesse de lire, et son absence passerait
  inaperçue.
- **Mention de prix obligatoire** (`abonnes.MENTION_PRIX`) : les règles
  Travelpayouts interdisent de présenter une remise comme garantie.

## Amendement à la spec (découvert en écrivant ce plan)

La spec demandait un avertissement de fraîcheur **et** « aucun script ». C'est
contradictoire : une page statique n'est pas régénérée quand le relevé ne tourne
pas, donc elle ne peut pas ajouter un avertissement après coup.

**Décision retenue :** la date du relevé est **toujours écrite en clair** dans la
page (visible sans JavaScript), et un script en ligne de quelques lignes ajoute
un bandeau d'avertissement quand l'âge dépasse 24 h. Sans JavaScript, on voit la
date sans le bandeau — jamais un prix présenté à tort comme frais.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `page.py` *(créé)* | Rendu HTML pur. Ne connaît ni git, ni réseau, ni horloge. |
| `souhaits.py` *(créé)* | Lecture/écriture de la table `villes_souhaitees`. Aucun réseau. |
| `hub_deals_db.py` *(modifié)* | `publier_page()` (worktree + alerte) et l'appel en fin de relevé. `preparer_liens_courts()` crée aussi les étiquettes de la page. |
| `bot_ecoute.py` *(modifié)* | Reconnaît le payload `attente_<etiquette>`. |
| `magasin.py` *(modifié)* | DDL de `villes_souhaitees`. |
| `sauvegarde.py` *(modifié)* | `villes_souhaitees` entre dans `TABLES_PRIVEES`. |
| `abonnes.py` *(modifié)* | L'instantané local couvre aussi `villes_souhaitees`. |

---

### Tâche 1 : `page.py` — rendre les affaires d'une ville

**Fichiers :**
- Créer : `page.py`
- Test : `tests/test_page.py`

**Interfaces :**
- Consomme : `abonnes.NOMS_AFFICHES`, `abonnes.filtrer_groupes`,
  `hub_deals_db.etiquette_ville`, `hub_deals_db.url_aviasales`
- Produit : `page.ORDRE_VILLES: tuple[str, ...]`,
  `page.etiquette_page(ville: str) -> str`,
  `page.bloc_ville(ville: str, groupes: list) -> str`

- [ ] **Étape 1 : écrire le test qui échoue**

```python
"""Rendu de la page publique (spec du 2026-09-22)."""
import html
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import hub_deals_db
import page


def _affaire(ville="Dakar", destination="Milan", hub="CMN", prix=118.0,
             baisse=19.0, economie=28.0, lien="/vol/CMNMIL", rabattement=468):
    return {"ville_depart": ville, "destination": destination, "hub": hub,
            "prix_actuel": prix, "baisse_pct": baisse, "economie": economie,
            "lien": lien, "rabattement": rabattement}


class TestOrdreDesVilles(unittest.TestCase):
    def test_couvre_exactement_les_villes_connues(self):
        """Une ville absente de l'ordre ne serait jamais affichee ; une
        ville en trop planterait sur NOMS_AFFICHES."""
        self.assertEqual(set(page.ORDRE_VILLES), set(abonnes.NOMS_AFFICHES))
        self.assertEqual(len(page.ORDRE_VILLES), len(abonnes.NOMS_AFFICHES))


class TestEtiquette(unittest.TestCase):
    def test_prefixee_et_normalisee(self):
        """Distinguer le trafic de la page de celui du bot, et survivre
        aux noms composes."""
        self.assertEqual(page.etiquette_page("Dakar"), "page_dakar")
        self.assertEqual(page.etiquette_page("Le Caire"), "page_le_caire")

    def test_passe_par_etiquette_ville(self):
        for ville in abonnes.NOMS_AFFICHES:
            self.assertEqual(page.etiquette_page(ville),
                             "page_" + hub_deals_db.etiquette_ville(ville))


class TestBlocVille(unittest.TestCase):
    def test_affiche_prix_baisse_economie_et_destination(self):
        bloc = page.bloc_ville("Dakar", [[_affaire()]])

        self.assertIn("Milan", bloc)
        self.assertIn("118", bloc)
        self.assertIn("19", bloc)
        self.assertIn("28", bloc)

    def test_un_rabattement_nul_est_un_vol_direct(self):
        bloc = page.bloc_ville("Dakar", [[_affaire(rabattement=0)]])

        self.assertIn("direct", bloc.lower())
        self.assertNotIn("via CMN", bloc)

    def test_un_rabattement_non_nul_nomme_le_hub(self):
        bloc = page.bloc_ville("Dakar", [[_affaire(hub="CMN", rabattement=468)]])

        self.assertIn("CMN", bloc)

    def test_le_nom_de_destination_est_echappe(self):
        """destination_nom vient de l'API Travelpayouts : c'est du texte
        etranger, il n'entre jamais brut dans la page."""
        bloc = page.bloc_ville("Dakar", [[_affaire(destination='<script>x</script>')]])

        self.assertNotIn("<script>", bloc)
        self.assertIn(html.escape("<script>x</script>"), bloc)
```

- [ ] **Étape 2 : vérifier l'échec**

Lancer : `python -m pytest tests/test_page.py -q`
Attendu : ÉCHEC, `ModuleNotFoundError: No module named 'page'`

- [ ] **Étape 3 : implémentation minimale**

```python
"""
Rendu de la page publique quotidienne.

PUR : des affaires en entree, du HTML en sortie. Aucun reseau, aucun git,
aucune horloge implicite -- la date et l'age sont donnes par l'appelant,
pour que le rendu soit testable et reproductible.

Spec : docs/superpowers/specs/2026-09-22-page-publique-design.md
"""

import html

import abonnes
import hub_deals_db

# Ordre d'affichage : par qualite mesuree le 2026-09-22 (114 releves
# rejoues). Les villes rabattues vers plusieurs hubs d'abord -- ce sont
# celles ou le programme trouve vraiment quelque chose --, les villes
# residentes ensuite. Un test verifie que cet ordre couvre exactement
# NOMS_AFFICHES : une ville oubliee ici ne serait jamais affichee.
ORDRE_VILLES = (
    "Kinshasa", "Lome", "Dakar", "Abidjan", "Brazzaville",
    "Casablanca", "Le Caire", "Istanbul", "Lagos",
    "Addis-Abeba", "Paris", "Nairobi", "Johannesburg",
)


def etiquette_page(ville: str) -> str:
    """Etiquette Sous-ID de la page, distincte de celle du bot.

    Le bot utilise « dakar », la page « page_dakar » : c'est ce qui
    rendra les deux canaux distinguables dans Travelpayouts. Passe par
    etiquette_ville() et jamais par une table recopiee -- defaut paye le
    2026-09-16 sur « Le Caire » et « Addis-Abeba ».
    """
    return "page_" + hub_deals_db.etiquette_ville(ville)


def bloc_ville(ville: str, groupes: list) -> str:
    """Les affaires d'une ville. `groupes` sort de filtrer_groupes()."""
    lignes = []
    for groupe in groupes:
        a = groupe[0]  # une ville n'a qu'une ligne par groupe
        trajet = "vol direct" if a.get("rabattement") == 0 else f"via {html.escape(a['hub'])}"
        lien = hub_deals_db.url_aviasales(a["lien"], etiquette_page(ville))
        lignes.append(
            f'<li class="affaire">'
            f'<a href="{html.escape(lien)}" rel="nofollow sponsored">'
            f'<span class="dest">{html.escape(str(a["destination"]))}</span>'
            f'<span class="trajet">{trajet}</span>'
            f'<span class="prix">{a["prix_actuel"]:.0f}€</span>'
            f'<span class="gain">−{a["baisse_pct"]:.0f} % '
            f'· {a["economie"]:.0f}€ de moins</span>'
            f'</a></li>')
    return "<ul class=\"affaires\">" + "".join(lignes) + "</ul>"
```

- [ ] **Étape 4 : vérifier que les tests passent**

Lancer : `python -m pytest tests/test_page.py -q`
Attendu : 6 tests passés

- [ ] **Étape 5 : committer**

```bash
git add page.py tests/test_page.py
git commit -m "Page : rendre les affaires d'une ville"
```

---

### Tâche 2 : `page.py` — villes vides, mention de prix, fraîcheur

**Fichiers :**
- Modifier : `page.py`
- Test : `tests/test_page.py`

**Interfaces :**
- Produit : `page.rendre(groupes: list, quand: str, bot: str | None = None, code: str | None = None) -> str`

- [ ] **Étape 1 : écrire le test qui échoue**

```python
class TestRendre(unittest.TestCase):
    QUAND = "2026-09-22T09:18:00+00:00"

    def test_les_treize_villes_sont_presentes(self):
        """Masquer une ville vide laisserait croire qu'elle n'est pas
        couverte du tout."""
        html_page = page.rendre([], self.QUAND)

        for ville in abonnes.NOMS_AFFICHES.values():
            self.assertIn(ville, html_page)

    def test_une_ville_sans_affaire_le_dit(self):
        html_page = page.rendre([], self.QUAND)

        self.assertIn("Rien aujourd'hui", html_page)

    def test_la_mention_de_prix_est_presente(self):
        """Regle Travelpayouts : ne jamais presenter une remise comme
        garantie. Les prix viennent d'un cache pouvant aller a 7 jours."""
        texte = page.rendre([], self.QUAND)

        self.assertIn("vérifie", texte.lower())

    def test_la_date_du_releve_est_ecrite_en_clair(self):
        """Elle doit se lire SANS JavaScript : c'est le seul garde-fou
        contre un prix de trois jours pris pour celui du jour."""
        texte = page.rendre([], self.QUAND)

        self.assertIn("2026-09-22", texte)

    def test_la_page_ne_contient_aucune_donnee_personnelle(self):
        groupes = [[_affaire()]]

        texte = page.rendre(groupes, self.QUAND)

        for interdit in ("chat_id", "8296006641", "Mariama", "abonnes"):
            self.assertNotIn(interdit, texte)

    def test_une_affaire_apparait_sous_sa_ville(self):
        texte = page.rendre([[_affaire(ville="Dakar", destination="Milan")]],
                            self.QUAND)

        avant = texte.index(abonnes.NOMS_AFFICHES["Dakar"])
        self.assertIn("Milan", texte[avant:])
```

- [ ] **Étape 2 : vérifier l'échec**

Lancer : `python -m pytest tests/test_page.py::TestRendre -q`
Attendu : ÉCHEC, `AttributeError: module 'page' has no attribute 'rendre'`

- [ ] **Étape 3 : implémentation minimale**

Ajouter à `page.py` :

```python
# Au-dela, la page affiche un bandeau : les prix ne sont plus ceux du jour.
AGE_SUSPECT_HEURES = 24

_CSS = """
:root{--fond:#fbfaf8;--encre:#1c1b19;--doux:#6b6862;--trait:#e3e0d9;
--accent:#1f6f5c;--alerte:#8a5a00;--alerte-fond:#fdf4e3}
@media(prefers-color-scheme:dark){:root{--fond:#17181a;--encre:#ececea;
--doux:#9a9792;--trait:#2e3033;--accent:#6fc3a8;--alerte:#f0c675;
--alerte-fond:#2b2317}}
*{box-sizing:border-box}
body{margin:0;padding:1.5rem 1rem 4rem;background:var(--fond);color:var(--encre);
font:17px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:38rem;margin:0 auto}
h1{font-size:1.4rem;letter-spacing:-.015em;margin:0 0 .25rem}
.date{color:var(--doux);font-size:.9rem;margin:0 0 2rem}
h2{font-size:1rem;letter-spacing:.02em;margin:2.25rem 0 .6rem;
padding-bottom:.35rem;border-bottom:1px solid var(--trait)}
.affaires{list-style:none;margin:0;padding:0}
.affaire a{display:grid;grid-template-columns:1fr auto;gap:.1rem .75rem;
padding:.7rem 0;text-decoration:none;color:inherit;
border-bottom:1px solid var(--trait)}
.dest{font-weight:600}
.prix{font-weight:600;text-align:right;color:var(--accent)}
.trajet,.gain{font-size:.85rem;color:var(--doux)}
.gain{text-align:right}
.rien{color:var(--doux);font-size:.9rem;margin:.3rem 0 0}
.action{display:inline-block;margin:.7rem 0 0;padding:.5rem .9rem;
border:1px solid var(--accent);border-radius:999px;color:var(--accent);
text-decoration:none;font-size:.85rem}
.vieux{display:none;background:var(--alerte-fond);color:var(--alerte);
padding:.8rem 1rem;border-radius:.5rem;margin:0 0 1.5rem;font-size:.9rem}
.pied{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--trait);
color:var(--doux);font-size:.85rem}
"""

# La page est statique : quand le releve ne tourne pas, elle n'est pas
# regeneree et ne peut donc pas s'avertir elle-meme. Ces quelques lignes
# le font cote visiteur. Sans JavaScript, la date reste lisible en clair
# juste au-dessus -- on ne fait jamais passer un vieux prix pour frais.
_SCRIPT = """
(function(){var b=document.getElementById('vieux');
var t=Date.parse(document.getElementById('releve').dateTime);
var h=(Date.now()-t)/3600000;
if(h>%d){b.textContent='Ces prix datent de plus de '+Math.floor(h/24)+
' jour(s). Ils ont tres probablement change.';b.style.display='block';}})();
""" % AGE_SUSPECT_HEURES


def rendre(groupes: list, quand: str, bot: str = None, code: str = None) -> str:
    """La page complete. `groupes` sort de grouper_anomalies()."""
    total = 0
    sections = []
    for ville in ORDRE_VILLES:
        propres = abonnes.filtrer_groupes(groupes, ville)
        total += len(propres)
        nom = html.escape(abonnes.NOMS_AFFICHES[ville])
        corps = (bloc_ville(ville, propres) if propres
                 else "<p class=\"rien\">Rien aujourd'hui au départ de "
                      f"{nom}.</p>")
        action = lien_action(ville, bot, code)
        sections.append(f"<section><h2>{nom}</h2>{corps}{action}</section>")

    titre = ("Aucune affaire aujourd'hui" if total == 0
             else f"{total} bonne{'s' if total > 1 else ''} affaire"
                  f"{'s' if total > 1 else ''} aujourd'hui")
    return (
        "<!DOCTYPE html>\n<html lang=\"fr\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
        "<title>Bonnes affaires vol</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n<main>\n"
        f"<h1>{titre}</h1>\n"
        f"<p class=\"date\">Relevé du <time id=\"releve\" datetime=\"{html.escape(quand)}\">"
        f"{html.escape(quand[:10])}</time></p>\n"
        "<p class=\"vieux\" id=\"vieux\"></p>\n"
        + "\n".join(sections) +
        f"\n<p class=\"pied\">{html.escape(_sans_balises(abonnes.MENTION_PRIX))}</p>\n"
        f"</main>\n<script>{_SCRIPT}</script>\n</body>\n</html>\n")


def _sans_balises(texte: str) -> str:
    """MENTION_PRIX porte des balises Telegram (<i>), pas du HTML de page."""
    return texte.replace("<i>", "").replace("</i>", "")


def lien_action(ville: str, bot: str, code: str) -> str:
    """Provisoire : rempli par la tache 3."""
    return ""
```

- [ ] **Étape 4 : vérifier que les tests passent**

Lancer : `python -m pytest tests/test_page.py -q`
Attendu : 12 tests passés

- [ ] **Étape 5 : committer**

```bash
git add page.py tests/test_page.py
git commit -m "Page : les 13 villes, la mention de prix et la date du releve"
```

---

### Tâche 3 : `page.py` — les appels à l'action

**Fichiers :**
- Modifier : `page.py`
- Test : `tests/test_page.py`

**Interfaces :**
- Consomme : `abonnes.VILLES_PROPOSEES`
- Produit : `page.lien_action(ville, bot, code) -> str` (chaîne vide si `bot`
  manque), `page.PREFIXE_ATTENTE = "attente_"`

- [ ] **Étape 1 : écrire le test qui échoue**

```python
class TestAppelsALAction(unittest.TestCase):
    QUAND = "2026-09-22T09:18:00+00:00"
    BOT = "ianniv_vols_bot"
    CODE = "invitation_test_2026"

    def test_une_ville_abonnable_porte_le_lien_d_inscription(self):
        lien = page.lien_action("Dakar", self.BOT, self.CODE)

        self.assertIn(f"t.me/{self.BOT}?start={self.CODE}", lien)

    def test_une_ville_non_abonnable_porte_le_lien_de_liste_d_attente(self):
        lien = page.lien_action("Nairobi", self.BOT, self.CODE)

        self.assertIn(f"t.me/{self.BOT}?start={page.PREFIXE_ATTENTE}nairobi",
                      lien)
        self.assertNotIn(self.CODE, lien)

    def test_le_payload_d_attente_survit_aux_noms_composes(self):
        """Le payload start de Telegram n'accepte que A-Za-z0-9_- :
        « attente_Le Caire » serait rejete par Telegram."""
        lien = page.lien_action("Le Caire", self.BOT, self.CODE)

        self.assertIn(f"start={page.PREFIXE_ATTENTE}le_caire", lien)
        self.assertNotIn(" ", lien[lien.index("start="):])

    def test_sans_nom_de_bot_aucun_lien_bancal(self):
        """Des affaires sans bouton valent mieux qu'aucune page, et un
        lien vers t.me/None serait pire que pas de lien."""
        texte = page.rendre([], self.QUAND, bot=None, code=self.CODE)

        self.assertNotIn("t.me", texte)

    def test_le_code_n_apparait_pas_pour_les_villes_non_abonnables(self):
        texte = page.rendre([], self.QUAND, bot=self.BOT, code=self.CODE)

        debut = texte.index(abonnes.NOMS_AFFICHES["Nairobi"])
        fin = texte.index("</section>", debut)
        self.assertNotIn(self.CODE, texte[debut:fin])
```

- [ ] **Étape 2 : vérifier l'échec**

Lancer : `python -m pytest tests/test_page.py::TestAppelsALAction -q`
Attendu : ÉCHEC — `lien_action` rend `""`

- [ ] **Étape 3 : implémentation**

Remplacer la fonction provisoire de la tâche 2 :

```python
# Prefixe du payload « start » qui inscrit un souhait de ville au lieu
# d'abonner. Lu par bot_ecoute.traiter_update().
PREFIXE_ATTENTE = "attente_"


def lien_action(ville: str, bot: str, code: str) -> str:
    """Le bouton sous une ville, ou rien.

    Sans nom de bot, on ne rend AUCUN lien : la page reste utile, et un
    « t.me/None » serait pire que pas de bouton.

    Une ville proposee mene a l'inscription ; une ville que le programme
    ne sert pas mene a la liste d'attente -- on n'y promet pas une
    alerte quotidienne qu'on ne tiendrait pas 3 jours sur 4.
    """
    if not bot:
        return ""
    if ville in abonnes.VILLES_PROPOSEES:
        if not code:
            return ""
        cible = code
        texte = "Recevoir ces affaires chaque jour"
    else:
        cible = PREFIXE_ATTENTE + hub_deals_db.etiquette_ville(ville)
        texte = "Me prévenir quand cette ville sera couverte"
    url = f"https://t.me/{html.escape(bot)}?start={html.escape(cible)}"
    return f'<a class="action" href="{url}">{texte}</a>'
```

- [ ] **Étape 4 : vérifier que les tests passent**

Lancer : `python -m pytest tests/test_page.py -q`
Attendu : 17 tests passés

- [ ] **Étape 5 : committer**

```bash
git add page.py tests/test_page.py
git commit -m "Page : inscription pour les villes servies, liste d'attente pour les autres"
```

---

### Tâche 4 : les liens courts de la page

**Fichiers :**
- Modifier : `hub_deals_db.py` (`preparer_liens_courts`, vers la ligne 506)
- Test : `tests/test_page_liens.py`

**Interfaces :**
- Consomme : `page.etiquette_page`
- Produit : rien de nouveau ; `LIENS_COURTS` contient désormais aussi les
  paires `(lien, "page_<ville>")`

- [ ] **Étape 1 : écrire le test qui échoue**

```python
"""Les liens de la page ont leur propre etiquette (spec du 2026-09-22)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db
import page


class TestPairesDemandees(unittest.TestCase):
    def setUp(self):
        self.vraie = hub_deals_db.raccourcir_liens
        self.demandees = []
        hub_deals_db.raccourcir_liens = self._espion

    def tearDown(self):
        hub_deals_db.raccourcir_liens = self.vraie

    def _espion(self, paires):
        self.demandees = list(paires)
        return {}

    def test_la_page_a_ses_propres_etiquettes(self):
        """Sans etiquette distincte, le trafic de la page et celui du bot
        sont indiscernables dans Travelpayouts."""
        groupe = [{"ville_depart": "Dakar", "lien": "/vol/CMNMIL"}]

        hub_deals_db.preparer_liens_courts([groupe])

        self.assertIn(("/vol/CMNMIL", "dakar"), self.demandees)
        self.assertIn(("/vol/CMNMIL", page.etiquette_page("Dakar")),
                      self.demandees)
        self.assertIn(("/vol/CMNMIL", "proprietaire"), self.demandees)
```

- [ ] **Étape 2 : vérifier l'échec**

Lancer : `python -m pytest tests/test_page_liens.py -q`
Attendu : ÉCHEC — la paire `("/vol/CMNMIL", "page_dakar")` est absente

- [ ] **Étape 3 : implémentation**

Dans `preparer_liens_courts`, après la ligne qui étend avec les étiquettes de
ville :

```python
        paires.extend((a["lien"], etiquette_ville(a["ville_depart"])) for a in groupe)
        # la page publique a ses propres etiquettes : c'est ce qui rendra
        # son trafic distinguable de celui du bot dans Travelpayouts
        paires.extend((a["lien"], page.etiquette_page(a["ville_depart"]))
                      for a in groupe)
```

Ajouter `import page` en tête de `hub_deals_db.py`.

> ⚠️ `page.py` importe `hub_deals_db`. Pour éviter la boucle d'import, faire
> l'import **dans la fonction** `preparer_liens_courts`, et non en tête de
> fichier. Vérifier avec `python -c "import hub_deals_db"`.

- [ ] **Étape 4 : vérifier que les tests passent**

Lancer : `python -m pytest tests/ -q` (toute la suite : l'import circulaire
casserait tout)
Attendu : tout passe

- [ ] **Étape 5 : committer**

```bash
git add hub_deals_db.py tests/test_page_liens.py
git commit -m "Liens courts : une etiquette propre a la page publique"
```

---

### Tâche 5 : publier sur `gh-pages`

**Fichiers :**
- Modifier : `hub_deals_db.py` (après `sauvegarder_et_alerter`, vers la ligne 958)
- Test : `tests/test_publier_page.py`

**Interfaces :**
- Consomme : `sauvegarde._executer`, `sauvegarde._fin`, `page.rendre`
- Produit : `hub_deals_db.publier_page(groupes, quand, dossier=".pages", executer=None) -> bool`

- [ ] **Étape 1 : écrire le test qui échoue**

```python
"""Publication de la page sur gh-pages (spec du 2026-09-22)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hub_deals_db

QUAND = "2026-09-22T09:18:00+00:00"


class TestPublierPage(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.commandes = []
        self.codes = {}
        self.envoyes = []
        self.journal = []
        self._vrai_envoyer = hub_deals_db.envoyer_telegram
        self._vrai_log = hub_deals_db.log
        hub_deals_db.envoyer_telegram = self._envoyer
        hub_deals_db.log = self.journal.append

    def tearDown(self):
        hub_deals_db.envoyer_telegram = self._vrai_envoyer
        hub_deals_db.log = self._vrai_log
        self.dossier.cleanup()

    def _envoyer(self, message):
        self.envoyes.append(message)
        return True

    def _executer(self, args, cwd=None, delai=None):
        self.commandes.append(args)
        return self.codes.get(args[1], 0), ""

    def test_ecrit_la_page_puis_commit_et_pousse(self):
        # « git diff --cached --quiet » rend 0 quand RIEN n'est indexe :
        # 1 signifie donc « il y a des changements a pousser »
        self.codes["diff"] = 1

        ok = hub_deals_db.publier_page([], QUAND, dossier=self.dossier.name,
                                       executer=self._executer)

        self.assertTrue(ok)
        with open(os.path.join(self.dossier.name, "index.html"),
                  encoding="utf-8") as f:
            self.assertIn("<!DOCTYPE html>", f.read())
        verbes = [c[1] for c in self.commandes]
        self.assertEqual(verbes, ["add", "diff", "commit", "push"])
        self.assertEqual(self.envoyes, [])

    def test_page_inchangee_pas_de_commit_vide(self):
        self.codes["diff"] = 0  # rien d'indexe

        ok = hub_deals_db.publier_page([], QUAND, dossier=self.dossier.name,
                                       executer=self._executer)

        self.assertTrue(ok)
        self.assertNotIn("commit", [c[1] for c in self.commandes])

    def test_un_push_en_echec_alerte(self):
        self.codes["diff"] = 1
        self.codes["push"] = 1

        ok = hub_deals_db.publier_page([], QUAND, dossier=self.dossier.name,
                                       executer=self._executer)

        self.assertFalse(ok)
        self.assertEqual(len(self.envoyes), 1)
        self.assertIn("page", self.envoyes[0].lower())

    def test_ne_leve_jamais(self):
        """Le releve est deja enregistre a ce stade."""
        ok = hub_deals_db.publier_page([], QUAND, dossier="/dossier/absent",
                                       executer=self._executer)

        self.assertFalse(ok)

    def test_une_alerte_non_partie_se_lit_au_journal(self):
        hub_deals_db.envoyer_telegram = lambda m: False
        self.codes["diff"] = 1
        self.codes["push"] = 1

        hub_deals_db.publier_page([], QUAND, dossier=self.dossier.name,
                                  executer=self._executer)

        self.assertTrue(any("NON envoyee" in l for l in self.journal))
```

- [ ] **Étape 2 : vérifier l'échec**

Lancer : `python -m pytest tests/test_publier_page.py -q`
Attendu : ÉCHEC, `AttributeError: module 'hub_deals_db' has no attribute 'publier_page'`

- [ ] **Étape 3 : implémentation**

```python
def publier_page(groupes: list, quand: str, dossier: str = ".pages",
                 executer=None) -> bool:
    """Ecrit la page du jour dans le worktree gh-pages et la pousse.

    Meme patron que sauvegarder_et_alerter, et pour les memes raisons :
    la tache tourne sans personne devant l'ecran, donc saisie interdite
    et delai maximal sur chaque commande git (reproduit le 2026-09-13,
    ou un push bloque a fait sauter trois jours de releves).

    On n'alerte QUE l'echec : une « page publiee » quotidienne
    deviendrait un bruit qu'on cesse de lire.

    Ne leve jamais : le releve est deja enregistre a ce stade.
    """
    try:
        import page
        from sauvegarde import _executer, _fin, _horodatage
        executer = executer or _executer

        chemin = os.path.join(dossier, "index.html")
        with open(chemin, "w", encoding="utf-8", newline="\n") as f:
            f.write(page.rendre(groupes, quand,
                                bot=os.environ.get("HUB_DEALS_BOT_USERNAME"),
                                code=os.environ.get("HUB_DEALS_CODE_INVITATION")))

        code, sortie = executer(["git", "add", "index.html"], cwd=dossier)
        if code != 0:
            raise RuntimeError(f"add : {_fin(sortie)}")

        code, _ = executer(["git", "diff", "--cached", "--quiet"], cwd=dossier)
        if code == 0:
            log("   -> page publique : inchangee, rien a pousser")
            return True

        code, sortie = executer(
            ["git", "commit", "-m", f"page du {_horodatage()}"], cwd=dossier)
        if code != 0:
            raise RuntimeError(f"commit : {_fin(sortie)}")

        code, sortie = executer(["git", "push", "origin", "gh-pages"],
                                cwd=dossier)
        if code != 0:
            raise RuntimeError(f"push : {_fin(sortie)}")

        log("   -> page publique poussee")
        return True

    except Exception as e:
        log(f"   -> page publique impossible : {e}")
        envoye = envoyer_telegram(
            "<b>Probleme technique -- page publique non mise a jour</b>\n\n"
            "Le releve du jour est bien enregistre, mais la page publique "
            "n'a pas pu etre publiee.\n\n"
            "Les gens qui ouvrent le lien voient encore les prix d'hier.\n\n"
            f"Detail : {e}"
        )
        log("   -> ALERTE page envoyee" if envoye
            else "   -> ALERTE page NON envoyee")
        return False
```

- [ ] **Étape 4 : vérifier que les tests passent**

Lancer : `python -m pytest tests/test_publier_page.py -q`
Attendu : 5 tests passés

- [ ] **Étape 5 : committer**

```bash
git add hub_deals_db.py tests/test_publier_page.py
git commit -m "Page : publication sur gh-pages, avec alerte en cas d'echec"
```

---

### Tâche 6 : raccorder au relevé

**Fichiers :**
- Modifier : `hub_deals_db.py` (`verifier_et_notifier_anomalies`, vers la ligne 1133 ; fin du relevé, vers la ligne 1278)
- Test : `tests/test_publier_page.py`

**Interfaces :**
- Consomme : `publier_page`
- Produit : la page est écrite à chaque relevé, y compris les jours sans affaire

- [ ] **Étape 1 : écrire le test qui échoue**

```python
class TestRaccordementAuReleve(unittest.TestCase):
    def test_la_page_est_publiee_meme_sans_anomalie(self):
        """Un jour sans affaire doit quand meme rafraichir la page :
        sinon elle resterait bloquee sur les prix de la veille, sans le
        dire."""
        appels = []
        vrai = hub_deals_db.publier_page
        hub_deals_db.publier_page = lambda *a, **k: appels.append(a) or True
        try:
            hub_deals_db.publier_page_sans_risque([], QUAND)
        finally:
            hub_deals_db.publier_page = vrai

        self.assertEqual(len(appels), 1)
```

- [ ] **Étape 2 : vérifier l'échec**

Lancer : `python -m pytest tests/test_publier_page.py::TestRaccordementAuReleve -q`
Attendu : ÉCHEC, `publier_page_sans_risque` n'existe pas

- [ ] **Étape 3 : implémentation**

Ajouter, juste après `publier_page` :

```python
def publier_page_sans_risque(groupes: list, quand: str) -> None:
    """Appelee par le releve. publier_page n'echoue deja jamais ; cette
    enveloppe existe pour que le raccordement soit testable sans git."""
    publier_page(groupes, quand)
```

Dans `verifier_et_notifier_anomalies`, mémoriser les groupes pour la fin du
relevé — ou, plus simple et sans etat global : appeler la publication depuis la
même fonction, juste après `notifier_abonnes_sans_risque(groupes)` (ligne 1133) :

```python
    notifier_abonnes_sans_risque(groupes)
    publier_page_sans_risque(groupes, date_collecte)
```

Et, pour couvrir le cas « aucune anomalie » (retour anticipé, vers la ligne 1128) :

```python
    if not anomalies:
        log("Aucune anomalie a notifier pour ce releve.")
        # la page doit quand meme etre rafraichie : sinon elle reste sur
        # les prix de la veille sans le dire
        publier_page_sans_risque([], date_collecte)
        return
```

- [ ] **Étape 4 : vérifier que les tests passent**

Lancer : `python -m pytest tests/ -q`
Attendu : tout passe

- [ ] **Étape 5 : committer**

```bash
git add hub_deals_db.py tests/test_publier_page.py
git commit -m "Releve : publier la page a chaque execution, meme sans affaire"
```

---

### Tâche 7 : la table `villes_souhaitees`

**Fichiers :**
- Créer : `souhaits.py`
- Modifier : `magasin.py` (`_DDL`), `sauvegarde.py` (`TABLES_PRIVEES`), `abonnes.py` (`instantane`)
- Test : `tests/test_souhaits.py`

**Interfaces :**
- Consomme : `magasin.ouvrir`
- Produit : `souhaits.noter(conn, chat_id, ville, quand) -> None`,
  `souhaits.compter(conn) -> dict[str, int]`,
  `souhaits.instantane(conn) -> list[dict]`

- [ ] **Étape 1 : écrire le test qui échoue**

```python
"""Liste d'attente des villes non couvertes (spec du 2026-09-22)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import magasin
import sauvegarde
import souhaits

T0 = "2026-09-22T10:00:00+00:00"


class TestSouhaits(unittest.TestCase):
    def setUp(self):
        self.conn = magasin.ouvrir(chemin=":memory:")

    def tearDown(self):
        self.conn.close()

    def test_note_un_souhait(self):
        souhaits.noter(self.conn, 111, "Nairobi", T0)

        self.assertEqual(souhaits.compter(self.conn), {"Nairobi": 1})

    def test_le_meme_souhait_deux_fois_ne_compte_qu_une_fois(self):
        """Quelqu'un qui reclique le lien ne doit pas gonfler la demande."""
        souhaits.noter(self.conn, 111, "Nairobi", T0)
        souhaits.noter(self.conn, 111, "Nairobi", T0)

        self.assertEqual(souhaits.compter(self.conn), {"Nairobi": 1})

    def test_deux_personnes_comptent_deux_fois(self):
        souhaits.noter(self.conn, 111, "Nairobi", T0)
        souhaits.noter(self.conn, 222, "Nairobi", T0)

        self.assertEqual(souhaits.compter(self.conn), {"Nairobi": 2})


class TestConfidentialite(unittest.TestCase):
    def test_la_table_ne_sort_jamais_sur_le_depot_public(self):
        """Elle porte des chat_id Telegram. C'est exactement la fuite du
        2026-09-20, ou les abonnes partaient sur un depot public."""
        self.assertIn("villes_souhaitees", sauvegarde.TABLES_PRIVEES)
```

- [ ] **Étape 2 : vérifier l'échec**

Lancer : `python -m pytest tests/test_souhaits.py -q`
Attendu : ÉCHEC, `ModuleNotFoundError: No module named 'souhaits'`

- [ ] **Étape 3 : implémentation**

Créer `souhaits.py` :

```python
"""
Liste d'attente : les villes que des gens reclament et que le programme
ne sert pas encore.

Aucun appel reseau. La table porte des chat_id Telegram : elle est
PRIVEE (voir sauvegarde.TABLES_PRIVEES et abonnes.ecrire_instantane).

Spec : docs/superpowers/specs/2026-09-22-page-publique-design.md
"""

_COLONNES = ("chat_id", "ville", "quand")


def noter(conn, chat_id: int, ville: str, quand: str) -> None:
    """Enregistre un souhait. Rejouable : recliquer le lien ne gonfle
    pas la demande, ce qui rendrait le compte inutilisable."""
    conn.execute(
        "INSERT INTO villes_souhaitees (chat_id, ville, quand) "
        "VALUES (?, ?, ?) ON CONFLICT (chat_id, ville) DO NOTHING",
        (chat_id, ville, quand))
    conn.commit()


def compter(conn) -> dict:
    """Combien de personnes reclament chaque ville."""
    return {v: n for v, n in conn.execute(
        "SELECT ville, COUNT(*) FROM villes_souhaitees "
        "GROUP BY ville ORDER BY COUNT(*) DESC").fetchall()}


def instantane(conn) -> list:
    lignes = conn.execute(
        f"SELECT {', '.join(_COLONNES)} FROM villes_souhaitees "
        "ORDER BY chat_id, ville").fetchall()
    return [dict(zip(_COLONNES, l)) for l in lignes]
```

Dans `magasin._DDL`, ajouter à **chacun** des deux dialectes :

```sql
        CREATE TABLE IF NOT EXISTS villes_souhaitees (
            chat_id  BIGINT NOT NULL,     -- INTEGER en sqlite
            ville    TEXT NOT NULL,
            quand    TEXT NOT NULL,
            PRIMARY KEY (chat_id, ville)
        )
```

Et mettre à jour `_tables_manquantes` : le compte passe de `< 2` à `< 3`, et la
liste des noms inclut `villes_souhaitees`.

Dans `sauvegarde.py` :

```python
TABLES_PRIVEES = ("abonnes", "etat_bot", "villes_souhaitees")
```

Dans `abonnes.ecrire_instantane`, ajouter les souhaits au contenu écrit :

```python
    import souhaits
    contenu = {"pris_le": quand, "lignes": len(lignes), "abonnes": lignes,
               "villes_souhaitees": souhaits.instantane(conn)}
```

- [ ] **Étape 4 : vérifier que les tests passent**

Lancer : `python -m pytest tests/ -q`
Attendu : tout passe

- [ ] **Étape 5 : committer**

```bash
git add souhaits.py magasin.py sauvegarde.py abonnes.py tests/test_souhaits.py
git commit -m "Souhaits : table privee des villes reclamees"
```

---

### Tâche 8 : le bot comprend `attente_<etiquette>`

**Fichiers :**
- Modifier : `bot_ecoute.py` (`traiter_update`, branche `/start`)
- Test : `tests/test_attente.py`

**Interfaces :**
- Consomme : `page.PREFIXE_ATTENTE`, `souhaits.noter`,
  `hub_deals_db.etiquette_ville`
- Produit : `bot_ecoute.ville_depuis_etiquette(etiquette) -> str | None`

- [ ] **Étape 1 : écrire le test qui échoue**

```python
"""Payload « attente_ » du lien de liste d'attente (spec du 2026-09-22)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import abonnes
import bot_ecoute
import hub_deals_db
import magasin
import souhaits

CODE = "invitation_test_2026"
T0 = "2026-09-22T10:00:00+00:00"


def _start(payload, chat_id=111):
    return {"update_id": 1, "message": {
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": chat_id, "first_name": "Awa"},
        "text": f"/start {payload}"}}


class TestListeDAttente(unittest.TestCase):
    def setUp(self):
        self.conn = magasin.ouvrir(chemin=":memory:")

    def tearDown(self):
        self.conn.close()

    def test_enregistre_le_souhait_sans_creer_d_abonne(self):
        bot_ecoute.traiter_update(self.conn, _start("attente_nairobi"),
                                  CODE, T0)

        self.assertEqual(souhaits.compter(self.conn), {"Nairobi": 1})
        self.assertIsNone(abonnes.trouver(self.conn, 111))

    def test_ne_consomme_pas_le_plafond(self):
        bot_ecoute.traiter_update(self.conn, _start("attente_nairobi"),
                                  CODE, T0)

        self.assertEqual(abonnes.nb_actifs(self.conn), 0)

    def test_survit_aux_noms_composes(self):
        bot_ecoute.traiter_update(self.conn, _start("attente_le_caire"),
                                  CODE, T0)

        self.assertEqual(souhaits.compter(self.conn), {"Le Caire": 1})

    def test_une_ville_inconnue_est_refusee(self):
        actions, _ = bot_ecoute.traiter_update(
            self.conn, _start("attente_atlantide"), CODE, T0)

        self.assertEqual(souhaits.compter(self.conn), {})
        self.assertIn(bot_ecoute.MSG_INVITATION,
                      [a.get("text") for a in actions])

    def test_le_chemin_inverse_couvre_les_treize_villes(self):
        """L'etiquette ecrite par la page et celle attendue par le bot
        viennent du meme etiquette_ville() -- defaut du 2026-09-16."""
        for ville in abonnes.NOMS_AFFICHES:
            etiquette = hub_deals_db.etiquette_ville(ville)
            self.assertEqual(bot_ecoute.ville_depuis_etiquette(etiquette),
                             ville)
```

- [ ] **Étape 2 : vérifier l'échec**

Lancer : `python -m pytest tests/test_attente.py -q`
Attendu : ÉCHEC, `ville_depuis_etiquette` n'existe pas

- [ ] **Étape 3 : implémentation**

Dans `bot_ecoute.py` :

```python
MSG_ATTENTE = ("C'est noté. Je te préviens dès que {ville} sera couverte. "
               "En attendant, les affaires du jour sont sur la page publique.")


def ville_depuis_etiquette(etiquette: str):
    """Chemin inverse de etiquette_ville(), CONSTRUIT et non recopie.

    Une table ecrite a la main divergerait des que NOMS_AFFICHES change
    -- exactement le defaut du 2026-09-16, ou deux endroits fabriquaient
    l'etiquette chacun de leur cote.
    """
    for ville in abonnes.NOMS_AFFICHES:
        if hub_deals_db.etiquette_ville(ville) == etiquette:
            return ville
    return None
```

Dans `traiter_update`, **au tout début de la branche `/start`**, avant le test du
code :

```python
    if commande == "/start" and argument.startswith(page.PREFIXE_ATTENTE):
        # une liste d'attente n'abonne pas : pas de code exige, pas de
        # plafond consomme, aucune alerte quotidienne promise
        import souhaits
        ville = ville_depuis_etiquette(argument[len(page.PREFIXE_ATTENTE):])
        if ville is None:
            return [_envoi(chat_id, MSG_INVITATION)], f"attente refusee (ville inconnue) chat_id={chat_id}"
        souhaits.noter(conn, chat_id, ville, quand)
        return ([_envoi(chat_id, MSG_ATTENTE.format(ville=abonnes.NOMS_AFFICHES[ville]))],
                f"attente {ville} chat_id={chat_id}")
```

Ajouter `import page` en tête de `bot_ecoute.py` (pas de boucle : `page`
n'importe pas `bot_ecoute`).

- [ ] **Étape 4 : vérifier que les tests passent**

Lancer : `python -m pytest tests/ -q`
Attendu : tout passe

- [ ] **Étape 5 : committer**

```bash
git add bot_ecoute.py tests/test_attente.py
git commit -m "Bot : le lien de liste d'attente enregistre une ville reclamee"
```

---

### Tâche 9 : retirer le `noindex` et documenter

**Fichiers :**
- Modifier : `README.md`
- Test : aucun (documentation)

- [ ] **Étape 1 : vérifier que la vraie page ne porte pas de `noindex`**

```bash
python -c "import page; assert 'noindex' not in page.rendre([], '2026-09-22T09:00:00+00:00'); print('ok')"
```

Attendu : `ok`. La page d'attente du 2026-09-22 en portait un ; le générateur
n'en met pas.

- [ ] **Étape 2 : documenter dans `README.md`**

Ajouter une section « Page publique » : l'adresse, la branche `gh-pages`, le
worktree `.pages/`, les deux variables d'environnement
(`HUB_DEALS_BOT_USERNAME`, `HUB_DEALS_CODE_INVITATION`), et le rappel que la
branche est **publique** — aucune donnée personnelle ne doit y entrer.

- [ ] **Étape 3 : committer**

```bash
git add README.md
git commit -m "Doc : la page publique quotidienne"
```

---

## À faire à la main, avant la première publication

1. **Activer GitHub Pages** : Settings → Pages → Source : branche `gh-pages`.
2. **Créer `villes_souhaitees` dans Neon**, avec le rôle propriétaire (celui du
   bot n'a que SELECT/INSERT/UPDATE) :
   ```sql
   CREATE TABLE IF NOT EXISTS villes_souhaitees (
       chat_id BIGINT NOT NULL, ville TEXT NOT NULL, quand TEXT NOT NULL,
       PRIMARY KEY (chat_id, ville));
   GRANT SELECT, INSERT, UPDATE ON villes_souhaitees TO <role_du_bot>;
   ```
3. **Poser `HUB_DEALS_BOT_USERNAME`** sur le portable (`setx`), par exemple
   `ianniv_vols_bot`. Vérifier ensuite via `HKCU\Environment`, jamais via
   l'environnement du processus courant, qui est obsolète après `setx`.
4. **Manual Deploy** du service `hub-deals-bot` sur Render : il ne se
   redéploie pas tout seul, et sans ça il ignorera le payload `attente_`.

## Hors sujet, volontairement

Le parrainage, l'attribution des clics par abonné, le retrait de
`PLAFOND_ABONNES`, et toute retouche des seuils de détection — la mesure du
2026-09-22 montre qu'elle ne servirait à rien.
