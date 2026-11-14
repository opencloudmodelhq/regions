# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Figures and tables that compare the placement methods, written as pgfplots and LaTeX tables."""

import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final

from ocm_regions.analysis.method_stats import AGREE_KM, CURVE_KM, StatsReport, StepPoint, threshold_curve
from ocm_regions.common.stats import wilson
from ocm_regions.common.vendors import vendor_name
from ocm_regions.fetch.sources import StrPath

COLOURS: Final[dict[str, str]] = {
    "ocmblue": "2A78D6",
    "ocmorange": "EB6834",
    "ocmaqua": "1BAF7A",
    "ocmviolet": "6250D6",
    "ocmgrey": "8A8984",
}

LINE_STYLES: Final = (
    "ocmblue,solid",
    "ocmorange,dashed",
    "ocmaqua,densely dotted",
    "ocmviolet,dashdotted",
    "ocmgrey,loosely dashed",
    "ocmgrey,dotted",
)

MIN_LABEL_GAIN: Final = 2
TIER_XMIN: Final = 40

__all__ = [
    "CURVE_KM",
    "threshold_curve",
    "coverage_figure",
    "threshold_figure",
    "tier_figure",
    "pairs_table",
    "kappa_table",
    "vendor_table",
    "write_method_figures",
]

_AXIS: Final = "tick label style={font=\\footnotesize},label style={font=\\footnotesize},title style={font=\\footnotesize\\bfseries}"


def coverage_figure(report: StatsReport, labels: Mapping[str, str], names: Sequence[str]) -> str:
    """The figure of answered and matched shares per method.

    Args:
        report: Method statistics.
        labels: Display name per method.
        names: Method names, in display order.

    Returns:
        The LaTeX source of the figure.
    """
    shown = [labels.get(n, n) for n in names]
    values = {n: _shares(report, n) for n in names}
    bars = " ".join(f"({values[n][0]:.1f},{_label(labels.get(n, n))})" for n in names)
    dots = " ".join(
        f"({values[n][1]:.1f},{_label(labels.get(n, n))}) += ({values[n][3]:.1f},0) -= ({values[n][2]:.1f},0)"
        for n in names
    )
    xmin = _floor([v[1] - v[2] for v in values.values()])
    left = _coverage_axis(
        "Regions answered, \\%",
        0,
        shown,
        f"\\addplot[xbar,bar width=5pt,fill=ocmblue,draw=none] coordinates {{{bars}}};\n",
    )
    right = _coverage_axis(
        "Within 50~km, \\% (95\\% interval)",
        xmin,
        shown,
        f"\\addplot[only marks,mark=*,mark size=2.2pt,ocmblue,error bars/.cd,x dir=both,x explicit] coordinates {{{dots}}};\n",
        False,
    )
    return f"{_colours()}{left}\\quad\n{right}\n"


def kappa_table(report: StatsReport, labels: Mapping[str, str]) -> str:
    """The table of agreement and Cohen's kappa between pairs of methods.

    Args:
        report: Method statistics.
        labels: Display name per method.

    Returns:
        The LaTeX source of the table.
    """
    lines = [
        "\\begin{tabular}{llrr}",
        "\\toprule",
        "Method A & Method B & Same outcome & $\\kappa$ \\\\",
        "\\midrule",
    ]
    for pair, test in report["agreement"].items():
        a, b = pair.split("|")
        lines.append(
            f"{labels.get(a, a)} & {labels.get(b, b)} & {test['share'] * 100:.1f}\\% & {test['kappa']:.2f} \\\\"
        )
    return "\n".join([*lines, "\\bottomrule", "\\end{tabular}", ""])


def pairs_table(report: StatsReport, labels: Mapping[str, str]) -> str:
    """The table of McNemar tests between pairs of methods.

    Args:
        report: Method statistics.
        labels: Display name per method.

    Returns:
        The LaTeX source of the table.
    """
    lines = [
        "\\begin{tabular}{llrrrrrrr}",
        "\\toprule",
        " & & \\multicolumn{3}{c}{All regions} & \\multicolumn{4}{c}{Both answered} \\\\",
        "Method A & Method B & Only A & Only B & $p_{\\mathrm{Holm}}$ & Regions & Only A & Only B & $p_{\\mathrm{Holm}}$ \\\\",
        "\\midrule",
    ]
    for pair, test in report["mcnemar"].items():
        a, b = pair.split("|")
        both = report["mcnemar_answered"][pair]
        lines.append(
            f"{labels.get(a, a)} & {labels.get(b, b)} & {test['only_first']} & {test['only_second']} & {_p(test.get('p_holm', test['p']))} & "
            + f"{both['pairs']} & {both['only_first']} & {both['only_second']} & {_p(both.get('p_holm', both['p']))} \\\\"
        )
    return "\n".join([*lines, "\\bottomrule", "\\end{tabular}", ""])


def threshold_figure(report: StatsReport, labels: Mapping[str, str], names: Sequence[str]) -> str:
    """The figure of each method's match rate against the distance threshold.

    Args:
        report: Method statistics.
        labels: Display name per method.
        names: Method names, in display order.

    Returns:
        The LaTeX source of the figure.
    """
    plots = "".join(
        f"\\addplot[thick,no markers,{style}] coordinates {{{' '.join(f'({km},{share * 100:.2f})' for km, share in report['curves'][n])}}};\n"
        for n, style in zip(names, LINE_STYLES, strict=False)
    )
    legend = ",".join(_label(labels.get(n, n)) for n in names)
    ymin = _floor([share * 100 for n in names for _, share in report["curves"][n]])
    return (
        f"{_colours()}\\begin{{tikzpicture}}\n\\begin{{axis}}[width=0.96\\linewidth,height=5cm,xmode=log,xmin=1,xmax={CURVE_KM[-1]},ymin={ymin},ymax=100,"
        f"xlabel={{Distance from the reference place (km)}},ylabel={{Answers within distance, \\%}},legend style={{font=\\footnotesize,at={{(0.5,-0.32)}},anchor=north,legend columns=2}},"
        f",xmajorgrids,ymajorgrids,major grid style={{gray!25}},{_AXIS}]\n"
        f"\\draw[black,densely dashed] (axis cs:{AGREE_KM},{ymin}) -- (axis cs:{AGREE_KM},100);\n{plots}\\legend{{{legend}}}\n\\end{{axis}}\n\\end{{tikzpicture}}\n"
    )


def tier_figure(report: StatsReport, labels: Mapping[str, str], models: Sequence[str]) -> str:
    """The figure of the rules' cumulative coverage and accuracy by step, with the models marked.

    Args:
        report: Method statistics.
        labels: Display name per method.
        models: Model names.

    Returns:
        The LaTeX source of the figure.
    """
    points = _tier_points(report["steps"])
    steps = " ".join(f"({x:.1f},{y:.2f})" for _, x, y in points)
    shown = [p for i, p in enumerate(points) if i == 0 or p[1] - points[i - 1][1] >= MIN_LABEL_GAIN]
    names = "".join(
        f"\\node[font=\\scriptsize,anchor={'north east' if p is points[-1] else 'south west'}] at (axis cs:{p[1]:.1f},{p[2]:.2f}) {{{p[0]}}};\n"
        for p in shown
    )
    ymin = _floor([y for _, _, y in points] + [_shares(report, m)[1] for m in models], 2)
    return (
        f"{_colours()}\\begin{{tikzpicture}}\n\\begin{{axis}}[width=0.96\\linewidth,height=5.5cm,xmin={TIER_XMIN},xmax=102,ymin={ymin},ymax=100.8,"
        f"xlabel={{Regions answered, \\%}},ylabel={{Answers within 50~km, \\%}},legend pos=south west,legend style={{font=\\footnotesize}},xmajorgrids,ymajorgrids,major grid style={{gray!25}},{_AXIS}]\n"
        f"\\addplot[thick,mark=*,mark size=1.8pt,ocmblue] coordinates {{{steps}}};\n\\addlegendentry{{Rules, adding steps}}\n"
        f"{names}{_model_marks(report, labels, models)}\\end{{axis}}\n\\end{{tikzpicture}}\n"
    )


def vendor_table(report: StatsReport, labels: Mapping[str, str], names: Sequence[str]) -> str:
    """The table of each method's matches per vendor.

    Args:
        report: Method statistics.
        labels: Display name per method.
        names: Method names, in display order.

    Returns:
        The LaTeX source of the table.
    """
    shown = [n for n in names if n in report["vendors"]]
    vendors = sorted({v for n in shown for v in report["vendors"][n]})
    lines = [
        "\\begin{tabular}{l" + "r" * len(shown) + "}",
        "\\toprule",
        "Vendor & " + " & ".join(labels.get(n, n) for n in shown) + " \\\\",
        "\\midrule",
    ]
    for vendor in vendors:
        cells = [report["vendors"][n].get(vendor) for n in shown]
        lines.append(
            f"{vendor_name(vendor)} & "
            + " & ".join(f"{c['matched']}/{c['answered']}" if c else "--" for c in cells)
            + " \\\\"
        )
    return "\n".join([*lines, "\\bottomrule", "\\end{tabular}", ""])


def write_method_figures(
    report: StatsReport, labels: Mapping[str, str], names: Sequence[str], generated: StrPath
) -> None:
    """Write the method figures and tables to the paper's generated folder.

    Args:
        report: Method statistics.
        labels: Display name per method.
        names: Method names, in display order.
        generated: The paper's generated folder.
    """
    folder = Path(generated)
    models = [n for n in names if n != "rules"]
    files = {
        "method_coverage.tex": coverage_figure(report, labels, names),
        "method_thresholds.tex": threshold_figure(report, labels, names[:4]),
        "method_tiers.tex": tier_figure(report, labels, models),
        "method_pairs_table.tex": pairs_table(report, labels),
        "method_kappa_table.tex": kappa_table(report, labels),
        "method_vendor_table.tex": vendor_table(report, labels, names),
    }
    for name, text in files.items():
        _ = (folder / name).write_text(text, encoding="utf-8")


def _colours() -> str:
    """LaTeX colour definitions for the figures.

    Returns:
        The LaTeX commands.
    """
    return "".join(f"\\definecolor{{{name}}}{{HTML}}{{{value}}}\n" for name, value in COLOURS.items())


def _coverage_axis(
    title: str, xmin: float, labels: Sequence[str], body: str, show_labels: bool = True
) -> str:
    """One horizontal bar axis of the coverage figure.

    Args:
        title: Title of the panel.
        xmin: Lower limit of the x axis.
        labels: Tick labels.
        body: Plot commands to place inside the axis.
        show_labels: Whether to print the tick labels.

    Returns:
        The LaTeX source of the axis.
    """
    ticks = ",".join(_label(t) for t in labels)
    hide = "" if show_labels else "yticklabels={},"
    return (
        f"\\begin{{tikzpicture}}\n\\begin{{axis}}[scale only axis,width=6cm,height={0.6 * len(labels):.2f}cm,xmin={xmin},xmax=100,title={{{title}}},"
        f"symbolic y coords={{{ticks}}},ytick=data,{hide}y dir=reverse,enlarge y limits=0.12,xmajorgrids,major grid style={{gray!25}},{_AXIS}]\n{body}"
        "\\end{axis}\n\\end{tikzpicture}"
    )


def _floor(values: Iterable[float], step: int = 5) -> int:
    """An axis minimum: the lowest value rounded down to a step, at most 95.

    Args:
        values: Numbers to summarise.
        step: Step to round down to.

    Returns:
        The axis minimum.
    """
    return min(95, math.floor(min(values) / step) * step)


def _label(text: str) -> str:
    """A label wrapped in braces when it contains a comma, as pgfplots needs.

    Args:
        text: Label text.

    Returns:
        The label to use.
    """
    return f"{{{text}}}" if "," in text else text


def _model_marks(report: StatsReport, labels: Mapping[str, str], models: Iterable[str]) -> str:
    """Plot marks for the models on the tier figure.

    Args:
        report: Method statistics.
        labels: Display name per method.
        models: Model names.

    Returns:
        The plot commands.
    """
    placed = sorted((_shares(report, m)[0], _shares(report, m)[1], labels.get(m, m)) for m in models)
    return "".join(
        f"\\addplot[only marks,mark=square*,mark size=2pt,ocmorange{'' if i == 0 else ',forget plot'}] coordinates {{({x:.1f},{y:.2f})}};\n"
        + ("\\addlegendentry{Model methods}\n" if i == 0 else "")
        + f"\\node[font=\\scriptsize,anchor={'south east' if i % 2 else 'north east'}] at (axis cs:{x:.1f},{y:.2f}) {{{label}}};\n"
        for i, (x, y, label) in enumerate(placed)
    )


def _p(value: float) -> str:
    """A p-value to three decimals, or ``<0.001`` in LaTeX.

    Args:
        value: The p-value.

    Returns:
        The text to print.
    """
    return "$<$0.001" if value < 0.001 else f"{value:.3f}"


def _shares(report: StatsReport, name: str) -> tuple[float, float, float, float]:
    """A method's answered share, matched share and the interval of the matched share.

    Args:
        report: Method statistics.
        name: Method name.

    Returns:
        The four values, as percentages.
    """
    error = report["errors"][name]
    answered, matched = error["answered"], error["within"][AGREE_KM]
    low, high = wilson(matched, answered)
    share = matched / answered if answered else 0.0
    return answered / report["regions"] * 100, share * 100, (share - low) * 100, (high - share) * 100


def _tier_points(steps: Iterable[StepPoint]) -> list[tuple[str, float, float]]:
    """One point per step that adds answers, so steps that add none do not stack their labels.

    Args:
        steps: Cumulative points per step.

    Returns:
        Each point as step name, coverage and accuracy.
    """
    points: list[tuple[str, float, float]] = []
    seen = 0
    for point in steps:
        if point["answered"] > seen:
            points.append(
                (point["step"], point["coverage"] * 100, point["matched"] / point["answered"] * 100)
            )
            seen = point["answered"]
    return points
