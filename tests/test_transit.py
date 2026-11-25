"""Tests for the offline nearest-station lookup."""

from src.transit import find_nearest_train_station, haversine_km


def test_uvaly_center_near_uvaly_station():
    st = find_nearest_train_station(50.0740, 14.7300)
    assert st["name"] == "Úvaly"
    assert st["distance_km"] < 1.5


def test_nehvizdy_within_4_km():
    assert find_nearest_train_station(50.1306, 14.7303)["distance_km"] < 4.0


def test_far_point_is_far():
    assert find_nearest_train_station(49.19, 16.61)["distance_km"] > 100  # Brno


def test_empty_station_list():
    assert find_nearest_train_station(50.07, 14.73, stations=[]) is None


def test_haversine_zero():
    assert haversine_km(50.0, 14.0, 50.0, 14.0) == 0
