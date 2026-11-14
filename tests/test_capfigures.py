# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest

from ocm_regions.analysis.capfigures import (
    bands_figure,
    f1_figure,
    measures_figure,
    tradeoff_figure,
    write_cap_inputs,
)
from ocm_regions.analysis.capsweep import Site, border_split_count, cross_pairs, summarise

ROOT = Path(__file__).resolve().parent.parent
needs_build = pytest.mark.skipif(
    not (ROOT / "output" / "regions.csv").exists(), reason="run `make build` first"
)


def sites():
    return [
        Site("a", "r1", "X", "city", 0.0, 0.0, "NEA", "HUB", 10.0, 50.0, "AA"),
        Site("b", "r2", "Y", "city", 0.0, 0.2, "HUB", "HUB", 20.0, 20.0, "AA"),
        Site("c", "r3", "Z", "metro", 0.0, 3.0, "FAR", "HUB", 5.0, 300.0, "AA"),
        Site("d", "r4", "W", "city", 0.0, 0.1, "OWN", "OWN", 5.0, 5.0, "BB"),
    ]


def summary():
    return summarise(sites(), [1.0, 2.0, 3.0], radii=(50, 100, 150, 200), runs=5)


def test_summary_records_the_rule_and_the_table_ends():
    result = summary()
    assert result["selection"]["cap"] == 180
    assert [row["label"] for row in result["named"]][0] == "0 (nearest airport)"
    assert result["named"][-1]["label"] == "None (major hub)"
    assert result["metro"]["links"] == 3


def test_border_splits_count_only_nearby_pairs_across_countries():
    assert border_split_count(sites(), cross_pairs(sites()), 50.0, 100) == 2


def test_step_figures_hold_each_value_until_the_next_cap_and_mark_the_choice():
    text = measures_figure(summary())
    assert text.count(r"\begin{tikzpicture}") == 4
    assert "const plot mark left" in text
    assert "(axis cs:180," in text


def test_f1_figure_draws_one_labelled_curve_per_radius():
    text = f1_figure(summary())
    assert text.count(r"\addplot") == 4
    assert r"\legend{$\rho=50$ km,$\rho=100$ km,$\rho=150$ km,$\rho=200$ km}" in text


def test_band_figure_stacks_correct_and_false_pairs():
    text = bands_figure(summary())
    assert "ybar stacked" in text
    assert text.count(r"\addplot") == 2


def test_tradeoff_figure_labels_both_tables_and_the_choice():
    text = tradeoff_figure(summary())
    assert "Nearest airport" in text and "Major hub" in text and "180 km" in text


def test_written_macros_use_letter_names_only(tmp_path):
    write_cap_inputs(tmp_path, summary())
    macros = (tmp_path / "generated" / "cap_macros.tex").read_text()
    names = [line.split("{", 1)[1].split("}", 1)[0].lstrip("\\") for line in macros.splitlines()]
    assert names and all(name.isalpha() for name in names)


@needs_build
def test_tradeoff_labels_sit_beside_the_rising_part_and_above_the_flat_part():
    rows = [
        {"label": "0 (nearest airport)", "cap": 0.0, "fp100": 4, "recall100": 0.8},
        {"label": "50 km", "cap": 50.0, "fp100": 4, "recall100": 0.85},
        {"label": "None (major hub)", "cap": None, "fp100": 21, "recall100": 0.9},
    ]
    text = tradeoff_figure({"rho": 100, "selection": {"cap": 100}, "sweep": rows, "named": rows})
    assert "anchor=west] at (axis cs:4,85.00) {50 km}" in text
    assert "anchor=south] at (axis cs:21,90.00) {Major hub}" in text


def test_macros_report_the_first_band_holding_both_correct_and_false_pairs(tmp_path):
    result = summary()
    result["bands"] = [
        {"low": 0.0, "high": 25, "moved": 1, "good": 3, "bad": 0},
        {"low": 25.0, "high": 50, "moved": 2, "good": 6, "bad": 5},
    ]
    write_cap_inputs(tmp_path, result)
    macros = (tmp_path / "generated" / "cap_macros.tex").read_text()
    assert r"\newcommand{\capMixedGood}{6}" in macros and r"\newcommand{\capMixedBad}{5}" in macros
    assert r"\newcommand{\capMixedLow}{25}" in macros and r"\newcommand{\capMixedHigh}{50}" in macros


def test_rule_stability_macros_name_the_vendors_that_move_the_cap():
    from ocm_regions.analysis.capfigures import rule_macros

    rule = {
        "selected": 100,
        "runs": 200,
        "caps": [],
        "share_selected": 0.385,
        "median": 150.0,
        "low": 90.0,
        "high": 270.0,
        "without": {"aws": 100, "alibaba": 160, "gcp": 210, "azure": 90},
    }
    macros = rule_macros(rule)
    assert macros["capRuleRuns"] == 200 and macros["capRuleShare"] == "38"
    assert (macros["capRuleLow"], macros["capRuleMedian"], macros["capRuleHigh"]) == ("90", "150", "270")
    assert macros["capRuleKept"] == 1 and macros["capRuleVendors"] == 4
    assert macros["capRuleMoved"] == "Alibaba 160~km, Azure 90~km and Google Cloud 210~km"


def test_interval_pairs_read_as_one_number_when_both_ends_match():
    from ocm_regions.analysis.capfigures import pairs_text

    assert pairs_text({"pairs_low": 870, "pairs_high": 870}) == "870"
    assert pairs_text({"pairs_low": 861, "pairs_high": 870}) == "861 to 870"
