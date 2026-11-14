# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Score the frozen rules on vendors that had no part in building them."""

from ocm_regions.common.geo import Airport
from ocm_regions.place.placer import Gazetteer

POPS = (
    "<table><tr><th>Location</th><th>POP Identifier</th><th>Approx location</th></tr>"
    '<tr><td>Dallas</td><td><strong>DFW</strong></td><td><a href="x">32.78<!-- -->, <!-- -->-96.80</a></td></tr>'
    '<tr><td>Atlantis</td><td><strong>ZZZ</strong></td><td><a href="x">1.0<!-- -->, <!-- -->1.0</a></td></tr>'
    "</table>"
    "<table><tr><th>Location</th><th>POP Identifier</th><th>Sites spanned</th></tr>"
    "<tr><td>Dallas</td><td>DFW</td><td>KDAL1, KDFW2</td></tr></table>"
)
REGIONS = (
    "<table><thead><tr><th>Area</th><th>Region</th><th>Availability Zone</th><th>Data Center Count</th></tr></thead>"
    "<tbody><tr><td>Americas</td><td>na-central-2(Dallas)</td><td>na-central-2a</td><td>1</td></tr>"
    "<tr><td>Americas</td><td>na-east-1(Washington, D.C.)</td><td>na-east-1a</td><td>1</td></tr></tbody></table>"
)
ZONES = (
    "| Region   |    City    |        Zone        |\n"
    "| -------- | :--------: | :----------------: |\n"
    "| Americas |   Dallas   | DFW A, DFW B, DFW C |\n"
    "|          |  Atlantis  |        ZZZ A       |\n"
    "\n| Model | CPU |\n| ----- | --- |\n| M1 | 8 |\n"
)


def _gazetteer():
    return Gazetteer(
        [
            {
                "name": "Dallas",
                "country": "US",
                "admin1": "TX",
                "population": 1300000,
                "lat": 32.78,
                "lon": -96.81,
            }
        ]
    )


def _airports():
    return [
        Airport("DFW", "DFW Airport", "", "US", 32.90, -97.04, "large_airport", True),
        Airport("DAL", "DAL Airport", "", "US", 32.85, -96.85, "large_airport", True),
    ]


def test_a_pop_page_gives_each_site_its_name_code_and_point():
    from ocm_regions.analysis.heldout import parse_pops

    assert parse_pops(POPS) == [("Dallas", "DFW", (32.78, -96.80)), ("Atlantis", "ZZZ", (1.0, 1.0))]


def test_a_region_page_gives_each_identifier_and_the_city_the_vendor_states():
    from ocm_regions.analysis.heldout import parse_regions

    assert parse_regions(REGIONS) == [("na-central-2", "Dallas"), ("na-east-1", "Washington, D.C.")]


def test_a_zone_page_gives_each_city_once_for_each_code_it_carries():
    from ocm_regions.analysis.heldout import parse_zones

    assert parse_zones(ZONES) == [("Dallas", "DFW", None), ("Atlantis", "ZZZ", None)]


def test_sites_are_placed_by_the_label_rule_and_scored_on_distance_and_code():
    from ocm_regions.analysis.heldout import compare_sites

    sites = [("Dallas", "DFW", (32.78, -96.80)), ("Atlantis", "ZZZ", (1.0, 1.0))]
    rows, summary = compare_sites(sites, _gazetteer(), _airports(), {"DFW": 0.9, "DAL": 0.1}, cap=90.0)
    assert [
        (r["label"], r["placed_city"], r["within_50_km"], r["nearest_airport"], r["major_hub"]) for r in rows
    ] == [
        ("Dallas", "Dallas", "yes", "DAL", "DFW"),
        ("Atlantis", "", "", "", ""),
    ]
    assert (summary["sites"], summary["placed"], summary["with_point"], summary["within_50_km"]) == (
        2,
        1,
        1,
        1,
    )
    assert (summary["nearest_airport"], summary["major_hub"], summary["nearby_hub"]) == (0, 1, 1)


def test_sites_without_a_point_are_scored_on_code_alone():
    from ocm_regions.analysis.heldout import compare_sites

    rows, summary = compare_sites(
        [("Dallas", "DFW", None)], _gazetteer(), _airports(), {"DFW": 0.9}, cap=90.0
    )
    assert rows[0]["km"] == "" and rows[0]["within_50_km"] == ""
    assert (summary["with_point"], summary["within_50_km"], summary["major_hub"]) == (0, 0, 1)


def test_regions_are_placed_from_the_label_the_vendor_prints():
    from ocm_regions.analysis.heldout import place_labels

    rows, summary = place_labels([("na-central-2", "Dallas"), ("xx-none-1", "Atlantis")], _gazetteer())
    assert [(r["region_id"], r["stated_city"], r["placed_city"], r["country"]) for r in rows] == [
        ("na-central-2", "Dallas", "Dallas", "US"),
        ("xx-none-1", "Atlantis", "", ""),
    ]
    assert summary == {"regions": 2, "placed": 1, "same_name": 1}


def test_macros_for_the_paper_have_letter_names():
    from ocm_regions.analysis.heldout import heldout_macros

    codes = {
        "sites": 2,
        "placed": 1,
        "with_point": 1,
        "within_50_km": 1,
        "nearest_airport": 0,
        "major_hub": 1,
        "nearby_hub": 1,
        "operator_code_is_airport": 1,
    }
    macros = heldout_macros(
        {"fastly": codes, "zenlayerZones": codes}, {"regions": 2, "placed": 1, "same_name": 1}
    )
    assert macros["fastlySites"] == 2 and macros["fastlyNearShare"] == "100"
    assert macros["zenlayerZonesNearbyHub"] == 1 and macros["zenlayerRegionsPlaced"] == 1
    assert all(name.isalpha() for name in macros)


def test_the_model_is_asked_with_the_identifier_alone_and_scored_against_the_stated_city():
    from ocm_regions.analysis.heldout import ask_memory

    class Recalls:
        def __init__(self):
            self.asked = []

        def chat(self, prompt):
            self.asked.append(prompt)
            return '{"city": "Dallas", "country": "US"}' if "na-central-2" in prompt else '{"city": null}'

    client = Recalls()
    rows, summary = ask_memory([("na-central-2", "Dallas"), ("na-east-9", "Dallas")], client, _gazetteer())
    assert not [prompt for prompt in client.asked if "Dallas" in prompt]
    assert [(r["region_id"], r["status"], r["answer_city"], r["within_50_km"]) for r in rows] == [
        ("na-central-2", "answered", "Dallas", "yes"),
        ("na-east-9", "abstained", "", ""),
    ]
    assert summary == {"regions": 2, "answered": 1, "within_50_km": 1}


def test_memory_macros_appear_only_when_the_model_was_asked():
    from ocm_regions.analysis.heldout import heldout_macros

    labels = {"regions": 2, "placed": 1, "same_name": 1}
    assert "zenlayerMemoryAnswered" not in heldout_macros({}, labels)
    macros = heldout_macros({}, labels, {"regions": 2, "answered": 1, "within_50_km": 1})
    assert (macros["zenlayerMemoryAnswered"], macros["zenlayerMemoryNear"]) == (1, 1)
