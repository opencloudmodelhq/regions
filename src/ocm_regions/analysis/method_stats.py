# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Paired tests and error profiles for the placement methods, computed from saved answers."""

import csv
import json
import math
import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import combinations
from pathlib import Path
from typing import Final, NotRequired, TypeAlias, TypedDict, cast

from ocm_regions.common.geo import haversine_km
from ocm_regions.common.schema import Kilometres, Point
from ocm_regions.common.stats import wilson
from ocm_regions.fetch.sources import StrPath
from ocm_regions.place.placer import RULE_STEPS, PlacerRow, step
from ocm_regions.publish.paper import macros_tex

Key: TypeAlias = tuple[str, str]
Places: TypeAlias = Mapping[Key, Point]
Outcomes: TypeAlias = dict[Key, bool]
Curve: TypeAlias = list[list[float]]

AGREE_KM: Final = 50
CURVE_KM: Final = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000)

DIGIT_WORDS: Final = dict(
    zip(
        "0123456789",
        ("Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"),
        strict=True,
    )
)

THRESHOLDS: Final = (10, 25, 50, 100)


class AnswerRow(TypedDict):
    """One method's answer for a region, from the rules or from a saved answer file."""

    provider: str
    region_id: str
    status: str
    lat: float | str
    lon: float | str
    step: NotRequired[str]


class McNemar(TypedDict):
    """A McNemar test on paired outcomes of two methods."""

    pairs: int
    only_first: int
    only_second: int
    p: float
    p_holm: NotRequired[float]


class Agreement(TypedDict):
    """The share of regions two methods agree on, and Cohen's kappa."""

    pairs: int
    share: float
    kappa: float


class ErrorProfile(TypedDict):
    """A method's distances from the reference: median, 90th percentile and counts within each threshold."""

    answered: int
    median_km: float
    p90_km: float
    misses: int
    miss_median_km: float
    within: dict[int, int]


class VendorScore(TypedDict):
    """One method's answers and matches for one vendor."""

    regions: int
    answered: int
    matched: int
    low: float
    high: float


class StepPoint(TypedDict):
    """Cumulative coverage and accuracy after one step of the rules."""

    step: str
    answered: int
    matched: int
    coverage: float


class StatsReport(TypedDict):
    """Every statistic the method comparison reports."""

    regions: int
    curves: dict[str, Curve]
    mcnemar: dict[str, McNemar]
    mcnemar_answered: dict[str, McNemar]
    agreement: dict[str, Agreement]
    all_agree: int
    errors: dict[str, ErrorProfile]
    vendors: dict[str, dict[str, VendorScore]]
    steps: list[StepPoint]


def agreement(first: Mapping[Key, bool], second: Mapping[Key, bool]) -> Agreement:
    """Raw agreement and Cohen's kappa on matched or not.

    Args:
        first: Outcome per region for the first method.
        second: Outcome per region for the second method.

    Returns:
        The agreement share and kappa.
    """
    shared = sorted(first.keys() & second.keys())
    if not shared:
        return {"pairs": 0, "share": 0.0, "kappa": 0.0}
    observed = sum(first[k] == second[k] for k in shared) / len(shared)
    rate_a = sum(first[k] for k in shared) / len(shared)
    rate_b = sum(second[k] for k in shared) / len(shared)
    expected = rate_a * rate_b + (1 - rate_a) * (1 - rate_b)
    kappa = 1.0 if expected == 1 else (observed - expected) / (1 - expected)
    return {"pairs": len(shared), "share": observed, "kappa": kappa}


def answered_outcomes(rows: Sequence[AnswerRow], places: Places, km: Kilometres = AGREE_KM) -> Outcomes:
    """Whether each answered region is matched, leaving out regions the method did not answer.

    Args:
        rows: Answers of one method.
        places: Reference place per region.
        km: Agreement radius in kilometres.

    Returns:
        Match flags keyed by region.
    """
    answered = {_key(r) for r in rows if _distance(r, places) is not None}
    return {k: v for k, v in outcomes(rows, places, km).items() if k in answered}


def compute(
    methods: Mapping[str, Sequence[AnswerRow]], all_places: Places, models: Sequence[str]
) -> StatsReport:
    """Compute every statistic for the methods on the regions the rules cover.

    Args:
        methods: Answers per method.
        all_places: Reference place of every region.
        models: Model names.

    Returns:
        The report.
    """
    covered = {_key(r) for r in methods["rules"]}
    places = {k: v for k, v in all_places.items() if k in covered}
    scored = {name: outcomes(rows, places) for name, rows in methods.items()}
    answered = {name: answered_outcomes(rows, places) for name, rows in methods.items()}
    pairs: list[tuple[str, str]] = [("rules", m) for m in models] + list(combinations(models, 2))
    return {
        "regions": len(places),
        "curves": {name: threshold_curve(rows, places) for name, rows in methods.items()},
        "mcnemar": _with_holm({f"{a}|{b}": mcnemar(scored[a], scored[b]) for a, b in pairs}),
        "mcnemar_answered": _with_holm({f"{a}|{b}": mcnemar(answered[a], answered[b]) for a, b in pairs}),
        "agreement": {f"{a}|{b}": agreement(scored[a], scored[b]) for a, b in combinations(models, 2)},
        "all_agree": sum(len({scored[m][k] for m in models}) == 1 for k in places) if models else 0,
        "errors": {name: error_profile(rows, places) for name, rows in methods.items()},
        "vendors": {name: per_vendor(rows, places) for name, rows in methods.items()},
        "steps": step_curve(methods["rules"], places),
    }


def error_profile(
    rows: Iterable[AnswerRow], places: Places, thresholds: Sequence[int] = THRESHOLDS
) -> ErrorProfile:
    """How far answers fall from the reference, over answered regions that have one.

    Args:
        rows: Answers of one method.
        places: Reference place per region.
        thresholds: Distance thresholds in kilometres.

    Returns:
        The profile.
    """
    distances = [d for d in (_distance(r, places) for r in rows) if d is not None]
    if not distances:
        return {
            "answered": 0,
            "median_km": 0.0,
            "p90_km": 0.0,
            "misses": 0,
            "miss_median_km": 0.0,
            "within": {t: 0 for t in thresholds},
        }
    misses = [d for d in distances if d > AGREE_KM]
    return {
        "answered": len(distances),
        "median_km": statistics.median(distances),
        "p90_km": _percentile(distances, 0.9),
        "misses": len(misses),
        "miss_median_km": statistics.median(misses) if misses else 0.0,
        "within": {t: sum(d <= t for d in distances) for t in thresholds},
    }


def gather(rules: Iterable[PlacerRow], files: Mapping[str, StrPath]) -> dict[str, list[AnswerRow]]:
    """The rules' placements with their steps, and each model answer file that exists.

    Args:
        rules: The rules' rows.
        files: Result file per method.

    Returns:
        Answer rows keyed by method.
    """
    methods: dict[str, list[AnswerRow]] = {
        "rules": [
            {
                "provider": r["provider"],
                "region_id": r["region_id"],
                "status": "answered" if r["city"] else "abstained",
                "lat": r["lat"],
                "lon": r["lon"],
                "step": step(r),
            }
            for r in rules
        ]
    }
    for name, path in files.items():
        if Path(path).exists():
            with Path(path).open(encoding="utf-8", newline="") as handle:
                methods[name] = cast(list[AnswerRow], list(csv.DictReader(handle)))
    return methods


def holm(p_values: Mapping[str, float]) -> dict[str, float]:
    """Holm step-down adjustment over one family of tests.

    Args:
        p_values: p-value per test.

    Returns:
        Adjusted p-values keyed by test.
    """
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (name, p) in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - rank) * p))
        adjusted[name] = running
    return adjusted


def mcnemar(first: Mapping[Key, bool], second: Mapping[Key, bool]) -> McNemar:
    """Exact two-sided McNemar test on the regions both methods were scored on.

    Args:
        first: Outcome per region for the first method.
        second: Outcome per region for the second method.

    Returns:
        The discordant counts and the p-value.
    """
    shared = first.keys() & second.keys()
    only_first = sum(first[k] and not second[k] for k in shared)
    only_second = sum(second[k] and not first[k] for k in shared)
    n = only_first + only_second
    tail = sum(math.comb(n, i) for i in range(min(only_first, only_second) + 1)) / 2**n if n else 0.5
    return {
        "pairs": len(shared),
        "only_first": only_first,
        "only_second": only_second,
        "p": min(1.0, 2 * tail),
    }


def outcomes(rows: Iterable[AnswerRow], places: Places, km: Kilometres = AGREE_KM) -> Outcomes:
    """Whether each referenced region is matched; a missing or abstained answer is a miss.

    Args:
        rows: Answers of one method.
        places: Reference place per region.
        km: Agreement radius in kilometres.

    Returns:
        Match flags keyed by region.
    """
    found = {k: False for k in places}
    for row in rows:
        distance = _distance(row, places)
        if distance is not None:
            found[_key(row)] = distance <= km
    return found


def per_vendor(
    rows: Iterable[AnswerRow], places: Places, km: Kilometres = AGREE_KM
) -> dict[str, VendorScore]:
    """A method's answers and matches per vendor, with Wilson intervals.

    Args:
        rows: Answers of one method.
        places: Reference place per region.
        km: Agreement radius in kilometres.

    Returns:
        Scores keyed by vendor.
    """
    groups: defaultdict[str, list[Kilometres | None]] = defaultdict(list)
    for row in rows:
        if _key(row) in places:
            groups[row["provider"]].append(_distance(row, places))
    result: dict[str, VendorScore] = {}
    for provider, distances in sorted(groups.items()):
        answered = [d for d in distances if d is not None]
        matched = sum(d <= km for d in answered)
        low, high = wilson(matched, len(answered))
        result[provider] = {
            "regions": len(distances),
            "answered": len(answered),
            "matched": matched,
            "low": low,
            "high": high,
        }
    return result


def stats_macros(report: StatsReport) -> dict[str, object]:
    """Paper macros for the method statistics.

    Args:
        report: Method statistics.

    Returns:
        Macro values keyed by name.
    """
    values: dict[str, object] = {}
    for pair, test in report["mcnemar"].items():
        a, b = pair.split("|")
        values |= {
            _macro_name(a, "vs", b, "p"): _p(test["p"]),
            _macro_name(a, "vs", b, "holm"): _p(test.get("p_holm", test["p"])),
            _macro_name(a, "vs", b, "only", "first"): test["only_first"],
            _macro_name(a, "vs", b, "only", "second"): test["only_second"],
        }
    for pair, test in report["mcnemar_answered"].items():
        a, b = pair.split("|")
        values |= {
            _macro_name(a, "vs", b, "answered", "p"): _p(test["p"]),
            _macro_name(a, "vs", b, "answered", "holm"): _p(test.get("p_holm", test["p"])),
            _macro_name(a, "vs", b, "answered", "pairs"): test["pairs"],
            _macro_name(a, "vs", b, "answered", "only", "first"): test["only_first"],
            _macro_name(a, "vs", b, "answered", "only", "second"): test["only_second"],
        }
    for pair, test in report["agreement"].items():
        a, b = pair.split("|")
        values |= {
            _macro_name(a, "and", b, "kappa"): f"{test['kappa']:.2f}",
            _macro_name(a, "and", b, "agree"): f"{test['share'] * 100:.1f}",
        }
    for name, profile in report["errors"].items():
        values |= {
            _macro_name(name, "median", "km"): f"{profile['median_km']:.0f}",
            _macro_name(name, "p90", "km"): f"{profile['p90_km']:.0f}",
            _macro_name(name, "misses"): profile["misses"],
            _macro_name(name, "miss", "median", "km"): f"{profile['miss_median_km']:.0f}",
        }
    return values | {"statAllAgree": report["all_agree"]}


def stats_table(report: StatsReport, labels: Mapping[str, str]) -> str:
    """The table of each method's error profile.

    Args:
        report: Method statistics.
        labels: Display name per method.

    Returns:
        The LaTeX source of the table.
    """
    head = " & ".join(f"$\\le${t}~km" for t in THRESHOLDS)
    lines = [
        "\\begin{tabular}{lrrr" + "r" * len(THRESHOLDS) + "}",
        "\\toprule",
        f"Method & Answers & Misses & Median miss km & {head} \\\\",
        "\\midrule",
    ]
    for name, profile in report["errors"].items():
        shares = " & ".join(
            f"{profile['within'][t] / profile['answered'] * 100:.0f}\\%" if profile["answered"] else "--"
            for t in THRESHOLDS
        )
        lines.append(
            f"{labels.get(name, name)} & {profile['answered']} & {profile['misses']} & {profile['miss_median_km']:.0f} & {shares} \\\\"
        )
    return "\n".join([*lines, "\\bottomrule", "\\end{tabular}", ""])


def step_curve(
    rows: Sequence[AnswerRow], places: Places, order: Sequence[str] = RULE_STEPS, km: Kilometres = AGREE_KM
) -> list[StepPoint]:
    """Coverage and accuracy as the rules' steps are accepted from strongest to weakest.

    Args:
        rows: Answers of one method.
        places: Reference place per region.
        order: Steps of the rules, in order.
        km: Agreement radius in kilometres.

    Returns:
        One cumulative point per step.
    """
    total = sum(_key(r) in places for r in rows)
    curve: list[StepPoint] = []
    answered, matched = 0, 0
    for name in order:
        for distance in (_distance(r, places) for r in rows if r.get("step") == name):
            if distance is not None:
                answered += 1
                matched += distance <= km
        curve.append(
            {
                "step": name,
                "answered": answered,
                "matched": matched,
                "coverage": answered / total if total else 0.0,
            }
        )
    return curve


def threshold_curve(rows: Iterable[AnswerRow], places: Places, steps: Sequence[int] = CURVE_KM) -> Curve:
    """The share of answered regions within each distance of the reference place.

    Args:
        rows: Answers of one method.
        places: Reference place per region.
        steps: Distance thresholds in kilometres.

    Returns:
        Shares keyed by threshold.
    """
    distances = [d for d in (_distance(r, places) for r in rows) if d is not None]
    return [[km, sum(d <= km for d in distances) / len(distances) if distances else 0.0] for km in steps]


def write_method_stats(
    methods: Mapping[str, Sequence[AnswerRow]],
    places: Places,
    models: Sequence[str],
    out: StrPath,
    generated: StrPath,
    labels: Mapping[str, str] | None = None,
) -> StatsReport:
    """Compute the method statistics and write them as JSON.

    Args:
        methods: Answers per method.
        places: Reference place per region.
        models: Model names.
        out: File to write.
        generated: The paper's generated folder.
        labels: Display name per method, if any.

    Returns:
        The report.
    """
    report = compute(methods, places, models)
    _ = Path(out).write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    folder = Path(generated)
    _ = (folder / "method_stats_macros.tex").write_text(macros_tex(stats_macros(report)), encoding="utf-8")
    _ = (folder / "method_stats_table.tex").write_text(
        stats_table(report, labels or {"rules": "Rules"}), encoding="utf-8"
    )
    return report


def _distance(row: AnswerRow, places: Places) -> Kilometres | None:
    """Distance from an answer to the reference place, or None when there is no answer or no reference.

    Args:
        row: Answer of one method for one region.
        places: Reference place per region.

    Returns:
        The distance in kilometres, or None.
    """
    place = places.get(_key(row))
    if not place or row["status"] != "answered" or row["lat"] == "":
        return None
    return haversine_km((float(row["lat"]), float(row["lon"])), place)


def _key(row: AnswerRow) -> Key:
    """The vendor and region of an answer.

    Args:
        row: Answer of one method for one region.

    Returns:
        The vendor and region ID.
    """
    return row["provider"], row["region_id"]


def _macro_name(*parts: str) -> str:
    """A LaTeX macro name from the parts, in camel case with no digits or punctuation.

    Args:
        parts: Words to join into the name.

    Returns:
        The macro name.
    """
    words: list[str] = []
    for part in parts:
        for word in re.split(r"[^A-Za-z0-9]+", part):
            words.append("".join(DIGIT_WORDS.get(c, c) for c in word).capitalize() if word else "")
    return "stat" + "".join(words)


def _p(value: float) -> str:
    """A p-value to three decimals, or ``<0.001``.

    Args:
        value: The p-value.

    Returns:
        The text to print.
    """
    return "<0.001" if value < 0.001 else f"{value:.3f}"


def _percentile(values: Iterable[float], share: float) -> float:
    """A percentile of the values by linear interpolation, with the share between 0 and 1.

    Args:
        values: Numbers to summarise.
        share: Percentile as a share between 0 and 1.

    Returns:
        The percentile value.
    """
    ordered = sorted(values)
    position = (len(ordered) - 1) * share
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _with_holm(family: Mapping[str, McNemar]) -> dict[str, McNemar]:
    """The tests of one family with Holm-adjusted p-values added.

    Args:
        family: Tests that form one family.

    Returns:
        The tests, each with ``p_holm`` set.
    """
    adjusted = holm({pair: test["p"] for pair, test in family.items()})
    return {pair: {**test, "p_holm": adjusted[pair]} for pair, test in family.items()}
