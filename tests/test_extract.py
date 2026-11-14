# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from ocm_regions.evidence.extract import document_lines, extract_from_text
from ocm_regions.place.placer import Gazetteer
from ocm_regions.regions.parsers import Region

GAZ = Gazetteer(
    cities=[
        {
            "name": "Ashburn",
            "country": "US",
            "admin1": "VA",
            "population": 43000,
            "lat": 39.04,
            "lon": -77.49,
        },
        {
            "name": "Tokyo",
            "country": "JP",
            "admin1": "40",
            "population": 8000000,
            "lat": 35.69,
            "lon": 139.69,
        },
    ]
)
REGION = Region("aws", "us-east-1", "US East", "US East")


def test_table_rows_become_single_lines():
    assert document_lines("<table><tr><td>us-east-1</td><td>Ashburn</td></tr></table>") == [
        "us-east-1 Ashburn"
    ]


def test_a_city_on_the_same_line_as_the_region_is_extracted_with_its_quote():
    found = extract_from_text(REGION, "intro\nus-east-1 is in Ashburn today\nend", GAZ, "doc.txt")
    assert [(f["city"], f["quote"], f["document"]) for f in found] == [
        ("Ashburn", "us-east-1 is in Ashburn today", "doc.txt")
    ]


def test_a_city_elsewhere_in_the_document_is_ignored():
    assert extract_from_text(REGION, "us-east-1 is big\nparagraph\nparagraph\nTokyo office", GAZ, "d") == []


def test_a_region_code_inside_a_longer_word_does_not_count():
    assert extract_from_text(Region("a", "ams", "x", "x"), "streams in Tokyo", GAZ, "d") == []


def test_extraction_is_repeatable_and_sorted():
    text = "us-east-1 Tokyo Ashburn"
    assert extract_from_text(REGION, text, GAZ, "d") == extract_from_text(REGION, text, GAZ, "d")
    assert [f["city"] for f in extract_from_text(REGION, text, GAZ, "d")] == ["Ashburn", "Tokyo"]


def test_a_whole_file_on_one_line_is_not_treated_as_a_table_row():
    long_line = "us-east-1 " + "Tokyo " + "filler " * 200
    assert extract_from_text(REGION, long_line, GAZ, "d") == []


def test_a_city_must_appear_capitalised_as_a_name():
    assert extract_from_text(REGION, "us-east-1 ashburn lowercase", GAZ, "d") == []
    assert [f["city"] for f in extract_from_text(REGION, "us-east-1 ASHBURN site", GAZ, "d")] == ["Ashburn"]


def test_very_short_place_names_are_skipped():
    gaz = Gazetteer(
        cities=[
            {"name": "Aba", "country": "NG", "admin1": "45", "population": 900000, "lat": 5.1, "lon": 7.3}
        ]
    )
    assert extract_from_text(REGION, "us-east-1 Aba site", gaz, "d") == []
