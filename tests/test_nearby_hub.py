# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0


def test_capped_airport_keeps_a_hub_at_the_cap_and_falls_back_beyond_it():
    from ocm_regions.common.geo import capped_airport

    assert capped_airport("HUB", 100.0, "NEA", cap=100) == "HUB"
    assert capped_airport("HUB", 100.1, "NEA", cap=100) == "NEA"


def test_coverage_reports_table_c():
    from ocm_regions.tables.coverage import scheme_coverage

    row = {
        "iata_nearest_airport": "A",
        "iata_major_hub": "B",
        "iata_nearby_hub": "C",
        "iata_metro": "",
        "unlocode": "",
        "clli": "",
    }
    assert scheme_coverage([row])["iata_nearby_hub"]["regions"] == 1


def test_macros_report_table_c_differences():
    from ocm_regions.publish.paper import table_c_macros

    regions = [
        {"iata_nearest_airport": "A", "iata_major_hub": "B", "iata_nearby_hub": "B"},
        {"iata_nearest_airport": "A", "iata_major_hub": "B", "iata_nearby_hub": "A"},
        {"iata_nearest_airport": "", "iata_major_hub": "", "iata_nearby_hub": ""},
    ]
    assert table_c_macros(regions) == {"tableDiffAC": 1, "tableDiffBC": 1}


def test_capped_airport_has_no_fixed_default_cap():
    import inspect

    from ocm_regions.common.geo import capped_airport

    assert inspect.signature(capped_airport).parameters["cap"].default is inspect.Parameter.empty


def sample_rows():
    return [
        {
            "provider": "aws",
            "region_id": "us_east",
            "city": "Ashburn",
            "country": "US",
            "grade": "vendor-stated",
            "code_nearest_airport": "iad",
            "code_major_hub": "iad",
            "code_nearby_hub": "iad",
            "iata_major_hub": "IAD",
            "iata_nearby_hub": "IAD",
        },
        {
            "provider": "gcp",
            "region_id": "west4",
            "city": "Eemshaven",
            "country": "NL",
            "grade": "vendor-stated",
            "code_nearest_airport": "grq",
            "code_major_hub": "ams",
            "code_nearby_hub": "grq",
            "iata_major_hub": "AMS",
            "iata_nearby_hub": "GRQ",
        },
        {
            "provider": "aws",
            "region_id": "gov",
            "city": "",
            "country": "",
            "grade": "assumed",
            "code_nearest_airport": "",
            "code_major_hub": "",
            "code_nearby_hub": "",
            "iata_major_hub": "",
            "iata_nearby_hub": "",
        },
    ]


def test_code_table_has_a_row_per_region_and_repeats_its_header():
    from ocm_regions.publish.paper import code_table

    text = code_table(sample_rows())
    assert r"\begin{longtable}" in text and r"\endhead" in text
    assert text.count(r"\\" + "\n") >= 4 and "us\\_east" in text


def test_code_table_marks_a_missing_code_and_a_change_from_the_hub():
    from ocm_regions.publish.paper import code_table

    text = code_table(sample_rows())
    assert "gov & -- & -- & assumed & -- & -- & --" in text
    assert r"\textbf{grq}" in text and r"\textbf{iad}" not in text


def test_scheme_labels_name_each_rule_without_a_fixed_distance():
    from ocm_regions.tables.report import SCHEME_LABELS

    assert (
        SCHEME_LABELS["iata_nearby_hub"]
        == "IATA airport, major hub within the cap, otherwise nearest (nearby hub)"
    )
    assert not any("Table" in label or "100 km" in label for label in SCHEME_LABELS.values())


def test_region_rows_say_whether_the_nearby_hub_is_a_large_or_a_medium_airport():
    from ocm_regions.common.geo import Airport
    from ocm_regions.tables.build import REGION_FIELDS, hub_class

    assert (
        "hub_class" in REGION_FIELDS
        and REGION_FIELDS.index("hub_class") == REGION_FIELDS.index("code_nearby_hub") + 1
    )
    assert hub_class(Airport("IAD", "Dulles", "", "US", 38.9, -77.4, "large_airport", True)) == "large"
    assert hub_class(Airport("JYO", "Leesburg", "", "US", 39.0, -77.5, "medium_airport", True)) == "medium"
