# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Score the frozen placement and code rules on vendors that had no part in building them."""

import csv
import html
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, TypeAlias, TypedDict

from ocm_regions.analysis.arearisk import rule_codes
from ocm_regions.common.geo import Airport, haversine_km
from ocm_regions.common.schema import CountryCode, IataCode, Kilometres, Point, ProviderId, RegionId
from ocm_regions.fetch.sources import StrPath
from ocm_regions.model.llm import Chat, place_with_memory
from ocm_regions.place.placer import Gazetteer, label_city
from ocm_regions.place.placer import norm as _norm
from ocm_regions.publish.paper import macros_tex
from ocm_regions.regions.parsers import Region

RegionLabel: TypeAlias = tuple[str, str]
Site: TypeAlias = tuple[str, str, Point | None]

CELL: Final = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
MEMORY: Final = "heldout_memory.json"
NEAR_KM: Final = 50
POPS: Final = "external/fastly/pop.html"
REGION_CELL: Final = re.compile(r"([a-z]+(?:-[a-z0-9]+)+)\((.+)\)")
REGION_VENDOR: Final = ProviderId("zenlayer")
REGIONS: Final = "external/zenlayer/compute-location.md"
ROW: Final = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
TAG: Final = re.compile(r"<[^>]+>")
ZONE_CODE: Final = re.compile(r"\b([A-Z]{3}) [A-Z]\b")
ZONES: Final = "external/zenlayer/bare-metal-zones.md"


class LabelRow(TypedDict):
    """One region placed from the label its vendor prints."""

    region_id: str
    stated_city: str
    placed_city: str
    country: str
    lat: float | str
    lon: float | str


class LabelSummary(TypedDict):
    """How many regions the label rule placed, and how many under the name the vendor states."""

    regions: int
    placed: int
    same_name: int


class MemoryRow(TypedDict):
    """One region the model was asked about by identifier alone."""

    region_id: str
    stated_city: str
    status: str
    answer_city: str
    country: str
    km: float | str
    within_50_km: str


class MemorySummary(TypedDict):
    """How many regions the model answered from memory, and how many near the stated city."""

    regions: int
    answered: int
    within_50_km: int


class SiteRow(TypedDict):
    """One operator site: where the label rule puts it and the code each table gives."""

    label: str
    operator_code: str
    placed_city: str
    country: str
    km: float | str
    within_50_km: str
    nearest_airport: str
    major_hub: str
    nearby_hub: str


class SiteSummary(TypedDict):
    """How often the label rule lands near the operator's point and each table picks its code."""

    sites: int
    placed: int
    with_point: int
    within_50_km: int
    nearest_airport: int
    major_hub: int
    nearby_hub: int
    operator_code_is_airport: int


def ask_memory(
    regions: Sequence[RegionLabel], client: Chat, gaz: Gazetteer
) -> tuple[list[MemoryRow], MemorySummary]:
    """Ask the model where each region is from its identifier alone, and score it against the stated city.

    Args:
        regions: Identifier and stated city of each region.
        client: Chat client.
        gaz: Gazetteer of cities.

    Returns:
        One row per region and the summary.
    """
    rows = [_memory_row(region_id, stated, client, gaz) for region_id, stated in regions]
    return rows, {
        "regions": len(rows),
        "answered": sum(row["status"] == "answered" for row in rows),
        "within_50_km": sum(row["within_50_km"] == "yes" for row in rows),
    }


def compare_sites(
    sites: Sequence[Site],
    gaz: Gazetteer,
    airports: Sequence[Airport],
    ranks: Mapping[IataCode, float],
    cap: Kilometres,
) -> tuple[list[SiteRow], SiteSummary]:
    """Place each site from its name alone, then compare the place and the three codes with the operator's own.

    Args:
        sites: Site name, operator code and the operator's point where it gives one.
        gaz: Gazetteer of cities.
        airports: Airports to choose from.
        ranks: Connectivity rank per airport.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        One row per site and the summary.
    """
    known = {airport.code for airport in airports}
    rows = [_site_row(site, gaz, airports, ranks, cap) for site in sites]
    placed = [row for row in rows if row["placed_city"]]
    return rows, {
        "sites": len(rows),
        "placed": len(placed),
        "with_point": sum(row["within_50_km"] != "" for row in placed),
        "within_50_km": sum(row["within_50_km"] == "yes" for row in placed),
        "nearest_airport": sum(row["nearest_airport"] == row["operator_code"] for row in placed),
        "major_hub": sum(row["major_hub"] == row["operator_code"] for row in placed),
        "nearby_hub": sum(row["nearby_hub"] == row["operator_code"] for row in placed),
        "operator_code_is_airport": sum(row["operator_code"] in known for row in placed),
    }


def heldout_macros(
    sites: Mapping[str, SiteSummary], labels: LabelSummary, memory: MemorySummary | None = None
) -> dict[str, object]:
    """Macro values the paper reads for the held-out vendors.

    Args:
        sites: Site summaries keyed by a letters-only macro prefix.
        labels: Summary of the regions placed from their labels.
        memory: Summary of the model's answers from memory; left out when the model was not asked.

    Returns:
        Macro values keyed by name.
    """
    macros: dict[str, object] = {
        "zenlayerRegions": labels["regions"],
        "zenlayerRegionsPlaced": labels["placed"],
        "zenlayerRegionsSameName": labels["same_name"],
    }
    if memory:
        macros |= {
            "zenlayerMemoryAnswered": memory["answered"],
            "zenlayerMemoryNear": memory["within_50_km"],
        }
    for prefix, summary in sites.items():
        placed = summary["placed"]
        macros |= {
            f"{prefix}Sites": summary["sites"],
            f"{prefix}Placed": placed,
            f"{prefix}WithPoint": summary["with_point"],
            f"{prefix}Near": summary["within_50_km"],
            f"{prefix}NearShare": _share(summary["within_50_km"], summary["with_point"]),
            f"{prefix}CodeIsAirport": summary["operator_code_is_airport"],
            f"{prefix}Nearest": summary["nearest_airport"],
            f"{prefix}NearestShare": _share(summary["nearest_airport"], placed),
            f"{prefix}MajorHub": summary["major_hub"],
            f"{prefix}MajorHubShare": _share(summary["major_hub"], placed),
            f"{prefix}NearbyHub": summary["nearby_hub"],
            f"{prefix}NearbyHubShare": _share(summary["nearby_hub"], placed),
        }
    return macros


def parse_pops(text: str) -> list[Site]:
    """Sites from a page whose table gives a name, a code and an approximate point.

    Args:
        text: The page as HTML.

    Returns:
        Name, code and point of each site; rows of other tables are left out.
    """
    sites: list[Site] = []
    for row in ROW.findall(text):
        cells = [_text(cell) for cell in CELL.findall(row)]
        point = _point(cells[2]) if len(cells) == 3 else None
        if point:
            sites.append((cells[0], cells[1], point))
    return sites


def parse_regions(text: str) -> list[RegionLabel]:
    """Regions from a page whose cells read ``identifier(City)``.

    Args:
        text: The page as Markdown with an HTML table.

    Returns:
        Identifier and stated city of each region, in page order.
    """
    found = (REGION_CELL.fullmatch(_text(cell)) for cell in CELL.findall(text))
    return list(dict.fromkeys((match[1], match[2]) for match in found if match))


def parse_zones(text: str) -> list[Site]:
    """Cities and zone codes from a Markdown table with a City and a Zone column.

    Args:
        text: The page as Markdown.

    Returns:
        Each city once for each three-letter code its zones carry; no point is given.
    """
    sites: dict[tuple[str, str], Site] = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 3:
            for code in ZONE_CODE.findall(cells[2]):
                _ = sites.setdefault((cells[1], code), (cells[1], code, None))
    return list(sites.values())


def place_labels(regions: Sequence[RegionLabel], gaz: Gazetteer) -> tuple[list[LabelRow], LabelSummary]:
    """Place each region from the label its vendor prints, with the rule the dataset uses.

    Args:
        regions: Identifier and stated city of each region.
        gaz: Gazetteer of cities.

    Returns:
        One row per region and the summary.
    """
    rows: list[LabelRow] = []
    for region_id, stated in regions:
        city = label_city(f"{region_id}({stated})", gaz)
        rows.append(
            {
                "region_id": region_id,
                "stated_city": stated,
                "placed_city": city.name if city else "",
                "country": city.country if city else "",
                "lat": city.lat if city else "",
                "lon": city.lon if city else "",
            }
        )
    placed = [row for row in rows if row["placed_city"]]
    return rows, {
        "regions": len(rows),
        "placed": len(placed),
        "same_name": sum(_norm(row["placed_city"]) in _norm(row["stated_city"]) for row in placed),
    }


def run_heldout(
    raw: StrPath,
    output: StrPath,
    paper: StrPath,
    gaz: Gazetteer,
    airports: Sequence[Airport],
    ranks: Mapping[IataCode, float],
    cap: Kilometres,
) -> tuple[dict[str, SiteSummary], LabelSummary]:
    """Score the rules on the held-out vendors and write the rows, the summary and the paper's macros.

    Args:
        raw: The data/raw folder.
        output: Output folder.
        paper: The paper folder.
        gaz: Gazetteer of cities.
        airports: Airports to choose from.
        ranks: Connectivity rank per airport.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The site summaries by macro prefix and the label summary.
    """
    read = {name: (Path(raw) / name).read_text(encoding="utf-8") for name in (POPS, ZONES, REGIONS)}
    pop_rows, pops = compare_sites(parse_pops(read[POPS]), gaz, airports, ranks, cap)
    zone_rows, zones = compare_sites(parse_zones(read[ZONES]), gaz, airports, ranks, cap)
    label_rows, labels = place_labels(parse_regions(read[REGIONS]), gaz)
    sites = {"fastly": pops, "zenlayerZones": zones}
    _write(Path(output) / "heldout_fastly.csv", pop_rows)
    _write(Path(output) / "heldout_zenlayer_zones.csv", zone_rows)
    _write(Path(output) / "heldout_zenlayer_regions.csv", label_rows)
    _ = (Path(output) / "heldout_summary.json").write_text(
        json.dumps({"sites": sites, "regions": labels}, indent=1) + "\n", encoding="utf-8"
    )
    asked = Path(output) / MEMORY
    memory: MemorySummary | None = json.loads(asked.read_text(encoding="utf-8")) if asked.exists() else None
    _ = (Path(paper) / "generated" / "heldout_macros.tex").write_text(
        macros_tex(heldout_macros(sites, labels, memory)), encoding="utf-8"
    )
    return sites, labels


def run_memory(raw: StrPath, output: StrPath, client: Chat, gaz: Gazetteer) -> MemorySummary:
    """Ask the model about the held-out regions and write its answers and their summary.

    Args:
        raw: The data/raw folder.
        output: Output folder.
        client: Chat client.
        gaz: Gazetteer of cities.

    Returns:
        The summary.
    """
    regions = parse_regions((Path(raw) / REGIONS).read_text(encoding="utf-8"))
    rows, summary = ask_memory(regions, client, gaz)
    _write(Path(output) / "heldout_zenlayer_memory.csv", rows)
    _ = (Path(output) / MEMORY).write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    return summary


def _memory_row(region_id: str, stated: str, client: Chat, gaz: Gazetteer) -> MemoryRow:
    """One region's answer from memory, with its distance from the city the vendor states.

    Args:
        region_id: The region's identifier, the only thing the model is shown.
        stated: The city the vendor states.
        client: Chat client.
        gaz: Gazetteer of cities.

    Returns:
        The row.
    """
    truth = label_city(f"{region_id}({stated})", gaz)
    answer = place_with_memory(Region(REGION_VENDOR, RegionId(region_id), region_id, region_id), client, gaz)
    row: MemoryRow = {
        "region_id": region_id,
        "stated_city": stated,
        "status": answer["status"],
        "answer_city": answer["city"],
        "country": answer["country"],
        "km": "",
        "within_50_km": "",
    }
    if truth and answer["status"] == "answered":
        km = haversine_km((float(answer["lat"]), float(answer["lon"])), (truth.lat, truth.lon))
        row["km"], row["within_50_km"] = round(km, 1), "yes" if km <= NEAR_KM else "no"
    return row


def _point(text: str) -> Point | None:
    """A latitude and longitude from text such as ``32.78, -96.80``.

    Args:
        text: Cell text.

    Returns:
        The point, or None when the text is not two numbers.
    """
    parts = [part.strip() for part in text.split(",")]
    try:
        return (float(parts[0]), float(parts[1])) if len(parts) == 2 else None
    except ValueError:
        return None


def _share(part: int, whole: int) -> str:
    """A share as a whole percentage.

    Args:
        part: Count in the share.
        whole: Count it is a share of.

    Returns:
        The percentage as text; ``0`` when the whole is empty.
    """
    return f"{100 * part / whole:.0f}" if whole else "0"


def _site_row(
    site: Site, gaz: Gazetteer, airports: Sequence[Airport], ranks: Mapping[IataCode, float], cap: Kilometres
) -> SiteRow:
    """One site's row: blank where the label rule finds no city.

    Args:
        site: Site name, operator code and optional point.
        gaz: Gazetteer of cities.
        airports: Airports to choose from.
        ranks: Connectivity rank per airport.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The row.
    """
    label, code, point = site
    city = label_city(label, gaz)
    row: SiteRow = {
        "label": label,
        "operator_code": code,
        "placed_city": "",
        "country": "",
        "km": "",
        "within_50_km": "",
        "nearest_airport": "",
        "major_hub": "",
        "nearby_hub": "",
    }
    if not city:
        return row
    here = (city.lat, city.lon)
    nearest, hub, nearby = rule_codes(here, CountryCode(city.country), airports, ranks, cap)
    row["placed_city"], row["country"] = city.name, city.country
    row["nearest_airport"], row["major_hub"], row["nearby_hub"] = nearest, hub, nearby
    if point:
        km = haversine_km(here, point)
        row["km"], row["within_50_km"] = round(km, 1), "yes" if km <= NEAR_KM else "no"
    return row


def _text(cell: str) -> str:
    """The text of a table cell without tags, comments or entities.

    Args:
        cell: Inner HTML of the cell.

    Returns:
        The plain text.
    """
    return " ".join(html.unescape(TAG.sub("", cell)).split())


def _write(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    """Write rows as CSV.

    Args:
        path: File to write.
        rows: Rows to write; an empty list writes nothing.
    """
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
