# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Evidence read from the corpus by code. A quote must tie the region to a place; only a place in the area supports it."""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Final, Literal, TypeAlias, TypedDict

from ocm_regions.common.geo import MATCH_KM, haversine_km
from ocm_regions.common.schema import EvidenceRecord, Grade, Located, QuotedCity, RegionKey, SourceType
from ocm_regions.model.document_types import TypedDocument
from ocm_regions.model.llm import city_level_quote
from ocm_regions.place.placer import norm
from ocm_regions.regions.parsers import Region
from ocm_regions.tables.grades import best_grade

ReadMethod: TypeAlias = Literal["rag", "quote-search"]
Found: TypeAlias = tuple[QuotedCity, TypedDocument]

MIN_NAME_CHARS: Final = 4
MIN_QUOTE_CHARS: Final = 20

NETWORK_PAGES: Final = (
    "directconnect",
    "localzone",
    "edgelocation",
    "pointofpresence",
    "wavelength",
    "onramp",
)

NETWORK_WORDS: Final = (
    "direct connect",
    "local zone",
    "edge location",
    "point of presence",
    "wavelength",
    "on-ramp",
    "office",
)

SOURCE_TYPES: Final[dict[str, SourceType]] = {
    "vendor": "vendor",
    "certification": "certification",
    "government": "government",
    "news": "news",
    "directory": "directory",
}
STATE_COUNTRY: Final = "US"


class AnswerWithDocument(QuotedCity):
    """A model answer with the document its quote came from."""

    document: str


class MethodGrades(TypedDict):
    """Grades with and without the model's answers, and the regions that differ."""

    with_model: Counter[Grade]
    without_model: Counter[Grade]
    regions_gaining: list[list[str]]


def means_region(quote: str, region: Region, city: str) -> bool:
    """The quote names the region, names the city outside the region's own names, and is not about a network site.

    Args:
        quote: Quoted text.
        region: Published region.
        city: City name.

    Returns:
        True when the quote ties the region to the city.
    """
    if any(word in quote.lower() for word in NETWORK_WORDS):
        return False
    named = [n for n in _independent_names(region, city) if _whole(n, quote)]
    if not named and not _short_code_named(region, city, quote):
        return False
    rest = re.sub(rf"(?<!\w){re.escape(region.region_id)}(?!\w)", " ", quote, flags=re.I)
    for name in sorted(named, key=len, reverse=True):
        rest = re.sub(re.escape(name), " ", rest, flags=re.I)
    return bool(_whole(city, rest))


def method_grades(records: Iterable[EvidenceRecord]) -> MethodGrades:
    """Grades with the model's answers and without them, to show what the model adds.

    Args:
        records: Evidence records.

    Returns:
        The two sets of grades and the regions that differ.
    """
    by_region: defaultdict[RegionKey, list[EvidenceRecord]] = defaultdict(list)
    for record in records:
        by_region[(record["provider"], record["region_id"])].append(record)
    with_model: dict[RegionKey, Grade] = {k: best_grade(v) for k, v in by_region.items()}
    without: dict[RegionKey, Grade] = {
        k: best_grade([r for r in v if not r.get("note", "").startswith("rag:")])
        for k, v in by_region.items()
    }
    with_counts: Counter[Grade] = Counter(with_model.values())
    without_counts: Counter[Grade] = Counter(without.values())
    return {
        "with_model": with_counts,
        "without_model": without_counts,
        "regions_gaining": sorted([list(k) for k in by_region if with_model[k] != without[k]]),
    }


def region_records(
    region: Region,
    placed: Located,
    answer: AnswerWithDocument | None,
    found: Iterable[Found],
    docs: Mapping[str, TypedDocument],
    answer_doc: str,
) -> list[EvidenceRecord]:
    """At most one record per method, kind of source and stance, so grades can be compared without the model.

    Args:
        region: Published region.
        placed: Placed city with coordinates.
        answer: Model answer for the region with its source document, if any.
        found: Lines that name the region and a city.
        docs: Corpus documents keyed by URL.
        answer_doc: URL of the document the model quoted.

    Returns:
        The evidence records for the region.
    """
    cited = docs.get(answer_doc)
    candidates: list[tuple[QuotedCity, TypedDocument, ReadMethod]] = (
        [(answer, cited, "rag")] if answer and cited else []
    )
    quotes: list[tuple[QuotedCity, TypedDocument, ReadMethod]] = [
        (row, doc, "quote-search") for row, doc in found
    ]
    candidates += quotes
    records: dict[tuple[str, str, str], EvidenceRecord] = {}
    for row, doc, method in candidates:
        record = scripted_record(row, doc, region, placed, method)
        if record:
            _ = records.setdefault((method, record.get("source_type", ""), record.get("stance", "")), record)
    return _once_per_line(list(records.values()))


def scripted_evidence(
    regions: Iterable[Region],
    placed: Mapping[RegionKey, Located],
    answers: Mapping[RegionKey, AnswerWithDocument],
    found: Mapping[RegionKey, Sequence[Found]],
    docs: Mapping[str, TypedDocument],
    areas: Mapping[str, str] | None = None,
) -> list[EvidenceRecord]:
    """Evidence records read from the corpus for every placed region.

    Args:
        regions: Published regions.
        placed: Placed city per region.
        answers: Model answers with their source documents, keyed by region.
        found: Lines that name a region and a city, keyed by region.
        docs: Corpus documents keyed by URL.
        areas: Country of each first-level area name; a state named as a city contradicts nothing.

    Returns:
        The evidence records.
    """
    records: list[EvidenceRecord] = []
    for region in regions:
        key = (region.provider, region.region_id)
        if key in placed:
            answer = answers.get(key)
            records += region_records(
                region, placed[key], answer, found.get(key, []), docs, answer["document"] if answer else ""
            )
    states = areas or {}
    return [
        r
        for r in records
        if r.get("stance") != "contradicts" or states.get(norm(r.get("value", ""))) != STATE_COUNTRY
    ]


def scripted_record(
    row: QuotedCity, doc: TypedDocument, region: Region, placed: Located, method: ReadMethod
) -> EvidenceRecord | None:
    """The evidence record for a quote that names the region and a city, or None when it does not qualify.

    Args:
        row: Line that names the region and a city.
        doc: Corpus document with its recorded type.
        region: Published region.
        placed: Placed city with coordinates.
        method: How the quote was read.

    Returns:
        The evidence record, or None.
    """
    if (
        doc["kind"] == "directory"
        or _network_page(doc)
        or len(row["quote"]) < MIN_QUOTE_CHARS
        or not means_region(row["quote"], region, row["city"])
    ):
        return None
    inside = _in_area(row, placed)
    if not inside and method != "rag":
        return None
    stance = "supports" if inside else "contradicts"
    return {
        "provider": region.provider,
        "region_id": region.region_id,
        "claim": "city",
        "value": row["city"],
        "source_type": source_type(doc),
        "publisher": doc["publisher"],
        "title": doc["title"],
        "url": doc["url"],
        "quote": city_level_quote(row["quote"], row["city"]),
        "stance": stance,
        "note": f"{method}: read from the corpus by code.",
        "published": "",
    }


def source_type(doc: TypedDocument) -> SourceType:
    """Anything unrecognised counts as a third-party listing, the weakest reading.

    Args:
        doc: Corpus document with its recorded type.

    Returns:
        The source type.
    """
    return SOURCE_TYPES.get(doc["kind"], "directory")


def _in_area(row: Located, placed: Located) -> bool:
    """Whether a row's point lies within the match radius of the placed city.

    Args:
        row: Row with coordinates.
        placed: Placed city with coordinates.

    Returns:
        True when the point is within the radius.
    """
    return (
        haversine_km((float(row["lat"]), float(row["lon"])), (float(placed["lat"]), float(placed["lon"])))
        <= MATCH_KM
    )


def _independent_names(region: Region, city: str) -> list[str]:
    """A region name that is the city itself cannot tie the region to that city.

    Args:
        region: Published region.
        city: City name.

    Returns:
        The region's names that do not contain the city.
    """
    names = {region.region_id, region.name, region.stated_location}
    return [n for n in names if n and len(n) >= MIN_NAME_CHARS and city.lower() not in n.lower()]


def _line(record: EvidenceRecord) -> tuple[str, str, str, str]:
    """What a record says and where: two records with the same line are one finding.

    Args:
        record: Evidence record.

    Returns:
        The claimed value, the URL, the kind of source and the quote.
    """
    return (
        record.get("value", ""),
        record.get("url", ""),
        record.get("source_type", ""),
        record.get("quote", ""),
    )


def _network_page(doc: TypedDocument) -> bool:
    """Whether a document's title or URL marks it as about network sites.

    Args:
        doc: Corpus document with its recorded type.

    Returns:
        True for a network page.
    """
    plain = re.sub(r"[^a-z]", "", f"{doc['title']} {doc['url']}".lower())
    return any(word in plain for word in NETWORK_PAGES)


def _once_per_line(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Keep one record where the model chose a line the quote search also read, credited to the search.

    Args:
        records: A region's evidence records.

    Returns:
        The records without the model's copy of a searched line.
    """
    searched = {_line(r): r for r in records if r.get("note", "").startswith("quote-search:")}
    kept: list[EvidenceRecord] = []
    for record in records:
        twin = searched.get(_line(record)) if record.get("note", "").startswith("rag:") else None
        if twin:
            twin["note"] = "quote-search: read from the corpus by code; the model chose the same line."
        else:
            kept.append(record)
    return kept


def _short_code_named(region: Region, city: str, quote: str) -> bool:
    """A short ID such as ERI only counts as an exact capitalised word.

    Args:
        region: Published region.
        city: City name.
        quote: Quoted text.

    Returns:
        True when the ID appears as an exact word.
    """
    code = region.region_id
    return (
        code.isupper()
        and city.lower() not in code.lower()
        and bool(re.search(rf"(?<!\w){re.escape(code)}(?!\w)", quote))
    )


def _whole(name: str, text: str) -> re.Match[str] | None:
    """Find a name in text as whole words.

    Args:
        name: Name to find.
        text: Text to search.

    Returns:
        The match, or None.
    """
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.I)


network_page = _network_page
