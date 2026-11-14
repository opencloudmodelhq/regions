# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0


import pytest


# ---- fetch retries
def test_a_throttled_download_waits_and_tries_again():
    import urllib.error

    from ocm_regions.fetch.sources import _get_with_retry

    calls, waits = [], []

    def get(url):
        calls.append(url)
        if len(calls) < 3:
            raise urllib.error.HTTPError(url, 429, "Too Many", {}, None)
        return b"ok"

    assert _get_with_retry("u", get, waits.append) == b"ok" and waits[0] < waits[1]


def test_a_missing_page_is_not_retried():
    import urllib.error

    from ocm_regions.fetch.sources import _get_with_retry

    calls = []

    def get(url):
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "Gone", {}, None)

    with pytest.raises(urllib.error.HTTPError):
        _get_with_retry("u", get, lambda _s: None)
    assert len(calls) == 1


# ---- mutation
def mutation_records():
    return [
        {"provider": "a", "url": "u1", "quote": "region east sits in Ashburn today", "value": "Ashburn"},
        {"provider": "a", "url": "u2", "quote": "region west sits in Tokyo today", "value": "Tokyo"},
        {"provider": "a", "url": "u3", "quote": "Oslo", "value": "Oslo"},
    ]


DOCS = {
    "u1": "intro region east sits in ashburn today end",
    "u2": "intro region west sits in tokyo today and also oslo",
    "u3": "oslo is here and tokyo",
}


def test_original_quotes_are_the_control_and_must_pass():
    from ocm_regions.analysis.mutation import run_mutation

    result = run_mutation(mutation_records(), DOCS.get, seed=1)
    assert result["control"] == {"records": 3, "passed": 3}


def test_a_city_swap_that_is_caught_counts_as_caught():
    from ocm_regions.analysis.mutation import run_mutation

    result = run_mutation(mutation_records(), DOCS.get, seed=1)
    assert (
        result["other_city"]["records"] >= 2
        and result["other_city"]["false_pass"] <= result["other_city"]["records"]
    )


def test_a_short_quote_can_pass_by_accident_and_is_reported_by_length():
    from ocm_regions.analysis.mutation import run_mutation

    records = [
        {"provider": "a", "url": "u1", "quote": "Oslo", "value": "Oslo"},
        {"provider": "a", "url": "u2", "quote": "Tokyo", "value": "Tokyo"},
    ]
    docs = {"u1": "oslo and tokyo", "u2": "tokyo and oslo"}
    result = run_mutation(records, docs.get, seed=1)
    assert result["other_quote"]["false_pass"] == 2
    assert result["other_quote"]["by_length"]["under 20"]["false_pass"] == 2
    assert result["other_quote"]["high"] <= 1.0


def test_mutation_is_repeatable():
    from ocm_regions.analysis.mutation import run_mutation

    assert run_mutation(mutation_records(), DOCS.get, seed=5) == run_mutation(
        mutation_records(), DOCS.get, seed=5
    )


def test_records_without_a_saved_document_are_left_out():
    from ocm_regions.analysis.mutation import run_mutation

    assert run_mutation(mutation_records(), {"u1": DOCS["u1"]}.get, seed=1)["control"]["records"] == 1


# ---- hubs
class Airport:
    def __init__(self, code, country, lat, lon, kind="large_airport", scheduled=True):
        self.code, self.country, self.point, self.kind, self.scheduled = (
            code,
            country,
            (lat, lon),
            kind,
            scheduled,
        )


AIRPORTS = [
    Airport("BIG", "AA", 0.0, 0.5),
    Airport("NEAR", "AA", 0.0, 0.1),
    Airport("FAR", "AA", 0.0, 5.0),
    Airport("ABROAD", "BB", 0.0, 0.2),
    Airport("SMALL", "AA", 0.0, 0.05, "small_airport"),
]
RANKS = {"BIG": 9.0, "NEAR": 1.0, "FAR": 50.0, "ABROAD": 99.0, "SMALL": 100.0}


def test_hub_is_the_highest_ranked_scheduled_airport_in_range_and_country():
    from ocm_regions.place.hubs import derive_hub

    assert derive_hub((0.0, 0.0), "AA", AIRPORTS, RANKS, km=100).code == "BIG"


def test_no_airport_in_range_gives_no_hub():
    from ocm_regions.place.hubs import derive_hub

    assert derive_hub((50.0, 50.0), "AA", AIRPORTS, RANKS, km=100) is None


def test_ranks_come_from_opentraveldata_airports():
    from ocm_regions.place.hubs import airport_ranks

    text = "iata_code^location_type^page_rank\nAAA^A^0.5\nBBB^C^0.9\nCCC^A^\n"
    assert airport_ranks(text) == {"AAA": 0.5}


# ---- extraction methods
def line(region, city, lat=1.0, lon=1.0, document="d", quote=None):
    return {
        "provider": "a",
        "region_id": region,
        "city": city,
        "lat": lat,
        "lon": lon,
        "document": document,
        "quote": quote or f"{region} {city}",
    }


def test_traditional_takes_the_most_common_city():
    from ocm_regions.analysis.extract_methods import pick_most_common

    rows = [line("r", "Boiler", document="x"), line("r", "Boiler", document="y"), line("r", "Tokyo")]
    assert pick_most_common(rows)[("a", "r")]["city"] == "Boiler"


def test_statistical_discounts_a_city_seen_with_every_region():
    from ocm_regions.analysis.extract_methods import pick_log_odds

    rows = [line("r", "Boiler"), line("r", "Boiler"), line("r", "Tokyo")]
    for other in "stuvw":
        rows += [line(other, "Boiler"), line(other, "Boiler"), line(other, f"City{other}")]
    assert pick_log_odds(rows)[("a", "r")]["city"] == "Tokyo"


def test_statistical_ties_break_the_same_way_every_time():
    from ocm_regions.analysis.extract_methods import pick_log_odds

    rows = [line("r", "Zeta"), line("r", "Alpha")]
    assert pick_log_odds(rows)[("a", "r")]["city"] == pick_log_odds(list(reversed(rows)))[("a", "r")]["city"]


def test_methods_are_scored_on_the_same_regions_with_a_range():
    from ocm_regions.analysis.extract_methods import score_method

    picks = {
        ("a", "r"): {"lat": 1.0, "lon": 1.0, "city": "X"},
        ("a", "s"): {"lat": 40.0, "lon": 40.0, "city": "Y"},
        ("a", "gone"): {"lat": 1.0, "lon": 1.0, "city": "Z"},
    }
    score = score_method(picks, {("a", "r"): (1.0, 1.0), ("a", "s"): (1.0, 1.0), ("a", "t"): (1.0, 1.0)})
    assert (score["regions"], score["answered"], score["matched"]) == (3, 2, 1)
    assert 0 <= score["low"] <= 0.5 <= score["high"] <= 1


def test_published_picks_never_carry_a_street_address():
    from ocm_regions.analysis.extract_methods import public_rows

    picks = {
        ("a", "r"): {
            "city": "Sydney",
            "country": "AU",
            "document": "d",
            "score": 1.0,
            "lat": 1,
            "lon": 1,
            "quote": "400 Harris Street Ultimo Sydney",
        }
    }
    assert public_rows(picks)[0]["quote"] == "Sydney"


def test_recorded_model_answers_become_picks_only_when_answered():
    from ocm_regions.analysis.extract_methods import picks_from_model

    rows = [
        {
            "provider": "a",
            "region_id": "r",
            "status": "answered",
            "city": "X",
            "lat": "1.0",
            "lon": "2.0",
            "quote": "q",
        },
        {
            "provider": "a",
            "region_id": "s",
            "status": "unresolved",
            "city": "Y",
            "lat": "",
            "lon": "",
            "quote": "",
        },
    ]
    assert list(picks_from_model(rows)) == [("a", "r")]


def test_method_macros_are_written_for_the_paper():
    from ocm_regions.analysis.extract_methods import method_macros

    scores = {
        "traditional": {"answered": 5, "matched": 4, "low": 0.3, "high": 0.9, "regions": 10},
        "statistical": {"answered": 5, "matched": 5, "low": 0.5, "high": 1.0, "regions": 10},
        "rag": {"answered": 1, "matched": 1, "low": 0.2, "high": 1.0, "regions": 10},
        "label": {"answered": 1, "matched": 1, "low": 0.2, "high": 1.0, "regions": 10},
    }
    mutation = {
        "control": {"records": 10, "passed": 9},
        "other_quote": {
            "records": 9,
            "false_pass": 1,
            "share": 0.11,
            "low": 0.02,
            "high": 0.4,
            "by_length": {
                "under 20": {"records": 3, "false_pass": 1},
                "50 or more": {"records": 3, "false_pass": 0},
            },
        },
        "other_city": {"records": 8, "false_pass": 0, "share": 0.0, "low": 0.0, "high": 0.3, "by_length": {}},
    }
    macros = method_macros(scores, mutation)
    assert macros["extractStatisticalMatched"] == 5 and macros["mutationQuoteShare"] == "11.0"


def test_contribution_macros_count_risk_and_the_corpus():
    from ocm_regions.analysis.extract_methods import contribution_macros

    grades = {
        "regions_gaining": [["a", "r"]],
        "with_model": {"vendor-document": 3},
        "without_model": {"vendor-document": 2},
    }
    risk = [
        {"provider": "a", "region_id": "r", "status": "code-safe"},
        {"provider": "a", "region_id": "r", "status": "code-safe"},
        {"provider": "a", "region_id": "s", "status": "code-at-risk"},
    ]
    corpus = [{"fetch": "yes"}, {"fetch": "no"}]
    macros = contribution_macros(grades, risk, corpus)
    assert (macros["modelGainRegions"], macros["areaRiskSafe"], macros["areaRiskAtRisk"]) == (1, 1, 1)
    assert (macros["corpusDocuments"], macros["corpusFetched"]) == (2, 1)


def test_length_is_reported_from_the_changed_city_test():
    from ocm_regions.analysis.extract_methods import method_macros

    item = {"records": 9, "false_pass": 0, "share": 0.0, "low": 0.0, "high": 0.1}
    by_length = {"under 20": {"records": 5, "false_pass": 3}, "50 or more": {"records": 4, "false_pass": 0}}
    mutation = {
        "control": {"records": 9, "passed": 9},
        "other_quote": {**item, "by_length": {}},
        "other_city": {**item, "by_length": by_length},
    }
    macros = method_macros({}, mutation)
    assert (macros["mutationShortFalse"], macros["mutationShortRecords"], macros["mutationLongRecords"]) == (
        3,
        5,
        4,
    )
