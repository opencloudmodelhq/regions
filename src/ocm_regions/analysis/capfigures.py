# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Write the cap sweep as pgfplots figures, tables and macros for the paper."""

import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final, TypedDict

from ocm_regions.analysis.captypes import Band, CapSummary, NamedRow, RuleStability, Selection, SweepRow
from ocm_regions.common.vendors import vendor_name
from ocm_regions.fetch.sources import StrPath
from ocm_regions.publish.paper import latex_escape, macros_tex

X_MAX: Final = 320
_AXIS: Final = "tick label style={font=\\footnotesize},label style={font=\\footnotesize},title style={font=\\footnotesize\\bfseries}"

_STYLES: Final = [
    "blue!70!black,solid",
    "orange!85!black,dashed",
    "teal!80!black,densely dotted",
    "violet!80!black,dashdotted",
]


class PairCounts(TypedDict):
    """The lowest and highest number of pairs across an interval of caps."""

    pairs_low: float
    pairs_high: float


def bands_figure(summary: CapSummary) -> str:
    """The figure of regions moved in each band of hub distance, split into correct and false merges.

    Args:
        summary: Results of the cap analysis.

    Returns:
        The LaTeX source of the figure.
    """
    bins = [b for b in summary["bands"] if b["high"] <= 325]
    rho = summary["rho"]
    good = _coords((b["high"], b["good"]) for b in bins)
    bad = _coords((b["high"], b["bad"]) for b in bins)
    return (
        "\\begin{tikzpicture}\n\\begin{axis}[ybar stacked,bar width=11pt,width=0.96\\linewidth,height=5.4cm,ymin=0,xmin=10,xmax=340,xtick=data,"
        f"xlabel={{Upper edge of the cap band (km)}},ylabel={{New shared pairs}},legend style={{font=\\footnotesize}},{_AXIS}]\n"
        f"\\addplot[fill=blue!60!black,draw=none] coordinates {{{good}}};\n\\addplot[fill=orange!85!black,draw=none] coordinates {{{bad}}};\n"
        f"\\legend{{Pair within {rho} km,Pair over {rho} km apart}}\n\\end{{axis}}\n\\end{{tikzpicture}}\n"
    )


def f1_figure(summary: CapSummary) -> str:
    """The figure of F1 against the cap for each market radius.

    Args:
        summary: Results of the cap analysis.

    Returns:
        The LaTeX source of the figure.
    """
    radii = [50, 100, 150, 200]
    plots = "".join(
        f"\\addplot[const plot mark left,thick,no markers,{style}] coordinates {{{_coords(_steps(summary['sweep'], f'f1{rho}'))}}};\n"
        for rho, style in zip(radii, _STYLES, strict=False)
    )
    legend = ",".join(f"$\\rho={rho}$ km" for rho in radii)
    return (
        f"\\begin{{tikzpicture}}\n\\begin{{axis}}[width=0.96\\linewidth,height=6cm,xmin=0,xmax={X_MAX},ymin=0.8,ymax=1,xlabel={{Cap (km)}},"
        f"ylabel={{F1}},legend pos=north east,legend columns=2,legend style={{font=\\footnotesize}},{_AXIS}]\n"
        f"{_marks(summary['selection'], 0.8, 1)}{plots}\\legend{{{legend}}}\n\\end{{axis}}\n\\end{{tikzpicture}}\n"
    )


def measures_figure(summary: CapSummary) -> str:
    """The figure of pair recall, false merges and distance to the code against the cap.

    Args:
        summary: Results of the cap analysis.

    Returns:
        The LaTeX source of the figure.
    """
    rho, rows = summary["rho"], summary["sweep"]
    panels = [
        _step_panel(
            summary,
            f"recall{rho}",
            f"Pair recall at {rho} km, \\% (maximise)",
            *_bounds(rows, f"recall{rho}", 5, 100),
            100,
        ),
        _step_panel(
            summary,
            f"fp{rho}",
            f"False merges over {rho} km (minimise)",
            *_bounds(rows, f"fp{rho}", 10, floor=0),
        ),
        _step_panel(summary, "shared", "Regions on a shared code (maximise)", *_bounds(rows, "shared", 50)),
        _step_panel(
            summary,
            "max_km",
            "Furthest region from its code, km (minimise)",
            *_bounds(rows, "max_km", 50, floor=0),
        ),
    ]
    return f"{panels[0]}\\hfill\n{panels[1]}\n\n\\medskip\n{panels[2]}\\hfill\n{panels[3]}\n"


def pairs_text(interval: PairCounts) -> str:
    """A count of pairs as one number, or as a range when it varies across the interval.

    Args:
        interval: Pair counts at each end of the interval.

    Returns:
        The text to print.
    """
    low, high = interval["pairs_low"], interval["pairs_high"]
    return f"{low}" if low == high else f"{low} to {high}"


def rule_macros(rule: RuleStability) -> dict[str, object]:
    """Paper macros for how the selected cap holds when vendors are left out or regions resampled.

    Args:
        rule: Stability results for the selection rule.

    Returns:
        Macro values keyed by name.
    """
    moved = sorted(
        f"{vendor_name(v)} {cap:.0f}~km" for v, cap in rule["without"].items() if cap != rule["selected"]
    )
    text = f"{', '.join(moved[:-1])} and {moved[-1]}" if len(moved) > 1 else "".join(moved)
    return {
        "capRuleRuns": rule["runs"],
        "capRuleShare": f"{rule['share_selected'] * 100:.0f}",
        "capRuleLow": f"{rule['low']:.0f}",
        "capRuleMedian": f"{rule['median']:.0f}",
        "capRuleHigh": f"{rule['high']:.0f}",
        "capRuleKept": len(rule["without"]) - len(moved),
        "capRuleVendors": len(rule["without"]),
        "capRuleMoved": text,
        **_interval_macros(rule),
    }


def tradeoff_figure(summary: CapSummary) -> str:
    """The figure of pair recall against false merges, with the named caps marked.

    Args:
        summary: Results of the cap analysis.

    Returns:
        The LaTeX source of the figure.
    """
    rho = summary["rho"]
    line = _coords((r[f"fp{rho}"], r[f"recall{rho}"] * 100) for r in summary["sweep"])
    x_max = _bounds(summary["sweep"], f"fp{rho}", 5, floor=0)[1]
    y_min, y_max = _bounds(summary["sweep"], f"recall{rho}", 5, 100)
    return (
        f"\\begin{{tikzpicture}}\n\\begin{{axis}}[width=0.96\\linewidth,height=5.6cm,xmin=0,xmax={x_max},ymin={y_min},ymax={y_max},clip=false,"
        f"xlabel={{False merges over {rho} km}},ylabel={{Pair recall at {rho} km, \\%}},{_AXIS}]\n"
        f"\\addplot[thick,blue!70!black,mark=*,mark size=1.2pt] coordinates {{{line}}};\n{_tradeoff_labels(summary, rho)}"
        "\\end{axis}\n\\end{tikzpicture}\n"
    )


def write_cap_inputs(paper_dir: StrPath, summary: CapSummary) -> None:
    """Write the cap figures, tables and macros to the paper's generated folder.

    Args:
        paper_dir: The paper folder.
        summary: Results of the cap analysis.
    """
    out = Path(paper_dir) / "generated"
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "cap_macros": macros_tex(_macros(summary)),
        "cap_bands": bands_figure(summary),
        "cap_measures": measures_figure(summary),
        "cap_tradeoff": tradeoff_figure(summary),
        "cap_f1": f1_figure(summary),
        "cap_named": _named_table(summary),
        "cap_radius": _radius_table(summary),
        "cap_stability": _stability_table(summary),
        "cap_moved": _moved_table(summary),
    }
    for name, text in files.items():
        _ = (out / f"{name}.tex").write_text(text, encoding="utf-8")


def _bounds(
    rows: Sequence[SweepRow], key: str, step: float, scale: float = 1.0, floor: float | None = None
) -> tuple[float, float]:
    """Axis limits for a measure, rounded outward to a step.

    Args:
        rows: Sweep rows, ordered by cap.
        key: Sweep measure the axis shows.
        step: Step the limits are rounded to.
        scale: Factor applied to the values.
        floor: Fixed lower limit, if any.

    Returns:
        The lower and upper limit.
    """
    values = [r[key] * scale for r in rows]
    low = floor if floor is not None else math.floor(min(values) / step) * step
    high = math.ceil(max(values) / step) * step
    return low, max(high, low + step)


def _coords(points: Iterable[tuple[float, float]]) -> str:
    """Points as a pgfplots coordinate list.

    Args:
        points: Points as x and y.

    Returns:
        The coordinate text.
    """
    return " ".join(f"({x:.2f},{y:.4f})" for x, y in points)


def _interval_macros(rule: RuleStability) -> dict[str, object]:
    """Paper macros for the merge interval, or none when the summary has no interval.

    Args:
        rule: Stability results for the selection rule.

    Returns:
        Macro values keyed by name.
    """
    if "interval" not in rule:
        return {}
    interval, regions, city = rule["interval"], rule["regions"], rule["city"]
    return {
        "capIntervalLow": f"{interval['low']:.0f}",
        "capIntervalHigh": f"{interval['high']:.1f}",
        "capIntervalPairs": pairs_text(interval),
        "capIntervalFalse": interval["false_merges"],
        "capUpperMedian": f"{regions['upper_median']:.0f}",
        "capUpperLow": f"{regions['upper_p5']:.0f}",
        "capUpperHigh": f"{regions['upper_p95']:.0f}",
        "capLowerMedian": f"{regions['lower_median']:.0f}",
        "capLowerLow": f"{regions['lower_p5']:.0f}",
        "capLowerHigh": f"{regions['lower_p95']:.0f}",
        "capCityShare": f"{city['share_selected'] * 100:.0f}",
        "capCityMedian": f"{city['median']:.0f}",
        "capCityLow": f"{city['low']:.0f}",
        "capCityHigh": f"{city['high']:.0f}",
        "capCityUpperMedian": f"{city['upper_median']:.0f}",
        "capCityUpperLow": f"{city['upper_p5']:.0f}",
        "capCityUpperHigh": f"{city['upper_p95']:.0f}",
        "capCityLowerMedian": f"{city['lower_median']:.0f}",
        "capCityLowerLow": f"{city['lower_p5']:.0f}",
        "capCityLowerHigh": f"{city['lower_p95']:.0f}",
        "capCityInside": f"{city['inside_interval'] * 100:.0f}",
        "capRegionInside": f"{regions['inside_interval'] * 100:.0f}",
        "capRegionHolds": f"{regions['interval_holds_selected'] * 100:.0f}",
        "capCityHolds": f"{city['interval_holds_selected'] * 100:.0f}",
    }


def _macros(summary: CapSummary) -> dict[str, object]:
    """Paper macros for the cap selection.

    Args:
        summary: Results of the cap analysis.

    Returns:
        Macro values keyed by name.
    """
    chosen, rho = summary["selection"], summary["rho"]
    mixed = _mixed_band(summary["bands"])
    named = {r["label"]: r for r in summary["named"]}
    at = named[f"{chosen['cap']:.0f} km (rule)"]
    table_a, table_b = named["0 (nearest airport)"], named["None (major hub)"]
    interval = next(r for r in summary["radii"] if r["rho"] == rho)
    splits = sum(int(row[4]) for row in summary["splits"])
    return {
        "capUsed": f"{chosen['cap']:.0f}",
        "capSelected": f"{chosen['cap']:.0f}",
        "capLow": f"{chosen['low']:.0f}",
        "capHigh": f"{chosen['high']:.1f}",
        "capMidpoint": f"{chosen['midpoint']:.0f}",
        "capPeak": f"{chosen['peak']:.1f}",
        "capRegions": summary["regions"],
        "capCount": len(summary["sweep"]),
        "capRadius": rho,
        "capSharedAt": at["shared"],
        "capSharedA": table_a["shared"],
        "capSharedB": table_b["shared"],
        "capRecallAt": f"{_num(at, f'recall{rho}') * 100:.1f}",
        "capRecallA": f"{_num(table_a, f'recall{rho}') * 100:.1f}",
        "capRecallMax": f"{_num(table_b, f'recall{rho}') * 100:.1f}",
        "capFalseAt": at[f"fp{rho}"],
        "capFalseB": table_b[f"fp{rho}"],
        "capMaxAt": f"{_num(at, 'max_km'):.0f}",
        "capMaxB": f"{_num(table_b, 'max_km'):.0f}",
        "capBootLow": f"{interval['p5']:.0f}",
        "capBootHigh": f"{interval['p95']:.0f}",
        "capMovedCount": len(summary["off_hub"]),
        "capSplits": splits,
        "capBorderSplits": summary["border_splits"],
        "capOtherSplits": splits - summary["border_splits"],
        "capMixedGood": mixed["good"],
        "capMixedBad": mixed["bad"],
        "capMixedLow": f"{mixed['low']:.0f}",
        "capMixedHigh": f"{mixed['high']:.0f}",
        "metroLinks": summary["metro"]["links"],
        "metroNinetyFive": f"{summary['metro']['p95']:.0f}",
        "metroNinetyNine": f"{summary['metro']['p99']:.0f}",
        **rule_macros(summary["rule_stability"]),
    }


def _marks(selection: Selection, ymin: float, ymax: float) -> str:
    """The shaded interval and the line that mark the selected cap on a plot.

    Args:
        selection: Selected cap with its interval.
        ymin: Lower limit of the y axis.
        ymax: Upper limit of the y axis.

    Returns:
        The TikZ commands.
    """
    return (
        f"\\fill[black,opacity=0.08] (axis cs:{selection['low']:.2f},{ymin}) rectangle (axis cs:{selection['high']:.2f},{ymax});\n"
        f"\\draw[black,densely dashed] (axis cs:{selection['cap']:.0f},{ymin}) -- (axis cs:{selection['cap']:.0f},{ymax});\n"
    )


def _mixed_band(bands: Iterable[Band]) -> Band:
    """The first distance band that holds both correct and false merges, or an empty band.

    Args:
        bands: Distance bands from the sweep.

    Returns:
        The band.
    """
    none: Band = {"low": 0, "high": 0, "moved": 0, "good": 0, "bad": 0}
    return next((b for b in bands if b["good"] and b["bad"]), none)


def _moved_table(summary: CapSummary) -> str:
    """The table of regions whose hub lies beyond the cap.

    Args:
        summary: Results of the cap analysis.

    Returns:
        The LaTeX source of the table.
    """
    rows = [
        [
            vendor_name(s["provider"]),
            s["region_id"],
            s["city"],
            s["hub"],
            f"{s['hub_km']:.0f}",
            s["nearest"],
            f"{s['nearest_km']:.0f}",
        ]
        for s in sorted(summary["off_hub"], key=lambda s: (s["provider"], s["region_id"], s["city"]))
    ]
    return _table("lllrrrr", ["Vendor", "Region", "Place", "Hub", "km", "Instead", "km"], rows)


def _named_table(summary: CapSummary) -> str:
    """The table of the named caps and their scores.

    Args:
        summary: Results of the cap analysis.

    Returns:
        The LaTeX source of the table.
    """
    rho = summary["rho"]
    rows: list[list[object]] = [
        [
            r["label"],
            r["distinct"],
            r["shared"],
            f"{_num(r, f'recall{rho}') * 100:.1f}%",
            r[f"fp{rho}"],
            r["fp150"],
            f"{_num(r, 'mean_km'):.1f}",
            f"{_num(r, 'max_km'):.0f}",
            f"{_num(r, f'f1{rho}'):.3f}",
        ]
        for r in summary["named"]
    ]
    return _table(
        "lrrrrrrrr",
        ["Cap", "Codes", "Shared", "Recall", f"FM$>${rho}", "FM$>$150", "Mean km", "Max km", "F1"],
        rows,
    )


def _num(row: NamedRow, key: str) -> float:
    """A numeric field of a named row; raises TypeError when it holds text or nothing.

    Args:
        row: Scores of one named cap.
        key: Field to read.

    Returns:
        The value as a float.

    Raises:
        TypeError: If the field holds text or nothing.
    """
    value = row[key]
    if isinstance(value, str) or value is None:
        raise TypeError(f"{key} is not a number")
    return value


def _point_label(row: NamedRow, rho: int, label: str, anchor: str) -> str:
    """A text label beside one named cap on the trade-off plot.

    Args:
        row: Scores of one named cap.
        rho: Market radius in kilometres.
        label: Text of the label.
        anchor: TikZ anchor for the label.

    Returns:
        The TikZ node.
    """
    return f"\\node[font=\\footnotesize,anchor={anchor}] at (axis cs:{row[f'fp{rho}']},{_num(row, f'recall{rho}') * 100:.2f}) {{{label}}};\n"


def _radius_table(summary: CapSummary) -> str:
    """The table of the F1 peak and its bootstrap range for each market radius.

    Args:
        summary: Results of the cap analysis.

    Returns:
        The LaTeX source of the table.
    """
    rows = [
        [
            f"{r['rho']} km",
            f"{r['peak']:.0f} km",
            f"{r['p5']:.0f} to {r['p95']:.0f} km",
            f"{r['selected']:.0f} km",
        ]
        for r in summary["radii"]
    ]
    return _table("lrrr", ["Market radius $\\rho$", "F1 peak", "Bootstrap 5--95\\%", "Rule selects"], rows)


def _stability_table(summary: CapSummary) -> str:
    """The table of the F1 peak when each vendor is left out.

    Args:
        summary: Results of the cap analysis.

    Returns:
        The LaTeX source of the table.
    """
    return _table(
        "lrr",
        ["Test", "Regions", "F1 peak"],
        [[_test_label(r["test"]), r["regions"], f"{r['cap']:.0f} km"] for r in summary["stability"]],
    )


def _step_panel(
    summary: CapSummary, key: str, title: str, ymin: float, ymax: float, scale: float = 1.0
) -> str:
    """One step-plot panel of a sweep measure against the cap.

    Args:
        summary: Results of the cap analysis.
        key: Sweep measure to plot.
        title: Title of the panel.
        ymin: Lower limit of the y axis.
        ymax: Upper limit of the y axis.
        scale: Factor applied to the values.

    Returns:
        The LaTeX source of the panel.
    """
    return (
        f"\\begin{{tikzpicture}}\n\\begin{{axis}}[width=0.47\\linewidth,height=4.6cm,xmin=0,xmax={X_MAX},ymin={ymin},ymax={ymax},"
        f"title={{{title}}},xlabel={{Cap (km)}},{_AXIS}]\n{_marks(summary['selection'], ymin, ymax)}"
        f"\\addplot[const plot mark left,thick,blue!70!black,no markers] coordinates {{{_coords(_steps(summary['sweep'], key, scale))}}};\n"
        "\\end{axis}\n\\end{tikzpicture}"
    )


def _steps(rows: Sequence[SweepRow], key: str, scale: float = 1.0) -> list[tuple[float, float]]:
    """A sweep measure as step points, extended to the right edge of the plot.

    Args:
        rows: Sweep rows, ordered by cap.
        key: Sweep measure to plot.
        scale: Factor applied to the values.

    Returns:
        The points as cap and value.
    """
    points = [(r["cap"], r[key] * scale) for r in rows]
    return [*points, (X_MAX, points[-1][1])]


def _table(spec: str, head: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """A booktabs table from a column spec, a header and rows, with cells escaped.

    Args:
        spec: Column specification of the tabular.
        head: Column headings.
        rows: Table rows.

    Returns:
        The LaTeX source of the table.
    """
    lines = [f"\\begin{{tabular}}{{{spec}}}", r"\toprule", " & ".join(head) + r" \\", r"\midrule"]
    lines += [" & ".join(latex_escape(c) for c in row) + r" \\" for row in rows]
    return "\n".join([*lines, r"\bottomrule", r"\end{tabular}", ""])


def _test_label(test: str) -> str:
    """A stability test's label, with a vendor ID replaced by the vendor's name.

    Args:
        test: Label of the stability test.

    Returns:
        The label to print.
    """
    prefix = "Without "
    return prefix + vendor_name(test[len(prefix) :]) if test.startswith(prefix) else test


def _tradeoff_labels(summary: CapSummary, rho: int) -> str:
    """Labels for the named caps on the trade-off plot.

    Args:
        summary: Results of the cap analysis.
        rho: Market radius in kilometres.

    Returns:
        The TikZ nodes.
    """
    names = {"0 (nearest airport)": "Nearest airport", "None (major hub)": "Major hub"}
    top = max(_num(row, f"recall{rho}") for row in summary["named"])
    lines: list[str] = []
    for row in summary["named"]:
        label = row["label"]
        text = (names.get(label) if isinstance(label, str) else None) or f"{_num(row, 'cap'):.0f} km"
        lines.append(_point_label(row, rho, text, "south" if row[f"recall{rho}"] == top else "west"))
    return "".join(lines)
