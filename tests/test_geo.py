# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from ocm_regions.common.geo import Airport, haversine_km, nearest_airport, parse_locode_coordinates, place_id


def airport(code, country, lat, lon, kind="large_airport", scheduled=True):
    return Airport(code, code, "town", country, lat, lon, kind, scheduled)


def test_parse_locode_coordinates_converts_degrees_minutes():
    lat, lon = parse_locode_coordinates("3903N 07729W")
    assert round(lat, 2) == 39.05
    assert round(lon, 2) == -77.48


def test_parse_locode_coordinates_rejects_blank():
    assert parse_locode_coordinates("") is None


def test_haversine_matches_known_distance():
    assert 340 < haversine_km((51.5, -0.12), (48.86, 2.35)) < 350


def test_nearest_airport_skips_unscheduled_airports():
    airports = [airport("AAA", "US", 40.0, -75.0, scheduled=False), airport("BBB", "US", 40.5, -75.0)]
    assert nearest_airport((40.0, -75.0), "US", airports).code == "BBB"


def test_nearest_airport_skips_small_airports():
    airports = [airport("AAA", "US", 40.0, -75.0, kind="small_airport"), airport("BBB", "US", 41.0, -75.0)]
    assert nearest_airport((40.0, -75.0), "US", airports).code == "BBB"


def test_nearest_airport_prefers_same_country():
    airports = [airport("CZE", "CZ", 50.2, 12.9), airport("DEU", "DE", 51.4, 12.2)]
    assert nearest_airport((50.48, 12.37), "DE", airports).code == "DEU"


def test_nearest_airport_falls_back_abroad_when_country_has_none():
    airports = [airport("CZE", "CZ", 50.2, 12.9)]
    assert nearest_airport((50.48, 12.37), "DE", airports).code == "CZE"


def test_place_id_includes_subdivision_where_given():
    assert place_id("US", "VA", "Ashburn") == "us-va-ashburn"


def test_place_id_omits_blank_subdivision_and_strips_accents():
    assert place_id("SE", "", "Gävle") == "se-gavle"
