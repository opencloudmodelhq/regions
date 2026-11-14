# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Build the master table, naming tables, evidence and call register."""

import csv
import hashlib
import json
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias, TypedDict, cast

from ocm_regions.analysis.capsweep import load_sites, open_rows, table_cap
from ocm_regions.analysis.captypes import SiteRow
from ocm_regions.common import data, layout
from ocm_regions.common.data import Facility, LocodeRow
from ocm_regions.common.geo import (
    MATCH_KM,
    Airport,
    capped_airport,
    haversine_km,
    nearest_airport,
    require_domestic_hub,
)
from ocm_regions.common.schema import (
    AreaBasis,
    CallRow,
    CandidateRow,
    DraftRow,
    EvidenceId,
    EvidenceRecord,
    FeedMatch,
    Grade,
    HubClass,
    IataCode,
    Kilometres,
    Locode,
    Place,
    PlaceMapping,
    Point,
    Precision,
    ProviderId,
    QuoteCheck,
    RegionId,
    RegionKey,
    RegionRow,
)
from ocm_regions.evidence.verify import GENERATED_SOURCES
from ocm_regions.fetch.sources import StrPath
from ocm_regions.place.feeds import FeedHit, load_candidates, load_feed_evidence
from ocm_regions.place.metro import MetroTable, metro_for_airport
from ocm_regions.place.placement import load_mappings, load_places
from ocm_regions.regions.parsers import Region
from ocm_regions.tables.calls import SEVERITY, Call, CallFacts, calls_for_region
from ocm_regions.tables.grades import best_grade
from ocm_regions.tables.ordinals import CodeRegistry, assign_codes

Stage: TypeAlias = Literal["evidence", "grade", "codes", "calls"]
IataColumn: TypeAlias = Literal["iata_nearest_airport", "iata_major_hub", "iata_nearby_hub"]
CodeColumn: TypeAlias = Literal["code_nearest_airport", "code_major_hub", "code_nearby_hub"]
WhyColumn: TypeAlias = Literal["iata_nearest_airport_why", "iata_major_hub_why", "iata_nearby_hub_why"]
Collected: TypeAlias = dict[RegionKey, tuple[Region, list[EvidenceRecord]]]
ByKey: TypeAlias = dict[RegionKey, tuple[Region, DraftRow, list[EvidenceRecord]]]

CANDIDATE_FIELDS: Final = [
    "provider",
    "region_id",
    "feed_city",
    "feed_subdivision",
    "feed_country",
    "feed_share",
]

CHANGE_FIELDS: Final = ("city", "iata_nearest_airport", "iata_major_hub", "iata_nearby_hub", "grade")
CONFLICT_KM: Final = 300

EVIDENCE_FIELDS: Final = [
    "evidence_id",
    "provider",
    "region_id",
    "claim",
    "value",
    "source_type",
    "publisher",
    "title",
    "url",
    "published",
    "quote",
    "stance",
    "quote_check",
    "note",
    "join_code",
    "also_found",
]

GRADE_TEXT: Final[dict[Grade, str]] = {
    "vendor-stated": "Vendor's region list names this place.",
    "vendor-document": "A vendor document names this place.",
    "audit-document": "An audit or certification document names this place.",
    "public-record": "An audit list ties the region to a facility code, and a public record puts the same operator and code in this area.",
    "measured": "Network measurement fits this place.",
    "vendor-feed": "The vendor's IP feed places this region's addresses here, which supports the area but does not prove a facility.",
    "reported": "Third-party reporting names this place; the vendor's region list does not.",
    "network-presence": "Only a PeeringDB network facility points here.",
    "partial": "Sources support the area, or tie the place to the vendor but not this exact region.",
    "assumed": "Assumed; no source checked supports this place. Codes here are a placeholder.",
}

GRADED_FIELDS: Final = [
    "provider",
    "partition",
    "region_id",
    "region_name",
    "stated_location",
    "vendor_code",
    "place_id",
    "city",
    "subdivision",
    "country",
    "lat",
    "lon",
    "geonames_id",
    "grade",
    "precision",
    "place_basis",
    "cross_check",
    "feed_city",
    "feed_km",
    "location_reasoning",
    "address",
    "address_status",
    "evidence_ids",
    "evidence_urls",
]

REGION_FIELDS: Final = [
    "provider",
    "partition",
    "region_id",
    "region_name",
    "stated_location",
    "vendor_code",
    "place_id",
    "city",
    "country",
    "lat",
    "lon",
    "grade",
    "precision",
    "place_basis",
    "cross_check",
    "location_reasoning",
    "address",
    "address_status",
    "iata_nearest_airport",
    "iata_nearest_airport_km",
    "iata_nearest_airport_why",
    "code_nearest_airport",
    "iata_major_hub",
    "iata_major_hub_why",
    "code_major_hub",
    "iata_nearby_hub",
    "iata_nearby_hub_why",
    "code_nearby_hub",
    "hub_class",
    "area_basis",
    "iata_metro",
    "unlocode",
    "unlocode_why",
    "clli",
    "clli_why",
    "feed_city",
    "feed_km",
    "evidence_ids",
    "evidence_urls",
    "call_count",
]

STAGES: Final[tuple[Stage, ...]] = ("evidence", "grade", "codes", "calls")

STRONG_GRADES: Final[tuple[Grade, ...]] = (
    "vendor-stated",
    "vendor-document",
    "audit-document",
    "public-record",
    "measured",
)

TABLE_FIELDS: Final = [
    "code",
    "provider",
    "partition",
    "region_id",
    "region_name",
    "city",
    "country",
    "iata",
    "grade",
    "precision",
    "area_basis",
    "reasoning",
    "evidence_urls",
]


class AreaFacts(TypedDict):
    """The fields that decide whether a row names a city or only an area."""

    precision: Precision
    grade: Grade
    iata_nearby_hub: str
    iata_major_hub: str


class IataFields(TypedDict):
    """The airport codes derived for a place, with distances and reasons."""

    iata_metro: str
    iata_nearest_airport: IataCode
    iata_nearest_airport_km: int
    iata_nearest_airport_why: str
    iata_major_hub: IataCode
    iata_major_hub_why: str


class VendorCodeFacts(TypedDict, total=False):
    """What a vendor's own code shows: unknown as an airport, or an airport far from the place."""

    vendor_code_unknown: bool
    vendor_code_note: str


class TableRow(TypedDict):
    """One row of a published code table."""

    code: str
    provider: ProviderId
    partition: str
    region_id: RegionId
    region_name: str
    city: str
    country: str
    iata: str
    grade: Grade
    precision: Precision
    area_basis: AreaBasis
    reasoning: str
    evidence_urls: str


@dataclass
class Context:
    """Everything a build reads once: airports, UN/LOCODE, facilities, operators, places, mappings, evidence and feeds."""

    airports: dict[IataCode, Airport]
    locodes: dict[Locode, LocodeRow]
    facilities: list[Facility]
    operators: dict[str, str]
    places: dict[str, Place]
    mappings: dict[RegionKey, PlaceMapping]
    curated_evidence: dict[RegionKey, list[EvidenceRecord]]
    quote_checks: dict[str, QuoteCheck]
    clli_counts: dict[str, int]
    metros: MetroTable
    feed_records: dict[RegionKey, list[EvidenceRecord]]
    feed_matches: dict[RegionKey, FeedMatch]
    feed_hits: dict[RegionKey, FeedHit]


def area_basis(row: AreaFacts) -> AreaBasis:
    """A region with no clear city, or a city on weak evidence, is named for its metro hub, within the hub cap.

    Args:
        row: Fields that decide the basis.

    Returns:
        The area basis.
    """
    if row["precision"] == "city" and row["grade"] in STRONG_GRADES:
        return "exact-city"
    return "metro-hub" if row["iata_nearby_hub"] == row["iata_major_hub"] else "nearest-hub-too-far"


def attach_quote_checks(records: Iterable[EvidenceRecord], checks: Mapping[str, QuoteCheck]) -> None:
    """Record each collected quote's check; with no checks on file a build leaves grades uncapped.

    Args:
        records: Evidence records.
        checks: Quote check status per evidence identifier.
    """
    if not checks:
        return
    for record in records:
        if record.get("source_type") not in GENERATED_SOURCES:
            record["quote_check"] = checks.get(record.get("evidence_id", ""), "not-checked")


def build(
    raw_dir: StrPath, inputs_dir: StrPath, output_dir: StrPath
) -> tuple[list[RegionRow], list[EvidenceRecord], list[CallRow]]:
    """Run the build through the calls stage and return the region rows, evidence and calls.

    Args:
        raw_dir: The data/raw folder.
        inputs_dir: Repository root that holds the input folders.
        output_dir: Output folder.

    Returns:
        The region rows, the evidence and the calls.
    """
    rows, evidence, calls = run_until("calls", raw_dir, inputs_dir, output_dir)
    return cast(list[RegionRow], rows), evidence, calls


def evidence_id(record: EvidenceRecord) -> EvidenceId:
    """A stable identifier for an evidence record, from a hash of its region, source and quote.

    Args:
        record: Evidence record.

    Returns:
        The identifier.
    """
    parts = (
        record.get("provider", ""),
        record.get("region_id", ""),
        record.get("claim", ""),
        record.get("value", ""),
        record.get("url", ""),
        record.get("source_type", ""),
        record.get("quote", ""),
    )
    return EvidenceId("E" + hashlib.sha1("|".join(str(part) for part in parts).encode()).hexdigest()[:10])


def hub_class(airport: Airport) -> HubClass:
    """Whether the airport a code names is one OurAirports calls large; a reader can filter on it.

    Args:
        airport: Airport to classify.

    Returns:
        The hub class.
    """
    return "large" if airport.kind == "large_airport" else "medium"


def region_changes(
    before: Iterable[Mapping[str, object]], after: Sequence[Mapping[str, object]]
) -> list[dict[str, object]]:
    """What a corpus change moved: one row for each region whose place, code or grade differs.

    Args:
        before: Region rows before the change.
        after: Region rows after the change.

    Returns:
        The change rows.
    """
    old = {(r["provider"], r["region_id"]): r for r in before}
    changes: list[dict[str, object]] = []
    nothing: Mapping[str, object] = {}
    for row in after:
        previous = old.get((row["provider"], row["region_id"]), nothing)
        changes += [
            {
                "provider": row["provider"],
                "region_id": row["region_id"],
                "field": f,
                "before": previous.get(f, ""),
                "after": row.get(f, ""),
            }
            for f in CHANGE_FIELDS
            if previous.get(f, "") != row.get(f, "")
        ]
    kept = {(r["provider"], r["region_id"]) for r in after}
    return changes + [
        {"provider": p, "region_id": r, "field": "region", "before": r, "after": ""}
        for p, r in old
        if (p, r) not in kept
    ]


def region_evidence(region: Region, mapping: PlaceMapping | None, ctx: Context) -> list[EvidenceRecord]:
    """Every evidence record for a region: the vendor list, PeeringDB facilities and the recorded evidence.

    Args:
        region: Published region.
        mapping: Mapping of the region to its place, if it has one.
        ctx: Build context.

    Returns:
        The evidence records, each with its identifier.
    """
    records = [_vendor_list_record(region, mapping), *_peeringdb_records(region, mapping, ctx)]
    records += ctx.curated_evidence.get((region.provider, region.region_id), [])
    records += ctx.feed_records.get((region.provider, region.region_id), [])
    for record in records:
        _ = record.setdefault("evidence_id", evidence_id(record))
    attach_quote_checks(records, ctx.quote_checks)
    return records


def run_until(
    stage: str, raw_dir: StrPath, inputs_dir: StrPath, output_dir: StrPath
) -> tuple[list[DraftRow], list[EvidenceRecord], list[CallRow]]:
    """Run the pipeline through one stage and write what that stage has produced so far.

    Args:
        stage: Last stage to run.
        raw_dir: The data/raw folder.
        inputs_dir: Repository root that holds the input folders.
        output_dir: Output folder.

    Returns:
        The rows, the evidence and the calls produced so far.

    Raises:
        ValueError: If the stage is unknown.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage}; choose from {', '.join(STAGES)}")
    raw, inputs, output = Path(raw_dir), Path(inputs_dir), Path(output_dir)
    (output / "stages").mkdir(parents=True, exist_ok=True)
    ctx = _load_context(raw, inputs, output)
    published = data.load_published_regions(raw)
    regions = data.open_regions(published)
    collected = _collect_evidence(regions, ctx)
    evidence = _write_evidence(collected, output)
    if stage == "evidence":
        return [], evidence, []
    rows, by_key = _grade_rows(collected, ctx)
    _write_csv(output / "stages" / "graded.csv", GRADED_FIELDS, rows)
    if stage == "grade":
        return rows, evidence, []
    _code_rows(rows, ctx, inputs)
    _write_tables(rows, output)
    if stage == "codes":
        return rows, evidence, []
    return rows, evidence, _write_calls(rows, by_key, published, ctx, output)


def vendor_facilities(operator: str, point: Point, facilities: Iterable[Facility]) -> list[Facility]:
    """Facilities the vendor itself owns, within the area of the region's place.

    Args:
        operator: Operator name the document must show.
        point: Latitude and longitude.
        facilities: PeeringDB facilities.

    Returns:
        The matching facilities.
    """
    return [
        f
        for f in facilities
        if operator.lower() in (f.get("org_name") or "").lower()
        and f.get("latitude")
        and f.get("longitude")
        and haversine_km(point, (float(f.get("latitude") or 0), float(f.get("longitude") or 0))) <= MATCH_KM
    ]


def _add_capped(row: DraftRow, cap: Kilometres, ctx: Context) -> None:
    """Set the nearby-hub code, its reason, its hub class and the area basis on a row.

    Args:
        row: Region row being built; changed in place.
        cap: Distance cap in kilometres for using the hub.
        ctx: Build context.
    """
    point = (float(row["lat"]), float(row["lon"]))
    nearest, hub = (
        ctx.airports[row.get("iata_nearest_airport", IataCode(""))],
        ctx.airports[row.get("iata_major_hub", IataCode(""))],
    )
    hub_km, km = haversine_km(point, hub.point), haversine_km(point, nearest.point)
    row["iata_nearby_hub"] = capped_airport(hub.code, hub_km, nearest.code, cap)
    row["iata_nearby_hub_why"] = _capped_why(hub, hub_km, nearest, km, cap)
    row["hub_class"] = hub_class(ctx.airports[row["iata_nearby_hub"]])
    row["area_basis"] = area_basis(
        {
            "precision": row["precision"],
            "grade": row["grade"],
            "iata_nearby_hub": row["iata_nearby_hub"],
            "iata_major_hub": hub.code,
        }
    )


def _add_codes(row: DraftRow, ctx: Context) -> None:
    """Set the airport codes, UN/LOCODE and CLLI prefix on a placed row, each with its reason.

    Args:
        row: Region row being built; changed in place.
        ctx: Build context.
    """
    place = ctx.places[row["place_id"]]
    point = (float(place["lat"]), float(place["lon"]))
    row["unlocode"], row["unlocode_why"] = place["unlocode"], _unlocode_why(place, ctx, row["grade"])
    row["clli"], row["clli_why"] = place["clli"], _clli_why(place, ctx)
    iata = _iata_fields(place, point, ctx)
    row["iata_metro"], row["iata_nearest_airport"], row["iata_nearest_airport_km"] = (
        iata["iata_metro"],
        iata["iata_nearest_airport"],
        iata["iata_nearest_airport_km"],
    )
    row["iata_nearest_airport_why"], row["iata_major_hub"], row["iata_major_hub_why"] = (
        iata["iata_nearest_airport_why"],
        iata["iata_major_hub"],
        iata["iata_major_hub_why"],
    )


def _address(region: Region, mapping: PlaceMapping | None, ctx: Context) -> tuple[str, str]:
    """The cities of the vendor's listed facilities and the address status; never a street address.

    Args:
        region: Published region.
        mapping: Mapping of the region to its place, if it has one.
        ctx: Build context.

    Returns:
        The city list and the address status.
    """
    parts = dict.fromkeys(
        f"{fac['city']}, {fac['country']}" for fac in _region_facilities(region, mapping, ctx)
    )
    return ("; ".join(parts), "provider-listed-peeringdb") if parts else ("", "not-published")


def _assign(rows: Sequence[DraftRow], column: IataColumn, code_column: CodeColumn, path: Path) -> None:
    """Give each region its code in one table and save the register, so issued codes never change.

    Args:
        rows: Region rows being built.
        column: Column that holds the airport code.
        code_column: Column that receives the assigned code.
        path: Path of the code register.
    """
    base = [(r["provider"], r["region_id"], code.lower()) for r in rows if (code := r.get(column))]
    assigned, registry = assign_codes(base, _read_registry(path))
    _write_registry(path, registry)
    for row in rows:
        row[code_column] = assigned.get((row["provider"], row["region_id"]), "")


def _build_calls(
    rows: Iterable[DraftRow], by_key: ByKey, airports: Mapping[IataCode, Airport]
) -> list[CallRow]:
    """The review calls for every region.

    Args:
        rows: Region rows being built.
        by_key: Evidence records keyed by region.
        airports: Airports keyed by IATA code.

    Returns:
        The call rows.
    """
    towns = _shared_places(rows)
    calls: list[CallRow] = []
    for key, (region, row, records) in by_key.items():
        airport = row.get("iata_nearest_airport")
        others = sorted(towns[airport] - {row["city"]}) if airport else []
        found = _region_calls(region, row, records, ", ".join(others), airports)
        if not row.get("place_id"):
            found.append(Call("gap-unmapped", f"No place mapped for {region.region_id}."))
        row["call_count"] = len(found)
        raised: list[CallRow] = [
            {
                "provider": key[0],
                "region_id": key[1],
                "kind": c.kind,
                "severity": c.severity,
                "detail": c.detail,
            }
            for c in found
        ]
        calls += raised
    return calls


def _candidate_calls(candidates: Iterable[CandidateRow]) -> list[CallRow]:
    """A call for each region ID seen in IP ranges that the vendor's region list does not name.

    Args:
        candidates: Region IDs seen in IP ranges but not in a vendor's list.

    Returns:
        The call rows.
    """
    return [
        {
            "provider": c["provider"],
            "region_id": c["region_id"],
            "kind": "candidate-region",
            "severity": SEVERITY["candidate-region"],
            "detail": f"In the vendor's IP ranges ({c['feed_city']}) but not its region list.",
        }
        for c in candidates
    ]


def _capped_why(
    hub: Airport, hub_km: Kilometres, nearest: Airport, nearest_km: Kilometres, cap: Kilometres
) -> str:
    """The reason the nearby-hub column holds the hub or the nearest airport, given the distance cap.

    Args:
        hub: Major hub airport.
        hub_km: Distance to the hub in kilometres.
        nearest: Nearest airport.
        nearest_km: Distance to the nearest airport in kilometres.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The reason text.
    """
    if hub.code == nearest.code:
        return f"{hub.name} is both the hub and the nearest airport, {hub_km:.0f} km away."
    if hub_km <= cap:
        return f"Hub {hub.name} is {hub_km:.0f} km away, within the {cap:.0f} km cap."
    return f"Hub {hub.name} is {hub_km:.0f} km away, beyond the {cap:.0f} km cap; uses the nearest airport, {nearest.name}, {nearest_km:.0f} km away."


def _clli_why(place: Place, ctx: Context) -> str:
    """The reason for the CLLI prefix, or for leaving it blank.

    Args:
        place: Place of the region.
        ctx: Build context.

    Returns:
        The reason text.
    """
    if place["clli"]:
        return (
            f"City prefix seen on {ctx.clli_counts[place['clli']]} PeeringDB facilities. Inferred for the area, "
            "not assigned to this region; 6 characters, no site code."
        )
    return f"No usable CLLI for {place['city']} in PeeringDB; the official registry is paid."


def _code_rows(rows: Sequence[DraftRow], ctx: Context, inputs: Path) -> None:
    """Add codes to placed rows, apply the distance cap, and assign the codes of all three tables.

    Args:
        rows: Region rows being built.
        ctx: Build context.
        inputs: Repository root that holds the input folders.
    """
    placed = [row for row in rows if row.get("place_id")]
    for row in placed:
        _add_codes(row, ctx)
    cap = table_cap(load_sites(cast(list[SiteRow], open_rows(placed)), ctx.airports))
    for row in placed:
        _add_capped(row, cap, ctx)
    _assign(rows, "iata_nearest_airport", "code_nearest_airport", layout.register(inputs, "nearest_airport"))
    _assign(rows, "iata_major_hub", "code_major_hub", layout.register(inputs, "major_hub"))
    _assign(rows, "iata_nearby_hub", "code_nearby_hub", layout.register(inputs, "nearby_hub"))


def _collect_evidence(regions: Iterable[Region], ctx: Context) -> Collected:
    """The region and its evidence records, for every region.

    Args:
        regions: Published regions.
        ctx: Build context.

    Returns:
        The region and records keyed by vendor and region.
    """
    return {
        (r.provider, r.region_id): (r, region_evidence(r, ctx.mappings.get((r.provider, r.region_id)), ctx))
        for r in regions
    }


def _derived_records(output: StrPath) -> list[EvidenceRecord]:
    """Evidence comes only from what the scripted steps read out of the corpus.

    Args:
        output: Output folder.

    Returns:
        The evidence records.
    """
    records: list[EvidenceRecord] = []
    for name in ("joins.jsonl", "scripted_evidence.jsonl"):
        path = Path(output) / name
        if path.exists():
            records += [
                cast(EvidenceRecord, json.loads(line))
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
    return records


def _evidence_ids(records: Iterable[EvidenceRecord]) -> str:
    """The evidence identifiers of the records, joined with semicolons.

    Args:
        records: Evidence records.

    Returns:
        The joined identifiers.
    """
    return ";".join(r.get("evidence_id", "") for r in records)


def _evidence_urls(records: Iterable[EvidenceRecord]) -> str:
    """The distinct source URLs of the records, sorted and joined with semicolons.

    Args:
        records: Evidence records.

    Returns:
        The joined URLs.
    """
    return ";".join(sorted({r.get("url", "") for r in records}))


def _grade(records: Sequence[EvidenceRecord]) -> Grade:
    """The best grade the records support.

    Args:
        records: Evidence records.

    Returns:
        The grade.
    """
    return best_grade(records)


def _grade_rows(collected: Collected, ctx: Context) -> tuple[list[DraftRow], ByKey]:
    """Build a graded row per region, placed or unmapped, and index the records by region.

    Args:
        collected: Region and evidence records per region.
        ctx: Build context.

    Returns:
        The rows, and the records keyed by region.
    """
    rows: list[DraftRow] = []
    by_key: ByKey = {}
    for key, (region, records) in collected.items():
        mapping = ctx.mappings.get(key)
        row = (
            _graded_row(region, mapping, records, ctx)
            if mapping and mapping["place_id"]
            else _unmapped_row(region, records, mapping["precision"] if mapping else "")
        )
        rows.append(row)
        by_key[key] = (region, row, records)
    return rows, by_key


def _graded_row(
    region: Region, mapping: PlaceMapping, records: Sequence[EvidenceRecord], ctx: Context
) -> DraftRow:
    """A row for a placed region: its place, grade, address status and evidence links.

    Args:
        region: Published region.
        mapping: Mapping of the region to its place.
        records: Evidence records.
        ctx: Build context.

    Returns:
        The row, without codes yet.
    """
    place = ctx.places[mapping["place_id"]]
    grade = _grade(records)
    match = ctx.feed_matches.get((region.provider, region.region_id))
    address, status = _address(region, mapping, ctx)
    return {
        "provider": region.provider,
        "partition": region.partition,
        "region_id": region.region_id,
        "region_name": region.name,
        "stated_location": region.stated_location,
        "vendor_code": region.vendor_code,
        "place_id": place["place_id"],
        "city": place["city"],
        "country": place["country"],
        "lat": place["lat"],
        "lon": place["lon"],
        "grade": grade,
        "precision": mapping["precision"],
        "place_basis": mapping["basis"],
        "cross_check": mapping["cross_check"],
        "feed_city": match["city"] if match else "",
        "feed_km": match["km"] if match else "",
        "feed_conflict": match["conflict"] if match else "",
        "location_reasoning": " ".join(t for t in (GRADE_TEXT[grade], mapping["note"]) if t),
        "address": address,
        "address_status": status,
        "evidence_ids": _evidence_ids(records),
        "evidence_urls": _evidence_urls(records),
    }


def _iata_fields(place: Place, point: Point, ctx: Context) -> IataFields:
    """The nearest airport, major hub and metropolitan code for a place, with distances and reasons.

    Args:
        place: Place of the region.
        point: Latitude and longitude.
        ctx: Build context.

    Returns:
        The airport fields.
    """
    nearest = nearest_airport(point, place["country"], ctx.airports.values())
    km = haversine_km(point, nearest.point)
    hub = require_domestic_hub(place["place_id"], place["country"], ctx.airports[place["hub_iata"]])
    a_why = f"Nearest scheduled large or medium airport in the country: {nearest.name}, {km:.0f} km from {place['city']}."
    b_why = (
        f"{hub.name} is the market gateway; same as nearest."
        if hub.code == nearest.code
        else f"{hub.name} is the market gateway for {place['city']}, {haversine_km(point, hub.point):.0f} km away; "
        + f"curated judgement. Nearest is {nearest.code}."
    )
    metro = metro_for_airport(hub.code, ctx.metros)
    if metro:
        b_why += f" OpenTravelData lists city code {metro}."
    return {
        "iata_metro": metro,
        "iata_nearest_airport": nearest.code,
        "iata_nearest_airport_km": round(km),
        "iata_nearest_airport_why": a_why,
        "iata_major_hub": hub.code,
        "iata_major_hub_why": b_why,
    }


def _load_context(raw: Path, inputs: Path, output: Path) -> Context:
    """Load every input a build needs into one context.

    Args:
        raw: The data/raw folder.
        inputs: Repository root that holds the input folders.
        output: Output folder.

    Returns:
        The context.
    """
    facilities = data.load_facilities(raw)
    evidence: defaultdict[RegionKey, list[EvidenceRecord]] = defaultdict(list)
    regional = _derived_records(output)
    for record in regional:
        evidence[(record["provider"], record["region_id"])].append(record)
    clli: defaultdict[str, int] = defaultdict(int)
    for fac in facilities:
        clli[(fac.get("clli") or "").strip().upper()[:6]] += 1
    places = {p["place_id"]: p for p in load_places(raw, inputs)}
    mappings = load_mappings(raw, inputs)
    feed_records, feed_matches, feed_hits = load_feed_evidence(raw, places, mappings)
    return Context(
        airports=data.load_airports(raw),
        locodes=data.load_locodes(raw),
        facilities=facilities,
        operators={r["provider"]: r["operator"] for r in data.read_csv(layout.operators(inputs))},
        places=places,
        mappings=mappings,
        feed_records=feed_records,
        feed_matches=feed_matches,
        feed_hits=feed_hits,
        curated_evidence=evidence,
        quote_checks=_read_quote_checks(output),
        clli_counts=clli,
        metros=data.load_metros(raw),
    )


def _peeringdb_records(region: Region, mapping: PlaceMapping | None, ctx: Context) -> list[EvidenceRecord]:
    """One evidence record for each PeeringDB facility the vendor lists in the placed city.

    Args:
        region: Published region.
        mapping: Mapping of the region to its place, if it has one.
        ctx: Build context.

    Returns:
        The evidence records.
    """
    return [
        {
            "provider": region.provider,
            "region_id": region.region_id,
            "claim": "city",
            "value": fac["city"],
            "source_type": "peeringdb",
            "publisher": "PeeringDB",
            "title": fac["name"],
            "url": f"https://www.peeringdb.com/fac/{fac['id']}",
            "published": "",
            "quote": f"{fac['name']}, {fac['city']}",
            "stance": "supports",
            "note": "Owned by the vendor's organisation; it shows a network facility.",
        }
        for fac in _region_facilities(region, mapping, ctx)
    ]


def _read_quote_checks(output: Path) -> dict[str, QuoteCheck]:
    """The quote check status per evidence identifier, or none before the verify step has run.

    Args:
        output: Output folder.

    Returns:
        Statuses keyed by evidence identifier.
    """
    path = output / "evidence_verification.csv"
    return (
        {r["evidence_id"]: cast(QuoteCheck, r["status"]) for r in data.read_csv(path)}
        if path.exists()
        else {}
    )


def _read_registry(path: Path) -> CodeRegistry:
    """The codes already assigned to regions, or none when the register does not exist.

    Args:
        path: Path of the code register.

    Returns:
        Codes keyed by vendor and region.
    """
    if not path.exists():
        return {}
    return {(ProviderId(r["provider"]), RegionId(r["region_id"])): r["code"] for r in data.read_csv(path)}


def _region_calls(
    region: Region,
    row: DraftRow,
    records: Iterable[EvidenceRecord],
    shared: str,
    airports: Mapping[IataCode, Airport],
) -> list[Call]:
    """The review calls for one region, from its codes, grade, vendor code and conflicting evidence.

    Args:
        region: Published region.
        row: Region row being built; changed in place.
        records: Evidence records.
        shared: Other cities that use the same airport.
        airports: Airports keyed by IATA code.

    Returns:
        The calls.
    """
    point = (float(row["lat"]), float(row["lon"])) if row.get("lat") else None
    codes: set[str | None] = {
        row.get("iata_nearest_airport"),
        row.get("iata_major_hub"),
        row.get("iata_metro"),
    }
    km = row.get("iata_nearest_airport_km")
    fields: CallFacts = {
        "iata_nearest_airport": row.get("iata_nearest_airport", ""),
        "iata_major_hub": row.get("iata_major_hub", ""),
        "unlocode": row.get("unlocode", ""),
        "grade": row["grade"],
        "airport_km": km if isinstance(km, int) else None,
        "vendor_code": region.vendor_code,
        "feed_conflict": row.get("feed_conflict", ""),
        "shared_with": shared,
        "conflict": any(r.get("stance") == "contradicts" for r in records),
    }
    extra = _vendor_code_fields(region, point, codes, airports)
    if "vendor_code_unknown" in extra:
        fields["vendor_code_unknown"] = extra["vendor_code_unknown"]
    if "vendor_code_note" in extra:
        fields["vendor_code_note"] = extra["vendor_code_note"]
    return calls_for_region(fields)


def _region_facilities(region: Region, mapping: PlaceMapping | None, ctx: Context) -> list[Facility]:
    """The vendor's PeeringDB facilities in the region's placed city.

    Args:
        region: Published region.
        mapping: Mapping of the region to its place, if it has one.
        ctx: Build context.

    Returns:
        The matching facilities.
    """
    place = ctx.places.get(mapping["place_id"]) if mapping else None
    if not place or region.provider not in ctx.operators:
        return []
    return vendor_facilities(
        ctx.operators[region.provider], (float(place["lat"]), float(place["lon"])), ctx.facilities
    )


def _shared_places(rows: Iterable[DraftRow]) -> defaultdict[str, set[str]]:
    """The cities that share each nearest airport.

    Args:
        rows: Region rows being built.

    Returns:
        City names keyed by airport code.
    """
    towns: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        airport = row.get("iata_nearest_airport")
        if airport:
            towns[airport].add(row["city"])
    return towns


def _stale_mapping_calls(ctx: Context, regions: Iterable[Region]) -> list[CallRow]:
    """A high-severity call for each mapping whose region the vendor no longer lists.

    Args:
        ctx: Build context.
        regions: Published regions.

    Returns:
        The calls.
    """
    live = {(r.provider, r.region_id) for r in regions}
    return [
        {
            "provider": p,
            "region_id": r,
            "kind": "gap-unmapped",
            "severity": "high",
            "detail": "Curated mapping has no matching region in the vendor's current list.",
        }
        for p, r in sorted(ctx.mappings.keys() - live)
    ]


def _table_rows(
    rows: Iterable[DraftRow], iata: IataColumn, code: CodeColumn, why: WhyColumn
) -> list[TableRow]:
    """The rows of one code table, for regions that have a code in it.

    Args:
        rows: Region rows being built.
        iata: Column that holds the airport code.
        code: Column that holds the assigned code.
        why: Column that holds the reason.

    Returns:
        The table rows.
    """
    return [
        {
            "code": assigned,
            "provider": r["provider"],
            "partition": r["partition"],
            "region_id": r["region_id"],
            "region_name": r["region_name"],
            "city": r.get("city", ""),
            "country": r.get("country", ""),
            "iata": r.get(iata, ""),
            "grade": r["grade"],
            "precision": r["precision"],
            "area_basis": r.get("area_basis", ""),
            "reasoning": r.get(why, ""),
            "evidence_urls": r["evidence_urls"],
        }
        for r in rows
        if (assigned := r.get(code))
    ]


def _unlocode_why(place: Place, ctx: Context, grade: Grade) -> str:
    """The reason for the UN/LOCODE, or for leaving it blank.

    Args:
        place: Place of the region.
        ctx: Build context.
        grade: Grade of the region.

    Returns:
        The reason text.
    """
    code = place["unlocode"]
    if not code:
        return f"No UN/LOCODE entry found for {place['city']}."
    row = ctx.locodes[Locode(code)]
    sub = f", {row['Subdivision']}" if row["Subdivision"] else ""
    return f"Listed as {row['Name']}{sub}. Place evidence: {grade}."


def _unmapped_row(
    region: Region, records: Iterable[EvidenceRecord], mapping_precision: Precision = ""
) -> DraftRow:
    """A row for a region with no place: codes blank, grade assumed, with the reason in each field.

    Args:
        region: Published region.
        records: Evidence records.
        mapping_precision: Precision to record for the row.

    Returns:
        The row.
    """
    listed = list(records)
    reason = "Vendor publishes no city and sources checked give none, so left blank."
    return cast(
        DraftRow,
        dict.fromkeys(REGION_FIELDS, "")
        | {
            "provider": region.provider,
            "partition": region.partition,
            "region_id": region.region_id,
            "region_name": region.name,
            "stated_location": region.stated_location,
            "vendor_code": region.vendor_code,
            "grade": "assumed",
            "precision": mapping_precision,
            "location_reasoning": reason,
            "address_status": "not-published",
            "iata_nearest_airport_why": reason,
            "iata_major_hub_why": reason,
            "iata_nearby_hub_why": reason,
            "unlocode_why": reason,
            "clli_why": reason,
            "evidence_ids": _evidence_ids(listed),
            "evidence_urls": _evidence_urls(listed),
        },
    )


def _vendor_code_fields(
    region: Region, point: Point | None, codes: Collection[str | None], airports: Mapping[IataCode, Airport]
) -> VendorCodeFacts:
    """Flag a vendor code that is no known airport, or an airport beyond the conflict distance from the place.

    Args:
        region: Published region.
        point: Latitude and longitude, if known.
        codes: Codes already derived for the region.
        airports: Airports keyed by IATA code.

    Returns:
        The flags; empty when the vendor code raises nothing.
    """
    code = region.vendor_code
    if not code or code in codes:
        return {}
    other = airports.get(IataCode(code))
    if not other:
        return {"vendor_code_unknown": True}
    if point and haversine_km(point, other.point) > CONFLICT_KM:
        return {"vendor_code_note": f"{other.name} ({other.country})"}
    return {}


def _vendor_list_record(region: Region, mapping: PlaceMapping | None) -> EvidenceRecord:
    """The evidence record that the vendor's own region list names the region.

    Args:
        region: Published region.
        mapping: Mapping of the region to its place, if it has one.

    Returns:
        The evidence record.
    """
    stated_city = mapping is not None and mapping["stated_as"] == "city"
    source = region.source or ("aws-partitions" if region.partition.startswith("aws-") else region.provider)
    return {
        "provider": region.provider,
        "region_id": region.region_id,
        "claim": "city" if stated_city else "area",
        "value": region.stated_location,
        "source_type": "vendor_region_list",
        "publisher": region.provider,
        "title": f"{region.provider} region list",
        "url": data.SOURCE_URLS[source],
        "published": "",
        "quote": region.stated_location,
        "stance": "supports" if stated_city else "partial",
        "note": "Parsed from the saved copy of the vendor list.",
    }


def _write_calls(
    rows: Sequence[DraftRow], by_key: ByKey, published: Sequence[Region], ctx: Context, output: Path
) -> list[CallRow]:
    """Collect every call, write the calls register and return it.

    Args:
        rows: Region rows being built.
        by_key: Evidence records keyed by region.
        published: Regions the vendors publish.
        ctx: Build context.
        output: Output folder.

    Returns:
        The call rows.
    """
    candidates = load_candidates(ctx.feed_hits, {(r.provider, r.region_id) for r in published})
    calls = (
        _build_calls(rows, by_key, ctx.airports)
        + _stale_mapping_calls(ctx, published)
        + _candidate_calls(candidates)
    )
    _write_csv(output / "candidate_regions.csv", CANDIDATE_FIELDS, candidates)
    _write_csv(output / "calls.csv", ["provider", "region_id", "kind", "severity", "detail"], calls)
    _write_csv(output / "regions.csv", REGION_FIELDS, rows)
    return calls


def _write_csv(path: StrPath, fields: Sequence[str], rows: Iterable[Mapping[str, object]]) -> None:
    """Write rows to CSV with the given columns.

    Args:
        path: CSV file to write.
        fields: Column names, in order.
        rows: Rows to write or read.
    """
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_evidence(collected: Collected, output: Path) -> list[EvidenceRecord]:
    """Write the evidence register and return its records.

    Args:
        collected: Region and evidence records per region.
        output: Output folder.

    Returns:
        The evidence records.
    """
    evidence = [record for _region, records in collected.values() for record in records]
    _write_csv(output / "evidence.csv", EVIDENCE_FIELDS, evidence)
    return evidence


def _write_registry(path: Path, registry: Mapping[RegionKey, str]) -> None:
    """Write the code register, sorted by vendor and region.

    Args:
        path: Path of the code register.
        registry: Codes already assigned, keyed by region.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"provider": p, "region_id": r, "code": c} for (p, r), c in sorted(registry.items())]
    _write_csv(path, ["provider", "region_id", "code"], rows)


def _write_tables(rows: Sequence[DraftRow], output: Path) -> None:
    """Write the three code tables.

    Args:
        rows: Region rows being built.
        output: Output folder.
    """
    _write_csv(
        output / "codes_nearest_airport.csv",
        TABLE_FIELDS,
        _table_rows(rows, "iata_nearest_airport", "code_nearest_airport", "iata_nearest_airport_why"),
    )
    _write_csv(
        output / "codes_major_hub.csv",
        TABLE_FIELDS,
        _table_rows(rows, "iata_major_hub", "code_major_hub", "iata_major_hub_why"),
    )
    _write_csv(
        output / "codes_nearby_hub.csv",
        TABLE_FIELDS,
        _table_rows(rows, "iata_nearby_hub", "code_nearby_hub", "iata_nearby_hub_why"),
    )
