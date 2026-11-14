# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Compare places with an outside dataset, and codes with the codes an operator chose for its own sites."""

import json

from ocm_regions.common.geo import Airport
from ocm_regions.place.placer import Gazetteer

GSF = (
    "year,cloud-provider,cloud-region,location,geolocation\n"
    '2023,Amazon Web Services,us-east-1,US East (N. Virginia),"38.9,-77.4"\n'
    '2024,Amazon Web Services,us-east-1,US East (N. Virginia),"39.04, -77.49 "\n'
    '2024,Microsoft Azure,eastus,East US,"37.37,-79.82"\n'
    "2024,Google Cloud,us-west3,Salt Lake City,\n"
    '2024,Other Cloud,x-1,Nowhere,"1,1"\n'
)
SITES = {
    "components": [
        {"name": "Ashburn, United States - (IAD)"},
        {"name": "Hong Kong - (HKG)"},
        {"name": "Atlantis, Nowhere - (ZZZ)"},
        {"name": "Cloudflare Sites and Services"},
        {"name": "North America"},
    ]
}


def test_the_outside_dataset_gives_one_point_per_region_from_its_latest_year():
    from ocm_regions.analysis.external import parse_outside_places

    places = parse_outside_places(GSF)
    assert places == {("aws", "us-east-1"): (39.04, -77.49), ("azure", "eastus"): (37.37, -79.82)}


def test_places_are_compared_by_distance_and_unshared_regions_are_listed():
    from ocm_regions.analysis.external import compare_places

    ours = [
        {
            "provider": "aws",
            "region_id": "us-east-1",
            "city": "Ashburn",
            "lat": "39.04372",
            "lon": "-77.48749",
        },
        {"provider": "azure", "region_id": "eastus", "city": "Boydton", "lat": "36.66", "lon": "-78.39"},
        {"provider": "aws", "region_id": "eu-west-1", "city": "Dublin", "lat": "53.3", "lon": "-6.2"},
        {"provider": "gcp", "region_id": "us-west3", "city": "", "lat": "", "lon": ""},
    ]
    rows, summary = compare_places(
        ours,
        {
            ("aws", "us-east-1"): (39.04, -77.49),
            ("azure", "eastus"): (37.37, -79.82),
            ("gcp", "gone-1"): (1.0, 1.0),
        },
    )
    assert [(r["region_id"], r["within_50_km"]) for r in rows] == [("us-east-1", "yes"), ("eastus", "no")]
    assert (summary["compared"], summary["within_50_km"], summary["within_100_km"]) == (2, 1, 1)
    assert summary["only_outside"] == ["gcp gone-1"] and summary["only_ours"] == 1
    assert summary["far_where_the_vendor_names_the_city"] == 0
    stated = [dict(ours[1], grade="vendor-stated")]
    assert (
        compare_places(stated, {("azure", "eastus"): (37.37, -79.82)})[1][
            "far_where_the_vendor_names_the_city"
        ]
        == 1
    )


def test_operator_site_names_give_a_city_a_country_and_the_code_it_chose():
    from ocm_regions.analysis.external import parse_operator_sites

    assert parse_operator_sites(json.dumps(SITES)) == [
        ("Ashburn", "United States", "IAD"),
        ("Hong Kong", "", "HKG"),
        ("Atlantis", "Nowhere", "ZZZ"),
    ]


def _airport(code, lat, lon, country="US"):
    return Airport(code, f"{code} Airport", "", country, lat, lon, "large_airport", True)


def test_each_table_is_scored_on_how_often_it_picks_the_operators_code():
    from ocm_regions.analysis.external import compare_codes

    gaz = Gazetteer(
        [
            {
                "name": "Ashburn",
                "country": "US",
                "admin1": "VA",
                "population": 43000,
                "lat": 39.04,
                "lon": -77.49,
            },
            {
                "name": "Hong Kong",
                "country": "HK",
                "admin1": "",
                "population": 7000000,
                "lat": 22.28,
                "lon": 114.16,
            },
        ],
        countries={"united states": "US", "hong kong": "HK"},
    )
    airports = [
        _airport("IAD", 38.95, -77.45),
        _airport("JYO", 39.08, -77.56),
        _airport("HKG", 22.31, 113.91, "HK"),
    ]
    sites = [("Ashburn", "United States", "IAD"), ("Hong Kong", "", "HKG"), ("Atlantis", "Nowhere", "ZZZ")]
    rows, summary = compare_codes(sites, gaz, airports, {"IAD": 0.9, "JYO": 0.1, "HKG": 0.8}, cap=90.0)
    assert (summary["sites"], summary["resolved"]) == (3, 2)
    assert [
        (r["city"], r["operator_code"], r["nearest_airport"], r["major_hub"], r["nearby_hub"]) for r in rows
    ] == [("Ashburn", "IAD", "JYO", "IAD", "IAD"), ("Hong Kong", "HKG", "HKG", "HKG", "HKG")]
    assert (summary["nearest_airport"], summary["major_hub"], summary["nearby_hub"]) == (1, 2, 2)


def test_macros_for_the_paper_have_letter_names_and_shares():
    from ocm_regions.analysis.external import external_macros

    places = {
        "compared": 2,
        "within_50_km": 1,
        "within_100_km": 1,
        "median_km": 12.3,
        "only_outside": [],
        "only_ours": 1,
        "far_where_the_vendor_names_the_city": 1,
    }
    codes = {
        "sites": 3,
        "resolved": 2,
        "nearest_airport": 1,
        "major_hub": 2,
        "nearby_hub": 2,
        "operator_code_is_airport": 2,
    }
    macros = external_macros(places, codes)
    assert (
        macros["outsidePlacesCompared"] == 2
        and macros["outsidePlacesNear"] == 1
        and macros["operatorSitesResolved"] == 2
    )
    assert macros["outsidePlacesFar"] == 1 and macros["outsidePlacesFarStated"] == 1
    assert macros["operatorNearbyHubShare"] == "100" and macros["operatorNearestShare"] == "50"
    assert all(name.isalpha() for name in macros)


def test_the_two_outside_sources_are_pinned_as_cited_not_redistributed():
    from ocm_regions.fetch.sources import SOURCES

    notes = {s.name: s.license_note for s in SOURCES}
    assert "not redistributed" in notes["gsf-regions"] and "not redistributed" in notes["cloudflare-sites"]
