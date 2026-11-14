# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Score the author's reading of every quoted evidence record against the stance it carries."""

import csv
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from ocm_regions.fetch.sources import StrPath
from ocm_regions.publish.paper import macros_tex

AGREES: Final = {"supports": "supports", "partial": "supports", "contradicts": "wrong-place"}
CANNOT_OPEN: Final = "cannot-open"
NOT_ON_PAGE: Final = "not-on-page"
PARSED: Final = frozenset({"vendor_region_list", "vendor_feed"})
VERDICTS: Final = "quote_review.csv"


def review_macros(
    verdicts: Sequence[Mapping[str, str]], evidence: Sequence[Mapping[str, str]]
) -> dict[str, object]:
    """Macro values the paper reads for the review of quoted records.

    Args:
        verdicts: One verdict per quoted evidence record; a record withdrawn since counts as a disagreement.
        evidence: Every evidence record; parsed list and feed rows are not quoted and are left out.

    Returns:
        Macro values keyed by name.

    Raises:
        ValueError: If a quoted record has no verdict.
    """
    stances = {e["evidence_id"]: e["stance"] for e in evidence if e["source_type"] not in PARSED}
    given = {v["evidence_id"]: v["verdict"] for v in verdicts}
    if missing := sorted(stances.keys() - given.keys()):
        raise ValueError(f"quoted records without a verdict: {', '.join(missing[:5])}")
    opened = {key: verdict for key, verdict in given.items() if verdict != CANNOT_OPEN}
    agree = sum(1 for key, verdict in opened.items() if AGREES.get(stances.get(key, "")) == verdict)
    return {
        "reviewRecords": len(given),
        "reviewCannotOpen": len(given) - len(opened),
        "reviewOpened": len(opened),
        "reviewOnPage": sum(1 for verdict in opened.values() if verdict != NOT_ON_PAGE),
        "reviewAgree": agree,
        "reviewDisagree": len(opened) - agree,
        "reviewWithdrawn": len(given.keys() - stances.keys()),
    }


def run_review(evaluation: StrPath, output: StrPath, paper: StrPath) -> dict[str, object]:
    """Score the saved verdicts and write the paper's macros.

    Args:
        evaluation: Folder holding the saved verdicts.
        output: Folder holding the evidence table.
        paper: Folder holding the paper.

    Returns:
        Macro values keyed by name.
    """
    macros = review_macros(_read(Path(evaluation) / VERDICTS), _read(Path(output) / "evidence.csv"))
    _ = (Path(paper) / "generated" / "review_macros.tex").write_text(macros_tex(macros), encoding="utf-8")
    return macros


def _read(path: Path) -> list[dict[str, str]]:
    """Rows of a CSV file.

    Args:
        path: The file.

    Returns:
        The rows.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
