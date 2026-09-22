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
            f'<span class="gain">−{a["baisse_pct"]:.0f} % '
            f'· {a["economie"]:.0f}€ de moins</span>'
            f'</a></li>')
    return "<ul class=\"affaires\">" + "".join(lignes) + "</ul>"


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
