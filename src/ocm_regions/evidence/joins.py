# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Derive public-record joins from the corpus: an audit list ties a region to a code, a government record ties that code to a place."""

import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Final, Literal, TypeAlias, TypedDict

from ocm_regions.common.geo import MATCH_KM, haversine_km
from ocm_regions.common.schema import (
    CountryCode,
    EvidenceRecord,
    Kilometres,
    Located,
    ProviderId,
    RegionId,
    RegionKey,
)
from ocm_regions.fetch.sources import StrPath
from ocm_regions.model.document_types import TypedDocument
from ocm_regions.model.llm import STREET_ADDRESS, corpus_text
from ocm_regions.place.placer import City, Gazetteer
from ocm_regions.place.placer import norm as _norm
from ocm_regions.regions.parsers import Region

CodeKey: TypeAlias = tuple[str, int]
Span: TypeAlias = tuple[int, int, set[CodeKey]]
JoinStatus: TypeAlias = Literal["joined", "outside-area", "no-city"]

CHEMICALS: Final[frozenset[CodeKey]] = frozenset({("CO", 2), ("NO", 2), ("SO", 2)})
CODE: Final = re.compile(r"([A-Za-z]+?)0*(\d+)")
HEADING: Final = re.compile(r"(.+?) Region")
LEADING_NUMBER: Final = re.compile(r"\b\d{2,5} [A-Z][a-z]+ [A-Z]")
LIST_CODE: Final = re.compile(r"[A-Z]{2,4}\d{0,3}")
MAX_NAME_WORDS: Final = 3
MIN_PLACE_POPULATION: Final = 100

NAMED: Final = re.compile(
    r"\b([A-Z][A-Za-z]{1,3}) ?(\d{1,3})\b((?:\s*(?:,\s*and|,|and|en|&|t/m|to|-|–)\s*(?:\1)? ?\d{1,3}\b(?![.,]\d))*)"
)

NEAR_CHARS: Final = 250
PART: Final = re.compile(r"\s*(,\s*and|,|and|en|&|t/m|to|-|–)\s*(?:[A-Z][A-Za-z]{1,3})? ?(\d{1,3})")
QUOTE_BEFORE: Final = 40
RANGE_WORDS: Final = ("t/m", "to", "-", "–")
REPORT_FIELDS: Final = ["provider", "region_id", "url", "codes", "city", "km", "status"]


class PlacedCity(Located):
    """A placed city with its coordinates and country."""

    country: CountryCode


class JoinDocument(TypedDict):
    """A document used in a join: its source details and saved text."""

    url: str
    title: str
    publisher: str
    published: str
    text: str


class JoinReport(TypedDict):
    """What one government document showed for a region's facility codes."""

    provider: ProviderId
    region_id: RegionId
    url: str
    codes: str
    city: str
    km: int | Literal[""]
    status: JoinStatus


class DocResult(TypedDict):
    """The codes, city and quote one document gives for a region."""

    joined: set[str]
    city: City | None
    code: str
    quote: str
    report: JoinReport
    doc: JoinDocument


class JoinsResult(TypedDict):
    """The evidence records and the report for one region's joins."""

    records: list[EvidenceRecord]
    report: list[JoinReport]


def audit_lists(text: str) -> dict[str, list[str]]:
    """Codes printed under each "<name> Region" heading; any other text ends the list.

    Args:
        text: Text of the audit document.

    Returns:
        Codes keyed by region name.
    """
    lists: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in (line.strip() for line in text.splitlines()):
        heading = HEADING.fullmatch(line)
        if heading:
            current = lists.setdefault(heading[1], [])
        elif current is not None and LIST_CODE.fullmatch(line):
            current.append(line)
        elif line:
            current = None
    return {name: codes for name, codes in lists.items() if codes}


def code_key(code: str) -> CodeKey | None:
    """A facility code as letters and a number, so ``DUB06`` and ``Dub 6`` compare equal.

    Args:
        code: Facility code as printed.

    Returns:
        The letters and the number, or None when the text is not a code.
    """
    match = CODE.fullmatch(code)
    return (match[1].upper(), int(match[2])) if match else None


def derive_joins(
    region: Region,
    placed: PlacedCity,
    audit: JoinDocument,
    codes: Sequence[str],
    documents: Iterable[JoinDocument],
    operator: str,
    gaz: Gazetteer,
) -> JoinsResult:
    """Records for one region, and a report of every document that named one of its codes.

    Args:
        region: Published region.
        placed: Placed city of the region.
        audit: Audit document that lists the facility codes.
        codes: Codes, in the same order as the sites or as audited.
        documents: Government documents to search.
        operator: Operator name the document must show.
        gaz: Gazetteer of cities.

    Returns:
        The evidence records and the report rows.
    """
    by_key = {key: c for c in codes if (key := code_key(c))}
    results = [
        r for r in (_doc_result(region, placed, d, codes, operator, gaz, by_key) for d in documents) if r
    ]
    good = [r for r in results if r["report"]["status"] == "joined"]
    share = f"{len(set[str]().union(*[r['joined'] for r in good])) if good else 0} of {len(codes)}"
    joins = [
        _join_record(region, r["doc"], r["code"], city, r["quote"], operator, share)
        for r in good
        if (city := r["city"])
    ]
    listed = {j.get("join_code", "") for j in joins} or {codes[0]}
    return {
        "records": [_code_record(region, audit, c, codes) for c in sorted(listed, key=codes.index)] + joins,
        "report": [r["report"] for r in results],
    }


def named_codes(text: str) -> set[CodeKey]:
    """Every facility code key a text names.

    Args:
        text: Text to search.

    Returns:
        The code keys.
    """
    return {key for _match, keys in _matches(text) for key in keys}


def provider_joins(
    provider: ProviderId,
    regions: Iterable[Region],
    placed: Mapping[RegionKey, PlacedCity],
    materials: Sequence[TypedDocument],
    operator: str,
    raw: StrPath,
    gaz: Gazetteer,
) -> tuple[list[EvidenceRecord], list[JoinReport]]:
    """Every audit list and government record held for a provider; only regions with a place and a matching heading count.

    Args:
        provider: Vendor identifier.
        regions: Published regions.
        placed: Placed city per region.
        materials: Corpus documents with their recorded types.
        operator: Operator name the document must show.
        raw: The data/raw folder.
        gaz: Gazetteer of cities.

    Returns:
        The evidence records and the report rows.
    """
    audits = [
        m
        for m in materials
        if m["provider"] == provider and m["kind"] == "certification" and m["fetch"] == "yes"
    ]
    documents = [
        _document(m, raw)
        for m in materials
        if m["provider"] == provider and m["kind"] == "government" and m["fetch"] == "yes"
    ]
    by_name = {_squeezed(r.name): r for r in regions if r.provider == provider}
    records: list[EvidenceRecord] = []
    report: list[JoinReport] = []
    for row in audits:
        audit = _document(row, raw)
        for heading, codes in audit_lists(audit["text"]).items():
            region = by_name.get(_squeezed(heading))
            if region and (provider, region.region_id) in placed:
                result = derive_joins(
                    region, placed[(provider, region.region_id)], audit, codes, documents, operator, gaz
                )
                records += result["records"]
                report += result["report"]
    return records, report


def _city_near(
    text: str, spans: Iterable[tuple[int, int]], gaz: Gazetteer, placed: PlacedCity
) -> City | None:
    """The city a document ties to the codes: nearest in the area, near the codes first.

    Args:
        text: Text of the document.
        spans: Start and end offsets of the code mentions.
        gaz: Gazetteer of cities.
        placed: Placed city of the region.

    Returns:
        The city, or None.
    """
    near = [text[max(0, s - NEAR_CHARS) : e + NEAR_CHARS] for s, e in spans]
    return (
        _closest_inside(near, gaz, placed)
        or _closest_inside([text], gaz, placed)
        or _most_named_outside(text, gaz, placed)
    )


def _closest_inside(windows: Iterable[str], gaz: Gazetteer, placed: PlacedCity) -> City | None:
    """A document may name many towns, some of them ordinary words, so only a place in the area counts, the nearest first.

    Args:
        windows: Windows of text around code mentions.
        gaz: Gazetteer of cities.
        placed: Placed city of the region.

    Returns:
        The nearest place within the area, or None.
    """
    inside = [i for w in windows for i in _places_in(w, gaz, placed) if _km(placed, i) <= MATCH_KM]
    return min(inside, key=lambda i: _km(placed, i), default=None)


def _code_record(region: Region, audit: JoinDocument, code: str, codes: Sequence[str]) -> EvidenceRecord:
    """The evidence record that an audit list ties a facility code to the region.

    Args:
        region: Published region.
        audit: Audit document that lists the facility codes.
        code: Facility code as the audit prints it.
        codes: Codes, in the same order as the sites or as audited.

    Returns:
        The evidence record.
    """
    return {
        "provider": region.provider,
        "region_id": region.region_id,
        "claim": "facility-code",
        "value": code,
        "source_type": "certification",
        "publisher": audit["publisher"],
        "title": audit["title"],
        "url": audit["url"],
        "quote": code,
        "stance": "partial",
        "note": f"One of {len(codes)} facility codes listed under this region; it names no city.",
        "published": "",
    }


def _doc_result(
    region: Region,
    placed: PlacedCity,
    doc: JoinDocument,
    codes: Sequence[str],
    operator: str,
    gaz: Gazetteer,
    by_key: Mapping[CodeKey, str],
) -> DocResult | None:
    """What one government document shows for a region: codes named, city and quote, or None.

    Args:
        region: Published region.
        placed: Placed city of the region.
        doc: Government document.
        codes: Codes, in the same order as the sites or as audited.
        operator: Operator name the document must show.
        gaz: Gazetteer of cities.
        by_key: Audited codes as printed, keyed by code key.

    Returns:
        The result, or None when the document names none of the codes.
    """
    text = " ".join(doc["text"].split())
    spans = _spans_for(text, set(by_key))
    if not spans or operator.lower() not in text.lower():
        return None
    start, end, _found = spans[0]
    joined = {by_key[k] for _s, _e, found in spans for k in found if k in by_key}
    city = _city_near(text, [(s, e) for s, e, _f in spans], gaz, placed)
    km: int | Literal[""] = round(_km(placed, city)) if city else ""
    near = isinstance(km, int) and km <= MATCH_KM
    status: JoinStatus = "joined" if city and near else ("outside-area" if city else "no-city")
    return {
        "joined": joined,
        "city": city,
        "code": _first_named(codes, by_key, text[start:end]),
        "quote": _quote(text, start, end),
        "report": _report(region, doc, joined, city, km, status),
        "doc": doc,
    }


def _document(row: TypedDocument, raw: StrPath) -> JoinDocument:
    """A document from the URL index with its saved text.

    Args:
        row: Corpus document with its recorded type.
        raw: The data/raw folder.

    Returns:
        The document.
    """
    return {
        "url": row["url"],
        "title": row["title"],
        "publisher": row["publisher"],
        "published": str(row.get("published", "")),
        "text": _saved_text(row, raw),
    }


def _first_named(codes: Sequence[str], by_key: Mapping[CodeKey, str], match_text: str) -> str:
    """The audited code a passage names, preferring one printed literally.

    Args:
        codes: Codes, in the same order as the sites or as audited.
        by_key: Audited codes as printed, keyed by code key.
        match_text: Text of the match.

    Returns:
        The code as the audit prints it.
    """
    named = [by_key[k] for k in named_codes(match_text) if k in by_key]
    literal = [c for c in named if _printed(c, match_text)]
    return min(literal or named or codes, key=codes.index)


def _is_chemical(prefix: str, first: int, text: str, end: int) -> bool:
    """Whether a match is a chemical formula such as CO2 and not a facility code.

    Args:
        prefix: Letters of the matched code.
        first: Offset where the match begins.
        text: Text the match was found in.
        end: End offset in the text.

    Returns:
        True for a chemical formula.
    """
    return (prefix, first) in CHEMICALS and text[end : end + 1] != "/"


def _join_record(
    region: Region, doc: JoinDocument, code: str, city: City, quote: str, operator: str, share: str
) -> EvidenceRecord:
    """The evidence record that a government document puts an audited code in a city.

    Args:
        region: Published region.
        doc: Government document.
        code: Audited facility code the document names.
        city: City the document names.
        quote: Quoted text.
        operator: Operator name the document must show.
        share: Share of the operator's sites, as text.

    Returns:
        The evidence record.
    """
    return {
        "provider": region.provider,
        "region_id": region.region_id,
        "claim": "city",
        "value": city.name,
        "source_type": "code_join",
        "publisher": doc["publisher"],
        "title": doc["title"],
        "url": doc["url"],
        "quote": quote,
        "stance": "supports",
        "join_code": code,
        "also_found": f"{operator}|{city.name}",
        "note": f"Joined {share} audited codes.",
        "published": doc["published"],
    }


def _km(placed: PlacedCity, item: City) -> Kilometres:
    """Distance from the placed city to a candidate city.

    Args:
        placed: Placed city of the region.
        item: Gazetteer city.

    Returns:
        The distance in kilometres.
    """
    return haversine_km((float(placed["lat"]), float(placed["lon"])), (item.lat, item.lon))


def _matches(text: str) -> Iterator[tuple[re.Match[str], set[CodeKey]]]:
    """Yield each run of facility codes in a text with the code keys it names.

    Args:
        text: Text to search.

    Yields:
        A match and the code keys it names.
    """
    for match in NAMED.finditer(text):
        prefix, first = match[1].upper(), int(match[2])
        if not _is_chemical(prefix, first, text, match.end(2)):
            yield match, {(prefix, n) for n in _numbers(first, match[3])}


def _most_named_outside(text: str, gaz: Gazetteer, placed: PlacedCity) -> City | None:
    """The place a text names most often, for a document that names none in the area.

    Args:
        text: Text of the document.
        gaz: Gazetteer of cities.
        placed: Placed city of the region.

    Returns:
        The place, or None when the text names none.
    """
    counts: Counter[str] = Counter()
    first: dict[str, City] = {}
    for item in _places_in(text, gaz, placed):
        counts[item.name] += 1
        _ = first.setdefault(item.name, item)
    return max(first.values(), key=lambda city: (counts[city.name], city.population)) if first else None


def _numbers(first: int, tail: str) -> list[int]:
    """The numbers in a run such as ``6, 7 and 9`` or ``6 to 9``.

    Args:
        first: Offset where the match begins.
        tail: Text after the first code.

    Returns:
        The numbers, with ranges expanded.
    """
    numbers, last = [first], first
    parts: list[tuple[str, str]] = PART.findall(tail)
    for word, digits in parts:
        value = int(digits)
        numbers += list(range(last + 1, value + 1)) if word in RANGE_WORDS else [value]
        last = value
    return numbers


def _place_named(name: str, gaz: Gazetteer, placed: PlacedCity) -> City | None:
    """Among places sharing a name, the one closest to the placed city is meant.

    Args:
        name: Place name found in the text.
        gaz: Gazetteer of cities.
        placed: Placed city of the region.

    Returns:
        The closest place of that name, or None.
    """
    items = [
        i
        for i in gaz.index.get(name, [])
        if i.country == placed["country"] and i.population >= MIN_PLACE_POPULATION
    ]
    return min(items, key=lambda i: _km(placed, i), default=None)


def _places_in(window: str, gaz: Gazetteer, placed: PlacedCity) -> Iterator[City]:
    """Yield the places a window of text names, longest names first.

    Args:
        window: Text around a code mention.
        gaz: Gazetteer of cities.
        placed: Placed city of the region.

    Yields:
        A place the window names.
    """
    words = [_norm(w.replace("-", "")) for w in window.split()]
    for size in range(MAX_NAME_WORDS, 0, -1):
        for start in range(len(words) - size + 1):
            item = _place_named(" ".join(words[start : start + size]), gaz, placed)
            if item:
                yield item


def _printed(code: str, text: str) -> bool:
    """Whether a code appears in a text in its own printed form.

    Args:
        code: Facility code as the audit prints it.
        text: Text to search.

    Returns:
        True when the printed form is found.
    """
    key = code_key(code)
    return key is not None and bool(re.search(rf"\b{key[0]} ?0*{key[1]}(?!\d)", text, re.I))


def _quote(text: str, start: int, end: int) -> str:
    """The words just before the code and the code itself; what follows a code is often a street address.

    Args:
        text: Text of the document.
        start: Start offset in the text.
        end: End offset in the text.

    Returns:
        The quote text.
    """
    begin = text.rfind(" ", 0, max(0, start - QUOTE_BEFORE)) + 1
    quote = text[begin:end]
    return (
        text[start:end]
        if STREET_ADDRESS.search(quote) or LEADING_NUMBER.search(quote[: start - begin])
        else quote
    )


def _report(
    region: Region,
    doc: JoinDocument,
    codes: Iterable[str],
    city: City | None,
    km: int | Literal[""],
    status: JoinStatus,
) -> JoinReport:
    """One row of the joins report.

    Args:
        region: Published region.
        doc: Government document.
        codes: Facility codes the document names.
        city: City the document names, if any.
        km: Distance from the placed city, or blank.
        status: Outcome of the join.

    Returns:
        The report row.
    """
    return {
        "provider": region.provider,
        "region_id": region.region_id,
        "url": doc["url"],
        "codes": ";".join(sorted(codes)),
        "city": city.name if city else "",
        "km": km,
        "status": status,
    }


def _saved_text(row: TypedDocument, raw: StrPath) -> str:
    """The saved text of a document in the URL index.

    Args:
        row: Corpus document with its recorded type.
        raw: The data/raw folder.

    Returns:
        The text; blank when the document is not saved.
    """
    folder = Path(raw) / "trust_docs" / row["provider"] / "fetched"
    return (
        " ".join(corpus_text(p) for p in sorted(folder.glob(f"{row['doc_id']}.*"))) if folder.is_dir() else ""
    )


def _spans_for(text: str, keys: set[CodeKey]) -> list[Span]:
    """Where a text names any of the given code keys.

    Args:
        text: Text to search.
        keys: Code keys to find.

    Returns:
        The start and end offsets of each mention.
    """
    return [(m.start(), m.end(), found) for m, found in _matches(text) if found & keys]


def _squeezed(name: str) -> str:
    """A name in lower case with spaces removed, for matching headings to region names.

    Args:
        name: Name to convert.

    Returns:
        The squeezed name.
    """
    return name.lower().replace(" ", "")
