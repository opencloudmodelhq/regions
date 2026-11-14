# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Find region-to-city statements in saved documents by rule, with no model."""

import html
import re
import unicodedata
from typing import Final, TypedDict

from ocm_regions.common.schema import ProviderId, RegionId
from ocm_regions.place.placer import City, Gazetteer
from ocm_regions.place.placer import norm as _norm
from ocm_regions.regions.parsers import Region

MAX_LINE_CHARS: Final = 500
MAX_WORDS: Final = 4
MIN_NAME_CHARS: Final = 4
MIN_POPULATION: Final = 1000
ROW_END: Final = re.compile(r"</tr\s*>|</li\s*>|</p\s*>|</h\d\s*>|<br\s*/?>", re.I)
TAG: Final = re.compile(r"<[^>]+>")


class FoundRow(TypedDict):
    """A line of a document that names a region and a city."""

    provider: ProviderId
    region_id: RegionId
    city: str
    country: str
    lat: float | str
    lon: float | str
    document: str
    quote: str
    matched: str


def document_lines(text: str) -> list[str]:
    """Split a document into lines at row, list and paragraph ends, with tags removed.

    Args:
        text: HTML or plain text of the document.

    Returns:
        The lines, without empty ones.
    """
    marked = ROW_END.sub("\n", text)
    lines = (" ".join(html.unescape(TAG.sub(" ", line)).split()) for line in marked.splitlines())
    return [line for line in lines if line]


def extract_from_text(region: Region, text: str, gaz: Gazetteer, document: str) -> list[FoundRow]:
    """Every short line of a document that names the region and a city, as rows.

    Args:
        region: Published region.
        text: Text of the document.
        gaz: Gazetteer of cities.
        document: Text of the document.

    Returns:
        One row per line and city.
    """
    rows: list[FoundRow] = []
    for line in document_lines(text):
        if len(line) <= MAX_LINE_CHARS and _names_region(region, line):
            for name, item in _cities_in(line, gaz).items():
                rows.append(
                    {
                        "provider": region.provider,
                        "region_id": region.region_id,
                        "city": item.name,
                        "country": item.country,
                        "lat": item.lat,
                        "lon": item.lon,
                        "document": document,
                        "quote": line[:300],
                        "matched": name,
                    }
                )
    return sorted(rows, key=lambda r: (r["document"], r["city"], r["quote"]))


def _capitalised(name: str, line: str) -> bool:
    """The name must start with a capital somewhere in the line, as a place name would.

    Args:
        name: Place name to look for.
        line: One line of text.

    Returns:
        True when a capitalised form is found.
    """
    return any(
        hit.group(0)[0].isupper()
        for hit in re.finditer(re.escape(name), unicodedata.normalize("NFKD", line), re.I)
    )


def _cities_in(line: str, gaz: Gazetteer) -> dict[str, City]:
    """The cities a line names, by the longest capitalised run of up to four words.

    Args:
        line: One line of text.
        gaz: Gazetteer of cities.

    Returns:
        Cities keyed by the words that matched.
    """
    words = _norm(line).split()
    found: dict[str, City] = {}
    for size in range(MAX_WORDS, 0, -1):
        for start in range(len(words) - size + 1):
            name = " ".join(words[start : start + size])
            items = gaz.index.get(name, []) if len(name) >= MIN_NAME_CHARS else []
            usable = [
                i
                for i in items
                if i.population >= MIN_POPULATION and name not in gaz.areas and name not in gaz.countries
            ]
            if usable and _capitalised(name, line):
                _ = found.setdefault(name, max(usable, key=lambda i: i.population))
    return found


def _names_region(region: Region, line: str) -> bool:
    """Whether a line names the region by its ID or name as whole words.

    Args:
        region: Published region.
        line: One line of text.

    Returns:
        True when the line names the region.
    """
    names = [region.region_id, region.name]
    return any(
        len(n) >= 2 and bool(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", line, re.I)) for n in names if n
    )
