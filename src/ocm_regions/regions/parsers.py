# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Parse each provider's published region list into Region records."""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from itertools import pairwise
from typing import Final, TypeAlias, TypedDict, cast, override

from ocm_regions.common.schema import Partition, ProviderId, RegionId

HtmlRow: TypeAlias = list[str]
HtmlTable: TypeAlias = list[HtmlRow]

AKAMAI: Final = ProviderId("akamai")
ALIBABA: Final = ProviderId("alibaba")
AWS: Final = ProviderId("aws")
AZURE: Final = ProviderId("azure")
DIGITALOCEAN: Final = ProviderId("digitalocean")
GCP: Final = ProviderId("gcp")
HETZNER: Final = ProviderId("hetzner")
HUAWEI: Final = ProviderId("huawei")
IBM: Final = ProviderId("ibm")
ORACLE: Final = ProviderId("oracle")
OVHCLOUD: Final = ProviderId("ovhcloud")
PUBLIC: Final = Partition("public")
TENCENT: Final = ProviderId("tencent")
VULTR: Final = ProviderId("vultr")


@dataclass(frozen=True)
class Region:
    """A region as a vendor publishes it: its ID, name, stated location, code and partition."""

    provider: ProviderId
    region_id: RegionId
    name: str
    stated_location: str
    vendor_code: str = ""
    partition: Partition = PUBLIC
    source: str = ""


class _BotocoreRegion(TypedDict, total=False):
    """A region entry in botocore's endpoints file."""

    description: str


class _BotocorePartition(TypedDict):
    """One AWS partition in botocore's endpoints file."""

    partition: str
    regions: dict[str, _BotocoreRegion]


class _BotocoreEndpoints(TypedDict):
    """The top level of botocore's endpoints file."""

    partitions: list[_BotocorePartition]


class _VultrRegion(TypedDict):
    """One region in Vultr's regions API reply."""

    id: str
    city: str
    country: str


class _VultrRegions(TypedDict):
    """Vultr's regions API reply."""

    regions: list[_VultrRegion]


class _AkamaiRegion(TypedDict):
    """One region in Akamai's (Linode's) regions API reply."""

    id: str
    label: str


class _AkamaiRegions(TypedDict):
    """Akamai's (Linode's) regions API reply."""

    data: list[_AkamaiRegion]


class _Tables(HTMLParser):
    """An HTML parser that collects every table as rows of cell text, ignoring scripts and styles."""

    def __init__(self) -> None:
        """Start with no tables and no open row or cell."""
        super().__init__()
        self.tables: list[HtmlTable] = []
        self.row: HtmlRow | None = None
        self.cell: list[str] | None = None
        self.skip: int = 0

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open a table, row or cell, and note when a script or style begins.

        Args:
            tag: Name of the tag.
            attrs: Attributes of the tag.
        """
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.row = []
            self.tables[-1].append(self.row)
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    @override
    def handle_endtag(self, tag: str) -> None:
        """Close a cell into its row with whitespace collapsed, and note when a script or style ends.

        Args:
            tag: Name of the tag.
        """
        if tag in ("script", "style"):
            self.skip -= 1
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None

    @override
    def handle_data(self, data: str) -> None:
        """Add text to the open cell unless it is inside a script or style.

        Args:
            data: Text inside the tag.
        """
        if self.cell is not None and not self.skip:
            self.cell.append(data)


def html_tables(html: str) -> list[HtmlTable]:
    """Every table in an HTML page, as rows of cell text.

    Args:
        html: HTML of the vendor page.

    Returns:
        One list of rows per table.
    """
    parser = _Tables()
    parser.feed(html)
    return parser.tables


def parse_akamai(text: str) -> list[Region]:
    """Parse Akamai's (Linode's) regions API reply.

    Args:
        text: The API reply as JSON text.

    Returns:
        The regions in the reply.
    """
    return [
        Region(AKAMAI, RegionId(r["id"]), r["label"], r["label"])
        for r in cast(_AkamaiRegions, json.loads(text))["data"]
    ]


def parse_alibaba(html: str) -> list[Region]:
    """Parse Alibaba Cloud's region tables.

    Args:
        html: HTML of the vendor page.

    Returns:
        The regions in the tables.
    """
    regions: list[Region] = []
    for table in html_tables(html):
        if not table or "Region ID" not in table[0]:
            continue
        header = table[0]
        for row in table[1:]:
            if len(row) == len(header):
                region_id = row[header.index("Region ID")]
                stated = row[header.index("Region")] if "Region" in header else row[0]
                regions.append(
                    Region(
                        ALIBABA, RegionId(region_id), row[0], stated, partition=_alibaba_partition(region_id)
                    )
                )
    return regions


def parse_aws(html: str) -> list[Region]:
    """Parse AWS's region page: code and name, with the name as the stated location.

    Args:
        html: HTML of the vendor page.

    Returns:
        The regions on the page.
    """
    return [
        Region(AWS, RegionId(row[h.index("Code")]), row[h.index("Name")], row[h.index("Name")])
        for h, row in _rows_with_header(html, "Code")
    ]


def parse_aws_partitions(text: str) -> list[Region]:
    """Return non-commercial AWS partitions from botocore; the commercial one comes from the region page.

    Args:
        text: botocore's partitions file as JSON text.

    Returns:
        The regions of the non-commercial partitions.
    """
    regions: list[Region] = []
    for partition in cast(_BotocoreEndpoints, json.loads(text))["partitions"]:
        label = partition["partition"]
        if label == "aws":
            continue
        for region_id, meta in partition["regions"].items():
            name = meta.get("description", region_id)
            regions.append(Region(AWS, RegionId(region_id), name, name, partition=Partition(label)))
    return regions


def parse_azure(html: str) -> list[Region]:
    """Parse Azure's region list: programmatic name, region and physical location.

    Args:
        html: HTML of the vendor page.

    Returns:
        The regions in the list.
    """
    regions: dict[str, Region] = {}
    for h, row in _rows_with_header(html, "Programmatic name"):
        region_id = row[h.index("Programmatic name")]
        regions[region_id] = Region(
            AZURE, RegionId(region_id), row[h.index("Region")], row[h.index("Physical location")]
        )
    return list(regions.values())


def parse_digitalocean(html: str) -> list[Region]:
    """Parse DigitalOcean's regional availability table.

    Args:
        html: HTML of the vendor page.

    Returns:
        The regions in the table.
    """
    return [
        Region(
            DIGITALOCEAN, RegionId(row[h.index("Slug")]), row[h.index("Datacenter")], row[h.index("Region")]
        )
        for h, row in _rows_with_header(html, "Slug")
    ]


def parse_gcp(html: str) -> list[Region]:
    """Parse Google Cloud's zone table, taking each region from its zones.

    Args:
        html: HTML of the vendor page.

    Returns:
        The regions, one per distinct zone prefix.
    """
    regions: dict[str, Region] = {}
    for table in html_tables(html):
        for row in table:
            match = re.fullmatch(r"([a-z]+-[a-z0-9]+)-[a-z]", row[0]) if len(row) > 1 else None
            if match:
                _ = regions.setdefault(match[1], Region(GCP, RegionId(match[1]), match[1], row[1]))
    return list(regions.values())


def parse_hetzner(html: str) -> list[Region]:
    """Parse Hetzner's location table, where a cell holds country, city and code.

    Args:
        html: HTML of the vendor page.

    Returns:
        The regions in the table.
    """
    regions: list[Region] = []
    for table in html_tables(html):
        for row in table:
            for cell in row:
                match = re.fullmatch(r"[A-Z]{2} (.+) ([a-z]{3}\d?)", cell)
                if match:
                    regions.append(Region(HETZNER, RegionId(match[2]), match[1], match[1]))
    return regions


def parse_huawei(primary: str, secondary: str) -> list[Region]:
    """Parse Huawei Cloud's two region tables, the first taking precedence.

    Args:
        primary: HTML of the main region page.
        secondary: HTML of the second region page.

    Returns:
        The regions in both tables.
    """
    first, second = _huawei_table(primary), _huawei_table(secondary)
    merged: dict[str, tuple[str, str]] = {
        **{k: (v, "huawei-scp") for k, v in second.items()},
        **{k: (v, "") for k, v in first.items()},
    }
    return [
        Region(
            HUAWEI,
            RegionId(region_id),
            name,
            name,
            partition=Partition("huawei-invitation") if "InvitationOnly" in name else PUBLIC,
            source=source,
        )
        for region_id, (name, source) in merged.items()
    ]


def parse_ibm(html: str) -> list[Region]:
    """Parse IBM Cloud's locations table, taking each region from ``Name (id)``.

    Args:
        html: HTML of the vendor page.

    Returns:
        The regions in the table.
    """
    found: dict[str, str] = {}
    for table in html_tables(html):
        if table and table[0][:2] == ["Region", "Zone"]:
            for row in table[1:]:
                match = re.fullmatch(r"(.+) \(([a-z]{2}-[a-z]{2,5})\)", row[0])
                if match:
                    found[match[2]] = match[1]
    return [Region(IBM, RegionId(region_id), name, name) for region_id, name in found.items()]


def parse_oracle(html: str) -> list[Region]:
    """Parse Oracle's region table: identifier, name, location and region key.

    Args:
        html: HTML of the vendor page.

    Returns:
        The regions in the table.
    """
    return [
        Region(
            ORACLE,
            RegionId(row[h.index("Region Identifier")]),
            row[h.index("Region Name")],
            row[h.index("Region Location")],
            row[h.index("Region Key")],
        )
        for h, row in _rows_with_header(html, "Region Identifier")
    ]


def parse_ovhcloud(html: str) -> list[Region]:
    """Parse OVHcloud's regions page, keeping the first name seen for each code.

    Args:
        html: HTML of the vendor page.

    Returns:
        The regions on the page.
    """
    found: dict[str, str] = {}
    for table in html_tables(html):
        for code, name in _ovh_pairs(table):
            _ = found.setdefault(code, name)
    return [Region(OVHCLOUD, RegionId(code), name, name, code) for code, name in found.items()]


def parse_tencent(html: str) -> list[Region]:
    """Parse Tencent Cloud's region table, with the zone as part of the stated location.

    Args:
        html: HTML of the vendor page.

    Returns:
        The regions in the table.
    """
    regions: list[Region] = []
    for h, row in _rows_with_header(html, "Region ID"):
        region_id = row[h.index("Region ID")].strip("\ufeff ")
        if region_id:
            name = re.split(r"Coverage:|Available only", row[0])[0].strip()
            zone = row[h.index("AZ")].strip("\ufeff* ")
            regions.append(
                Region(
                    TENCENT,
                    RegionId(region_id),
                    name,
                    f"{name}; {zone}",
                    partition=_tencent_partition(region_id),
                )
            )
    return regions


def parse_vultr(text: str) -> list[Region]:
    """Parse Vultr's regions API reply.

    Args:
        text: The API reply as JSON text.

    Returns:
        The regions in the reply.
    """
    return [
        Region(VULTR, RegionId(r["id"]), r["city"], f"{r['city']}, {r['country']}", r["id"].upper())
        for r in cast(_VultrRegions, json.loads(text))["regions"]
    ]


def _alibaba_partition(region_id: str) -> Partition:
    """Scope group of an Alibaba region, from marks in its ID.

    Args:
        region_id: Region identifier.

    Returns:
        The partition.
    """
    if "finance" in region_id:
        return Partition("alibaba-finance")
    return Partition("alibaba-gov") if "gov" in region_id else PUBLIC


def _huawei_table(html: str) -> dict[str, str]:
    """Region code to region name, from a Huawei Cloud documentation table.

    Args:
        html: HTML of the vendor page.

    Returns:
        Names keyed by region code.
    """
    pairs: dict[str, str] = {}
    for table in html_tables(html):
        if table and table[0][0] == "Region Name" and table[0][1] in ("Region", "Region Code"):
            pairs.update({row[1]: row[0] for row in table[1:] if len(row) > 1})
    return pairs


def _ovh_pairs(table: HtmlTable) -> Iterator[tuple[str, str]]:
    """Pair each three-letter site code in an OVHcloud table with the name above it.

    Args:
        table: Parsed HTML table.

    Yields:
        A site code and its name.
    """
    for names, codes in pairwise(table):
        labels = [c.replace("*", "").strip() for c in codes]
        filled = [c for c in labels if c]
        if filled and not labels[0] and all(re.fullmatch(r"[A-Z]{3}", c) for c in filled):
            for name, code in zip(names, labels, strict=False):
                if code:
                    yield code, name.replace("3-AZ", "").strip()


def _rows_with_header(html: str, header_name: str) -> Iterator[tuple[HtmlRow, HtmlRow]]:
    """Yield (header, row) from every table whose first row has the named column.

    Args:
        html: HTML of the vendor page.
        header_name: Heading that identifies the table.

    Yields:
        The header row and one data row.
    """
    for table in html_tables(html):
        if table and header_name in table[0]:
            for row in table[1:]:
                if len(row) == len(table[0]):
                    yield table[0], row


def _tencent_partition(region_id: str) -> Partition:
    """Scope group of a Tencent region, from the ending of its ID.

    Args:
        region_id: Region identifier.

    Returns:
        The partition.
    """
    if region_id.endswith("-fsi"):
        return Partition("tencent-finance")
    return Partition("tencent-autonomous") if region_id.endswith("-adc") else PUBLIC
