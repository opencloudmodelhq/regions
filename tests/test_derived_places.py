# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Every place, hub and rival comes from code, or from a frozen model prompt at temperature 0."""

from types import SimpleNamespace

from ocm_regions.common.geo import Airport
from ocm_regions.place.placer import Gazetteer, label_city

CITIES = [
    {
        "name": "Mexico City",
        "country": "MX",
        "admin1": "09",
        "population": 12000000,
        "lat": 19.4,
        "lon": -99.1,
    },
    {"name": "Singapore", "country": "SG", "admin1": "", "population": 5000000, "lat": 1.3, "lon": 103.8},
    {"name": "Belgrade", "country": "RS", "admin1": "", "population": 1200000, "lat": 44.8, "lon": 20.5},
    {"name": "Valley", "country": "US", "admin1": "CA", "population": 900, "lat": 33.0, "lon": -85.0},
    {"name": "San Jose", "country": "US", "admin1": "CA", "population": 1000000, "lat": 37.3, "lon": -121.9},
    {"name": "Ashburn", "country": "US", "admin1": "VA", "population": 43000, "lat": 39.0, "lon": -77.5},
]


def gaz():
    return Gazetteer(
        CITIES,
        aliases={"Mexico": "Mexico City"},
        areas={"california": "US", "virginia": "US", "san jose": "CR"},
        countries={"mexico": "MX", "singapore": "SG", "serbia": "RS", "us": "US"},
        states={"california": "CA", "ca": "CA"},
        capitals={"MX": "Mexico City", "RS": "Belgrade"},
    )


def test_a_country_word_in_a_label_is_not_the_city_it_shares_a_name_with():
    assert label_city("Mexico (Central)", gaz()) is None


def test_a_city_state_label_still_names_its_city():
    assert label_city("Singapore", gaz()).name == "Singapore"


def test_a_country_after_a_comma_gives_the_capital_when_the_town_is_unknown():
    from ocm_regions.place.placer import capital_of_tail

    assert capital_of_tail("Jovanovac,Serbia", gaz()).name == "Belgrade"


def test_one_word_of_an_area_name_is_not_a_city():
    assert label_city("Silicon Valley, California", gaz()) is None


def test_a_model_answer_with_a_country_accepts_a_namesake_of_a_foreign_province():
    from ocm_regions.model.llm import judge

    assert judge({"city": "San Jose", "country": "US"}, "", gaz(), False)["status"] == "answered"


REGION = SimpleNamespace(
    provider="aws", region_id="ca-central-1", name="Canada (Central)", stated_location="Canada (Central)"
)
DOC = {
    "doc_id": "d",
    "kind": "vendor",
    "publisher": "Amazon",
    "title": "Launch",
    "url": "https://a.test/launch",
}


def rule(basis, city="Feedtown", label=""):
    return {
        "provider": "aws",
        "region_id": "ca-central-1",
        "basis": basis,
        "cross_check": "",
        "city": city,
        "country": "CA",
        "lat": 45.0,
        "lon": -73.0,
        "label_city": label,
        "id_city": "",
        "feed_city": "",
        "split": "development",
    }


def answer(city, status="answered", quote="new AWS Canada (Central) Region in Montreal, Quebec"):
    return {
        "provider": "aws",
        "region_id": "ca-central-1",
        "status": status,
        "city": city,
        "country": "CA",
        "lat": 45.5,
        "lon": -73.6,
        "quote": quote,
        "document": "d",
    }


def apply(row, rag=(), memory=()):
    from ocm_regions.place.placement import apply_model_answers

    return apply_model_answers([row], list(rag), list(memory), [REGION], {"d": DOC}, {"virginia": "US"})[0]


def test_a_checked_answer_replaces_a_feed_guess():
    row = apply(rule("feed"), [answer("Montreal")])
    assert (row["city"], row["basis"]) == ("Montreal", "document")


def test_a_vendor_label_stays_ahead_of_a_checked_answer():
    assert apply(rule("label", "Toronto", "Toronto"), [answer("Montreal")])["city"] == "Toronto"


def test_an_answer_naming_a_us_state_is_not_a_city():
    quote = "new AWS Canada (Central) Region in Virginia, USA"
    row = apply(rule("feed"), [answer("Virginia", quote=quote)])
    assert row["city"] == "Feedtown"


def test_memory_only_fills_a_region_nothing_else_placed():
    assert apply(rule("none", ""), memory=[answer("Montreal", quote="")])["basis"] == "memory"
    assert apply(rule("feed"), memory=[answer("Montreal", quote="")])["city"] == "Feedtown"


def _airport(code, lat, lon, kind="large_airport"):
    return Airport(
        code=code, name=code, municipality="", kind=kind, country="US", lat=lat, lon=lon, scheduled=True
    )


def test_a_place_takes_its_codes_from_the_sources():
    from ocm_regions.place.placement import generated_place

    airports = [
        _airport("IAD", 38.95, -77.45),
        _airport("JFK", 40.6, -73.8),
        _airport("HEF", 38.72, -77.52, "medium_airport"),
    ]
    locodes = {
        "USASH": {
            "Country": "US",
            "Location": "ASH",
            "Name": "Ashburn",
            "NameWoDiacritics": "Ashburn",
            "Coordinates": "",
        }
    }
    facilities = [
        {"city": "Ashburn", "country": "US", "clli": "ASBNVA01"},
        {"city": "Ashburn", "country": "US", "clli": "ASBNVA"},
        {"city": "Reston", "country": "US", "clli": "RSTNVA"},
    ]
    place = generated_place(
        rule("label", "Ashburn") | {"country": "US", "lat": 39.0, "lon": -77.5},
        airports,
        {"IAD": 0.5, "JFK": 0.9, "HEF": 0.1},
        locodes,
        facilities,
    )
    assert (place["hub_iata"], place["unlocode"], place["clli"]) == ("IAD", "USASH", "ASBNVA")


def test_a_feed_city_in_capitals_reads_as_a_name():
    from ocm_regions.place.placement import generated_place

    place = generated_place(
        rule("feed", "CAMPINAS") | {"country": "US", "lat": 39.0, "lon": -77.5},
        [_airport("IAD", 38.95, -77.45)],
        {"IAD": 0.5},
        {},
        [],
    )
    assert place["city"] == "Campinas"


def test_short_feed_region_names_resolve_by_rule():
    from ocm_regions.place.feeds import feed_region_id

    ids = [
        "germanynorth",
        "germanywestcentral",
        "francecentral",
        "brazilsoutheast",
        "chilecentral",
        "norwaywest",
    ]
    assert feed_region_id("germanyn", ids) == "germanynorth"
    assert feed_region_id("germanywc", ids) == "germanywestcentral"
    assert feed_region_id("centralfrance", ids) == "francecentral"
    assert feed_region_id("brazilse", ids) == "brazilsoutheast"
    assert feed_region_id("chilec", ids) == "chilecentral"
    assert feed_region_id("unknown", ids) == "unknown"


def test_rivals_are_read_from_the_evidence():
    from ocm_regions.analysis.arearisk import derive_rivals

    placed = [{"provider": "a", "region_id": "r", "city": "Ashburn", "country": "US"}]
    evidence = [
        {
            "provider": "a",
            "region_id": "r",
            "claim": "city",
            "value": "Ashburn",
            "source_type": "vendor",
            "title": "t",
        },
        {
            "provider": "a",
            "region_id": "r",
            "claim": "city",
            "value": "Boydton, US",
            "source_type": "vendor_feed",
            "title": "feed",
        },
        {
            "provider": "a",
            "region_id": "r",
            "claim": "city",
            "value": "Leesburg",
            "source_type": "government",
            "title": "permit",
        },
        {
            "provider": "a",
            "region_id": "r",
            "claim": "area",
            "value": "Virginia",
            "source_type": "vendor_region_list",
            "title": "list",
        },
    ]
    rivals = derive_rivals(placed, evidence, {})
    assert [(r["rival"], r["basis"]) for r in rivals] == [("Boydton", "feed"), ("Leesburg", "regulator")]


def test_a_facility_counts_when_its_owner_is_the_vendor_and_it_sits_in_the_area():
    from ocm_regions.tables.build import vendor_facilities

    facilities = [
        {
            "name": "Hetzner FSN",
            "org_name": "Hetzner Online GmbH",
            "city": "Falkenstein",
            "country": "DE",
            "latitude": 50.4,
            "longitude": 12.3,
        },
        {
            "name": "Equinix",
            "org_name": "Equinix",
            "city": "Falkenstein",
            "country": "DE",
            "latitude": 50.4,
            "longitude": 12.3,
        },
        {
            "name": "Hetzner HEL",
            "org_name": "Hetzner Online GmbH",
            "city": "Helsinki",
            "country": "FI",
            "latitude": 60.2,
            "longitude": 24.9,
        },
    ]
    assert [f["name"] for f in vendor_facilities("Hetzner", (50.47, 12.37), facilities)] == ["Hetzner FSN"]


def test_a_place_finds_its_unlocode_under_a_local_name():
    from ocm_regions.place.placement import _unlocode

    locodes = {"BEBRU": {"Country": "BE", "NameWoDiacritics": "Bruxelles"}}
    assert _unlocode("Brussels", "BE", locodes, {"brussels": {"bruxelles"}}) == "BEBRU"
    assert _unlocode("Brussels", "BE", locodes, {}) == ""


def test_a_rival_takes_the_namesake_nearest_the_placed_city():
    from ocm_regions.analysis.arearisk import resolve_point

    cities = [
        {"name": "Quincy", "country": "US", "population": 100000, "lat": 42.2, "lon": -71.0},
        {"name": "Quincy", "country": "US", "population": 8000, "lat": 47.2, "lon": -119.8},
    ]
    assert resolve_point({"rival": "Quincy", "country": "US"}, cities, (47.5, -122.3)) == (47.2, -119.8)


def test_a_unlocode_name_can_hold_its_other_names():
    from ocm_regions.place.placement import _unlocode

    locodes = {
        "INBOM": {"Country": "IN", "NameWoDiacritics": "Mumbai (ex Bombay)"},
        "IDJKT": {"Country": "ID", "NameWoDiacritics": "Jakarta, Java"},
        "EGCAI": {"Country": "EG", "NameWoDiacritics": "El Qahira (Cairo)"},
    }
    assert (
        _unlocode("Mumbai", "IN", locodes, {}),
        _unlocode("Jakarta", "ID", locodes, {}),
        _unlocode("Cairo", "EG", locodes, {}),
    ) == ("INBOM", "IDJKT", "EGCAI")


def test_a_state_or_the_place_own_name_is_not_a_rival():
    from ocm_regions.analysis.arearisk import derive_rivals

    placed = [{"provider": "a", "region_id": "r", "city": "Quincy-Moses Lake", "country": "US"}]
    evidence = [
        {
            "provider": "a",
            "region_id": "r",
            "claim": "city",
            "value": "Quincy",
            "source_type": "government",
            "title": "t",
        },
        {
            "provider": "a",
            "region_id": "r",
            "claim": "city",
            "value": "Washington",
            "source_type": "vendor",
            "title": "t",
        },
        {
            "provider": "a",
            "region_id": "r",
            "claim": "city",
            "value": "Seattle",
            "source_type": "news",
            "title": "t",
        },
    ]
    assert [r["rival"] for r in derive_rivals(placed, evidence, {"washington": "US"})] == ["Seattle"]


def test_a_quote_must_name_the_region_to_replace_the_vendors_feed():
    quote = "Based out of Montreal, the new AWS could generate jobs across the country"
    assert apply(rule("feed"), [answer("Montreal", quote=quote)])["city"] == "Feedtown"


def test_a_quote_not_naming_the_region_still_beats_a_capital_or_nothing():
    quote = "Based out of Montreal, the new AWS could generate jobs across the country"
    assert apply(rule("none", ""), [answer("Montreal", quote=quote)])["city"] == "Montreal"
    assert (
        apply(rule("capital") | {"from_capital": "yes"}, [answer("Montreal", quote=quote)])["city"]
        == "Montreal"
    )


def test_a_unlocode_far_from_the_place_is_a_namesake():
    from ocm_regions.place.placement import _unlocode

    locodes = {
        "US98R": {"Country": "US", "NameWoDiacritics": "Columbus", "Coordinates": "3735N 09455W"},
        "USCMH": {"Country": "US", "NameWoDiacritics": "Columbus", "Coordinates": "3958N 08300W"},
    }
    assert _unlocode("Columbus", "US", locodes, {}, (39.96, -83.0)) == "USCMH"
    assert _unlocode("Columbus", "US", {"US98R": locodes["US98R"]}, {}, (39.96, -83.0)) == ""


def test_a_city_name_beats_a_district_that_mentions_the_city():
    from ocm_regions.place.placement import _unlocode

    locodes = {
        "JPARI": {"Country": "JP", "NameWoDiacritics": "Ariake (Tokyo)", "Coordinates": ""},
        "JPTYO": {"Country": "JP", "NameWoDiacritics": "Tokyo", "Coordinates": ""},
    }
    assert _unlocode("Tokyo", "JP", locodes, {}, (35.7, 139.7)) == "JPTYO"


def test_a_clli_prefix_is_six_letters_near_the_place():
    from ocm_regions.place.placement import _clli

    facilities = [
        {"city": "Tokyo", "country": "JP", "clli": "35.698"},
        {"city": "Tokyo", "country": "JP", "clli": "MX-DIF"},
        {"city": "Columbus", "country": "US", "clli": "CLMBOH", "latitude": 39.96, "longitude": -83.0},
        {"city": "Columbus", "country": "US", "clli": "CLMBGA", "latitude": 32.46, "longitude": -84.98},
    ]
    assert _clli("Tokyo", "JP", facilities, (35.7, 139.7)) == ""
    assert _clli("Columbus", "US", facilities, (39.96, -83.0)) == "CLMBOH"


def _locode(state, coordinates=""):
    return {
        "Country": "US",
        "NameWoDiacritics": "Washington",
        "Subdivision": state,
        "Coordinates": coordinates,
    }


def test_a_unlocode_in_the_citys_own_state_is_taken_before_a_namesake_elsewhere():
    from ocm_regions.place.placement import _unlocode

    locodes = {"USHQJ": _locode("LA"), "USW88": _locode("VA", "3842N 07809W"), "USWAS": _locode("DC")}
    assert _unlocode("Washington", "US", locodes, {}, (38.9, -77.04), "DC") == "USWAS"
    assert _unlocode("Washington", "US", locodes, {}, (38.9, -77.04), "MD") == "USW88"


def test_a_unlocode_with_coordinates_in_the_area_is_taken_before_one_with_none():
    from ocm_regions.place.placement import _unlocode

    locodes = {"USHQJ": _locode("LA"), "USW88": _locode("VA", "3842N 07809W")}
    assert _unlocode("Washington", "US", locodes, {}, (38.9, -77.04)) == "USW88"


def test_a_state_code_is_only_trusted_where_the_gazetteer_uses_the_same_codes():
    from ocm_regions.place.placement import generated_place

    osaka = {
        "name": "Osaka",
        "country": "JP",
        "admin1": "32",
        "population": 2600000,
        "lat": 34.7,
        "lon": 135.5,
    }
    row = {"Country": "JP", "Name": "Osaka", "NameWoDiacritics": "Osaka", "Coordinates": ""}
    locodes = {
        "JPOSA": row | {"Location": "OSA", "Subdivision": "27"},
        "JPZZZ": row | {"Location": "ZZZ", "Subdivision": "32"},
    }
    place = generated_place(
        rule("label", "Osaka") | {"country": "JP", "lat": 34.7, "lon": 135.5},
        [_airport("ITM", 34.78, 135.44)],
        {"ITM": 0.5},
        locodes,
        [],
        cities=Gazetteer([osaka]).index,
    )
    assert place["unlocode"] == "JPOSA"


def test_a_place_reads_its_state_from_the_gazetteer_city_of_the_same_name_nearby():
    from ocm_regions.place.placement import generated_place

    locodes = {
        "USAAA": {"Country": "US", "Location": "AAA", "Name": "Ashburn", "NameWoDiacritics": "Ashburn"}
        | {"Subdivision": "GA", "Coordinates": ""},
        "USQAS": {"Country": "US", "Location": "QAS", "Name": "Ashburn", "NameWoDiacritics": "Ashburn"}
        | {"Subdivision": "VA", "Coordinates": ""},
    }
    place = generated_place(
        rule("label", "Ashburn") | {"country": "US", "lat": 39.0, "lon": -77.5},
        [_airport("IAD", 38.95, -77.45)],
        {"IAD": 0.5},
        locodes,
        [],
        cities=gaz().index,
    )
    assert place["unlocode"] == "USQAS"


def test_a_unlocode_with_the_citys_own_name_is_taken_before_one_with_another_of_its_names():
    from ocm_regions.place.placement import _unlocode

    row = {"Country": "US", "Subdivision": "MO", "Coordinates": ""}
    locodes = {
        "USEEO": row | {"NameWoDiacritics": "Greenwood"},
        "USMKC": row | {"NameWoDiacritics": "Kansas City"},
    }
    alternates = {"kansas city": {"greenwood"}}
    assert _unlocode("Kansas City", "US", locodes, alternates, None, "MO") == "USMKC"


def test_two_names_for_one_point_are_one_place():
    from ocm_regions.place.placement import derive_places

    here = {"country": "MX", "lat": 20.58806, "lon": -100.38806}
    rules = [
        rule("feed", "Querétaro") | here | {"provider": "aws", "region_id": "mx-central-1"},
        rule("label", "Santiago de Querétaro")
        | here
        | {"provider": "gcp", "region_id": "northamerica-south1"},
        rule("label", "Mexico City") | {"country": "MX", "lat": 19.4, "lon": -99.1, "region_id": "other"},
    ]
    places, mappings = derive_places(rules, [_airport("QRO", 20.6, -100.2)], {"QRO": 0.5}, {}, [])
    assert [p["city"] for p in places] == ["Querétaro", "Mexico City"]
    assert (
        mappings[("aws", "mx-central-1")]["place_id"] == mappings[("gcp", "northamerica-south1")]["place_id"]
    )
