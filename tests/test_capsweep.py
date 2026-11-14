# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import pytest

from ocm_regions.analysis.capsweep import (
    Site,
    bands,
    best_cap,
    bootstrap_best,
    candidate_caps,
    code_at,
    cross_pairs,
    metro_link_distances,
    off_hub,
    percentile,
    score,
    select_cap,
    splits_at,
    stability,
    sweep,
)


def toy():
    return [
        Site("a", "r1", "X", "city", 0.0, 0.0, "NEA", "HUB", 10.0, 50.0),
        Site("b", "r2", "Y", "city", 0.0, 0.2, "HUB", "HUB", 20.0, 20.0),
        Site("c", "r3", "Z", "metro", 0.0, 3.0, "FAR", "HUB", 5.0, 300.0),
    ]


def test_cap_keeps_a_hub_within_reach_and_falls_back_beyond_it():
    site = toy()[0]
    assert code_at(site, 49.9) == "NEA"
    assert code_at(site, 50.0) == "HUB"


def test_candidates_are_zero_plus_distances_where_the_tables_differ():
    assert candidate_caps(toy()) == [0.0, 50.0, 300.0]


def test_pairs_exclude_regions_of_the_same_vendor():
    sites = [*toy(), Site("a", "r4", "X", "city", 0.0, 0.0, "NEA", "HUB", 10.0, 50.0)]
    assert (0, 3) not in {(i, j) for i, j, _ in cross_pairs(sites)}


def test_score_counts_correct_and_false_merges():
    sites = toy()
    pairs = cross_pairs(sites)
    assert score(sites, pairs, 0.0, (100,))["recall100"] == 0
    near = score(sites, pairs, 50.0, (100,))
    assert (near["recall100"], near["fp100"], near["f1100"]) == (1, 0, 1)
    far = score(sites, pairs, 300.0, (100,))
    assert (far["fp100"], round(far["f1100"], 3)) == (2, 0.5)


def test_score_reports_overlap_and_distance_to_code():
    sites = toy()
    result = score(sites, cross_pairs(sites), 50.0, (100,))
    assert (result["distinct"], result["multi"], result["shared"]) == (2, 1, 2)
    assert result["max_km"] == 50.0


def test_selection_takes_the_rounded_midpoint_of_the_widest_stable_range():
    sites = toy()
    chosen = select_cap(sweep(sites, (100,)), 100, tolerance=0.01, step=10)
    assert (chosen["peak"], chosen["low"], chosen["high"], chosen["cap"]) == (50.0, 50.0, 300.0, 180)


def test_selection_never_rounds_outside_the_stable_range():
    rows = [
        {"cap": 0.0, "tp100": 1, "fp100": 0, "f1100": 0.5},
        {"cap": 101.0, "tp100": 2, "fp100": 0, "f1100": 1.0},
        {"cap": 104.0, "tp100": 3, "fp100": 1, "f1100": 0.2},
    ]
    chosen = select_cap(rows, 100, tolerance=0.01, step=10)
    assert 101.0 <= chosen["cap"] < 104.0


def test_best_cap_breaks_ties_with_the_lowest_cap():
    assert best_cap([{"cap": 5.0, "f1100": 1.0}, {"cap": 9.0, "f1100": 1.0}], 100) == 5.0


def test_bands_split_new_pairs_into_correct_and_false():
    sites = toy()
    result = bands(sites, cross_pairs(sites), 100, width=100, top=300)
    assert [(b["good"], b["bad"], b["moved"]) for b in result] == [(1, 0, 1), (0, 0, 0), (0, 2, 1)]


def test_bootstrap_is_repeatable_and_picks_only_real_caps():
    sites = toy()
    first = bootstrap_best(sites, 100, runs=50, seed=7)
    assert first == bootstrap_best(sites, 100, runs=50, seed=7)
    assert first["p5"] in candidate_caps(sites) and first["p95"] in candidate_caps(sites)


def test_stability_covers_city_precision_and_each_missing_vendor():
    labels = [row["test"] for row in stability(toy(), 100)]
    assert labels == ["All regions", "City-level locations only", "Without a", "Without b", "Without c"]


def test_off_hub_lists_regions_beyond_the_cap_only_when_the_tables_differ():
    assert [s.region_id for s in off_hub(toy(), 100.0)] == ["r3"]


def test_splits_list_nearby_pairs_that_still_differ():
    sites = toy()
    assert splits_at(sites, cross_pairs(sites), 0.0, 100) == [("HUB", "Y", "NEA", "X", 1)]


def test_percentile_interpolates():
    assert percentile([0.0, 10.0], 50) == pytest.approx(5.0)


def test_metro_links_measure_airports_to_their_city_in_the_same_country():
    header = "iata_code^location_type^latitude^longitude^country_code^city_code_list^date_until\n"
    text = (
        header
        + "NYC^C^0^0^US^NYC^\nJFK^A^0^1^US^NYC^\nOLD^A^0^2^US^NYC^2000-01-01\nFOR^A^0^3^CA^NYC^\nNYC^CA^0^0^US^NYC^\n"
    )
    assert [round(d) for d in metro_link_distances(text)] == [111]


def _rule_sites():
    def site(provider, region, lon, hub_km):
        return Site(provider, region, region, "city", 0.0, lon, f"N{region}", "HUB", 5.0, hub_km, "US")

    near = [site(p, f"{p}{n}", n * 0.2, 20.0 + n * 10.0) for p in "abc" for n in range(4)]
    return near + [site(p, f"{p}far", 20.0, 900.0) for p in "abc"]


def test_the_table_cap_is_the_sweep_selection():
    from ocm_regions.analysis.capsweep import RADII, SELECTION_RADIUS, select_cap, sweep, table_cap

    assert table_cap(_rule_sites()) == select_cap(sweep(_rule_sites(), RADII), SELECTION_RADIUS)["cap"]


def test_rule_stability_reports_the_rule_itself_over_resamples_and_vendor_drops():
    from ocm_regions.analysis.capsweep import rule_stability

    result = rule_stability(_rule_sites(), 100, runs=20, seed=1)
    assert set(result["without"]) == {"a", "b", "c"}
    assert result["runs"] == 20 and len(result["caps"]) == 20
    assert 0.0 <= result["share_selected"] <= 1.0
    assert result["low"] <= result["median"] <= result["high"]


def test_rule_stability_is_repeatable_for_a_seed():
    from ocm_regions.analysis.capsweep import rule_stability

    assert rule_stability(_rule_sites(), 100, runs=10, seed=7) == rule_stability(
        _rule_sites(), 100, runs=10, seed=7
    )


def test_summary_includes_the_rule_stability():
    from ocm_regions.analysis.capsweep import summarise

    assert "rule_stability" in summarise(_rule_sites(), [1.0, 2.0], runs=5)


def _rows(*triples):
    return [{"cap": c, "tp100": t, "fp100": f} for c, t, f in triples]


def test_merge_interval_ends_at_the_cap_that_adds_the_first_false_merge():
    from ocm_regions.analysis.capsweep import merge_interval

    rows = _rows((0.0, 800, 6), (84.0, 990, 6), (100.0, 990, 6), (121.0, 1000, 6), (122.0, 1000, 11))
    result = merge_interval(rows, 100, share=0.99)
    assert (result["low"], result["high"], result["pairs_high"], result["false_merges"]) == (
        84.0,
        122.0,
        1000,
        6,
    )


def test_merge_interval_with_no_added_false_merge_ends_at_the_last_cap():
    from ocm_regions.analysis.capsweep import merge_interval

    result = merge_interval(_rows((0.0, 800, 6), (84.0, 1000, 6)), 100)
    assert (result["low"], result["high"]) == (84.0, 84.0)


def test_interval_holds_a_cap_below_its_upper_edge_only():
    from ocm_regions.analysis.capsweep import interval_holds

    edge = {"low": 84.0, "high": 102.5}
    assert interval_holds(edge, 90) and interval_holds(edge, 84.0) and not interval_holds(edge, 102.5)


def test_city_resampling_keeps_all_regions_of_a_city_together():
    import random

    from ocm_regions.analysis.capsweep import city_sample

    sites = [Site(p, f"{p}{c}", c, "city", 0.0, 0.0, "N", "H", 1.0, 2.0, "US") for c in "XYZ" for p in "ab"]
    sample = city_sample(sites, random.Random(1))
    per_city = {}
    for site in sample:
        per_city.setdefault(site.city, []).append(site.provider)
    assert all(len(found) % 2 == 0 for found in per_city.values())


def test_rule_stability_reports_city_resampling_and_the_interval():
    from ocm_regions.analysis.capsweep import rule_stability

    result = rule_stability(_rule_sites(), 100, runs=5, seed=1)
    assert result["city"]["runs"] == 5 and 0.0 <= result["city"]["share_selected"] <= 1.0
    for key in ("lower_median", "upper_median", "inside_interval"):
        assert key in result["regions"] and key in result["city"]


def test_rule_stability_counts_how_often_the_interval_holds_the_selected_cap():
    from ocm_regions.analysis.capsweep import rule_stability

    result = rule_stability(_rule_sites(), 100, runs=5, seed=1)
    assert 0.0 <= result["city"]["interval_holds_selected"] <= 1.0


def test_the_summary_reports_regions_moved_at_the_selected_cap():
    from ocm_regions.analysis.capsweep import summarise

    result = summarise(_rule_sites(), [1.0, 2.0], runs=5)
    cap = result["selection"]["cap"]
    assert not any("published" in row["label"] for row in result["named"])
    assert all(site["hub_km"] > cap for site in result["off_hub"])


def test_named_caps_are_listed_in_order_of_distance():
    from ocm_regions.analysis.capsweep import _named_caps

    values = [value for value, _ in _named_caps({"cap": 90.0, "peak": 84.1})]
    assert values == sorted(values)
