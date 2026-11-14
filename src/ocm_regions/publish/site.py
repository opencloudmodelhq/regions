# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Render a single-file HTML explorer of the taxonomy."""

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from ocm_regions.common.schema import CallRow, EvidenceRecord, RegionRow
from ocm_regions.tables.coverage import Coverage
from ocm_regions.tables.grades import weak_regions

TEMPLATE: Final = (Path(__file__).parent / "site_template.html").read_text(encoding="utf-8")


def render_site(
    rows: Sequence[RegionRow],
    evidence: Sequence[EvidenceRecord],
    calls: Sequence[CallRow],
    coverage: Coverage,
    documents: Sequence[Mapping[str, object]],
) -> str:
    """The single-page site, with the data placed in the template.

    Args:
        rows: Region rows.
        evidence: Evidence records.
        calls: Review calls.
        coverage: Coverage per code scheme.
        documents: Indexed documents.

    Returns:
        The HTML of the page.
    """
    payload: dict[str, object] = {
        "weak": weak_regions(rows, calls, evidence),
        "regions": rows,
        "evidence": evidence,
        "calls": calls,
        "coverage": coverage,
        "documents": documents,
        "callCounts": Counter(c["kind"] for c in calls),
    }
    return TEMPLATE.replace("/*DATA*/null", _json_for_script(payload))


def _json_for_script(value: object) -> str:
    """JSON that is safe inside an HTML script element.

    Args:
        value: Value to encode.

    Returns:
        The JSON text.
    """
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
