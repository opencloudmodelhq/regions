# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Append-only assignment of vendor-scoped resource codes."""

import re
from collections.abc import Collection, Iterable, Mapping
from typing import TypeAlias

from ocm_regions.common.schema import ProviderId, RegionId, RegionKey

CodeRegistry: TypeAlias = dict[RegionKey, str]


def assign_codes(
    rows: Iterable[tuple[ProviderId, RegionId, str]], registry: Mapping[RegionKey, str]
) -> tuple[CodeRegistry, CodeRegistry]:
    """Assign codes to (provider, region_id, base) rows, keeping every code the registry already holds.

    Args:
        rows: Vendor, region and base code of each region.
        registry: Codes already assigned, keyed by region.

    Returns:
        The codes of the given rows, and the updated registry.
    """
    updated: CodeRegistry = dict(registry)
    ordered = sorted(rows)
    current: CodeRegistry = {(p, r): b for p, r, b in ordered}
    for key, base in current.items():
        if key in updated and not _base_matches(updated[key], base):
            del updated[key]
    for provider, region_id, base in ordered:
        key = (provider, region_id)
        if key not in updated:
            used = {code for (p, _), code in updated.items() if p == provider}
            updated[key] = _next_free(base, used)
    return {key: updated[key] for key in current}, updated


def _base_matches(code: str, base: str) -> bool:
    """Whether a code is the base, or the base followed by a number.

    Args:
        code: Code to test.
        base: Code without a number.

    Returns:
        True when the code belongs to the base.
    """
    return re.fullmatch(rf"{re.escape(base)}\d*", code) is not None


def _next_free(base: str, used: Collection[str]) -> str:
    """The base when unused, else the base with the lowest free number from 2.

    Args:
        base: Code without a number.
        used: Codes already taken.

    Returns:
        The free code.
    """
    if base not in used:
        return base
    number = 2
    while f"{base}{number}" in used:
        number += 1
    return f"{base}{number}"
