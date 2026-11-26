"""Tests for the ceskereality alert parser; the HTML mirrors a real 2026-11 alert card."""

from src.parsers.ceskereality import email_listings_to_records, parse_email_html
from src.storage import generate_property_id

LINK = "https://www.ceskereality.cz/nemovitost.html?nid={nid}&amp;utm_source=hlidac&amp;utm_medium=email"


def _card(nid: int, title: str, desc: str = "") -> str:
    """One listing card: image link, title link, agency, description, 'Více informací' link."""
    href = LINK.format(nid=nid)
    desc_p = f'<p class="hide-on-mobile">{desc}</p>' if desc else ""
    return (
        f'<td><table><tr><td><a href="{href}"><img src="x.jpg"/></a></td></tr></table>'
        f'<table><tr><td><p><a href="{href}">{title}</a></p><p>BIDLI reality, a.s.</p>{desc_p}</td></tr>'
        f'<tr><td><a href="{href}">Více informací</a></td></tr></table></td>'
    )


def _email(*cards: str) -> str:
    footer = '<a href="https://www.ceskereality.cz/moje-filtry">filtry</a>'
    return f"<html><body><table><tr>{''.join(cards)}</tr></table>{footer}</body></html>"


FLAT = _card(3960862, "Úvaly - Prodej bytu 4+kk",
             "Prodej bytu 4+kk, 100 m<sup>2</sup>, který se nachází v přízemí cihlového domu.")


class TestParseEmail:
    def test_one_listing_from_three_links(self):
        [item] = parse_email_html(_email(FLAT))
        assert item["url"] == "https://www.ceskereality.cz/nemovitost.html?nid=3960862"
        assert item["name"] == "Úvaly - Prodej bytu 4+kk"
        assert item["description"].startswith("Prodej bytu 4+kk, 100 m2")

    def test_two_listings(self):
        items = parse_email_html(_email(FLAT, _card(3960400, "Lešany - Prodej stavební parcely")))
        assert [i["url"][-7:] for i in items] == ["3960862", "3960400"]

    def test_empty_email(self):
        assert parse_email_html("") == []
        assert parse_email_html(_email()) == []


class TestToRecords:
    def test_flat(self):
        [rec] = email_listings_to_records(parse_email_html(_email(FLAT)))
        assert rec.source == "ceskereality"
        assert rec.property_type == "apartment"
        assert rec.size_category == "4+kk"
        assert rec.size_sqm == 100.0
        assert rec.location == "Úvaly"
        assert rec.building_type == "cihlová"
        assert rec.price_total is None  # the alert card carries no price
        assert rec.property_id == generate_property_id("ceskereality", rec.url)

    def test_house(self):
        card = _card(1, "Praha 9 - Prodej rodinného domu 5+kk", "Dům 160 m<sup>2</sup>, pozemek 800 m2.")
        [rec] = email_listings_to_records(parse_email_html(_email(card)))
        assert rec.property_type == "house"
        assert rec.size_sqm == 160.0
        assert rec.district == "Praha 9"

    def test_land_has_no_type_so_rules_drop_it(self):
        card = _card(2, "Lešany - Prodej stavební parcely", "Pozemek 705 m²")
        [rec] = email_listings_to_records(parse_email_html(_email(card)))
        assert rec.property_type == ""

    def test_panel_detected(self):
        card = _card(3, "Praha 4 - Prodej bytu 3+1", "Byt 80 m2 v panelovém domě.")
        [rec] = email_listings_to_records(parse_email_html(_email(card)))
        assert rec.building_type == "panelová"

    def test_missing_description(self):
        [rec] = email_listings_to_records(parse_email_html(_email(_card(4, "Říčany - Prodej bytu 3+kk"))))
        assert rec.size_sqm is None
        assert rec.building_type == ""

    def test_empty_input(self):
        assert email_listings_to_records([]) == []
