# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""A label naming a place the town list lacks is placed from a vendor sentence that locates that place."""

from ocm_regions.place.placer import Gazetteer
from ocm_regions.regions.parsers import Region

CITIES = [
    {"name": "Kragujevac", "country": "RS", "admin1": "", "population": 147473, "lat": 44.01, "lon": 20.92},
    {"name": "Belgrade", "country": "RS", "admin1": "", "population": 1200000, "lat": 44.8, "lon": 20.5},
    {"name": "Novi Sad", "country": "RS", "admin1": "", "population": 250000, "lat": 45.25, "lon": 19.84},
    {"name": "Valley", "country": "US", "admin1": "AL", "population": 9000, "lat": 32.8, "lon": -85.2},
    {"name": "Ashburn", "country": "US", "admin1": "VA", "population": 43000, "lat": 39.0, "lon": -77.5},
    {"name": "Budapest", "country": "HU", "admin1": "", "population": 1700000, "lat": 47.5, "lon": 19.0},
]
SERBIA = Region("oracle", "eu-jovanovac-1", "Serbia Central (Jovanovac)", "Jovanovac,Serbia", "BEG")
VALLEY = Region("alibaba", "us-west-1", "US (Silicon Valley)", "US (Silicon Valley)")
PLAYBOOK = "Oracle has opened the Oracle Cloud Jovanovac Region located in Kragujevac, Serbia. This region is unique among Oracle regions."


def gaz():
    return Gazetteer(
        CITIES, countries={"serbia": "RS", "us": "US", "hungary": "HU"}, capitals={"RS": "Belgrade"}
    )


def test_a_label_place_the_town_list_lacks_is_an_unresolved_name():
    from ocm_regions.place.named_place import unresolved_names

    assert unresolved_names(SERBIA, gaz()) == ["Jovanovac"]
    assert (
        unresolved_names(Region("oracle", "eu-belgrade-1", "Serbia (Belgrade)", "Belgrade,Serbia"), gaz())
        == []
    )


def test_a_vendor_sentence_locating_the_label_place_gives_the_city():
    from ocm_regions.place.named_place import named_place

    assert named_place(SERBIA, [PLAYBOOK], gaz()).name == "Kragujevac"


def test_a_city_must_follow_a_locating_word_after_the_label_place():
    from ocm_regions.place.named_place import named_place

    assert (
        named_place(
            SERBIA, ["Kragujevac and Jovanovac are in Serbia. Jovanovac Region, Kragujevac office."], gaz()
        )
        is None
    )


def test_a_city_in_another_country_does_not_place_the_region():
    from ocm_regions.place.named_place import named_place

    assert (
        named_place(SERBIA, ["The Jovanovac Region is located in Budapest for some customers."], gaz())
        is None
    )


def test_two_different_cities_leave_the_region_unplaced():
    from ocm_regions.place.named_place import named_place

    texts = [PLAYBOOK, "The Jovanovac Region is located in Novi Sad, Serbia."]
    assert named_place(SERBIA, texts, gaz()) is None


def test_a_town_inside_the_label_place_name_is_not_its_city():
    from ocm_regions.place.named_place import named_place

    assert (
        named_place(
            VALLEY,
            ["Silicon Valley is located in Valley according to nobody. US (Silicon Valley) us-west-1"],
            gaz(),
        )
        is None
    )


def _rule(basis, city=""):
    return {
        "provider": "oracle",
        "region_id": "eu-jovanovac-1",
        "basis": basis,
        "cross_check": "",
        "city": city,
        "country": "",
        "lat": "",
        "lon": "",
        "label_city": "",
        "id_city": "",
        "feed_city": "",
        "from_capital": "",
        "split": "development",
    }


def test_placement_takes_the_located_city_before_memory_or_the_capital():
    from ocm_regions.place.placement import apply_named_places

    for basis in ("none", "memory"):
        placed = apply_named_places(
            [_rule(basis, "Belgrade" if basis != "none" else "")],
            [SERBIA],
            gaz(),
            lambda _provider: [PLAYBOOK],
        )[0]
        assert (placed["basis"], placed["city"], placed["country"], placed["lat"]) == (
            "document",
            "Kragujevac",
            "RS",
            44.01,
        )
        assert "town list lacks" in placed["basis_note"]


def test_placement_leaves_a_region_the_rules_placed_alone():
    from ocm_regions.place.placement import apply_named_places

    rule = _rule("label", "Belgrade")
    assert apply_named_places([rule], [SERBIA], gaz(), lambda _provider: [PLAYBOOK])[0] == rule


def test_every_basis_has_a_note_and_a_mapping_carries_basis_and_cross_check():
    from ocm_regions.place.placement import BASIS_NOTES, _mapping

    assert set(BASIS_NOTES) == {
        "label, agrees",
        "label",
        "label, conflicts",
        "document",
        "feed",
        "capital",
        "memory",
        "none",
    }
    mapping = _mapping(
        _rule("label", "Belgrade") | {"cross_check": "agrees", "label_city": "Belgrade"},
        {"place_id": "rs-belgrade"},
    )
    assert (mapping["basis"], mapping["cross_check"], mapping["note"]) == (
        "label",
        "agrees",
        BASIS_NOTES["label, agrees"],
    )
