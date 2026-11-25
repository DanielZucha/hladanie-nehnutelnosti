"""Tests for the hard listing rules (region corridor, train, size, price, walls)."""

import pytest

from src.config import SearchConfig
from src.region_filter import filter_records, is_prague, passes_rules
from src.storage import PropertyRecord

CFG = SearchConfig()


def _rec(**kw) -> PropertyRecord:
    """Valid Prague apartment; override fields per test."""
    base = dict(
        url="u", property_type="apartment", size_category="3+kk", size_sqm=85.0,
        price_total=9_000_000, location="Vítkova, Praha 8 - Karlín",
        district="Praha 8", building_type="cihlová",
    )
    base.update(kw)
    return PropertyRecord(**base)


def _uvaly(**kw) -> PropertyRecord:
    """Valid Úvaly house near the station; override fields per test."""
    base = dict(
        property_type="house", size_category="", location="Smrková, Úvaly",
        district="Praha-východ", lat=50.074, lon=14.73, distance_to_train_km=1.2,
    )
    base.update(kw)
    return _rec(**base)


class TestIsPrague:
    @pytest.mark.parametrize("loc,dist", [
        ("Vítkova, Praha 8 - Karlín", "Praha 8"),
        ("Broumarská, Praha 9 - Kyje", ""),
        ("Somewhere", "Praha"),
    ])
    def test_prague(self, loc, dist):
        assert is_prague(loc, dist)

    @pytest.mark.parametrize("loc,dist", [
        ("Smrková, Úvaly, okres Praha-východ", "Praha-východ"),
        ("Úvaly", "Praha-východ"),
        ("Jesenice, okres Praha-západ", ""),
        ("", ""),
    ])
    def test_not_prague(self, loc, dist):
        assert not is_prague(loc, dist)


class TestRegion:
    def test_prague_passes_without_gps(self):
        assert passes_rules(_rec(), CFG)

    def test_uvaly_passes(self):
        assert passes_rules(_uvaly(), CFG)

    def test_nehvizdy_at_3_8_km_passes(self):
        assert passes_rules(_uvaly(lat=50.130, lon=14.730, distance_to_train_km=3.8), CFG)

    def test_train_too_far_fails(self):
        assert not passes_rules(_uvaly(distance_to_train_km=4.5), CFG)

    def test_train_unknown_fails(self):
        assert not passes_rules(_uvaly(distance_to_train_km=None), CFG)

    def test_missing_coordinates_fail(self):
        assert not passes_rules(_uvaly(lat=None, lon=None), CFG)

    @pytest.mark.parametrize("lat,lon", [
        (50.030, 15.200),  # Kolín
        (49.990, 14.650),  # Říčany
        (50.190, 14.660),  # Brandýs nad Labem
        (50.074, 14.860),  # Český Brod
    ])
    def test_outside_corridor_fails(self, lat, lon):
        assert not passes_rules(_uvaly(lat=lat, lon=lon), CFG)


class TestExcludedTowns:
    def test_skvorec_fails(self):
        assert not passes_rules(_uvaly(location="Na Výsluní, Škvorec", lat=50.047, distance_to_train_km=3.0), CFG)

    def test_skvorec_part_fails(self):
        assert not passes_rules(_uvaly(location="Třebohostice, Škvorec, okres Praha-východ"), CFG)

    def test_case_insensitive(self):
        assert not passes_rules(_uvaly(location="ŠKVOREC"), CFG)

    def test_other_town_passes(self):
        assert passes_rules(_uvaly(location="Smrková, Úvaly"), CFG)


class TestListingRules:
    def test_small_fails(self):
        assert not passes_rules(_rec(size_sqm=78.9), CFG)

    def test_size_unknown_fails(self):
        assert not passes_rules(_rec(size_sqm=None), CFG)

    @pytest.mark.parametrize("cat", ["2+kk", "2+1", "1+kk"])
    def test_small_disposition_fails(self, cat):
        assert not passes_rules(_rec(size_category=cat), CFG)

    @pytest.mark.parametrize("cat", ["3+kk", "3+1", "4+kk", "5+1", "6+kk"])
    def test_disposition_passes(self, cat):
        assert passes_rules(_rec(size_category=cat), CFG)

    def test_house_without_disposition_passes(self):
        assert passes_rules(_uvaly(size_category=""), CFG)

    @pytest.mark.parametrize("price", [5_999_999, 14_000_001, 10**12])
    def test_price_out_of_range_fails(self, price):
        assert not passes_rules(_rec(price_total=price), CFG)

    def test_price_on_request_passes(self):
        assert passes_rules(_rec(price_total=None), CFG)

    def test_panel_fails(self):
        assert not passes_rules(_rec(building_type="panelová"), CFG)

    @pytest.mark.parametrize("bt", ["cihlová", "smíšená", "dřevostavba", ""])
    def test_non_panel_passes(self, bt):
        assert passes_rules(_rec(building_type=bt), CFG)

    def test_land_fails(self):
        assert not passes_rules(_rec(property_type="land"), CFG)


class TestFilterRecords:
    def test_empty(self):
        assert filter_records([], CFG) == []

    def test_one_kept_one_dropped(self):
        kept = filter_records([_rec(url="a"), _rec(url="b", size_sqm=50.0)], CFG)
        assert [r.url for r in kept] == ["a"]


class TestDropDiagnostics:
    def test_missing_coordinates_has_own_reason(self, caplog):
        caplog.set_level("INFO")
        filter_records([_uvaly(lat=None, lon=None)], CFG)
        assert "'no_coords': 1" in caplog.text

    def test_outside_box_is_region(self, caplog):
        caplog.set_level("INFO")
        filter_records([_uvaly(location="Beroun", lat=49.96, lon=14.07)], CFG)
        assert "'region': 1" in caplog.text

    def test_log_drops_names_location_and_coords(self, caplog):
        caplog.set_level("INFO")
        filter_records([_uvaly(location="Beroun", lat=49.96, lon=14.07)], CFG, log_drops=True)
        assert "Dropped region: Beroun (49.96, 14.07)" in caplog.text

    def test_no_per_record_log_by_default(self, caplog):
        caplog.set_level("INFO")
        filter_records([_uvaly(location="Beroun", lat=49.96, lon=14.07)], CFG)
        assert "Dropped region" not in caplog.text

    def test_empty_input(self, caplog):
        caplog.set_level("INFO")
        assert filter_records([], CFG, log_drops=True) == []
        assert caplog.text == ""
