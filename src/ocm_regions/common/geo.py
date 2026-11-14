# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Distance, coordinate parsing, airport selection and place identifiers."""

import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from ocm_regions.common.schema import CountryCode, IataCode, Kilometres, Point

MATCH_KM: Final = 100
SERVICEABLE_TYPES: Final[frozenset[str]] = frozenset({"large_airport", "medium_airport"})


@dataclass(frozen=True)
class Airport:
    """An airport from OurAirports, with its code, country, point, class and whether it has scheduled service."""

    code: IataCode
    name: str
    municipality: str
    country: CountryCode
    lat: float
    lon: float
    kind: str
    scheduled: bool

    @property
    def point(self) -> Point:
        """The airport's latitude and longitude as one point.

        Returns:
            Latitude and longitude.
        """
        return (self.lat, self.lon)


def capped_airport(hub: IataCode, hub_km: Kilometres, nearest: IataCode, cap: Kilometres) -> IataCode:
    """Keep the hub within the cap, otherwise use the nearest airport.

    Args:
        hub: IATA code of the major hub.
        hub_km: Distance to the hub in kilometres.
        nearest: IATA code of the nearest airport.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The hub's code when it is within the cap, else the nearest airport's.
    """
    return hub if hub_km <= cap else nearest


def haversine_km(a: Point, b: Point) -> Kilometres:
    """Great-circle distance in kilometres between two points, on a sphere of radius 6371 km.

    Args:
        a: First point, as latitude and longitude.
        b: Second point, as latitude and longitude.

    Returns:
        The distance in kilometres.
    """
    rad = math.pi / 180
    h = (
        math.sin((b[0] - a[0]) * rad / 2) ** 2
        + math.cos(a[0] * rad) * math.cos(b[0] * rad) * math.sin((b[1] - a[1]) * rad / 2) ** 2
    )
    return 2 * 6371 * math.asin(min(1, math.sqrt(h)))


def nearest_airport(point: Point, country: CountryCode, airports: Iterable[Airport]) -> Airport:
    """Return the closest scheduled large or medium airport, preferring the place's own country.

    Args:
        point: Latitude and longitude.
        country: ISO country code.
        airports: Airports to choose from.

    Returns:
        The closest airport.
    """
    usable = [a for a in airports if a.scheduled and a.kind in SERVICEABLE_TYPES]
    domestic = [a for a in usable if a.country == country]
    return min(domestic or usable, key=lambda a: haversine_km(point, a.point))


def parse_locode_coordinates(text: str | None) -> Point | None:
    """Turn UN/LOCODE's ``DDMMN DDDMME`` text into a point, or None when it is absent or malformed.

    Args:
        text: Coordinates as UN/LOCODE prints them, if any.

    Returns:
        Latitude and longitude in decimal degrees, or None.
    """
    match = re.fullmatch(r"(\d{2})(\d{2})([NS]) (\d{3})(\d{2})([EW])", text or "")
    if not match:
        return None
    lat = (int(match[1]) + int(match[2]) / 60) * (1 if match[3] == "N" else -1)
    lon = (int(match[4]) + int(match[5]) / 60) * (1 if match[6] == "E" else -1)
    return lat, lon


def place_id(country: str, subdivision: str, city: str) -> str:
    """Stable place identifier built from the country, subdivision and city that are given.

    Args:
        country: ISO country code.
        subdivision: First-level area code.
        city: City name.

    Returns:
        The identifier, as hyphen-joined slugs.
    """
    return "-".join(slug(p) for p in (country, subdivision, city) if p)


def require_domestic_hub(place_id: str, country: CountryCode, hub: Airport) -> Airport:
    """Return the hub, refusing one outside the place's country.

    Args:
        place_id: Identifier of the place.
        country: ISO country code.
        hub: Major hub airport.

    Returns:
        The hub, unchanged.

    Raises:
        ValueError: If the hub is in another country.
    """
    if hub.country != country:
        raise ValueError(f"{place_id}: hub {hub.code} is in {hub.country}, not {country}")
    return hub


def slug(text: str) -> str:
    """Lower-case ASCII form of a name, with every run of other characters as one hyphen.

    Args:
        text: Name to convert.

    Returns:
        The slug.
    """
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
