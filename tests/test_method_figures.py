# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import pytest

from ocm_regions.analysis.method_figures import (
    CURVE_KM,
    coverage_figure,
    kappa_table,
    pairs_table,
    threshold_curve,
    threshold_figure,
    tier_figure,
    vendor_table,
    write_method_figures,
)

LABELS = {"rules": "Rules", "rag": "RAG, Qwen 3.8", "memory": "Model, no documents"}
PLACES = {
    ("aws", "a"): (0.0, 0.0),
    ("aws", "b"): (10.0, 10.0),
    ("gcp", "c"): (20.0, 20.0),
    ("gcp", "d"): (30.0, 30.0),
}


def row(provider, region_id, lat=None, lon=None):
    return {
        "provider": provider,
        "region_id": region_id,
        "status": "answered" if lat is not None else "abstained",
        "lat": "" if lat is None else str(lat),
        "lon": "" if lon is None else str(lon),
    }


REPORT = {
    "regions": 4,
    "errors": {
        "rules": {
            "answered": 4,
            "misses": 1,
            "miss_median_km": 120.0,
            "within": {10: 3, 25: 3, 50: 3, 100: 3},
        },
        "rag": {"answered": 2, "misses": 0, "miss_median_km": 0.0, "within": {10: 2, 25: 2, 50: 2, 100: 2}},
        "memory": {
            "answered": 3,
            "misses": 1,
            "miss_median_km": 500.0,
            "within": {10: 1, 25: 2, 50: 2, 100: 2},
        },
    },
    "curves": {
        "rules": [[1, 0.5], [10, 0.75], [1000, 1.0]],
        "rag": [[1, 1.0], [10, 1.0], [1000, 1.0]],
        "memory": [[1, 0.0], [10, 0.33], [1000, 1.0]],
    },
    "mcnemar": {"rules|rag": {"pairs": 4, "only_first": 2, "only_second": 0, "p": 0.5}},
    "mcnemar_answered": {"rules|rag": {"pairs": 2, "only_first": 0, "only_second": 0, "p": 1.0}},
    "agreement": {"rag|memory": {"pairs": 4, "share": 0.75, "kappa": 0.5}},
    "vendors": {
        "rules": {"aws": {"regions": 2, "answered": 2, "matched": 2, "low": 0.3, "high": 1.0}},
        "rag": {"aws": {"regions": 2, "answered": 1, "matched": 1, "low": 0.2, "high": 1.0}},
    },
    "steps": [
        {"step": "label, agrees", "answered": 2, "matched": 2, "coverage": 0.5},
        {"step": "label", "answered": 4, "matched": 3, "coverage": 1.0},
    ],
}


def test_threshold_curve_gives_the_share_of_answers_within_each_distance():
    rows = [
        row("aws", "a", 0.0, 0.0),
        row("aws", "b", 10.0, 10.2),
        row("gcp", "c", 21.0, 20.0),
        row("gcp", "d"),
    ]
    curve = threshold_curve(rows, PLACES, steps=(1, 50, 200))
    assert curve == [[1, pytest.approx(1 / 3)], [50, pytest.approx(2 / 3)], [200, pytest.approx(1.0)]]


def test_curve_steps_run_from_one_to_thousands_of_kilometres():
    assert CURVE_KM[0] == 1 and CURVE_KM[-1] >= 2000 and list(CURVE_KM) == sorted(CURVE_KM)


def test_coverage_figure_shows_coverage_and_matched_share_with_intervals_per_method():
    tikz = coverage_figure(REPORT, LABELS, ("rules", "rag", "memory"))
    assert tikz.count("\\begin{axis}") == 2
    assert "Rules" in tikz and "RAG, Qwen 3.8" in tikz
    assert "(100.0,Rules)" in tikz and "(50.0,{RAG, Qwen 3.8})" in tikz
    assert "error bars" in tikz


def test_threshold_figure_draws_one_styled_line_per_method_on_a_log_axis_with_a_legend():
    tikz = threshold_figure(REPORT, LABELS, ("rules", "rag", "memory"))
    assert "xmode=log" in tikz and tikz.count("\\addplot") == 3
    assert "\\legend{Rules,{RAG, Qwen 3.8},{Model, no documents}}" in tikz
    assert "dashed" in tikz and "(10,75.00)" in tikz


def test_tier_figure_steps_through_the_rules_tiers_and_places_model_methods_as_labelled_points():
    tikz = tier_figure(REPORT, LABELS, ("rag", "memory"))
    assert "(50.0,100.00)" in tikz and "(100.0,75.00)" in tikz
    assert "label, agrees" in tikz and "Model, no documents" in tikz


def test_pairs_table_lists_both_tests_for_each_pair():
    table = pairs_table(REPORT, LABELS)
    assert "Rules & RAG, Qwen 3.8 & 2 & 0 & 0.500 & 2 & 0 & 0 & 1.000 \\\\" in table


def test_kappa_table_gives_agreement_and_kappa_per_model_pair():
    assert "RAG, Qwen 3.8 & Model, no documents & 75.0\\% & 0.50 \\\\" in kappa_table(REPORT, LABELS)


def test_vendor_table_puts_vendors_in_rows_and_methods_in_columns():
    table = vendor_table(REPORT, LABELS, ("rules", "rag"))
    assert "Vendor & Rules & RAG, Qwen 3.8 \\\\" in table
    assert "AWS & 2/2 & 1/1 \\\\" in table


def test_write_method_figures_writes_every_figure_and_table(tmp_path):
    write_method_figures(REPORT, LABELS, ("rules", "rag", "memory"), tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "method_coverage.tex",
        "method_kappa_table.tex",
        "method_pairs_table.tex",
        "method_thresholds.tex",
        "method_tiers.tex",
        "method_vendor_table.tex",
    ]
