# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Derive each place's hub by rule: the best-connected airport in the country within the search radius."""

import csv
import io
from collections.abc import Iterable, Mapping
from typing import Final

from ocm_regions.common.geo import SERVICEABLE_TYPES, Airport, haversine_km
from ocm_regions.common.schema import CountryCode, IataCode, Kilometres, Point

RULE_KM: Final = 200


def airport_ranks(text: str) -> dict[IataCode, float]:
    """OpenTravelData's page rank for airports, a rough measure of how connected each one is.

    Args:
        text: OpenTravelData's rank file as text.

    Returns:
        Ranks keyed by IATA code.
    """
    ranks: dict[IataCode, float] = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="^"):
        if row["iata_code"] and "A" in row["location_type"] and row["page_rank"]:
            code = IataCode(row["iata_code"])
            ranks[code] = max(ranks.get(code, 0.0), float(row["page_rank"]))
    return ranks


def derive_hub(
    point: Point,
    country: CountryCode,
    airports: Iterable[Airport],
    ranks: Mapping[IataCode, float],
    km: Kilometres = RULE_KM,
) -> Airport | None:
    """The best-connected scheduled large or medium airport in the country within the search radius, or None.

    Args:
        point: Latitude and longitude.
        country: ISO country code.
        airports: Airports to choose from.
        ranks: Connectivity rank per airport.
        km: Agreement radius in kilometres.

    Returns:
        The hub airport, or None when none qualifies.
    """
    usable = [
        a
        for a in airports
        if a.scheduled
        and a.kind in SERVICEABLE_TYPES
        and a.country == country
        and a.code in ranks
        and haversine_km(point, a.point) <= km
    ]
    return max(usable, key=lambda a: (ranks[a.code], a.code), default=None)
