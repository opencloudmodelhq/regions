# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Register of gaps and judgement calls raised for each region."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final, NotRequired, TypedDict

from ocm_regions.common.schema import CallKind, Grade, Severity

FAR_AIRPORT_KM: Final = 100

SEVERITY: Final[dict[CallKind, Severity]] = {
    "gap-unmapped": "high",
    "gap-iata": "high",
    "evidence-conflict": "high",
    "vendor-code-conflict": "high",
    "city-assumed": "medium",
    "city-partial": "medium",
    "gap-unlocode": "medium",
    "city-reported": "low",
    "nearest-differs-from-hub": "low",
    "far-airport": "low",
    "shared-airport": "low",
    "gap-clli": "low",
    "vendor-code-not-iata": "low",
    "feed-conflict": "medium",
    "candidate-region": "medium",
    "city-feed": "low",
}

WEAK_GRADES: Final[dict[Grade, CallKind]] = {
    "vendor-feed": "city-feed",
    "assumed": "city-assumed",
    "partial": "city-partial",
    "reported": "city-reported",
    "network-presence": "city-reported",
}


class CallFacts(TypedDict):
    """What the call rules read about one region."""

    grade: Grade
    iata_nearest_airport: str
    iata_major_hub: str
    unlocode: str
    conflict: NotRequired[bool]
    feed_conflict: NotRequired[str]
    airport_km: NotRequired[float | None]
    shared_with: NotRequired[str]
    vendor_code: NotRequired[str]
    vendor_code_note: NotRequired[str]
    vendor_code_unknown: NotRequired[bool]


@dataclass(frozen=True)
class Call:
    """A point for a reviewer to check: its kind and detail."""

    kind: CallKind
    detail: str

    @property
    def severity(self) -> Severity:
        """The severity fixed for this kind of call.

        Returns:
            The severity.
        """
        return SEVERITY[self.kind]


def calls_for_region(region: CallFacts) -> list[Call]:
    """Every review call for one region.

    Args:
        region: Facts about the region that calls depend on.

    Returns:
        The calls.
    """
    return [*_location_calls(region), *_gap_calls(region), *_code_calls(region), *_vendor_code_calls(region)]


def _code_calls(region: CallFacts) -> Iterator[Call]:
    """Yield calls where the nearest airport differs from the hub, is far away, or serves several cities.

    Args:
        region: Facts about the region that calls depend on.

    Yields:
        A call about the airport codes.
    """
    if region["iata_nearest_airport"] and region["iata_nearest_airport"] != region["iata_major_hub"]:
        yield Call(
            "nearest-differs-from-hub",
            f"Nearest airport {region['iata_nearest_airport']}; market hub {region['iata_major_hub']}.",
        )
    airport_km = region.get("airport_km") or 0
    if airport_km > FAR_AIRPORT_KM:
        yield Call("far-airport", f"Nearest airport is {airport_km:.0f} km away.")
    if region.get("shared_with"):
        yield Call("shared-airport", f"Airport also serves {region.get('shared_with')}.")


def _gap_calls(region: CallFacts) -> Iterator[Call]:
    """Yield a call for each code the region lacks.

    Args:
        region: Facts about the region that calls depend on.

    Yields:
        A call about a missing code.
    """
    if not region["iata_nearest_airport"]:
        yield Call("gap-iata", "No place, so no airport code.")
    if not region["unlocode"]:
        yield Call("gap-unlocode", "UN/LOCODE has no entry for this place.")


def _location_calls(region: CallFacts) -> Iterator[Call]:
    """Yield calls about a weak grade, conflicting evidence or a feed that disagrees.

    Args:
        region: Facts about the region that calls depend on.

    Yields:
        A call about the place.
    """
    kind = WEAK_GRADES.get(region["grade"])
    if kind:
        yield Call(kind, f"Place rests on '{region['grade']}' evidence, not a vendor statement.")
    if region.get("conflict"):
        yield Call("evidence-conflict", "Sources disagree on this region's place.")
    if region.get("feed_conflict"):
        yield Call(
            "feed-conflict",
            f"The vendor's IP feed says {region.get('feed_conflict')}. A feed cannot override a place.",
        )


def _vendor_code_calls(region: CallFacts) -> Iterator[Call]:
    """Yield calls where the vendor's own code is unknown or names a distant airport.

    Args:
        region: Facts about the region that calls depend on.

    Yields:
        A call about the vendor's code.
    """
    note = region.get("vendor_code_note", "")
    if note:
        yield Call("vendor-code-conflict", f"Vendor code {region.get('vendor_code')} is really {note}.")
    if region.get("vendor_code_unknown"):
        yield Call("vendor-code-not-iata", f"Vendor code {region.get('vendor_code')} is not in OurAirports.")
