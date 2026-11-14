# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Load raw sources and the project's own inputs."""

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Final, NamedTuple, Required, TypedDict, cast

from ocm_regions.common import layout
from ocm_regions.common.geo import Airport
from ocm_regions.common.schema import (
    CountryCode,
    IataCode,
    Locode,
    Partition,
    ProviderId,
    RegionId,
    RegionKey,
)
from ocm_regions.fetch.sources import SOURCES, StrPath
from ocm_regions.place.metro import MetroTable, parse_optd
from ocm_regions.regions import parsers
from ocm_regions.regions.parsers import Region

OPEN_PARTITIONS: Final[frozenset[Partition]] = frozenset(
    {Partition("public"), Partition("aws-cn"), Partition("aws-eusc")}
)

SOURCE_URLS: Final[dict[str, str]] = {s.name: s.url for s in SOURCES}


class LocodeRow(TypedDict):
    """One row of the UN/LOCODE code list, with the columns the build reads."""

    Country: str
    Location: str
    Name: str
    NameWoDiacritics: str
    Subdivision: str
    Coordinates: str


class Facility(TypedDict, total=False):
    """A PeeringDB facility; the API leaves fields null or absent."""

    id: Required[int]
    name: Required[str]
    org_name: str | None
    city: Required[str]
    country: Required[str]
    clli: str | None
    latitude: float | str | None
    longitude: float | str | None


class _FacilityFile(TypedDict):
    """The PeeringDB facility file: a list of facilities under ``data``."""

    data: list[Facility]


class ReferencePlacement(TypedDict):
    """An earlier placement of one region, used only to score methods."""

    provider: ProviderId
    region_id: RegionId
    city: str
    country: str
    lat: str
    lon: str


class Scope(NamedTuple):
    """Why a partition is out of scope, and the vendor mark that shows it."""

    reason: str
    marked_by: str


CLASSIFIED: Final = Scope("Classified government only", "botocore partition label")

EXCLUDED_PARTITIONS: Final[dict[Partition, Scope]] = {
    Partition("aws-us-gov"): Scope("Government customers only", "botocore partition label"),
    Partition("aws-iso"): CLASSIFIED,
    Partition("aws-iso-b"): CLASSIFIED,
    Partition("aws-iso-e"): CLASSIFIED,
    Partition("aws-iso-f"): CLASSIFIED,
    Partition("alibaba-gov"): Scope("Government customers only", "region ID contains 'gov'"),
    Partition("alibaba-finance"): Scope("Financial institutions only", "region ID contains 'finance'"),
    Partition("tencent-finance"): Scope("Financial institutions only", "region ID ends '-fsi'"),
    Partition("tencent-autonomous"): Scope("Self-driving customers only", "region ID ends '-adc'"),
    Partition("huawei-invitation"): Scope("Invitation only", "region name contains 'InvitationOnly'"),
}


def load_airports(raw: Path) -> dict[IataCode, Airport]:
    """Load OurAirports rows that have an IATA code, keyed by that code.

    Args:
        raw: The data/raw folder.

    Returns:
        Airports keyed by IATA code.
    """
    airports: dict[IataCode, Airport] = {}
    for r in read_csv(raw / "ourairports/airports.csv"):
        code = IataCode(r["iata_code"].strip())
        if code:
            airports[code] = Airport(
                code,
                r["name"],
                r["municipality"],
                CountryCode(r["iso_country"]),
                float(r["latitude_deg"]),
                float(r["longitude_deg"]),
                r["type"],
                r["scheduled_service"] == "yes",
            )
    return airports


def load_facilities(raw: Path) -> list[Facility]:
    """Load PeeringDB facilities from the saved API reply.

    Args:
        raw: The data/raw folder.

    Returns:
        The facilities, in file order.
    """
    return cast(_FacilityFile, json.loads((raw / "peeringdb/fac.json").read_text(encoding="utf-8")))["data"]


def load_locodes(raw: Path) -> dict[Locode, LocodeRow]:
    """Load the UN/LOCODE list, keyed by the five-character code.

    Args:
        raw: The data/raw folder.

    Returns:
        UN/LOCODE rows keyed by code.
    """
    rows = cast(list[LocodeRow], read_csv(raw / "un-locode/code-list.csv"))
    return {Locode(r["Country"] + r["Location"]): r for r in rows}


def load_metros(raw: Path) -> MetroTable:
    """Load the airport-to-metropolitan-code table from OpenTravelData.

    Args:
        raw: The data/raw folder.

    Returns:
        Metropolitan codes per airport.
    """
    return parse_optd((raw / "opentraveldata/optd_por_public.csv").read_text(encoding="utf-8"))


def load_published_regions(raw: Path) -> list[Region]:
    """Parse every vendor's saved region list into one list of regions.

    Args:
        raw: The data/raw folder.

    Returns:
        Every region the vendors publish, restricted partitions included.
    """
    regions_dir = raw / "cloud-regions"

    def read(name: str) -> str:
        """Read one saved vendor file from the cloud-regions folder.

        Args:
            name: File name in the cloud-regions folder.

        Returns:
            The file's text.
        """
        return (regions_dir / name).read_text(encoding="utf-8", errors="replace")

    return [
        *parsers.parse_aws(read("aws/regions.html")),
        *parsers.parse_aws_partitions(read("aws/botocore-endpoints.json")),
        *parsers.parse_azure(read("azure/regions-list.html")),
        *parsers.parse_gcp(read("gcp/regions-zones.html")),
        *parsers.parse_alibaba(read("alibaba/regions-zones.html")),
        *parsers.parse_oracle(read("oracle/regions.html")),
        *parsers.parse_hetzner(read("hetzner/locations.html")),
        *parsers.parse_vultr(read("vultr/regions.json")),
        *parsers.parse_digitalocean(read("digitalocean/regional-availability.html")),
        *parsers.parse_tencent(read("tencent/regions.html")),
        *parsers.parse_ovhcloud(read("ovhcloud/regions.html")),
        *parsers.parse_ibm(read("ibm/locations.html")),
        *parsers.parse_akamai(read("akamai/regions.json")),
        *parsers.parse_huawei(read("huawei/cts-endpoints.html"), read("huawei/scp-regions.html")),
    ]


def load_reference(inputs: Path) -> dict[RegionKey, ReferencePlacement]:
    """Load the reference placements, keyed by vendor and region.

    Args:
        inputs: Repository root that holds the input folders.

    Returns:
        Reference placements keyed by vendor and region.
    """
    rows = cast(list[ReferencePlacement], read_csv(layout.reference(inputs)))
    return {(r["provider"], r["region_id"]): r for r in rows}


def load_regions(raw: Path) -> list[Region]:
    """Load every published region that is open to ordinary customers.

    Args:
        raw: The data/raw folder.

    Returns:
        The open regions of every vendor.
    """
    return open_regions(load_published_regions(raw))


def open_regions(regions: Iterable[Region]) -> list[Region]:
    """Keep regions open to ordinary customers; a partition nobody has reviewed stops the build.

    Args:
        regions: Published regions.

    Returns:
        The regions outside the restricted partitions.

    Raises:
        ValueError: If a region sits in a partition nobody has reviewed.
    """
    listed = list(regions)
    unknown = {r.partition for r in listed} - OPEN_PARTITIONS - set(EXCLUDED_PARTITIONS)
    if unknown:
        raise ValueError(f"unreviewed region partitions: {sorted(unknown)}")
    return [r for r in listed if r.partition in OPEN_PARTITIONS]


def read_as[Row](path: StrPath, shape: type[Row]) -> list[Row]:
    """Read a CSV as rows of one shape, refusing a file that lacks a column the shape requires.

    Args:
        path: CSV file to read.
        shape: TypedDict class the rows must satisfy.

    Returns:
        The rows, typed as the given shape.

    Raises:
        ValueError: If the file lacks a column the shape requires.
    """
    rows = read_csv(path)
    required = cast(frozenset[str], getattr(shape, "__required_keys__", frozenset[str]()))
    missing = sorted(required - set(rows[0])) if rows else []
    if missing:
        raise ValueError(f"{path} lacks columns: {', '.join(missing)}")
    return cast(list[Row], rows)


def read_csv(path: StrPath) -> list[dict[str, str]]:
    """Read a CSV file as a list of rows keyed by column name, all values as text.

    Args:
        path: CSV file to read.

    Returns:
        One dict per row, keyed by column name.
    """
    return cast(list[dict[str, str]], list(csv.DictReader(Path(path).open(encoding="utf-8"))))
