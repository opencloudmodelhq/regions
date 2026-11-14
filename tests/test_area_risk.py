# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Would a rival city change a region's area code, or only its label?"""

from ocm_regions.analysis.arearisk import compare_rival, region_status, resolve_point
from ocm_regions.common.geo import Airport

BIG = "large_airport"
NEAR = Airport("NER", "Near", "", "XX", 0.0, 0.0, BIG, True)
FAR = Airport("FAR", "Far", "", "XX", 0.0, 5.0, BIG, True)
AIRPORTS = [NEAR, FAR]
RANKS = {"NER": 0.5, "FAR": 0.4}
PLACED = {
    "lat": "0.0",
    "lon": "0.1",
    "country": "XX",
    "iata_nearest_airport": "NER",
    "iata_major_hub": "NER",
    "iata_nearby_hub": "NER",
}


def test_a_rival_in_the_same_area_leaves_the_code_alone():
    row = compare_rival(PLACED, (0.0, 0.2), AIRPORTS, RANKS, 100)
    assert row["rival_nearby_hub"] == "NER" and row["changes_nearby_hub"] == "no"


def test_a_rival_nearer_another_airport_changes_the_code():
    row = compare_rival(PLACED, (0.0, 4.9), AIRPORTS, RANKS, 100)
    assert row["rival_nearby_hub"] == "FAR" and row["changes_nearby_hub"] == "yes"


def test_a_recorded_hub_that_differs_from_the_rule_is_not_counted_as_a_change():
    curated = {**PLACED, "iata_major_hub": "CUR", "iata_nearby_hub": "CUR"}
    assert compare_rival(curated, (0.0, 0.2), AIRPORTS, RANKS, 100)["changes_nearby_hub"] == "no"


def test_the_distance_to_the_rival_is_reported():
    assert compare_rival(PLACED, (0.0, 0.2), AIRPORTS, RANKS, 100)["rival_km"] == 11


def test_one_changing_rival_puts_the_region_at_risk():
    assert region_status([{"changes_nearby_hub": "no"}, {"changes_nearby_hub": "yes"}]) == "code-at-risk"


def test_rivals_that_all_agree_make_the_code_safe():
    assert region_status([{"changes_nearby_hub": "no"}]) == "code-safe"


def test_no_rival_is_reported_as_untested_not_safe():
    assert region_status([]) == "no-rival-named"


CITIES = [
    {"name": "Springfield", "country": "XX", "population": 10, "lat": 1.0, "lon": 1.0},
    {"name": "Springfield", "country": "XX", "population": 99, "lat": 2.0, "lon": 2.0},
    {"name": "Springfield", "country": "YY", "population": 999, "lat": 3.0, "lon": 3.0},
]


def test_an_unknown_rival_has_no_point():
    assert resolve_point({"rival": "Nowhere", "country": "XX"}, CITIES, (0.0, 0.0)) is None
