# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Derive IATA metropolitan codes from OpenTravelData, an open but unofficial source."""

import csv
import io
from collections.abc import Mapping
from typing import TypeAlias

from ocm_regions.common.schema import IataCode

MetroTable: TypeAlias = dict[IataCode, list[str]]


def metro_for_airport(code: IataCode, table: Mapping[IataCode, list[str]]) -> str:
    """The metropolitan code an airport belongs to, or blank when it has none of its own.

    Args:
        code: IATA code of the airport.
        table: Metropolitan codes per airport.

    Returns:
        The metropolitan code, or blank.
    """
    cities = table.get(code, [])
    return cities[0] if cities and cities[0] != code else ""


def parse_optd(text: str) -> MetroTable:
    """Read each airport's metropolitan city codes from OpenTravelData.

    Args:
        text: OpenTravelData airport file as text.

    Returns:
        Metropolitan codes per airport.
    """
    table: MetroTable = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="^"):
        kinds: str = row["location_type"]
        if "A" in kinds and row["iata_code"] and row["city_code_list"]:
            _ = table.setdefault(IataCode(row["iata_code"]), str(row["city_code_list"]).split(","))
    return table
