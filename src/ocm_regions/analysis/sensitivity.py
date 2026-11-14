# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Rerun the placer with other values of each round number we chose, and show what changes."""

import csv
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Final, TypedDict

from ocm_regions.common import data
from ocm_regions.common.data import ReferencePlacement
from ocm_regions.common.schema import RegionKey
from ocm_regions.common.stats import wilson
from ocm_regions.fetch.sources import StrPath
from ocm_regions.place import feeds, placer
from ocm_regions.place.placer import Gazetteer, PlacerRow
from ocm_regions.regions.parsers import Region

FIELDS: Final = [
    "setting",
    "value",
    "placed",
    "matched",
    "abstained",
    "low",
    "high",
    "holdout_placed",
    "holdout_matched",
]

IN_USE: Final[dict[str, float]] = {
    "agreement radius km": 50,
    "minimum population": 20000,
    "minimum feed share": 0.5,
}

SETTINGS: Final[list[tuple[str, ModuleType, str, tuple[float, ...]]]] = [
    ("agreement radius km", placer, "AGREE_KM", (25, 50, 75, 100)),
    ("minimum population", placer, "MIN_POPULATION", (5000, 10000, 20000, 50000, 100000)),
    ("minimum feed share", feeds, "MIN_SHARE", (0.3, 0.5, 0.7, 0.9)),
]


class SensitivityRow(TypedDict):
    """The rules' placed and matched counts under one setting value."""

    setting: str
    value: float
    placed: int
    matched: int
    abstained: int
    low: float
    high: float
    holdout_placed: int
    holdout_matched: int


@contextmanager
def overridden(module: ModuleType, attribute: str, value: float) -> Iterator[None]:
    """Set a module constant for the length of a block, then restore it.

    Args:
        module: Module that holds the constant.
        attribute: Name of the module constant to override.
        value: Value to set while the block runs.

    Yields:
        Nothing; the block runs while the value is set.
    """
    before: object = getattr(module, attribute)
    setattr(module, attribute, value)
    try:
        yield
    finally:
        setattr(module, attribute, before)


def run_sensitivity(
    raw: StrPath, inputs: StrPath, output: StrPath, paper: StrPath | None = None
) -> list[SensitivityRow]:
    """Rerun the rules under each alternative setting and write the results.

    Args:
        raw: The data/raw folder.
        inputs: Repository root that holds the input folders.
        output: Output folder.
        paper: The paper folder; nothing is written there when None.

    Returns:
        One row per setting value.
    """
    raw, inputs = Path(raw), Path(inputs)
    regions, gaz = data.load_regions(raw), placer.load_gazetteer(raw)
    reference = data.load_reference(inputs)
    results: list[SensitivityRow] = []
    for name, module, attribute, values in SETTINGS:
        for value in values:
            with overridden(module, attribute, value):
                results.append(summarise_run(name, value, _run_once(regions, gaz, raw, reference)))
    with (Path(output) / "sensitivity.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(results)
    if paper:
        _ = (Path(paper) / "generated" / "sensitivity.tex").write_text(
            sensitivity_table(results), encoding="utf-8"
        )
    return results


def sensitivity_table(rows: Sequence[SensitivityRow]) -> str:
    """The table of placed and matched counts per setting, with the values in use marked.

    Args:
        rows: Sensitivity results.

    Returns:
        The LaTeX source of the table.
    """
    lines = [
        r"\begin{tabular}{llrrr}",
        r"\toprule",
        r"Setting & Value & Placed & Matched & 95\% range \\",
        r"\midrule",
    ]
    for row in rows:
        mark = "*" if IN_USE.get(row["setting"]) == row["value"] else ""
        lines.append(
            f"{row['setting']} & {row['value']}{mark} & {row['placed']} & {row['matched']} & {row['low'] * 100:.1f}--{row['high'] * 100:.1f}\\% \\\\"
        )
    return "\n".join([*lines, r"\bottomrule", r"\end{tabular}", ""])


def summarise_run(setting: str, value: float, rows: Sequence[PlacerRow]) -> SensitivityRow:
    """Count the regions placed and matched under one setting value.

    Args:
        setting: Name of the setting.
        value: Value the setting was given.
        rows: Placer rows.

    Returns:
        The row.
    """
    placed = [r for r in rows if r["city"] and r.get("matches_curated")]
    matched = sum(r.get("matches_curated") == "yes" for r in placed)
    held = [r for r in placed if r["split"] == "holdout"]
    low, high = wilson(matched, len(placed))
    return {
        "setting": setting,
        "value": value,
        "placed": len(placed),
        "matched": matched,
        "abstained": sum(not r["city"] for r in rows),
        "low": round(low, 4),
        "high": round(high, 4),
        "holdout_placed": len(held),
        "holdout_matched": sum(r.get("matches_curated") == "yes" for r in held),
    }


def _run_once(
    regions: Sequence[Region], gaz: Gazetteer, raw: Path, reference: Mapping[RegionKey, ReferencePlacement]
) -> list[PlacerRow]:
    """Place every region with the current settings and compare with the reference.

    Args:
        regions: Published regions.
        gaz: Gazetteer of cities.
        raw: The data/raw folder.
        reference: Reference placement per region.

    Returns:
        The compared placer rows.
    """
    rows = placer.place_regions(regions, gaz, placer.feed_points(raw))
    return placer.compare_to_reference(rows, reference)
