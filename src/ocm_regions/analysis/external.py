# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Compare places with an outside dataset, and codes with the codes an operator chose for its own sites."""

import csv
import io
import json
import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final, Literal, TypeAlias, TypedDict, cast

from ocm_regions.analysis.arearisk import rule_codes
from ocm_regions.common.geo import Airport, haversine_km
from ocm_regions.common.schema import CountryCode, IataCode, Kilometres, Point
from ocm_regions.common.stats import wilson
from ocm_regions.fetch.sources import StrPath
from ocm_regions.place.placer import Gazetteer
from ocm_regions.place.placer import allowed as _allowed
from ocm_regions.place.placer import norm as _norm
from ocm_regions.publish.paper import macros_tex

Key: TypeAlias = tuple[str, str]
Site: TypeAlias = tuple[str, str, str]
YesNo: TypeAlias = Literal["yes", "no"]

NEAR_KM: Final = 50
OPERATOR_SITES: Final = "external/cloudflare/components.json"
OUTSIDE_PLACES: Final = "external/gsf/Cloud_Region_Metadata.csv"

OUTSIDE_PROVIDERS: Final[dict[str, str]] = {
    "Amazon Web Services": "aws",
    "Microsoft Azure": "azure",
    "Google Cloud": "gcp",
}

SITE_NAME: Final = re.compile(r"(.+?)(?:, (.+))? - \(([A-Z]{3})\)")
WIDE_KM: Final = 100


class PlaceRow(TypedDict):
    """One region compared with an outside list's coordinates."""

    provider: str
    region_id: str
    city: str
    km: float
    within_50_km: YesNo


class PlaceSummary(TypedDict):
    """How many regions fall within 50 and 100 km of an outside list's coordinates."""

    compared: int
    within_50_km: int
    within_100_km: int
    median_km: float
    only_outside: list[str]
    only_ours: int
    far_where_the_vendor_names_the_city: int


class CodeRow(TypedDict):
    """One operator site and the code each table would give its city."""

    city: str
    country: str
    operator_code: str
    nearest_airport: str
    major_hub: str
    nearby_hub: str


class CodeSummary(TypedDict):
    """How often each table's code matches the operator's own site code."""

    sites: int
    resolved: int
    nearest_airport: int
    major_hub: int
    nearby_hub: int
    operator_code_is_airport: int


class _Component(TypedDict):
    """One component on a status page."""

    name: str


class _Components(TypedDict):
    """The components reply of a status page."""

    components: list[_Component]


def compare_codes(
    sites: Sequence[Site],
    gaz: Gazetteer,
    airports: Sequence[Airport],
    ranks: Mapping[IataCode, float],
    cap: Kilometres,
) -> tuple[list[CodeRow], CodeSummary]:
    """For each site whose city the town list holds, the code each naming rule gives against the code the operator chose.

    Args:
        sites: Placed sites.
        gaz: Gazetteer of cities.
        airports: Airports to choose from.
        ranks: Connectivity rank per airport.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The compared rows and their summary.
    """
    known = {airport.code for airport in airports}
    rows: list[CodeRow] = []
    for city, country, code in sites:
        located = _site_point(city, country, gaz)
        if located:
            nearest, hub, nearby = rule_codes(located[0], located[1], airports, ranks, cap)
            rows.append(
                {
                    "city": city,
                    "country": located[1],
                    "operator_code": code,
                    "nearest_airport": nearest,
                    "major_hub": hub,
                    "nearby_hub": nearby,
                }
            )
    return rows, {
        "sites": len(sites),
        "resolved": len(rows),
        "nearest_airport": sum(r["nearest_airport"] == r["operator_code"] for r in rows),
        "major_hub": sum(r["major_hub"] == r["operator_code"] for r in rows),
        "nearby_hub": sum(r["nearby_hub"] == r["operator_code"] for r in rows),
        "operator_code_is_airport": sum(r["operator_code"] in known for r in rows),
    }


def compare_places(
    ours: Iterable[Mapping[str, str]], outside: Mapping[Key, Point]
) -> tuple[list[PlaceRow], PlaceSummary]:
    """Distance from each of our places to the outside dataset's point for the same region.

    Args:
        ours: Our region rows.
        outside: Outside coordinates keyed by region.

    Returns:
        The compared rows and their summary.
    """
    placed = {(r["provider"], r["region_id"]): r for r in ours if r["lat"]}
    rows: list[PlaceRow] = []
    far_stated = 0
    for key, region in placed.items():
        if key in outside:
            km = haversine_km((float(region["lat"]), float(region["lon"])), outside[key])
            far_stated += km > NEAR_KM and region.get("grade") == "vendor-stated"
            rows.append(
                {
                    "provider": key[0],
                    "region_id": key[1],
                    "city": region["city"],
                    "km": round(km, 1),
                    "within_50_km": "yes" if km <= NEAR_KM else "no",
                }
            )
    distances = [row["km"] for row in rows]
    providers = {key[0] for key in outside}
    return rows, {
        "compared": len(rows),
        "within_50_km": sum(km <= NEAR_KM for km in distances),
        "within_100_km": sum(km <= WIDE_KM for km in distances),
        "median_km": statistics.median(distances) if distances else 0.0,
        "only_outside": sorted(f"{p} {r}" for p, r in outside if (p, r) not in placed),
        "only_ours": sum(key[0] in providers and key not in outside for key in placed),
        "far_where_the_vendor_names_the_city": far_stated,
    }


def external_macros(places: PlaceSummary, codes: CodeSummary) -> dict[str, object]:
    """Paper macros for both outside comparisons.

    Args:
        places: Summary of the place comparison.
        codes: Summary of the code comparison.

    Returns:
        Macro values keyed by name.
    """
    low, high = wilson(places["within_50_km"], places["compared"])
    resolved = codes["resolved"]
    return {
        "outsidePlacesCompared": places["compared"],
        "outsidePlacesNear": places["within_50_km"],
        "outsidePlacesWide": places["within_100_km"],
        "outsidePlacesShare": _share(places["within_50_km"], places["compared"]),
        "outsidePlacesLow": f"{low * 100:.0f}",
        "outsidePlacesHigh": f"{high * 100:.0f}",
        "outsidePlacesMedianKm": f"{places['median_km']:.0f}",
        "outsidePlacesFar": places["compared"] - places["within_50_km"],
        "outsidePlacesFarStated": places["far_where_the_vendor_names_the_city"],
        "outsidePlacesMissing": len(places["only_outside"]),
        "outsidePlacesExtra": places["only_ours"],
        "operatorSites": codes["sites"],
        "operatorSitesResolved": resolved,
        "operatorCodeIsAirport": codes["operator_code_is_airport"],
        "operatorNearest": codes["nearest_airport"],
        "operatorNearestShare": _share(codes["nearest_airport"], resolved),
        "operatorMajorHub": codes["major_hub"],
        "operatorMajorHubShare": _share(codes["major_hub"], resolved),
        "operatorNearbyHub": codes["nearby_hub"],
        "operatorNearbyHubShare": _share(codes["nearby_hub"], resolved),
    }


def parse_operator_sites(text: str) -> list[Site]:
    """Status page components named "City, Country - (CODE)"; group and service names are left out.

    Args:
        text: The status page's components reply as JSON text.

    Returns:
        The sites.
    """
    names = [component["name"] for component in cast(_Components, json.loads(text))["components"]]
    found = [SITE_NAME.fullmatch(name) for name in names]
    return [(match[1], match[2] or "", match[3]) for match in found if match]


def parse_outside_places(text: str) -> dict[Key, Point]:
    """One point per region, from the latest year that gives one.

    Args:
        text: The outside list as CSV text.

    Returns:
        Points keyed by vendor and region.
    """
    latest: dict[Key, tuple[int, Point]] = {}
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        provider, point = OUTSIDE_PROVIDERS.get(row["cloud-provider"]), _point(row.get("geolocation") or "")
        if provider and point:
            key, year = (provider, str(row["cloud-region"])), int(row["year"])
            if year >= latest.get(key, (0, point))[0]:
                latest[key] = (year, point)
    return {key: point for key, (_year, point) in latest.items()}


def run_external(
    regions: Sequence[Mapping[str, str]],
    raw: StrPath,
    output: StrPath,
    paper: StrPath,
    gaz: Gazetteer,
    airports: Sequence[Airport],
    ranks: Mapping[IataCode, float],
    cap: Kilometres,
) -> tuple[PlaceSummary, CodeSummary]:
    """Compare placements and codes with the two outside sources and write the rows, summary and macros.

    Args:
        regions: Region rows.
        raw: The data/raw folder.
        output: Output folder.
        paper: The paper folder.
        gaz: Gazetteer of cities.
        airports: Airports to choose from.
        ranks: Connectivity rank per airport.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The place summary and the code summary.
    """
    outside = parse_outside_places((Path(raw) / OUTSIDE_PLACES).read_text(encoding="utf-8"))
    sites = parse_operator_sites((Path(raw) / OPERATOR_SITES).read_text(encoding="utf-8"))
    place_rows, places = compare_places(regions, outside)
    code_rows, codes = compare_codes(sites, gaz, airports, ranks, cap)
    _write(Path(output) / "external_places.csv", place_rows)
    _write(Path(output) / "external_codes.csv", code_rows)
    _ = (Path(output) / "external_summary.json").write_text(
        json.dumps({"places": places, "codes": codes}, indent=1) + "\n", encoding="utf-8"
    )
    _ = (Path(paper) / "generated" / "external_macros.tex").write_text(
        macros_tex(external_macros(places, codes)), encoding="utf-8"
    )
    return places, codes


def _point(text: str) -> Point | None:
    """Coordinates from ``lat, lon`` text, or None.

    Args:
        text: Text of the form ``lat, lon``.

    Returns:
        Latitude and longitude, or None.
    """
    parts = [part.strip() for part in text.split(",")]
    return (float(parts[0]), float(parts[1])) if len(parts) == 2 and all(parts) else None


def _share(part: int, whole: int) -> str:
    """A share as a whole percentage; zero when the whole is zero.

    Args:
        part: Count in the share.
        whole: Count the share is taken of.

    Returns:
        The percentage as text.
    """
    return f"{part / whole * 100:.0f}" if whole else "0"


def _site_point(city: str, country: str, gaz: Gazetteer) -> tuple[Point, CountryCode] | None:
    """The coordinates and country of an operator site's city, or None when the gazetteer lacks it.

    Args:
        city: City name.
        country: Country name as the operator prints it.
        gaz: Gazetteer of cities.

    Returns:
        The point and country code, or None.
    """
    code = gaz.countries.get(_norm(country or city))
    town = _allowed(gaz.index.get(_norm(city), []), None, code) if code else None
    return ((town.lat, town.lon), CountryCode(code)) if town and code else None


def _write(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    """Write rows to CSV, with columns taken from the first row.

    Args:
        path: CSV file to write.
        rows: Rows to write.
    """
    with path.open("w", newline="", encoding="utf-8") as handle:
        if rows:
            writer = csv.DictWriter[str](handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
