"""Tests for region filtering logic."""

from src.region_filter import is_in_target_region, filter_records
from src.storage import PropertyRecord


class TestTextMatching:
    """Text-based matching against Praha + 3 target districts."""

    def test_praha_district(self):
        assert is_in_target_region("Broumarská, Praha 9 - Kyje", "Praha 9")

    def test_praha_bare(self):
        assert is_in_target_region("Somewhere, Praha", "Praha")

    def test_praha_vychod(self):
        assert is_in_target_region("Říčany, okres Praha-východ", "Praha-východ")

    def test_kolin(self):
        assert is_in_target_region("Nová, Veltruby, okres Kolín", "Kolín")

    def test_nymburk(self):
        assert is_in_target_region("Josefa Mánesa, Nymburk", "Nymburk")

    def test_case_insensitive(self):
        assert is_in_target_region("PRAHA 5", "PRAHA 5")

    # Rejected: other Středočeský districts no longer in scope
    def test_kladno_rejected(self):
        assert not is_in_target_region("Kladno - Kročehlavy", "Kladno")

    def test_benesov_rejected(self):
        assert not is_in_target_region("Benešov", "Benešov")

    def test_beroun_rejected(self):
        assert not is_in_target_region("Beroun", "Beroun")

    # Rejected: other regions
    def test_brno_rejected(self):
        assert not is_in_target_region("Brno - Bohunice, okres Brno-město", "Brno-město")

    def test_ostrava_rejected(self):
        assert not is_in_target_region("Ostrava - Poruba", "Ostrava-město")

    def test_empty_strings(self):
        assert not is_in_target_region("", "")


class TestGpsFallback:
    """GPS bounding box for towns without okres in the district field."""

    def test_cesky_brod_by_gps(self):
        # Český Brod: ~50.07N, 14.86E (okres Kolín)
        assert is_in_target_region("Slezská, Český Brod", "Český Brod",
                                   lat=50.07, lon=14.86)

    def test_brandys_by_gps(self):
        # Brandýs nad Labem: ~50.19N, 14.66E (okres Praha-východ)
        assert is_in_target_region(
            "Třebízského, Brandýs nad Labem", "Brandýs nad Labem",
            lat=50.19, lon=14.66,
        )

    def test_brno_gps_still_rejected(self):
        # Brno: ~49.19N, 16.61E -- well outside bbox
        assert not is_in_target_region("Brno - Bohunice", "Brno-město",
                                       lat=49.19, lon=16.61)

    def test_ostrava_gps_still_rejected(self):
        # Ostrava: ~49.83N, 18.29E -- outside bbox
        assert not is_in_target_region("Ostrava", "Ostrava-město",
                                       lat=49.83, lon=18.29)

    def test_kladno_gps_rejected(self):
        # Kladno: ~50.14N, 14.10E -- west of bbox (lon < 14.20)
        assert not is_in_target_region("Kladno", "Kladno",
                                       lat=50.14, lon=14.10)

    def test_no_gps_no_text_rejected(self):
        assert not is_in_target_region("Unknown town", "Unknown")

    def test_none_gps_rejected(self):
        assert not is_in_target_region("Unknown", "Unknown", lat=None, lon=None)


class TestFilterRecords:
    def _make_record(self, location: str, district: str,
                     lat=None, lon=None) -> PropertyRecord:
        return PropertyRecord(
            property_id="test",
            source="test",
            location=location,
            district=district,
            lat=lat,
            lon=lon,
        )

    def test_keeps_prague(self):
        records = [self._make_record("Praha 3 - Žižkov", "Praha 3")]
        assert len(filter_records(records)) == 1

    def test_drops_brno(self):
        records = [self._make_record("Brno - Bohunice", "Brno-město",
                                     lat=49.19, lon=16.61)]
        assert len(filter_records(records)) == 0

    def test_keeps_by_gps_fallback(self):
        records = [self._make_record("Slezská, Český Brod", "Český Brod",
                                     lat=50.07, lon=14.86)]
        assert len(filter_records(records)) == 1

    def test_mixed(self):
        records = [
            self._make_record("Praha 5 - Smíchov", "Praha 5"),
            self._make_record("Brno", "Brno-město", lat=49.19, lon=16.61),
            self._make_record("Český Brod", "Český Brod", lat=50.07, lon=14.86),
            self._make_record("Kladno", "Kladno", lat=50.14, lon=14.10),
        ]
        kept = filter_records(records)
        assert len(kept) == 2
        locs = [r.location for r in kept]
        assert "Praha 5 - Smíchov" in locs
        assert "Český Brod" in locs
