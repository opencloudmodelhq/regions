# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Compare how much of the region list each code scheme can name."""

from collections.abc import Callable, Sequence
from typing import Final, Literal, TypeAlias, TypedDict

from ocm_regions.common.schema import IataCode

SchemeName: TypeAlias = Literal[
    "iata_nearest_airport",
    "iata_major_hub",
    "iata_nearby_hub",
    "iata_metro",
    "metro_or_airport",
    "unlocode",
    "clli",
]


class CodedRow(TypedDict):
    """The code columns of a region row."""

    iata_nearest_airport: IataCode
    iata_major_hub: IataCode
    iata_nearby_hub: IataCode
    iata_metro: str
    unlocode: str
    clli: str


SCHEMES: Final[dict[SchemeName, Callable[[CodedRow], str]]] = {
    "iata_nearest_airport": lambda r: r["iata_nearest_airport"],
    "iata_major_hub": lambda r: r["iata_major_hub"],
    "iata_nearby_hub": lambda r: r["iata_nearby_hub"],
    "iata_metro": lambda r: r["iata_metro"],
    "metro_or_airport": lambda r: r["iata_metro"] or r["iata_nearest_airport"],
    "unlocode": lambda r: r["unlocode"],
    "clli": lambda r: r["clli"],
}


class SchemeCoverage(TypedDict):
    """How many regions a code scheme names, the distinct codes and the share of all regions."""

    regions: int
    distinct: int
    share: float


Coverage: TypeAlias = dict[SchemeName, SchemeCoverage]


def scheme_coverage(rows: Sequence[CodedRow]) -> Coverage:
    """Coverage of each code scheme over the rows.

    Args:
        rows: Region rows with codes.

    Returns:
        Coverage keyed by scheme.
    """
    result: Coverage = {}
    for name, code_of in SCHEMES.items():
        codes = [code_of(r) for r in rows if code_of(r)]
        result[name] = {
            "regions": len(codes),
            "distinct": len(set(codes)),
            "share": round(len(codes) / len(rows), 3) if rows else 0,
        }
    return result
