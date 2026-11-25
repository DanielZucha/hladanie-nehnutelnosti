"""Tests for the sreality v1 search API client."""

import pytest
import requests
import responses

from src.config import SearchConfig
from src.parsers import sreality

CFG = SearchConfig()
SEARCH_URL = f"{sreality.SREALITY_API_BASE}/estates/search"


def _estate(**kw) -> dict:
    """Minimal v1 search result for a Prague 4+kk apartment."""
    est = {
        "hash_id": 778305612,
        "advert_name": "Prodej bytu 4+kk 113\xa0m²",
        "category_main_cb": {"name": "Byty", "value": 1},
        "category_sub_cb": {"name": "4+kk", "value": 8},
        "category_type_cb": {"name": "Prodej", "value": 1},
        "locality": {
            "city": "Praha", "citypart": "Hlubočepy", "district": "Praha 5",
            "street": "Ondráčkova", "gps_lat": 50.03, "gps_lon": 14.39,
            "city_seo_name": "praha", "citypart_seo_name": "hlubocepy",
        },
        "poi_train_distance": 1058,
        "price_czk": 9590000.0,
    }
    est.update(kw)
    return est


class TestEstateToRecord:
    def test_apartment(self):
        rec = sreality.estate_to_record(_estate())
        assert rec.property_type == "apartment"
        assert rec.size_category == "4+kk"
        assert rec.size_sqm == 113.0
        assert rec.price_total == 9_590_000
        assert rec.price_per_sqm == pytest.approx(84867.26, 0.01)
        assert rec.district == "Praha 5"
        assert rec.location == "Ondráčkova, Hlubočepy, Praha"
        assert (rec.lat, rec.lon) == (50.03, 14.39)
        assert rec.distance_to_train_km == 1.058
        assert rec.url.endswith("/detail/prodej/byt/4+kk/praha-hlubocepy/778305612")
        assert rec.source == "sreality"

    def test_house_has_no_disposition(self):
        rec = sreality.estate_to_record(_estate(
            advert_name="Prodej rodinného domu 211\xa0m², pozemek 735\xa0m²",
            category_main_cb={"value": 2}, category_sub_cb={"value": 37},
        ))
        assert rec.property_type == "house"
        assert rec.size_category == ""
        assert rec.size_sqm == 211.0
        assert "/dum/rodinny/" in rec.url

    def test_price_on_request(self):
        rec = sreality.estate_to_record(_estate(price_czk=1.0))
        assert rec.price_total is None
        assert rec.price_per_sqm is None

    def test_missing_gps_and_train(self):
        est = _estate(poi_train_distance=None)
        est["locality"] = {"city": "Úvaly", "district": "Praha-východ"}
        rec = sreality.estate_to_record(est)
        assert rec.lat is None and rec.lon is None
        assert rec.distance_to_train_km is None
        assert rec.location == "Úvaly"


class TestSearchParams:
    def test_apartment_params(self):
        p = sreality.search_params(CFG, "apartment")
        assert p["category_main_cb"] == 1
        assert p["category_sub_cb"] == CFG.apartment_sub_cbs
        assert p["building_type"] == CFG.building_types
        assert p["price_from"] == 6_000_000 and p["price_to"] == 14_000_000
        assert p["usable_area_from"] == 79
        assert p["locality_region_id"] == 10
        assert p["locality_district_id"] == "56"

    def test_house_params(self):
        p = sreality.search_params(CFG, "house")
        assert p["category_main_cb"] == 2
        assert p["category_sub_cb"] == CFG.house_sub_cbs


class TestFetch:
    @responses.activate
    def test_paginates(self):
        responses.get(SEARCH_URL, json={"pagination": {"total": 3}, "results": [_estate(hash_id=1), _estate(hash_id=2)]})
        responses.get(SEARCH_URL, json={"pagination": {"total": 3}, "results": [_estate(hash_id=3)]})
        out = sreality.fetch_all(requests.Session(), {}, limit=2, delay_range=(0, 0))
        assert [e["hash_id"] for e in out] == [1, 2, 3]

    @responses.activate
    def test_empty(self):
        responses.get(SEARCH_URL, json={"pagination": {"total": 0}, "results": []})
        assert sreality.fetch_all(requests.Session(), {}, delay_range=(0, 0)) == []

    @responses.activate
    def test_blocked_raises(self):
        responses.get(SEARCH_URL, status=403, body="<html>blocked</html>")
        with pytest.raises(sreality.SrealityBlockedError):
            sreality.fetch_all(requests.Session(), {}, delay_range=(0, 0))

    @responses.activate
    def test_non_json_raises(self):
        responses.get(SEARCH_URL, status=200, body="<html>captcha</html>")
        with pytest.raises(sreality.SrealityBlockedError):
            sreality.fetch_all(requests.Session(), {}, delay_range=(0, 0))

    @responses.activate
    def test_scrape_dedups_across_queries(self):
        body = {"pagination": {"total": 1}, "results": [_estate()]}
        responses.get(SEARCH_URL, json=body)
        responses.get(SEARCH_URL, json=body)
        recs = sreality.scrape_listings(requests.Session(), CFG, delay_range=(0, 0))
        assert len(recs) == 1


class TestFailLoudly:
    @responses.activate
    def test_missing_results_raises(self):
        responses.get(SEARCH_URL, json={"status_code": 500, "status_message": "x"})
        with pytest.raises(sreality.SrealityBlockedError):
            sreality.fetch_all(requests.Session(), {}, delay_range=(0, 0))

    @responses.activate
    def test_missing_total_raises(self):
        responses.get(SEARCH_URL, json={"results": [_estate()]})
        with pytest.raises(sreality.SrealityBlockedError):
            sreality.fetch_all(requests.Session(), {}, delay_range=(0, 0))

    @responses.activate
    def test_zero_apartments_raises(self):
        responses.get(SEARCH_URL, json={"pagination": {"total": 0}, "results": []})
        with pytest.raises(sreality.SrealityBlockedError):
            sreality.scrape_listings(requests.Session(), CFG, delay_range=(0, 0))


def test_id_stable_when_slug_changes():
    a = sreality.estate_to_record(_estate())
    est = _estate()
    est["locality"] = {**est["locality"], "citypart_seo_name": "hlubocepy-ondrakove"}
    assert sreality.estate_to_record(est).property_id == a.property_id
