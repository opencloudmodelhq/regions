# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from ocm_regions.tables.calls import Call, calls_for_region
from ocm_regions.tables.grades import best_grade, grade_of

MS = {"source_type": "vendor", "stance": "supports", "claim": "city"}


def test_grade_ranks_vendor_region_statement_above_document():
    assert best_grade([{"source_type": "vendor_region_list", "stance": "supports"}, MS]) == "vendor-stated"


def test_grade_uses_vendor_document_when_no_statement():
    assert best_grade([MS]) == "vendor-document"


def test_certification_counts_as_audit_document():
    assert grade_of({"source_type": "certification", "stance": "supports"}) == "audit-document"


def test_news_grades_as_reported():
    assert best_grade([{"source_type": "news", "stance": "supports"}]) == "reported"


def test_contradicting_evidence_does_not_raise_grade():
    assert best_grade([{"source_type": "vendor", "stance": "contradicts"}]) == "assumed"


def test_no_evidence_is_assumed():
    assert best_grade([]) == "assumed"


def base_region(**extra):
    return {
        "provider": "aws",
        "region_id": "r",
        "grade": "assumed",
        "unlocode": "USQAS",
        "iata_nearest_airport": "IAD",
        "iata_major_hub": "IAD",
        "airport_km": 12.0,
        "vendor_code": "",
        "shared_with": "",
        "conflict": False,
    } | extra


def test_assumed_city_raises_a_call():
    assert "city-assumed" in {c.kind for c in calls_for_region(base_region())}


def test_vendor_stated_city_raises_no_city_call():
    assert "city-assumed" not in {c.kind for c in calls_for_region(base_region(grade="vendor-stated"))}


def test_missing_unlocode_raises_gap():
    assert "gap-unlocode" in {c.kind for c in calls_for_region(base_region(unlocode=""))}


def test_tables_disagreeing_raises_call():
    kinds = {c.kind for c in calls_for_region(base_region(iata_nearest_airport="PSC", iata_major_hub="PDX"))}
    assert "nearest-differs-from-hub" in kinds


def test_vendor_code_that_is_another_airport_raises_conflict():
    calls = calls_for_region(base_region(vendor_code="LEJ", vendor_code_note="Leipzig/Halle Airport (DE)"))
    assert any(c.kind == "vendor-code-conflict" and "Leipzig" in c.detail for c in calls)


def test_conflicting_evidence_raises_call():
    assert "evidence-conflict" in {c.kind for c in calls_for_region(base_region(conflict=True))}


def test_call_has_severity():
    assert Call("gap-iata", "x").severity == "high"


def test_peeringdb_never_grades_above_network_presence():
    assert grade_of({"source_type": "peeringdb", "stance": "supports"}) == "network-presence"


def _row(region, grade):
    return {
        "provider": "a",
        "region_id": region,
        "stated_location": "S",
        "city": "C",
        "country": "US",
        "grade": grade,
        "precision": "metro",
        "location_reasoning": "because",
    }


def test_weak_regions_are_ranked_weakest_first_and_strong_ones_left_out():
    from ocm_regions.tables.grades import weak_regions

    rows = [
        _row("feed", "vendor-feed"),
        _row("none", "assumed"),
        _row("press", "reported"),
        _row("part", "partial"),
        _row("ok", "vendor-stated"),
    ]
    assert [r["region_id"] for r in weak_regions(rows, [], [])] == ["none", "part", "press", "feed"]


def test_a_strongly_graded_region_with_a_source_conflict_is_listed():
    from ocm_regions.tables.grades import weak_regions

    calls = [
        {"provider": "a", "region_id": "ok", "kind": "feed-conflict", "detail": "Feed says Taipei."},
        {"provider": "a", "region_id": "ok", "kind": "shared-airport", "detail": "x"},
    ]
    listed = weak_regions([_row("ok", "vendor-stated")], calls, [])
    assert (
        listed[0]["tier"] == "disputed"
        and "Feed says Taipei." in listed[0]["flags"]
        and "x" not in listed[0]["flags"].split("; ")
    )


def test_a_failed_quote_check_is_flagged_on_a_weak_region():
    from ocm_regions.tables.grades import weak_regions

    evidence = [
        {"provider": "a", "region_id": "part", "quote_check": "unreachable"},
        {"provider": "a", "region_id": "part", "quote_check": "quote-found"},
    ]
    assert "1 quote failed its check" in weak_regions([_row("part", "partial")], [], evidence)[0]["flags"]


JOIN = {
    "source_type": "code_join",
    "stance": "supports",
    "join_code": "DUB06",
    "quote": "seven datacentres, Dub 6, 7, 8, 9, 10, 12 and 13",
}
CERTIFICATE_CODE = {"source_type": "certification", "stance": "partial", "quote": "DUB06"}


def test_a_code_in_both_an_audit_list_and_a_public_record_earns_the_public_record_grade():
    assert best_grade([JOIN, CERTIFICATE_CODE]) == "public-record"


def test_a_public_record_without_the_code_on_an_audit_list_stays_partial():
    assert best_grade([JOIN, {**CERTIFICATE_CODE, "quote": "DUB07"}]) == "partial"


def test_a_public_record_whose_quote_lacks_the_code_stays_partial():
    assert best_grade([{**JOIN, "join_code": "DUB11"}, {**CERTIFICATE_CODE, "quote": "DUB11"}]) == "partial"


def test_a_shorter_code_does_not_match_inside_a_longer_one():
    record = {**JOIN, "join_code": "BN1", "quote": "buildings BN13 and BN14"}
    assert best_grade([record, {**CERTIFICATE_CODE, "quote": "BN1"}]) == "partial"


def test_a_failed_quote_check_stops_the_public_record_grade():
    assert best_grade([{**JOIN, "quote_check": "also-not-found"}, CERTIFICATE_CODE]) == "partial"


def test_the_public_record_grade_ranks_below_audit_documents_and_above_feeds():
    from ocm_regions.tables.grades import GRADES

    assert GRADES.index("audit-document") < GRADES.index("public-record") < GRADES.index("vendor-feed")


def _coded(precision="city", grade="vendor-stated", b="HUB", c="HUB"):
    return {"precision": precision, "grade": grade, "iata_major_hub": b, "iata_nearby_hub": c}


def test_a_stated_city_with_strong_evidence_is_an_exact_city():
    from ocm_regions.tables.build import area_basis

    assert area_basis(_coded()) == "exact-city"


def test_a_region_without_a_clear_city_takes_the_metro_hub():
    from ocm_regions.tables.build import area_basis

    assert area_basis(_coded(precision="metro")) == "metro-hub"
    assert area_basis(_coded(precision="province")) == "metro-hub"


def test_a_city_with_weak_evidence_takes_the_metro_hub():
    from ocm_regions.tables.build import area_basis

    for grade in ("vendor-feed", "reported", "network-presence", "partial", "assumed"):
        assert area_basis(_coded(grade=grade)) == "metro-hub"


def test_a_hub_beyond_the_cap_falls_back_to_the_nearest_airport_and_says_so():
    from ocm_regions.tables.build import area_basis

    assert area_basis(_coded(precision="metro", c="NEAR")) == "nearest-hub-too-far"


def test_an_exact_city_stays_exact_when_its_hub_is_beyond_the_cap():
    from ocm_regions.tables.build import area_basis

    assert area_basis(_coded(c="NEAR")) == "exact-city"
