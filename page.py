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
