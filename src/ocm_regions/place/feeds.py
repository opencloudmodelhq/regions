# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Use vendor IP location feeds as evidence for the place of a region."""

import bisect
import csv
import io
import ipaddress
import json
import zipfile
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias, TypedDict, cast

from ocm_regions.common import data
from ocm_regions.common.geo import haversine_km, parse_locode_coordinates, slug
from ocm_regions.common.schema import (
    CandidateRow,
    EvidenceRecord,
    FeedMatch,
    Kilometres,
    Place,
    PlaceMapping,
    Point,
    ProviderId,
    RegionId,
    RegionKey,
)
from ocm_regions.fetch.sources import StrPath

PointKey: TypeAlias = tuple[str, str] | tuple[str, str, str]
PointLookup: TypeAlias = Callable[[PointKey], Point | None]
CityKey: TypeAlias = tuple[str, str, str]
Ranges: TypeAlias = dict[str, list[str]]

ALIAS_POPULATION: Final = 200000

AZURE_FEED_URL: Final = (
    "https://download.microsoft.com/download/0b9dc6ca-68f9-43b2-b094-da4f6d509bc3/geoloc-Microsoft.csv"
)

DIRECTIONS: Final[dict[str, str]] = {"n": "north", "e": "east", "s": "south", "w": "west", "c": "central"}
MIN_SHARE: float = 0.5
SUPPORT_KM: Final = 50

_CITY_ONLY: Final[dict[str, str]] = {
    "vultr": "feeds/vultr/geofeed.csv",
    "digitalocean": "feeds/digitalocean/geofeed.csv",
    "hetzner": "feeds/hetzner/geofeed.csv",
}

_FEED_URLS: Final[dict[str, str]] = {
    "aws": "https://ip-ranges.amazonaws.com/geo-ip-feed.csv",
    "gcp": "https://www.gstatic.com/ipranges/cloud_geofeed",
    "azure": AZURE_FEED_URL,
    "akamai": "https://ipgeo.akamai.com/linode-geofeed.csv",
    "vultr": "https://geofeed.constant.com/",
    "digitalocean": "https://digitalocean.com/geo/google.csv",
    "hetzner": "https://www.hetzner.com/geofeed.csv",
}

_REGION_TIED: Final = frozenset({"aws", "gcp", "azure"})


@dataclass(frozen=True)
class FeedRow:
    """One row of an RFC 8805 IP feed: an address range with its country, subdivision and city."""

    start: int
    end: int
    country: str
    subdivision: str
    city: str
    raw: str


@dataclass(frozen=True)
class FeedHit:
    """The city that holds most of a region's addresses in a feed, with its share and a sample row."""

    city: str
    subdivision: str
    country: str
    share: float
    quote: str


class _AwsPrefix(TypedDict):
    """One prefix in AWS's IP ranges file."""

    region: str
    ip_prefix: str


class _AwsRanges(TypedDict):
    """AWS's IP ranges file."""

    prefixes: list[_AwsPrefix]


class _GcpPrefix(TypedDict, total=False):
    """One prefix in Google Cloud's IP ranges file."""

    scope: str
    ipv4Prefix: str


class _GcpRanges(TypedDict):
    """Google Cloud's IP ranges file."""

    prefixes: list[_GcpPrefix]


class _AzureProperties(TypedDict, total=False):
    """The region and prefixes of one Azure service tag."""

    region: str
    addressPrefixes: list[str]


class _AzureValue(TypedDict):
    """One service tag in Azure's service tags file."""

    name: str
    properties: _AzureProperties


class _AzureTags(TypedDict):
    """Azure's service tags file."""

    values: list[_AzureValue]


class _AkamaiResolvers(TypedDict):
    """The resolver addresses Akamai lists for a region."""

    ipv4: str


class _AkamaiRegion(TypedDict):
    """One region in Akamai's regions reply, with its resolvers."""

    id: str
    resolvers: _AkamaiResolvers


class _AkamaiRegions(TypedDict):
    """Akamai's regions reply."""

    data: list[_AkamaiRegion]


def akamai_ranges(text: str) -> Ranges:
    """Akamai resolver addresses per region, each as a single-address prefix.

    Args:
        text: Akamai's regions reply as JSON text.

    Returns:
        Prefixes keyed by region.
    """
    return {
        r["id"]: [f"{ip.strip()}/32" for ip in r["resolvers"]["ipv4"].split(",")]
        for r in cast(_AkamaiRegions, json.loads(text))["data"]
    }


def aws_ranges(text: str) -> Ranges:
    """AWS IPv4 prefixes per region, leaving out global ranges.

    Args:
        text: AWS's IP ranges file as JSON text.

    Returns:
        Prefixes keyed by region.
    """
    return _grouped(
        (p["region"], p["ip_prefix"])
        for p in cast(_AwsRanges, json.loads(text))["prefixes"]
        if p["region"] != "GLOBAL"
    )


def azure_ranges(text: str, aliases: Mapping[str, str]) -> Ranges:
    """Azure IPv4 prefixes per region, with Microsoft's short region names mapped to region IDs.

    Args:
        text: Azure's service tags file as JSON text.
        aliases: Azure's region names mapped to region IDs.

    Returns:
        Prefixes keyed by region ID.
    """
    pairs: list[tuple[str, str]] = []
    for value in cast(_AzureTags, json.loads(text))["values"]:
        region = value["properties"].get("region")
        if value["name"].startswith("AzureCloud.") and region:
            pairs += [
                (aliases.get(region, region), p)
                for p in value["properties"].get("addressPrefixes", [])
                if ":" not in p
            ]
    return _grouped(pairs)


def candidate_rows(hits: Mapping[RegionKey, FeedHit], known: Collection[RegionKey]) -> list[CandidateRow]:
    """Region IDs in a vendor's IP ranges that its region list does not name.

    Args:
        hits: Dominant feed city per region.
        known: Regions the vendors publish.

    Returns:
        One row per unlisted region ID.
    """
    return [
        {
            "provider": key[0],
            "region_id": key[1],
            "feed_city": hit.city,
            "feed_subdivision": hit.subdivision,
            "feed_country": hit.country,
            "feed_share": round(hit.share, 2),
        }
        for key, hit in sorted(hits.items())
        if key not in known
    ]


def dominant_city(prefixes: Iterable[str], rows: Sequence[FeedRow]) -> FeedHit | None:
    """Return the city holding most addresses across prefixes, counting a repeated prefix each time.

    Args:
        prefixes: IP prefixes of the region.
        rows: Feed rows sorted by start address.

    Returns:
        The leading city with its share, or None when no prefix matches.
    """
    starts = [r.start for r in rows]
    counts: Counter[CityKey] = Counter()
    quotes: dict[CityKey, str] = {}
    for prefix in prefixes:
        network = ipaddress.ip_network(prefix, strict=False)
        for row, size in _overlaps(int(network[0]), int(network[-1]), rows, starts):
            key = (row.city, row.subdivision, row.country)
            counts[key] += size
            _ = quotes.setdefault(key, row.raw)
    if not counts:
        return None
    key, top = counts.most_common(1)[0]
    return FeedHit(*key, top / sum(counts.values()), quotes[key])


def feed_km(hit: FeedHit, place: Place, lookup: PointLookup) -> Kilometres | None:
    """Distance from a feed city to a place, or None when the name cannot be resolved.

    Args:
        hit: Dominant feed city for a region.
        place: Place of the region.
        lookup: Coordinates by country and city name.

    Returns:
        The distance in kilometres, or None.
    """
    own = {slug(place["city"]), slug(place.get("geonames_name") or place["city"])}
    if own & {slug(n) for n in _names(hit)}:
        return 0
    point = feed_point(hit, lookup)
    return haversine_km(point, (float(place["lat"]), float(place["lon"]))) if point else None


def feed_point(hit: FeedHit, lookup: PointLookup) -> Point | None:
    """Coordinates of a feed city, trying the US state first where one is given.

    Args:
        hit: Dominant feed city for a region.
        lookup: Coordinates by country and city name.

    Returns:
        Latitude and longitude, or None when the name is unknown.
    """
    state = hit.subdivision.split("-")[-1]
    for name in _names(hit):
        point = (hit.country == "US" and lookup((hit.country, state, slug(name)))) or lookup(
            (hit.country, slug(name))
        )
        if point:
            return point
    return None


def feed_record(
    provider: ProviderId, region_id: RegionId, hit: FeedHit, km: Kilometres | None, url: str, linked: bool
) -> EvidenceRecord:
    """Build the evidence record for a feed hit; it supports a place only within 50 km and with half the addresses.

    Args:
        provider: Vendor identifier.
        region_id: Region identifier.
        hit: Dominant feed city for a region.
        km: Distance from the feed city to the place, if known.
        url: URL of the document.
        linked: Whether the feed names the region.

    Returns:
        The evidence record.
    """
    supports = linked and km is not None and km <= SUPPORT_KM and hit.share >= MIN_SHARE
    return {
        "provider": provider,
        "region_id": region_id,
        "claim": "city",
        "value": f"{hit.city}, {hit.country}",
        "source_type": "vendor_feed",
        "publisher": provider,
        "title": f"{provider} IP location feed",
        "url": url,
        "published": "",
        "quote": hit.quote,
        "stance": "supports" if supports else "partial",
        "note": _feed_note(hit, km, linked),
    }


def feed_region_id(name: str, region_ids: Collection[str]) -> str:
    """Microsoft's feed shortens some names: a trailing letter is a direction, and word order can differ.

    Args:
        name: Region name as the feed prints it.
        region_ids: Region identifiers of the vendor.

    Returns:
        The matching region ID, or blank.
    """
    if name in region_ids:
        return name
    for region_id in region_ids:
        if sorted(region_id) == sorted(name):
            return region_id
    for size in (1, 2):
        letters = name[-size:]
        if all(letter in DIRECTIONS for letter in letters):
            expanded = name[:-size] + "".join(DIRECTIONS[letter] for letter in letters)
            if expanded in region_ids:
                return expanded
    return name


def gcp_ranges(text: str) -> Ranges:
    """Google Cloud IPv4 prefixes per region, leaving out global ranges.

    Args:
        text: Google Cloud's IP ranges file as JSON text.

    Returns:
        Prefixes keyed by region.
    """
    prefixes = cast(_GcpRanges, json.loads(text))["prefixes"]
    return _grouped(
        (p.get("scope", ""), p["ipv4Prefix"])
        for p in prefixes
        if "ipv4Prefix" in p and p.get("scope") != "global"
    )


def load_candidates(hits: Mapping[RegionKey, FeedHit], known: Collection[RegionKey]) -> list[CandidateRow]:
    """Region IDs in AWS's and Google's IP ranges that their region lists do not name.

    Args:
        hits: Dominant feed city per region.
        known: Regions the vendors publish.

    Returns:
        One row per unlisted region ID.
    """
    tied = {k: v for k, v in hits.items() if k[0] in {"aws", "gcp"}}
    return candidate_rows(tied, known)


def load_feed_evidence(
    raw: StrPath,
    places: Mapping[str, Place],
    mappings: Mapping[RegionKey, PlaceMapping],
) -> tuple[dict[RegionKey, list[EvidenceRecord]], dict[RegionKey, FeedMatch], dict[RegionKey, FeedHit]]:
    """Return (records, matches, region_hits) keyed by (provider, region_id).

    Args:
        raw: The data/raw folder.
        places: Places keyed by identifier.
        mappings: Mapping per region.

    Returns:
        The evidence records, the match summaries and the region hits.
    """
    lookup, hits = make_lookup(raw), load_region_hits(raw)
    records: dict[RegionKey, list[EvidenceRecord]] = {}
    matches: dict[RegionKey, FeedMatch] = {}
    cities = _city_hits(raw)
    for key, mapping in mappings.items():
        place = places.get(mapping["place_id"])
        if not place:
            continue
        if key in hits:
            record, matches[key] = _tied_match(key, hits[key], place, lookup)
            records[key] = [record]
        elif key[0] in cities and (near := _nearest_city(cities[key[0]], place, lookup)):
            records[key] = [feed_record(*key, near[1], near[0], _FEED_URLS[key[0]], False)]
            matches[key] = {"city": near[1].city, "km": round(near[0]), "supports": False, "conflict": ""}
    return records, matches, hits


def load_region_hits(raw: StrPath) -> dict[RegionKey, FeedHit]:
    """The dominant feed city for every region whose IP ranges a vendor publishes.

    Args:
        raw: The data/raw folder.

    Returns:
        Feed hits keyed by vendor and region.
    """
    azure = _read(raw, "feeds/azure/service-tags.json")
    sets: list[tuple[ProviderId, Ranges, str]] = [
        (ProviderId("aws"), aws_ranges(_read(raw, "feeds/aws/ip-ranges.json")), "feeds/aws/geo-ip-feed.csv"),
        (ProviderId("gcp"), gcp_ranges(_read(raw, "feeds/gcp/cloud.json")), "feeds/gcp/cloud_geofeed.csv"),
        (
            ProviderId("azure"),
            azure_ranges(azure, _azure_aliases(raw, azure)),
            "feeds/azure/geoloc-Microsoft.csv",
        ),
        (
            ProviderId("akamai"),
            akamai_ranges(_read(raw, "cloud-regions/akamai/regions.json")),
            "feeds/akamai/geofeed.csv",
        ),
    ]
    hits: dict[RegionKey, FeedHit] = {}
    for provider, ranges, path in sets:
        rows = parse_geofeed(_read(raw, path))
        for region_id, prefixes in ranges.items():
            hit = dominant_city(prefixes, rows)
            if hit:
                hits[(provider, RegionId(region_id))] = hit
    return hits


def make_lookup(raw: StrPath) -> PointLookup:
    """Build a lookup from country and city name to coordinates, from GeoNames and UN/LOCODE.

    Args:
        raw: The data/raw folder.

    Returns:
        The lookup.
    """
    points: dict[PointKey, Point] = {}
    with zipfile.ZipFile(Path(raw) / "geonames/cities500.zip") as archive:
        lines = archive.read("cities500.txt").decode().splitlines()
    best: dict[PointKey, int] = {}
    for line in lines:
        f = line.split("\t")
        names = {f[1], f[2]} | (set(f[3].split(",")) if int(f[14] or 0) >= ALIAS_POPULATION else set[str]())
        for name in names:
            keys: tuple[PointKey, PointKey] = ((f[8], slug(name)), (f[8], f[10], slug(name)))
            for key in keys:
                if int(f[14] or 0) >= best.get(key, -1):
                    best[key], points[key] = int(f[14] or 0), (float(f[4]), float(f[5]))
    for row in data.load_locodes(Path(raw)).values():
        coordinates = parse_locode_coordinates(row["Coordinates"])
        if coordinates:
            _ = points.setdefault((row["Country"], slug(row["NameWoDiacritics"])), coordinates)
    return points.get


def parse_geofeed(text: str) -> list[FeedRow]:
    """Parse a feed into its IPv4 rows, sorted by start address.

    Args:
        text: The feed as CSV text.

    Returns:
        The feed rows.
    """
    rows = (_feed_row(line.strip()) for line in text.splitlines())
    return sorted((r for r in rows if r), key=lambda r: r.start)


def _azure_aliases(raw: StrPath, text: str) -> dict[str, str]:
    """Map each region name in Azure's service tags to the region ID it stands for.

    Args:
        raw: The data/raw folder.
        text: Azure's service tags file as JSON text.

    Returns:
        Region IDs keyed by the feed's region name.
    """
    ids = [r.region_id for r in data.load_regions(Path(raw)) if r.provider == "azure"]
    names = {v["properties"].get("region", "") for v in cast(_AzureTags, json.loads(text))["values"]} - {""}
    return {name: feed_region_id(name, ids) for name in names}


def _city_hits(raw: StrPath) -> dict[str, list[FeedHit]]:
    """First row for each city in feeds that name no region, without parsing every address.

    Args:
        raw: The data/raw folder.

    Returns:
        Feed hits keyed by vendor.
    """
    result: dict[str, list[FeedHit]] = {}
    for provider, path in _CITY_ONLY.items():
        cities: dict[CityKey, str] = {}
        for line in io.StringIO(_read(raw, path)):
            row = next(csv.reader([line.strip()]), cast(list[str], []))
            if len(row) > 3 and row[3].strip() and not line.startswith("#"):
                _ = cities.setdefault((row[3].strip(), row[2].strip(), row[1].strip().upper()), line.strip())
        result[provider] = [FeedHit(*key, 1.0, quote) for key, quote in cities.items()]
    return result


def _feed_note(hit: FeedHit, km: Kilometres | None, linked: bool) -> str:
    """The note on a feed record: the share of addresses and the distance, or that the feed names no region.

    Args:
        hit: Dominant feed city for a region.
        km: Distance from the feed city to the place, if known.
        linked: Whether the feed names the region.

    Returns:
        The note text.
    """
    if not linked:
        return "The feed names this city but not the region. A feed declares where addresses are, not a facility."
    where = "name not resolved" if km is None else f"{km:.0f} km from the place"
    return f"{hit.share:.0%} of this region's addresses; {where}. A feed declares where addresses are, not a facility."


def _feed_row(line: str) -> FeedRow | None:
    """Parse one feed line into an IPv4 range with a city, or None for a comment, IPv6 or a line with no city.

    Args:
        line: One line of text.

    Returns:
        The feed row, or None.
    """
    parts = line.split(",")
    if line.startswith("#") or len(parts) < 4 or not parts[3].strip():
        return None
    try:
        network = ipaddress.ip_network(parts[0].strip(), strict=False)
    except ValueError:
        return None
    if network.version != 4:
        return None
    return FeedRow(
        int(network[0]), int(network[-1]), parts[1].strip().upper(), parts[2].strip(), parts[3].strip(), line
    )


def _grouped(pairs: Iterable[tuple[str, str]]) -> Ranges:
    """Group prefixes by the region they belong to.

    Args:
        pairs: Region and prefix pairs.

    Returns:
        Prefixes keyed by region.
    """
    grouped: Ranges = {}
    for region, prefix in pairs:
        grouped.setdefault(region, []).append(prefix)
    return grouped


def _names(hit: FeedHit) -> list[str]:
    """The feed city's name, and the part before a hyphen, as names to look up.

    Args:
        hit: Dominant feed city for a region.

    Returns:
        The names, most specific first.
    """
    return [hit.city, hit.city.split("-")[0]]


def _nearest_city(
    hits: Iterable[FeedHit], place: Place, lookup: PointLookup
) -> tuple[Kilometres, FeedHit] | None:
    """The feed city nearest a place, within the support distance, for feeds that name no region.

    Args:
        hits: Feed cities to choose from.
        place: Place of the region.
        lookup: Coordinates by country and city name.

    Returns:
        The distance and the feed hit, or None.
    """
    scored = [
        (km, hit) for hit in hits if (km := feed_km(hit, place, lookup)) is not None and km <= SUPPORT_KM
    ]
    return min(scored, key=lambda pair: pair[0]) if scored else None


def _overlaps(
    start: int, end: int, rows: Sequence[FeedRow], starts: Sequence[int]
) -> Iterator[tuple[FeedRow, int]]:
    """Yield each feed row that overlaps an address range, with the number of addresses shared.

    Args:
        start: Start offset in the text.
        end: End offset in the text.
        rows: Feed rows sorted by start address.
        starts: Start address of each feed row.

    Yields:
        A feed row and the number of addresses it shares with the range.
    """
    index = max(bisect.bisect_right(starts, start) - 1, 0)
    while index < len(rows) and rows[index].start <= end:
        row = rows[index]
        size = min(end, row.end) - max(start, row.start) + 1
        if size > 0:
            yield row, size
        index += 1


def _read(raw: StrPath, path: str) -> str:
    """Read one saved file under data/raw as text.

    Args:
        raw: The data/raw folder.
        path: Path relative to the data/raw folder.

    Returns:
        The file's text.
    """
    return (Path(raw) / path).read_text(encoding="utf-8", errors="replace")


def _tied_match(
    key: RegionKey, hit: FeedHit, place: Place, lookup: PointLookup
) -> tuple[EvidenceRecord, FeedMatch]:
    """The evidence record and match summary for a feed that is tied to the region.

    Args:
        key: Vendor and region ID.
        hit: Dominant feed city for a region.
        place: Place of the region.
        lookup: Coordinates by country and city name.

    Returns:
        The record and its match summary.
    """
    km = feed_km(hit, place, lookup)
    linked = key[0] in _REGION_TIED
    record = feed_record(*key, hit, km, _FEED_URLS[key[0]], linked)
    conflict = linked and km is not None and km > SUPPORT_KM and hit.share >= MIN_SHARE
    return record, {
        "city": hit.city,
        "km": "" if km is None else round(km),
        "supports": record.get("stance") == "supports",
        "conflict": f"{hit.city}, {km:.0f} km from the place" if conflict else "",
    }
