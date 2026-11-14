# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Place a region whose label names a place the town list lacks, from a vendor sentence that locates that place."""

import re
from collections.abc import Iterable
from typing import Final

from ocm_regions.place.placer import City, Gazetteer
from ocm_regions.place.placer import allowed as _allowed
from ocm_regions.place.placer import norm as _norm
from ocm_regions.regions.parsers import Region

LOCATING: Final = re.compile(r"\b(?:located|situated|based)\s+(?:in|at|near)\s+((?:[A-Z][\w'’-]*\s?){1,3})")
MAX_CITY_WORDS: Final = 3
MAX_GAP_CHARS: Final = 80
MIN_NAME_CHARS: Final = 4
SENTENCE_END: Final = re.compile(r"(?<=[.!?])\s+")


def named_place(region: Region, texts: Iterable[str], gaz: Gazetteer) -> City | None:
    """The one town vendor sentences locate the label place in; none when the label resolves, or sentences disagree.

    Args:
        region: Published region.
        texts: Texts to search or embed.
        gaz: Gazetteer of cities.

    Returns:
        The town, or None.
    """
    country, names = _country(region, gaz), unresolved_names(region, gaz)
    if not country or not names:
        return None
    documents = list(texts)
    cities = {
        city.name: city
        for name in names
        for text in documents
        for city in _located_cities(name, text, gaz, country)
    }
    return next(iter(cities.values())) if len(cities) == 1 else None


def unresolved_names(region: Region, gaz: Gazetteer) -> list[str]:
    """Place names in the vendor's label that are not a town, an area or a country the lists hold.

    Args:
        region: Published region.
        gaz: Gazetteer of cities.

    Returns:
        The names that resolve to nothing.
    """
    known = (gaz.index, gaz.areas, gaz.countries)
    names = [
        part
        for part in _label_parts(region)
        if len(part) >= MIN_NAME_CHARS and not any(_norm(part) in table for table in known)
    ]
    return list(dict.fromkeys(names))


def _city_after(words: str, name: str, gaz: Gazetteer, country: str) -> City | None:
    """The longest run of leading words that is a town in the country and is not part of the label place's own name.

    Args:
        words: Words after the locating term.
        name: The label's own place name.
        gaz: Gazetteer of cities.
        country: ISO country code of the label.

    Returns:
        The town, or None.
    """
    parts = words.split()
    for size in range(min(MAX_CITY_WORDS, len(parts)), 0, -1):
        candidate = _norm(" ".join(parts[:size]))
        city = (
            _allowed(gaz.index.get(candidate, []), None, country)
            if candidate and candidate not in _norm(name)
            else None
        )
        if city:
            return city
    return None


def _country(region: Region, gaz: Gazetteer) -> str | None:
    """The country a label names, from the text after its last comma or a word in the region name.

    Args:
        region: Published region.
        gaz: Gazetteer of cities.

    Returns:
        The ISO country code, or None.
    """
    tail = region.stated_location.split(",")[-1] if "," in region.stated_location else ""
    return gaz.countries.get(_norm(tail)) or next(
        (gaz.countries[word] for word in _norm(region.name).split() if word in gaz.countries), None
    )


def _label_parts(region: Region) -> list[str]:
    """Candidate place names in a label: text in brackets, and the part before the first comma.

    Args:
        region: Published region.

    Returns:
        The candidate names.
    """
    brackets = [
        found for text in (region.name, region.stated_location) for found in re.findall(r"\(([^)]*)\)", text)
    ]
    lead = [region.stated_location.split(",")[0]] if "," in region.stated_location else []
    return [part.strip() for part in [*brackets, *lead] if part.strip()]


def _located_cities(name: str, text: str, gaz: Gazetteer, country: str) -> Iterable[City]:
    """Yield each town a sentence locates the named place in, using a locating word shortly after the name.

    Args:
        name: The label's own place name.
        text: Text of a vendor document.
        gaz: Gazetteer of cities.
        country: ISO country code of the label.

    Yields:
        A town the text locates the place in.
    """
    pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.I)
    for sentence in SENTENCE_END.split(text):
        for found in pattern.finditer(sentence):
            located = LOCATING.search(sentence, found.end(), found.end() + MAX_GAP_CHARS)
            city = _city_after(located[1], name, gaz, country) if located else None
            if city:
                yield city
