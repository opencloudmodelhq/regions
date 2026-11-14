# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Public-record joins are derived from the corpus: an audit list ties a region to a code, a government record ties that code to a place."""

from types import SimpleNamespace

from ocm_regions.evidence.joins import audit_lists, code_key, derive_joins, named_codes

CERT = """Scope
Central US Region
DM2
DSM09
East US 2 Region
BN1
BN2
LVL01
West US2 Region
CO1
CO2
Other text that ends the list and is long enough
"""


def test_a_code_key_ignores_zero_padding():
    assert code_key("DUB06") == ("DUB", 6) and code_key("BN1") == ("BN", 1)


def test_a_code_with_no_number_has_no_key():
    assert code_key("BLU") is None


def test_audit_lists_read_codes_under_each_region_heading():
    lists = audit_lists(CERT)
    assert lists["Central US"] == ["DM2", "DSM09"] and lists["East US 2"] == ["BN1", "BN2", "LVL01"]


def test_a_list_ends_at_ordinary_text():
    assert audit_lists(CERT)["West US2"] == ["CO1", "CO2"]


def test_named_codes_expand_ranges_and_lists():
    found = named_codes("buildings (BN1-BN4, BN6-BN8, and BN14) and Leesburg (IAD01-02-03)")
    assert {("BN", n) for n in (1, 2, 3, 4, 6, 7, 8, 14)} <= found and {
        ("IAD", 1),
        ("IAD", 2),
        ("IAD", 3),
    } <= found


def test_named_codes_read_spaced_lists_and_dutch_ranges():
    assert {("DUB", 6), ("DUB", 7), ("DUB", 13)} <= named_codes("seven datacentres, Dub 6, 7, 10, 12 and 13")
    assert {("AMS", 5), ("AMS", 7), ("AMS", 9), ("AMS", 12)} <= named_codes("AMS 5 t/m 9, AMS 11 en AMS 12")


def test_a_shorter_code_is_not_found_inside_a_longer_one():
    assert ("BN", 1) not in named_codes("building BN13")


def test_a_chemical_formula_is_not_a_facility_code():
    assert ("CO", 2) not in named_codes("emissions of CO2 and CO2e are limited")
    assert ("CO", 2) in named_codes("Phase CO2/1 generators")


def _gazetteer():
    def entry(name, lat, lon, pop):
        return SimpleNamespace(name=name, country="US", lat=lat, lon=lon, population=pop)

    return SimpleNamespace(
        index={
            "boydton": [entry("Boydton", 36.67, -78.39, 416)],
            "richmond": [entry("Richmond", 37.55, -77.46, 226610)],
            "seattle": [entry("Seattle", 47.6, -122.3, 700000)],
        },
        areas={},
    )


REGION = SimpleNamespace(provider="azure", region_id="eastus2", name="East US 2")
PLACED = {"lat": "36.66764", "lon": "-78.3875", "country": "US"}
DOC = {
    "url": "https://a.test/permit",
    "title": "Air permit",
    "publisher": "Virginia DEQ",
    "published": "2023-09-05",
    "text": "Microsoft Corporation engine sets at LVL01 and BN1, Boydton, VA. Send mail to the Richmond office.",
}
AUDIT = {
    "url": "https://a.test/cert",
    "title": "Certificate",
    "publisher": "GSMA",
    "text": CERT,
    "valid_until": "2025-06-30",
}


def _joins(doc=DOC, operator="Microsoft", placed=PLACED):
    return derive_joins(REGION, placed, AUDIT, ["BN1", "BN2", "LVL01"], [doc], operator, _gazetteer())


def test_a_code_on_the_audit_list_and_in_a_government_record_makes_a_join():
    join = next(r for r in _joins()["records"] if r["source_type"] == "code_join")
    assert join["value"] == "Boydton" and join["join_code"] in ("BN1", "LVL01") and "2 of 3" in join["note"]
    assert join["also_found"] == "Microsoft|Boydton" and join["url"] == DOC["url"]


def test_each_join_has_a_matching_code_record_from_the_audit_list():
    records = _joins()["records"]
    join = next(r for r in records if r["source_type"] == "code_join")
    assert any(
        r["source_type"] == "certification" and r["quote"] == join["join_code"] and r["stance"] == "partial"
        for r in records
    )


def test_a_record_that_does_not_name_the_operator_makes_no_join():
    assert not [r for r in _joins(operator="Amazon")["records"] if r["source_type"] == "code_join"]


def test_a_city_outside_the_area_is_reported_and_makes_no_join():
    doc = {**DOC, "text": "Microsoft Corporation engine sets at LVL01, Seattle, WA."}
    result = _joins(doc)
    assert not [r for r in result["records"] if r["source_type"] == "code_join"]
    assert result["report"][0]["status"] == "outside-area" and result["report"][0]["city"] == "Seattle"


def test_a_region_on_the_list_with_no_public_record_keeps_a_code_record():
    result = _joins({**DOC, "text": "Nothing relevant from Microsoft here."})
    assert [r["quote"] for r in result["records"]] == ["BN1"]


def test_a_quote_leaves_out_a_street_address():
    doc = {**DOC, "text": "Microsoft Corporation LVL01 at 960 Prison Road Boydton, VA 23917 has generators."}
    join = next(r for r in _joins(doc)["records"] if r["source_type"] == "code_join")
    assert "Prison Road" not in join["quote"] and "LVL01" in join["quote"]


def test_a_quote_carries_nothing_after_the_code():
    doc = {**DOC, "text": "Facility MICROSOFT LVL01 DATA CENTER 5855 SW KERRY ST, Boydton"}
    join = next(r for r in _joins(doc)["records"] if r["source_type"] == "code_join")
    assert "KERRY" not in join["quote"] and join["quote"].endswith("LVL01")
