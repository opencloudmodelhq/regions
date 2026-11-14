# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from ocm_regions.place.metro import metro_for_airport, parse_optd

HEADER = "iata_code^location_type^city_code_list\n"


def test_parse_keeps_city_code_list_for_airports():
    table = parse_optd(HEADER + "HND^A^TYO\nTYO^C^TYO\n")
    assert table["HND"] == ["TYO"]
    assert "TYO" not in table


def test_metro_is_a_city_code_other_than_the_airport():
    assert metro_for_airport("HND", {"HND": ["TYO"]}) == "TYO"


def test_airport_sharing_its_city_code_has_no_metro():
    assert metro_for_airport("GVA", {"GVA": ["GVA"]}) == ""


def test_airport_with_several_cities_uses_the_first_listed():
    assert metro_for_airport("LHR", {"LHR": ["LON", "XXX"]}) == "LON"


def test_unknown_airport_has_no_metro():
    assert metro_for_airport("ZZZ", {}) == ""
