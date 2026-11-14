# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Shared types: identifiers, closed vocabularies and the points code passes around."""

from typing import Final, Literal, NewType, NotRequired, Required, TypeAlias, TypedDict, get_args

ProviderId = NewType("ProviderId", str)
RegionId = NewType("RegionId", str)
PlaceId = NewType("PlaceId", str)
IataCode = NewType("IataCode", str)
CountryCode = NewType("CountryCode", str)
Locode = NewType("Locode", str)
ClliPrefix = NewType("ClliPrefix", str)
EvidenceId = NewType("EvidenceId", str)
Sha256 = NewType("Sha256", str)
Url = NewType("Url", str)
Partition = NewType("Partition", str)
RegionKey: TypeAlias = tuple[ProviderId, RegionId]
Point: TypeAlias = tuple[float, float]
Kilometres: TypeAlias = float

Grade: TypeAlias = Literal[
    "vendor-stated",
    "vendor-document",
    "audit-document",
    "public-record",
    "measured",
    "vendor-feed",
    "reported",
    "network-presence",
    "partial",
    "assumed",
]

Precision: TypeAlias = Literal["city", "metro", ""]
Stance: TypeAlias = Literal["supports", "partial", "contradicts"]

SourceType: TypeAlias = Literal[
    "vendor_region_list",
    "vendor",
    "vendor_feed",
    "certification",
    "code_join",
    "measurement",
    "government",
    "paper",
    "news",
    "directory",
    "peeringdb",
]

QuoteCheck: TypeAlias = Literal[
    "quote-found",
    "quote-found-saved-copy",
    "quote-not-found",
    "also-not-found",
    "no-quote",
    "unreachable",
    "format-not-checked",
    "not-checked",
]

Severity: TypeAlias = Literal["high", "medium", "low"]

CallKind: TypeAlias = Literal[
    "gap-unmapped",
    "gap-iata",
    "evidence-conflict",
    "vendor-code-conflict",
    "city-assumed",
    "city-partial",
    "gap-unlocode",
    "city-reported",
    "nearest-differs-from-hub",
    "far-airport",
    "shared-airport",
    "gap-clli",
    "vendor-code-not-iata",
    "feed-conflict",
    "candidate-region",
    "city-feed",
]

Basis: TypeAlias = Literal["label", "document", "feed", "memory", "capital", "none"]
CrossCheck: TypeAlias = Literal["agrees", "conflicts", ""]
AreaBasis: TypeAlias = Literal["exact-city", "metro-hub", "nearest-hub-too-far", ""]
HubClass: TypeAlias = Literal["large", "medium", ""]
Claim: TypeAlias = Literal["city", "area", "facility-code"]
StatedAs: TypeAlias = Literal["city", "area", "none"]

GRADES: Final[tuple[Grade, ...]] = get_args(Grade)


class EvidenceRecord(TypedDict, total=False):
    """One claim a source makes about a region, with its quote, stance and check result."""

    evidence_id: EvidenceId
    provider: Required[ProviderId]
    region_id: Required[RegionId]
    claim: Claim
    value: str
    source_type: SourceType
    publisher: str
    title: str
    url: str
    published: str
    quote: str
    stance: Stance
    quote_check: QuoteCheck | Literal[""]
    note: str
    join_code: str
    also_found: str


class CallRow(TypedDict):
    """One gap or judgement call raised for a region, as written to calls.csv."""

    provider: ProviderId
    region_id: RegionId
    kind: CallKind
    severity: Severity
    detail: str


class Signal(TypedDict):
    """A named point that one source gives for a region."""

    name: str
    lat: float
    lon: float
    country: NotRequired[str]


class Place(TypedDict):
    """A town with coordinates and the codes derived for it."""

    place_id: str
    city: str
    country: CountryCode
    lat: float | str
    lon: float | str
    unlocode: str
    hub_iata: IataCode
    clli: str
    geonames_name: NotRequired[str]


class PlaceMapping(TypedDict):
    """Which place a region maps to, on what basis, and how precisely."""

    provider: ProviderId
    region_id: RegionId
    place_id: str
    stated_as: StatedAs
    precision: Precision
    basis: Basis
    cross_check: CrossCheck
    note: str


class FeedMatch(TypedDict):
    """How a vendor's IP feed compares with a region's place."""

    city: str
    km: int | Literal[""]
    supports: bool
    conflict: str


class CandidateRow(TypedDict):
    """A region ID found in a vendor's IP ranges but absent from its region list."""

    provider: ProviderId
    region_id: RegionId
    feed_city: str
    feed_subdivision: str
    feed_country: str
    feed_share: float


class Located(TypedDict):
    """Anything with a latitude and a longitude, as numbers or as CSV text."""

    lat: float | str
    lon: float | str


class QuotedCity(Located):
    """A city read in a passage, with the words that name it."""

    quote: str
    city: str


class DraftRow(TypedDict, total=False):
    """A region row while the build fills it in: graded first, then coded, then counted."""

    provider: Required[ProviderId]
    partition: Required[Partition]
    region_id: Required[RegionId]
    region_name: Required[str]
    stated_location: Required[str]
    vendor_code: Required[str]
    place_id: Required[str]
    city: Required[str]
    country: Required[str]
    lat: Required[float | str]
    lon: Required[float | str]
    grade: Required[Grade]
    precision: Required[Precision]
    place_basis: Required[Basis | Literal[""]]
    cross_check: Required[CrossCheck]
    location_reasoning: Required[str]
    address: Required[str]
    address_status: Required[str]
    feed_city: Required[str]
    feed_km: Required[int | str]
    evidence_ids: Required[str]
    evidence_urls: Required[str]
    feed_conflict: str
    iata_nearest_airport: IataCode
    iata_nearest_airport_km: int | str
    iata_nearest_airport_why: str
    code_nearest_airport: str
    iata_major_hub: IataCode
    iata_major_hub_why: str
    code_major_hub: str
    iata_nearby_hub: IataCode
    iata_nearby_hub_why: str
    code_nearby_hub: str
    hub_class: HubClass
    area_basis: AreaBasis
    iata_metro: str
    unlocode: str
    unlocode_why: str
    clli: str
    clli_why: str
    call_count: int | str


class RegionRow(TypedDict):
    """A finished region row, as written to regions.csv."""

    provider: ProviderId
    partition: Partition
    region_id: RegionId
    region_name: str
    stated_location: str
    vendor_code: str
    place_id: str
    city: str
    country: str
    lat: float | str
    lon: float | str
    grade: Grade
    precision: Precision
    place_basis: Basis | Literal[""]
    cross_check: CrossCheck
    location_reasoning: str
    address: str
    address_status: str
    feed_city: str
    feed_km: int | str
    evidence_ids: str
    evidence_urls: str
    iata_nearest_airport: IataCode
    iata_nearest_airport_km: int | str
    iata_nearest_airport_why: str
    code_nearest_airport: str
    iata_major_hub: IataCode
    iata_major_hub_why: str
    code_major_hub: str
    iata_nearby_hub: IataCode
    iata_nearby_hub_why: str
    code_nearby_hub: str
    hub_class: HubClass
    area_basis: AreaBasis
    iata_metro: str
    unlocode: str
    unlocode_why: str
    clli: str
    clli_why: str
    call_count: int | str
    feed_conflict: NotRequired[str]
