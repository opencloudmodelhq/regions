# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import json

import pytest

from ocm_regions.analysis.method_stats import (
    agreement,
    error_profile,
    mcnemar,
    outcomes,
    per_vendor,
    step_curve,
    write_method_stats,
)

PLACES = {
    ("aws", "a"): (0.0, 0.0),
    ("aws", "b"): (10.0, 10.0),
    ("gcp", "c"): (20.0, 20.0),
    ("gcp", "d"): (30.0, 30.0),
}


def row(provider, region_id, lat=None, lon=None, step=""):
    answered = lat is not None
    return {
        "provider": provider,
        "region_id": region_id,
        "status": "answered" if answered else "abstained",
        "lat": "" if lat is None else str(lat),
        "lon": "" if lon is None else str(lon),
        "step": step,
    }


def test_outcomes_count_an_abstention_as_a_miss_and_skip_regions_without_a_reference():
    rows = [
        row("aws", "a", 0.0, 0.1),
        row("aws", "b"),
        row("gcp", "c", 25.0, 25.0),
        row("gcp", "zz", 1.0, 1.0),
    ]
    assert outcomes(rows, PLACES) == {
        ("aws", "a"): True,
        ("aws", "b"): False,
        ("gcp", "c"): False,
        ("gcp", "d"): False,
    }


def test_mcnemar_counts_only_discordant_pairs():
    a = {1: True, 2: True, 3: True, 4: False, 5: True}
    b = {1: True, 2: False, 3: False, 4: False, 5: True}
    result = mcnemar(a, b)
    assert (result["only_first"], result["only_second"], result["pairs"]) == (2, 0, 5)
    assert result["p"] == pytest.approx(0.5)


def test_mcnemar_matches_an_exact_binomial_value():
    a = {i: i < 10 for i in range(20)}
    b = {i: i >= 9 and i < 10 for i in range(20)}
    assert mcnemar(a, b)["p"] == pytest.approx(0.00390625)


def test_mcnemar_with_no_discordant_pairs_is_one():
    same = {1: True, 2: False}
    assert mcnemar(same, same)["p"] == 1.0


def test_agreement_reports_kappa_and_raw_share():
    a = {1: True, 2: True, 3: False, 4: False}
    b = {1: True, 2: False, 3: False, 4: False}
    result = agreement(a, b)
    assert result["share"] == pytest.approx(0.75)
    assert result["kappa"] == pytest.approx(0.5)


def test_agreement_is_one_when_both_methods_never_vary():
    same = {1: True, 2: True}
    assert agreement(same, same)["kappa"] == 1.0


def test_error_profile_reports_distances_and_thresholds_over_answered_regions():
    rows = [
        row("aws", "a", 0.0, 0.0),
        row("aws", "b", 10.0, 10.2),
        row("gcp", "c", 21.0, 20.0),
        row("gcp", "d"),
    ]
    profile = error_profile(rows, PLACES, thresholds=(10, 50, 200))
    assert profile["answered"] == 3
    assert profile["within"] == {10: 1, 50: 2, 200: 3}
    assert profile["median_km"] == pytest.approx(21.9, abs=0.5)
    assert profile["p90_km"] > profile["median_km"]


def test_per_vendor_scores_each_provider_with_an_interval():
    rows = [
        row("aws", "a", 0.0, 0.0),
        row("aws", "b"),
        row("gcp", "c", 20.0, 20.0),
        row("gcp", "d", 30.0, 30.0),
    ]
    result = per_vendor(rows, PLACES)
    assert (result["aws"]["regions"], result["aws"]["answered"], result["aws"]["matched"]) == (2, 1, 1)
    assert result["gcp"]["matched"] == 2
    assert 0.0 <= result["gcp"]["low"] <= result["gcp"]["high"] <= 1.0


def test_step_curve_adds_steps_in_order_and_reports_cumulative_accuracy():
    rows = [
        row("aws", "a", 0.0, 0.0, "label, agrees"),
        row("aws", "b", 50.0, 50.0, "label"),
        row("gcp", "c", 20.0, 20.0, "label"),
        row("gcp", "d", step="none"),
    ]
    curve = step_curve(rows, PLACES, order=("label, agrees", "label", "none"))
    assert [(p["step"], p["answered"], p["matched"]) for p in curve] == [
        ("label, agrees", 1, 1),
        ("label", 3, 2),
        ("none", 3, 2),
    ]
    assert curve[-1]["coverage"] == pytest.approx(0.75)


def test_write_method_stats_writes_json_macros_and_a_table(tmp_path):
    methods = {
        "rules": [
            row("aws", "a", 0.0, 0.0, "label, agrees"),
            row("aws", "b", 10.0, 10.0, "label"),
            row("gcp", "c", 20.0, 20.0, "label"),
            row("gcp", "d", step="none"),
        ],
        "rag": [row("aws", "a", 0.0, 0.0), row("aws", "b"), row("gcp", "c", 20.0, 20.0), row("gcp", "d")],
        "memory": [
            row("aws", "a", 5.0, 5.0),
            row("aws", "b", 10.0, 10.0),
            row("gcp", "c"),
            row("gcp", "d", 30.0, 30.0),
        ],
    }
    report = write_method_stats(
        methods, PLACES, models=("rag", "memory"), out=tmp_path / "method_stats.json", generated=tmp_path
    )
    saved = json.loads((tmp_path / "method_stats.json").read_text(encoding="utf-8"))
    assert saved["mcnemar"]["rules|rag"]["only_first"] == 1
    assert "rules|memory" in report["mcnemar"] and "rag|memory" in report["agreement"]
    macros = (tmp_path / "method_stats_macros.tex").read_text(encoding="utf-8")
    assert "\\newcommand{\\statRulesVsRagP}" in macros and "\\newcommand{\\statRulesMedianKm}" in macros
    table = (tmp_path / "method_stats_table.tex").read_text(encoding="utf-8")
    assert table.startswith("\\begin{tabular}") and "Rules" in table


def test_gather_reads_rules_and_each_model_answer_file(tmp_path):
    from ocm_regions.analysis.method_stats import gather

    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "rag.csv").write_text(
        "provider,region_id,status,city,lat,lon\naws,a,answered,X,0,0\n", encoding="utf-8"
    )
    rules = [
        {
            "provider": "aws",
            "region_id": "a",
            "basis": "label",
            "cross_check": "agrees",
            "city": "X",
            "lat": "0",
            "lon": "0",
        },
        {
            "provider": "aws",
            "region_id": "b",
            "basis": "none",
            "cross_check": "",
            "city": "",
            "lat": "",
            "lon": "",
        },
    ]
    methods = gather(rules, {"rag": tmp_path / "m" / "rag.csv", "absent": tmp_path / "m" / "none.csv"})
    assert set(methods) == {"rules", "rag"}
    assert [r["status"] for r in methods["rules"]] == ["answered", "abstained"]
    assert methods["rules"][0]["step"] == "label, agrees" and methods["rag"][0]["region_id"] == "a"


def test_compute_scores_only_the_regions_the_rules_cover():
    from ocm_regions.analysis.method_stats import compute

    places = {**PLACES, ("old", "gone"): (5.0, 5.0)}
    methods = {
        "rules": [
            row("aws", "a", 0.0, 0.0, "label, agrees"),
            row("aws", "b", 10.0, 10.0, "label"),
            row("gcp", "c", step="none"),
            row("gcp", "d", step="none"),
        ],
        "rag": [row("aws", "a", 0.0, 0.0), row("aws", "b"), row("gcp", "c", 20.0, 20.0), row("gcp", "d")],
    }
    report = compute(methods, places, ("rag",))
    assert report["mcnemar"]["rules|rag"]["pairs"] == 4


def test_compute_also_compares_methods_on_regions_both_answered():
    from ocm_regions.analysis.method_stats import compute

    methods = {
        "rules": [
            row("aws", "a", 0.0, 0.0, "label, agrees"),
            row("aws", "b", 10.0, 10.0, "label"),
            row("gcp", "c", 25.0, 25.0, "label"),
            row("gcp", "d", step="none"),
        ],
        "rag": [
            row("aws", "a", 0.0, 0.0),
            row("aws", "b"),
            row("gcp", "c", 20.0, 20.0),
            row("gcp", "d", 30.0, 30.0),
        ],
    }
    both = compute(methods, PLACES, ("rag",))["mcnemar_answered"]["rules|rag"]
    assert (both["pairs"], both["only_first"], both["only_second"]) == (2, 0, 1)


def test_error_profile_reports_how_far_the_misses_fall():
    rows = [row("aws", "a", 0.0, 0.0), row("aws", "b", 12.0, 10.0), row("gcp", "c", 30.0, 20.0)]
    profile = error_profile(rows, PLACES)
    assert profile["misses"] == 2
    assert profile["miss_median_km"] == pytest.approx((222.4 + 1111.9) / 2, rel=0.01)


def test_compute_reports_the_region_count_and_a_distance_curve_per_method():
    from ocm_regions.analysis.method_stats import compute

    methods = {
        "rules": [
            row("aws", "a", 0.0, 0.0, "label, agrees"),
            row("aws", "b", 10.0, 10.0, "label"),
            row("gcp", "c", step="none"),
            row("gcp", "d", step="none"),
        ]
    }
    report = compute(methods, PLACES, ())
    assert report["regions"] == 4
    assert report["curves"]["rules"][0] == [1, 1.0]


def test_holm_adjusts_a_family_of_p_values_and_keeps_their_order():
    from ocm_regions.analysis.method_stats import holm

    assert holm({"a": 0.01, "b": 0.04, "c": 0.03}) == pytest.approx({"a": 0.03, "b": 0.06, "c": 0.06})
    assert holm({"a": 0.6, "b": 0.9}) == {"a": 1.0, "b": 1.0}


def test_compute_adds_holm_adjusted_p_values_to_both_families():
    from ocm_regions.analysis.method_stats import compute

    methods = {
        "rules": [
            row("aws", "a", 0.0, 0.0, "label, agrees"),
            row("aws", "b", 10.0, 10.0, "label"),
            row("gcp", "c", step="none"),
            row("gcp", "d", step="none"),
        ],
        "rag": [row("aws", "a", 0.0, 0.0), row("aws", "b"), row("gcp", "c", 20.0, 20.0), row("gcp", "d")],
    }
    report = compute(methods, PLACES, ("rag",))
    assert report["mcnemar"]["rules|rag"]["p_holm"] == report["mcnemar"]["rules|rag"]["p"]
    assert "p_holm" in report["mcnemar_answered"]["rules|rag"]
