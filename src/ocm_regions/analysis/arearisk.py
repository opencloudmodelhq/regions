# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Sort weak regions by whether a rival city would change the area code or only the label."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Final, Literal, Required, TypeAlias, TypedDict

from ocm_regions.common.geo import Airport, capped_airport, haversine_km, nearest_airport
from ocm_regions.common.schema import CountryCode, EvidenceRecord, IataCode, Kilometres, Point
from ocm_regions.place.hubs import derive_hub
from ocm_regions.place.placer import GazetteerCity
from ocm_regions.place.placer import norm as _norm

YesNo: TypeAlias = Literal["yes", "no"]
RiskStatus: TypeAlias = Literal["code-at-risk", "no-rival-named", "code-safe"]
RuleCodes: TypeAlias = tuple[IataCode, IataCode, IataCode]

BASIS: Final[dict[str, str]] = {
    "government": "regulator",
    "code_join": "regulator",
    "vendor": "vendor",
    "certification": "vendor",
    "vendor_feed": "feed",
    "news": "press",
}

FIELDS: Final = [
    "status",
    "provider",
    "region_id",
    "city",
    "grade",
    "iata_nearest_airport",
    "iata_major_hub",
    "iata_nearby_hub",
    "rival",
    "basis",
    "source",
    "rival_km",
    "rival_nearest_airport",
    "rival_major_hub",
    "rival_nearby_hub",
    "changes_nearest_airport",
    "changes_major_hub",
    "changes_nearby_hub",
]

RULES: Final = ("nearest_airport", "major_hub", "nearby_hub")
STATUS_ORDER: Final[tuple[RiskStatus, ...]] = ("code-at-risk", "no-rival-named", "code-safe")


class RiskRegion(TypedDict):
    """A placed region as the risk check reads it."""

    provider: str
    region_id: str
    place_id: str
    city: str
    country: str
    grade: str
    lat: float | str
    lon: float | str
    iata_nearest_airport: str
    iata_major_hub: str
    iata_nearby_hub: str


class Rival(TypedDict):
    """Another town a source names for a region placed only to an area."""

    provider: str
    region_id: str
    rival: str
    country: str
    basis: str
    source: str


class RivalCodes(TypedDict):
    """The codes a rival town would get, and whether each differs from the published one."""

    rival_km: int
    rival_nearest_airport: str
    changes_nearest_airport: YesNo
    rival_major_hub: str
    changes_major_hub: YesNo
    rival_nearby_hub: str
    changes_nearby_hub: YesNo


class RiskRow(TypedDict, total=False):
    """One region and rival town, with the codes that would change if the rival were the true place."""

    provider: Required[str]
    region_id: Required[str]
    city: Required[str]
    grade: Required[str]
    iata_nearest_airport: Required[str]
    iata_major_hub: Required[str]
    iata_nearby_hub: Required[str]
    status: Required[RiskStatus]
    rival: str
    basis: str
    source: str
    rival_km: int
    rival_nearest_airport: str
    changes_nearest_airport: YesNo
    rival_major_hub: str
    changes_major_hub: YesNo
    rival_nearby_hub: str
    changes_nearby_hub: YesNo


def area_risk(
    regions: Iterable[RiskRegion],
    rivals: Iterable[Rival],
    airports: Sequence[Airport],
    ranks: Mapping[IataCode, float],
    cities: Sequence[GazetteerCity],
    cap: Kilometres,
) -> list[RiskRow]:
    """One row per rival, or one empty row for a region with none; the riskiest regions come first.

    Args:
        regions: Placed regions.
        rivals: Rival towns.
        airports: Airports to choose from.
        ranks: Connectivity rank per airport.
        cities: Gazetteer cities.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The rows.
    """
    by_region: dict[tuple[str, str], list[Rival]] = {}
    for rival in rivals:
        by_region.setdefault((rival["provider"], rival["region_id"]), []).append(rival)
    rows = [
        row
        for placed in regions
        for row in _region_rows(
            placed, by_region.get((placed["provider"], placed["region_id"]), []), airports, ranks, cities, cap
        )
    ]
    return sorted(rows, key=lambda r: (STATUS_ORDER.index(r["status"]), r["provider"], r["region_id"]))


def compare_rival(
    placed: RiskRegion,
    point: Point,
    airports: Sequence[Airport],
    ranks: Mapping[IataCode, float],
    cap: Kilometres,
) -> RivalCodes:
    """A change counts only if the rival differs from both the recorded code and the rule's code for the placed city.

    Args:
        placed: Placed region.
        point: Latitude and longitude.
        airports: Airports to choose from.
        ranks: Connectivity rank per airport.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The rival's distance and codes, with a change flag per table.
    """
    origin = (float(placed["lat"]), float(placed["lon"]))
    country = CountryCode(placed["country"])
    rule = _rule_codes(origin, country, airports, ranks, cap)
    rival = _rule_codes(point, country, airports, ranks, cap)
    return {
        "rival_km": round(haversine_km(origin, point)),
        "rival_nearest_airport": rival[0],
        "changes_nearest_airport": _changes(rival[0], placed["iata_nearest_airport"], rule[0]),
        "rival_major_hub": rival[1],
        "changes_major_hub": _changes(rival[1], placed["iata_major_hub"], rule[1]),
        "rival_nearby_hub": rival[2],
        "changes_nearby_hub": _changes(rival[2], placed["iata_nearby_hub"], rule[2]),
    }


def derive_rivals(
    placed: Iterable[RiskRegion], evidence: Iterable[EvidenceRecord], areas: Mapping[str, str] | None = None
) -> list[Rival]:
    """A rival is any other city a record names for the region; the source type gives its basis.

    Args:
        placed: Placed regions.
        evidence: Evidence records.
        areas: Area name per region, where the vendor names only an area.

    Returns:
        One rival per region and other city named.
    """
    by_region: dict[tuple[str, str], list[EvidenceRecord]] = {}
    for record in evidence:
        if record.get("claim") == "city":
            by_region.setdefault((record["provider"], record["region_id"]), []).append(record)
    rivals: list[Rival] = []
    for region in placed:
        seen = {_norm(region["city"])}
        for record in by_region.get((region["provider"], region["region_id"]), []):
            name = record.get("value", "").split(",")[0].strip()
            if (
                _norm(name) not in seen
                and _norm(name) not in _norm(region["city"])
                and (areas or {}).get(_norm(name)) != "US"
            ):
                seen.add(_norm(name))
                rivals.append(
                    {
                        "provider": region["provider"],
                        "region_id": region["region_id"],
                        "rival": name,
                        "country": region["country"],
                        "basis": BASIS.get(record.get("source_type", ""), "lead"),
                        "source": record.get("title", ""),
                    }
                )
    return rivals


def region_status(rows: Sequence[RivalCodes] | Sequence[RiskRow]) -> RiskStatus:
    """A region's risk status: no rival named, a code at risk, or codes that hold.

    Args:
        rows: Rival rows of one region.

    Returns:
        The status.
    """
    if not rows:
        return "no-rival-named"
    return "code-at-risk" if any(r.get("changes_nearby_hub") == "yes" for r in rows) else "code-safe"


def resolve_point(rival: Rival, cities: Iterable[GazetteerCity], near: Point) -> Point | None:
    """Coordinates of a rival town: the gazetteer city of that name in the country nearest the placed point, or None.

    Args:
        rival: Rival town.
        cities: Gazetteer cities.
        near: Point the town should lie near.

    Returns:
        Latitude and longitude, or None when no city of that name exists.
    """
    named = [
        c for c in cities if c["country"] == rival["country"] and _norm(c["name"]) == _norm(rival["rival"])
    ]
    if not named:
        return None
    best = min(named, key=lambda c: haversine_km(near, (c["lat"], c["lon"])))
    return best["lat"], best["lon"]


def _base(placed: RiskRegion, status: RiskStatus) -> RiskRow:
    """The columns of a risk row that describe the region itself.

    Args:
        placed: Placed region.
        status: Risk status of the region.

    Returns:
        The row, with rival columns blank.
    """
    return {
        "provider": placed["provider"],
        "region_id": placed["region_id"],
        "city": placed["city"],
        "grade": placed["grade"],
        "iata_nearest_airport": placed["iata_nearest_airport"],
        "iata_major_hub": placed["iata_major_hub"],
        "iata_nearby_hub": placed["iata_nearby_hub"],
        "status": status,
    }


def _changes(rival: str, recorded: str, rule: str) -> YesNo:
    """Whether a rival's code differs from both the recorded code and the code the rules give now.

    Args:
        rival: Code the rival town would get.
        recorded: Code recorded in the tables.
        rule: Code the rules give now.

    Returns:
        ``yes`` when the code would change, else ``no``.
    """
    return "no" if rival in (recorded, rule) else "yes"


def _region_rows(
    placed: RiskRegion,
    rivals: Iterable[Rival],
    airports: Sequence[Airport],
    ranks: Mapping[IataCode, float],
    cities: Sequence[GazetteerCity],
    cap: Kilometres,
) -> list[RiskRow]:
    """The risk rows for one region, one per rival town that resolves to a point.

    Args:
        placed: Placed region.
        rivals: Rival towns.
        airports: Airports to choose from.
        ranks: Connectivity rank per airport.
        cities: Gazetteer cities.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The rows.
    """
    near = (float(placed["lat"]), float(placed["lon"]))
    located = [(r, resolve_point(r, cities, near)) for r in rivals]
    compared = [(r, compare_rival(placed, point, airports, ranks, cap)) for r, point in located if point]
    status = region_status([codes for _rival, codes in compared])
    return [_rival_row(placed, rival, codes, status) for rival, codes in compared] or [_base(placed, status)]


def _rival_row(placed: RiskRegion, rival: Rival, codes: RivalCodes, status: RiskStatus) -> RiskRow:
    """A risk row for one rival town.

    Args:
        placed: Placed region.
        rival: Rival town.
        codes: Codes the rival town would get.
        status: Risk status of the region.

    Returns:
        The row.
    """
    return {
        **_base(placed, status),
        "rival": rival["rival"],
        "basis": rival["basis"],
        "source": rival["source"],
        "rival_km": codes["rival_km"],
        "rival_nearest_airport": codes["rival_nearest_airport"],
        "changes_nearest_airport": codes["changes_nearest_airport"],
        "rival_major_hub": codes["rival_major_hub"],
        "changes_major_hub": codes["changes_major_hub"],
        "rival_nearby_hub": codes["rival_nearby_hub"],
        "changes_nearby_hub": codes["changes_nearby_hub"],
    }


def _rule_codes(
    point: Point,
    country: CountryCode,
    airports: Sequence[Airport],
    ranks: Mapping[IataCode, float],
    cap: Kilometres,
) -> RuleCodes:
    """The nearest airport, major hub and nearby hub the rules give a point.

    Args:
        point: Latitude and longitude.
        country: ISO country code.
        airports: Airports to choose from.
        ranks: Connectivity rank per airport.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The three codes.
    """
    nearest = nearest_airport(point, country, airports)
    hub = derive_hub(point, country, airports, ranks) or nearest
    capped = capped_airport(hub.code, haversine_km(point, hub.point), nearest.code, cap)
    return nearest.code, hub.code, capped


rule_codes = _rule_codes
