"""Tests for iDNES detail-page parsing and the geocode + train locate step."""

from src.locate import locate_records
from src.parsers import idnes
from src.storage import PropertyRecord

DETAIL_HTML = """
<html><body><dl>
<dt>Cena</dt><dd>Cena&zwj; na&zwj; vyžádání</dd>
<dt>Konstrukce budovy</dt><dd>cihlová</dd>
<dt>Užitná plocha</dt><dd>142 m<sup>2</sup></dd>
<dt>Plocha pozemku</dt><dd>329 m2</dd>
<dt>Počet místností</dt><dd>5</dd>
</dl></body></html>
"""


class TestDetailParams:
    def test_parses_fields(self):
        p = idnes.parse_detail_params(DETAIL_HTML)
        assert p["building_type"] == "cihlová"
        assert p["size_sqm"] == 142.0

    def test_empty_page(self):
        assert idnes.parse_detail_params("<html></html>") == {"building_type": "", "size_sqm": None}


def _rec(**kw) -> PropertyRecord:
    base = dict(url="u", location="Smrková, Úvaly, okres Praha-východ", district="Praha-východ")
    base.update(kw)
    return PropertyRecord(**base)


class TestLocate:
    def test_geocodes_and_measures(self):
        calls = []
        recs = locate_records(
            [_rec()],
            geocode=lambda q: calls.append(q) or (50.074, 14.73),
            nearest_station=lambda lat, lon: {"distance_km": 1.2},
        )
        assert (recs[0].lat, recs[0].lon, recs[0].distance_to_train_km) == (50.074, 14.73, 1.2)
        assert calls == ["Smrková, Úvaly, okres Praha-východ, Česko"]

    def test_prague_skipped(self):
        def boom(*a):
            raise AssertionError("must not call")
        recs = locate_records([_rec(location="Praha 9 - Kyje", district="Praha 9")], geocode=boom, nearest_station=boom)
        assert recs[0].lat is None

    def test_existing_values_kept(self):
        def boom(*a):
            raise AssertionError("must not call")
        r = _rec(lat=50.0, lon=14.7, distance_to_train_km=2.0)
        assert locate_records([r], geocode=boom, nearest_station=boom)[0].distance_to_train_km == 2.0

    def test_geocode_miss_leaves_none(self):
        recs = locate_records([_rec()], geocode=lambda q: None, nearest_station=lambda a, b: None)
        assert recs[0].lat is None and recs[0].distance_to_train_km is None

    def test_no_station_found(self):
        recs = locate_records([_rec()], geocode=lambda q: (50.07, 14.73), nearest_station=lambda a, b: None)
        assert recs[0].distance_to_train_km is None

    def test_station_error_raises_after_whole_batch(self):
        import pytest
        from src.locate import LocateError

        def fail(*a):
            raise RuntimeError("lookup down")
        recs = [_rec(url="a"), _rec(url="b")]
        with pytest.raises(LocateError, match="2 lookups"):
            locate_records(recs, geocode=lambda q: (50.07, 14.73), nearest_station=fail)

    def test_empty(self):
        assert locate_records([], geocode=None, nearest_station=None) == []


class TestLocateErrors:
    def test_geocode_error_raises_after_loop(self):
        import pytest
        from src.locate import LocateError

        def fail(q):
            raise RuntimeError("nominatim down")
        with pytest.raises(LocateError):
            locate_records([_rec()], geocode=fail, nearest_station=lambda a, b: None)


def test_detail_fetch_failure_drops_listing(monkeypatch):
    monkeypatch.setattr(idnes, "enrich_from_page", lambda url, s: None)
    item = {"url": "https://reality.idnes.cz/detail/x/", "price": 9_000_000, "size_sqm": 100.0,
            "location": "Úvaly", "property_type": "house", "size_category": "", "block_text": ""}
    assert idnes.email_listings_to_records([item], session=None, delay_range=(0, 0)) == []
