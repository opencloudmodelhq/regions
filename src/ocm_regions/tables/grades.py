# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Evidence grades: how strongly a source ties a region to a place."""

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Final, Literal, TypeAlias, TypedDict

from ocm_regions.common.schema import GRADES as GRADES
from ocm_regions.common.schema import (
    CallRow,
    EvidenceRecord,
    Grade,
    Precision,
    ProviderId,
    QuoteCheck,
    RegionId,
    RegionKey,
    SourceType,
)

WeakTier: TypeAlias = Literal["no place", "partial", "third party only", "feed only", "disputed"]

CONFLICT_KINDS: Final = ("feed-conflict", "evidence-conflict")
PASSED_CHECKS: Final[tuple[QuoteCheck, ...]] = ("quote-found", "quote-found-saved-copy")
TIER_ORDER: Final[tuple[WeakTier, ...]] = ("no place", "partial", "third party only", "feed only", "disputed")

WEAK_FIELDS: Final = (
    "provider",
    "region_id",
    "stated_location",
    "city",
    "country",
    "grade",
    "precision",
    "tier",
    "flags",
    "reasoning",
)

WEAK_TIERS: Final[dict[Grade, WeakTier]] = {
    "assumed": "no place",
    "partial": "partial",
    "reported": "third party only",
    "vendor-feed": "feed only",
}

_BY_SOURCE: Final[dict[SourceType, Grade]] = {
    "vendor_region_list": "vendor-stated",
    "vendor": "vendor-document",
    "vendor_feed": "vendor-feed",
    "certification": "audit-document",
    "code_join": "public-record",
    "measurement": "measured",
    "government": "reported",
    "paper": "reported",
    "news": "reported",
    "directory": "reported",
    "peeringdb": "network-presence",
}


class GradedPlace(TypedDict):
    """What the weak-region list reads from a region row."""

    provider: ProviderId
    region_id: RegionId
    stated_location: str
    city: str
    country: str
    grade: Grade
    precision: Precision
    location_reasoning: str


class WeakRegion(TypedDict):
    """A region whose place needs review, with the reasons."""

    provider: ProviderId
    region_id: RegionId
    stated_location: str
    city: str
    country: str
    grade: Grade
    precision: Precision
    tier: WeakTier
    flags: str
    reasoning: str


def best_grade(records: Sequence[EvidenceRecord]) -> Grade:
    """The strongest grade among the records, or assumed when there are none.

    Args:
        records: Evidence records.

    Returns:
        The grade.
    """
    grades: list[Grade] = [_grade_in_context(r, records) for r in records] or ["assumed"]
    return min(grades, key=GRADES.index)


def grade_of(record: EvidenceRecord) -> Grade:
    """The grade one evidence record supports on its own, from its stance, source type and quote check.

    Args:
        record: Evidence record.

    Returns:
        The grade.
    """
    stance = record.get("stance")
    if stance == "contradicts":
        return "assumed"
    check = record.get("quote_check")
    if stance == "partial" or (check and check not in PASSED_CHECKS):
        return "partial"
    source = record.get("source_type")
    return _BY_SOURCE.get(source, "assumed") if source else "assumed"


def weak_regions(
    rows: Iterable[GradedPlace], calls: Iterable[CallRow], evidence: Iterable[EvidenceRecord]
) -> list[WeakRegion]:
    """Regions a reader should distrust first: weak grades, then strong grades that sources dispute.

    Args:
        rows: Region rows with grades.
        calls: Review calls.
        evidence: Evidence records.

    Returns:
        The flagged regions, weakest first.
    """
    conflicts, failed = _conflicts(calls), _failed_checks(evidence)
    listed: list[WeakRegion] = []
    for row in rows:
        key = (row["provider"], row["region_id"])
        tier = WEAK_TIERS.get(row.get("grade")) or ("disputed" if key in conflicts else None)
        if tier:
            listed.append(
                {
                    "provider": row["provider"],
                    "region_id": row["region_id"],
                    "stated_location": row["stated_location"],
                    "city": row["city"],
                    "country": row["country"],
                    "grade": row["grade"],
                    "precision": row["precision"],
                    "tier": tier,
                    "flags": "; ".join(_flags(key, conflicts, failed)),
                    "reasoning": row["location_reasoning"],
                }
            )
    return sorted(listed, key=lambda r: TIER_ORDER.index(r["tier"]))


def _conflicts(calls: Iterable[CallRow]) -> dict[RegionKey, list[CallRow]]:
    """Calls of a conflict kind, grouped by region.

    Args:
        calls: Review calls.

    Returns:
        Calls keyed by vendor and region.
    """
    conflicts: dict[RegionKey, list[CallRow]] = {}
    for call in calls:
        if call["kind"] in CONFLICT_KINDS:
            conflicts.setdefault((call["provider"], call["region_id"]), []).append(call)
    return conflicts


def _failed_checks(evidence: Iterable[EvidenceRecord]) -> dict[RegionKey, int]:
    """The number of failed quote checks per region.

    Args:
        evidence: Evidence records.

    Returns:
        Counts keyed by vendor and region.
    """
    failed: dict[RegionKey, int] = {}
    for record in evidence:
        check = record.get("quote_check")
        if check and check not in PASSED_CHECKS:
            key = (record["provider"], record["region_id"])
            failed[key] = failed.get(key, 0) + 1
    return failed


def _flags(
    key: RegionKey, calls: Mapping[RegionKey, Sequence[CallRow]], failed: Mapping[RegionKey, int]
) -> list[str]:
    """The reasons a region is flagged: its calls and any failed quote checks.

    Args:
        key: Vendor and region ID.
        calls: Review calls keyed by region.
        failed: Number of failed quote checks per region.

    Returns:
        The reasons.
    """
    flags = [c["detail"] for c in calls.get(key, [])]
    return [*flags, f"{failed[key]} quote failed its check"] if failed.get(key) else flags


def _grade_in_context(record: EvidenceRecord, records: Sequence[EvidenceRecord]) -> Grade:
    """A record's grade given the others; a public record without its audited code counts as partial.

    Args:
        record: Evidence record.
        records: Evidence records.

    Returns:
        The grade.
    """
    grade = grade_of(record)
    return "partial" if grade == "public-record" and not _join_holds(record, records) else grade


def _join_holds(record: EvidenceRecord, records: Iterable[EvidenceRecord]) -> bool:
    """Whether a government document's facility code is also named in an audit list for the region.

    Args:
        record: Evidence record.
        records: Evidence records.

    Returns:
        True when the join is backed by an audit list.
    """
    code = record.get("join_code", "")
    audited = any(r.get("source_type") == "certification" and r.get("quote") == code for r in records)
    return bool(code) and audited and _names_code(record.get("quote", ""), code)


def _names_code(quote: str, code: str) -> bool:
    """Match a facility code despite spacing and zero padding, so "Dub 6" matches DUB06 but BN13 does not match BN1.

    Args:
        quote: Quoted text.
        code: Facility code as the audit prints it.

    Returns:
        True when the text names the code.

    Raises:
        ValueError: If the code is not a facility code.
    """
    match = re.fullmatch(r"([A-Za-z]+)0*(\d+)", code)
    if not match:
        raise ValueError(f"not a facility code: {code}")
    letters, digits = match[1], match[2]
    joined = re.sub(r"([A-Za-z]+) (\d)", r"\1\2", quote)
    return bool(re.search(rf"\b{letters}0*{digits}(?!\d)", joined, re.IGNORECASE))
