# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Evidence read from the corpus by code: a quote must tie the region to a place, and only a place in the area supports it."""

from types import SimpleNamespace

from ocm_regions.evidence.scripted import means_region, region_records, scripted_record

REGION = SimpleNamespace(
    provider="aws", region_id="ca-central-1", name="Canada (Central)", stated_location="Canada (Central)"
)
PLACED = {"lat": "45.5", "lon": "-73.57", "country": "CA", "city": "Montreal"}
DOC = {
    "doc_id": "abc",
    "kind": "vendor",
    "publisher": "Amazon",
    "title": "Launch",
    "url": "https://a.test/launch",
}
ROW = {
    "city": "Montreal",
    "lat": "45.5",
    "lon": "-73.57",
    "quote": "new AWS Canada (Central) Region in Montreal, Quebec",
}


def test_a_quote_naming_the_region_and_a_city_means_the_region():
    assert means_region(ROW["quote"], REGION, "Montreal")


def test_a_quote_that_does_not_name_the_region_means_nothing():
    assert not means_region("AWS has offices in Montreal and Toronto", REGION, "Montreal")


def test_a_city_found_only_inside_the_region_id_means_nothing():
    region = SimpleNamespace(
        provider="tencent", region_id="na-ashburn", name="Virginia", stated_location="Virginia"
    )
    assert not means_region("Region na-ashburn is available in Virginia", region, "Ashburn")


def test_a_region_name_that_is_itself_the_city_proves_nothing():
    region = SimpleNamespace(provider="ovhcloud", region_id="WAW", name="Warsaw", stated_location="Warsaw")
    assert not means_region("Henryk Klaba graduated in Warsaw", region, "Warsaw")


def test_a_network_site_is_not_a_region_site():
    assert not means_region("Canada (Central) Direct Connect location in Montreal", REGION, "Montreal")
    assert not means_region("Canada (Central) Local Zone in Montreal", REGION, "Montreal")


def test_a_place_in_the_area_supports_the_region():
    record = scripted_record(ROW, DOC, REGION, PLACED, "quote-search")
    assert (
        record["stance"] == "supports" and record["source_type"] == "vendor" and record["value"] == "Montreal"
    )


def test_a_nearby_place_with_another_name_still_supports_the_area():
    row = {
        **ROW,
        "city": "Laval",
        "lat": "45.57",
        "lon": "-73.69",
        "quote": "new AWS Canada (Central) Region in Laval",
    }
    assert scripted_record(row, DOC, REGION, PLACED, "quote-search")["stance"] == "supports"


TORONTO = {
    **ROW,
    "city": "Toronto",
    "lat": "43.65",
    "lon": "-79.38",
    "quote": "new AWS Canada (Central) Region in Toronto",
}


def test_a_model_answer_outside_the_area_contradicts_and_moves_nothing():
    record = scripted_record(TORONTO, DOC, REGION, PLACED, "rag")
    assert record["stance"] == "contradicts" and record["value"] == "Toronto"


def test_a_quote_search_hit_outside_the_area_is_noise_not_a_contradiction():
    assert scripted_record(TORONTO, DOC, REGION, PLACED, "quote-search") is None


def test_a_short_quote_or_a_directory_gives_no_record():
    assert (
        scripted_record({**ROW, "quote": "Canada (Central) Montreal"[:15]}, DOC, REGION, PLACED, "rag")
        is None
    )
    assert scripted_record(ROW, {**DOC, "kind": "directory"}, REGION, PLACED, "rag") is None


def test_a_region_keeps_one_record_per_method_and_source_type():
    other = {**ROW, "quote": "the AWS Canada (Central) Region is located in Montreal today"}
    third = {**ROW, "quote": "AWS Canada (Central) Region opened in Montreal this year"}
    records = region_records(REGION, PLACED, ROW, [(other, DOC), (third, DOC)], {"abc": DOC}, "abc")
    assert [r["note"].split(":")[0] for r in records] == ["rag", "quote-search"]


def test_grades_without_the_model_keep_the_quote_search_record_it_stood_beside():
    from ocm_regions.evidence.scripted import method_grades

    other = {**ROW, "quote": "the AWS Canada (Central) Region is located in Montreal today"}
    records = region_records(REGION, PLACED, ROW, [(other, DOC)], {"abc": DOC}, "abc")
    assert method_grades(records)["regions_gaining"] == []


def test_a_page_about_network_sites_gives_no_record_whatever_the_quote_says():
    for doc in (
        {**DOC, "title": "AWS Direct Connect Locations"},
        {**DOC, "url": "https://a.test/directconnect/locations/"},
    ):
        assert scripted_record(ROW, doc, REGION, PLACED, "quote-search") is None


def test_a_build_reads_scripted_evidence_and_no_agent_records(tmp_path):
    import json

    from ocm_regions.tables.build import _derived_records

    (tmp_path / "joins.jsonl").write_text(json.dumps({"a": 1}) + "\n", encoding="utf-8")
    (tmp_path / "scripted_evidence.jsonl").write_text(json.dumps({"b": 2}) + "\n", encoding="utf-8")
    assert _derived_records(tmp_path) == [{"a": 1}, {"b": 2}]


def test_a_region_with_no_model_answer_still_gets_a_quote_search_record():
    assert region_records(REGION, PLACED, None, [(ROW, DOC)], {"abc": DOC}, "")[0]["note"].startswith(
        "quote-search"
    )


def test_a_short_code_id_counts_only_as_an_exact_capitalised_word():
    region = SimpleNamespace(provider="ovhcloud", region_id="ERI", name="London", stated_location="London")
    assert means_region("Erith (ERI), United Kingdom", region, "Erith")
    assert not means_region("Erith is near the eri river", region, "Erith")


def test_a_small_town_is_found_when_a_line_names_the_region():
    from types import SimpleNamespace as N

    from ocm_regions.evidence.extract import extract_from_text

    town = N(name="Beauharnois", country="CA", lat=45.3, lon=-73.9, population=12000, admin1="10")
    gaz = N(index={"beauharnois": [town]}, areas={}, countries={}, states={})
    region = N(provider="ovhcloud", region_id="BHS", name="Montreal", stated_location="Montreal")
    rows = extract_from_text(region, "<p>BHS Beauharnois, Canada</p>", gaz, "doc")
    assert [r["city"] for r in rows] == ["Beauharnois"]


def test_a_quote_holding_a_street_address_is_cut_back_to_the_city():
    row = {**ROW, "quote": "Canada (Central) Region in Montreal at 1801 McGill College Avenue"}
    assert scripted_record(row, DOC, REGION, PLACED, "quote-search")["quote"] == "Montreal"


def test_adding_a_document_changes_only_the_region_it_names():
    from ocm_regions.evidence.scripted import scripted_evidence
    from ocm_regions.tables.grades import best_grade

    paris = SimpleNamespace(
        provider="aws", region_id="eu-west-3", name="Europe (Paris)", stated_location="Europe (Paris)"
    )
    placed = {
        ("aws", "ca-central-1"): PLACED,
        ("aws", "eu-west-3"): {"lat": "48.85", "lon": "2.35", "country": "FR", "city": "Paris"},
    }
    found = {("aws", "ca-central-1"): [(ROW, DOC)]}
    before = scripted_evidence([REGION, paris], placed, {}, {}, {})
    after = scripted_evidence([REGION, paris], placed, {}, found, {})
    assert before == [] and [r["region_id"] for r in after] == ["ca-central-1"]
    assert best_grade(after) == "vendor-document"


def test_method_grades_show_what_the_model_adds():
    from ocm_regions.evidence.scripted import method_grades

    records = [
        {
            "provider": "a",
            "region_id": "r",
            "source_type": "vendor_region_list",
            "stance": "partial",
            "note": "",
        },
        {
            "provider": "a",
            "region_id": "r",
            "source_type": "vendor",
            "stance": "supports",
            "note": "rag: read",
        },
        {
            "provider": "a",
            "region_id": "s",
            "source_type": "vendor",
            "stance": "supports",
            "note": "quote-search: read",
        },
    ]
    result = method_grades(records)
    assert result["with_model"]["vendor-document"] == 2 and result["without_model"]["vendor-document"] == 1
    assert result["regions_gaining"] == [["a", "r"]]


def test_a_line_both_methods_read_gives_one_record_credited_to_the_code():
    records = region_records(REGION, PLACED, ROW, [(ROW, DOC)], {"abc": DOC}, "abc")
    assert len(records) == 1
    assert (
        records[0]["note"].startswith("quote-search:") and "model chose the same line" in records[0]["note"]
    )


def test_a_state_name_the_model_gives_as_a_city_is_not_a_contradiction():
    from ocm_regions.evidence.scripted import scripted_evidence

    west = SimpleNamespace(
        provider="azure", region_id="westus2", name="West US 2", stated_location="Washington"
    )
    placed = {("azure", "westus2"): {"lat": "47.23", "lon": "-119.85", "country": "US", "city": "Quincy"}}
    answer = {
        "city": "Washington",
        "lat": "38.9",
        "lon": "-77.04",
        "quote": "West US 2 Washington All customers and partners",
        "document": "abc",
    }
    answers = {("azure", "westus2"): answer}
    kept = scripted_evidence([west], placed, answers, {}, {"abc": DOC})
    assert [r["stance"] for r in kept] == ["contradicts"]
    assert scripted_evidence([west], placed, answers, {}, {"abc": DOC}, {"washington": "US"}) == []
