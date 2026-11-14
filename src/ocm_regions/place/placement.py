# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Derive each region's place: vendor label, then a checked model answer, then the feed or capital, then the model's memory."""

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import cache, lru_cache
from pathlib import Path
from typing import Final, Required, TypeAlias, cast

from ocm_regions.common import data, layout
from ocm_regions.common.data import Facility, LocodeRow
from ocm_regions.common.geo import (
    MATCH_KM,
    Airport,
    haversine_km,
    nearest_airport,
    parse_locode_coordinates,
    place_id,
)
from ocm_regions.common.schema import (
    Basis,
    CountryCode,
    IataCode,
    Locode,
    Place,
    PlaceMapping,
    Point,
    ProviderId,
    RegionId,
    RegionKey,
)
from ocm_regions.evidence.scripted import AnswerWithDocument, scripted_record
from ocm_regions.evidence.scripted import network_page as _network_page
from ocm_regions.fetch.sources import StrPath
from ocm_regions.model.document_types import TypedDocument, read_with_types
from ocm_regions.model.llm import DOCUMENT_TYPES, PAPER_MODEL, corpus_paths, corpus_text
from ocm_regions.place.hubs import airport_ranks, derive_hub
from ocm_regions.place.named_place import named_place
from ocm_regions.place.placer import (
    City,
    Gazetteer,
    PlacerRow,
    capital_of_tail,
    feed_points,
    load_gazetteer,
    place_regions,
    step,
)
from ocm_regions.place.placer import as_signal as _as_signal
from ocm_regions.place.placer import norm as _norm
from ocm_regions.regions.parsers import Region

Alternates: TypeAlias = Mapping[str, set[str]]
Areas: TypeAlias = Mapping[str, str]
Chosen: TypeAlias = dict[tuple[str, str], list[Place]]
Cities: TypeAlias = Mapping[str, Sequence[City]]
TextsOf: TypeAlias = Callable[[str], list[str]]

BASIS_NOTES: Final[dict[str, str]] = {
    "label, agrees": "Label or ID and a second signal agree on the place.",
    "label": "The vendor's label or ID names the place.",
    "label, conflicts": "The vendor's label names the place; its feed disagrees.",
    "document": "A local model read this place in a document, and code found its quote.",
    "feed": "Only the vendor's feed gives a place.",
    "capital": "The label names only the country, so the capital stands in.",
    "memory": "A local model recalled this place; no document supports it.",
    "none": "",
}

CLLI_PREFIX: Final = re.compile(r"[A-Z]{6}")
LABEL_DOCUMENT_NOTE: Final = "The label names a place the town list lacks; a vendor document locates it here."
OPEN_TO_ANY_DOCUMENT: Final[frozenset[Basis]] = frozenset({"capital", "none"})
OPEN_TO_DOCUMENTS: Final[frozenset[Basis]] = frozenset({"feed", "capital", "none"})
OPEN_TO_NAMED_PLACES: Final[frozenset[Basis]] = frozenset({"none", "memory"})
ROOT: Final = Path(__file__).resolve().parents[3]
ANSWERS: Final = ROOT / "output" / "llm" / PAPER_MODEL
MATERIALS: Final = layout.documents(ROOT)
SAME_STATE_CODES: Final = frozenset({"US"})


class ModelAnswer(AnswerWithDocument, total=False):
    """A saved model answer; the memory method records no document."""

    provider: Required[ProviderId]
    region_id: Required[RegionId]
    status: Required[str]
    country: Required[str]


def apply_model_answers(
    rules: Iterable[PlacerRow],
    rag: Iterable[ModelAnswer],
    memory: Iterable[ModelAnswer],
    regions: Iterable[Region],
    docs: Mapping[str, TypedDocument],
    areas: Areas,
) -> list[PlacerRow]:
    """The label stays first; memory only fills what nothing else placed.

    Args:
        rules: The rules' rows.
        rag: Model answers given with retrieved passages.
        memory: Model answers given without documents.
        regions: Published regions.
        docs: Corpus documents keyed by URL.
        areas: First-level area names per country.

    Returns:
        The rows, with model answers applied.
    """
    by_key, rag_rows, memory_rows = (
        {(r.provider, r.region_id): r for r in regions},
        _answered(rag),
        _answered(memory),
    )
    placed: list[PlacerRow] = []
    for rule in rules:
        key = (rule["provider"], rule["region_id"])
        document, recalled = rag_rows.get(key), memory_rows.get(key)
        if (
            rule["basis"] in OPEN_TO_DOCUMENTS
            and document
            and _replaces(rule, document, by_key[key], docs.get(document.get("document", "")), areas)
        ):
            placed.append(_with_answer(rule, document, "document"))
        elif rule["basis"] == "none" and recalled and recalled["status"] == "answered":
            placed.append(_with_answer(rule, recalled, "memory"))
        else:
            placed.append(rule)
    return placed


def apply_named_places(
    rules: Iterable[PlacerRow], regions: Iterable[Region], gaz: Gazetteer, texts_of: TextsOf
) -> list[PlacerRow]:
    """A vendor sentence locating the label's place outranks memory and the capital, and never a place the rules or a checked quote gave.

    Args:
        rules: The rules' rows.
        regions: Published regions.
        gaz: Gazetteer of cities.
        texts_of: Function that returns a vendor's document texts.

    Returns:
        The rows, with named places applied.
    """
    by_key = {(r.provider, r.region_id): r for r in regions}
    placed: list[PlacerRow] = []
    for rule in rules:
        region = (
            by_key.get((rule["provider"], rule["region_id"]))
            if rule["basis"] in OPEN_TO_NAMED_PLACES
            else None
        )
        city = named_place(region, texts_of(region.provider), gaz) if region else None
        placed.append(
            {
                **rule,
                "basis": "document",
                "cross_check": "",
                "basis_note": LABEL_DOCUMENT_NOTE,
                "city": city.name,
                "country": city.country,
                "lat": city.lat,
                "lon": city.lon,
            }
            if city
            else rule
        )
    return placed


def derive_places(
    rules: Iterable[PlacerRow],
    airports: Sequence[Airport],
    ranks: Mapping[IataCode, float],
    locodes: Mapping[Locode, LocodeRow],
    facilities: Sequence[Facility],
    alternates: Alternates | None = None,
    cities: Cities | None = None,
) -> tuple[list[Place], dict[RegionKey, PlaceMapping]]:
    """One place per town, and one mapping per region.

    Args:
        rules: The rules' rows.
        airports: Airports to choose from.
        ranks: Connectivity rank per airport.
        locodes: UN/LOCODE rows keyed by code.
        facilities: PeeringDB facilities.
        alternates: Other names for each city.
        cities: Gazetteer cities keyed by normalised name, used to read a place's state.

    Returns:
        The places and the mappings keyed by region.
    """
    chosen: Chosen = {}
    mappings: dict[RegionKey, PlaceMapping] = {}
    for rule in rules:
        key = (rule["provider"], rule["region_id"])
        place = None
        if rule["city"]:
            place = _place_of(rule, chosen)
            if not place:
                place = generated_place(rule, airports, ranks, locodes, facilities, alternates, cities)
                siblings = chosen.setdefault((rule["country"], _norm(rule["city"])), [])
                place["place_id"] += f"-{len(siblings) + 1}" if siblings else ""
                siblings.append(place)
        mappings[key] = _mapping(rule, place)
    return [p for group in chosen.values() for p in group], mappings


def generated_place(
    rule: PlacerRow,
    airports: Sequence[Airport],
    ranks: Mapping[IataCode, float],
    locodes: Mapping[Locode, LocodeRow],
    facilities: Iterable[Facility],
    alternates: Alternates | None = None,
    cities: Cities | None = None,
) -> Place:
    """Build a place for a rule row: its identifier, UN/LOCODE, hub airport and CLLI prefix.

    Args:
        rule: The rules' row for the region.
        airports: Airports to choose from.
        ranks: Connectivity rank per airport.
        locodes: UN/LOCODE rows keyed by code.
        facilities: PeeringDB facilities.
        alternates: Other names for each city.
        cities: Gazetteer cities keyed by normalised name, used to read the place's state.

    Returns:
        The place.
    """
    point, country = (float(rule["lat"]), float(rule["lon"])), CountryCode(rule["country"])
    city = _display(rule["city"])
    hub = derive_hub(point, country, airports, ranks) or nearest_airport(point, country, airports)
    return {
        "place_id": place_id(country, "", city),
        "city": city,
        "country": country,
        "lat": rule["lat"],
        "lon": rule["lon"],
        "unlocode": _unlocode(
            city, country, locodes, alternates or {}, point, _state(city, country, point, cities or {})
        ),
        "hub_iata": hub.code,
        "clli": _clli(city, country, facilities, point),
    }


def load_mappings(raw: StrPath, inputs: StrPath) -> dict[RegionKey, PlaceMapping]:
    """The mapping of every region to its place.

    Args:
        raw: The data/raw folder.
        inputs: Repository root that holds the input folders.

    Returns:
        Mappings keyed by vendor and region.
    """
    return _derived(str(raw), str(inputs))[1]


def load_places(raw: StrPath, inputs: StrPath) -> list[Place]:
    """Every place the placement order produces.

    Args:
        raw: The data/raw folder.
        inputs: Repository root that holds the input folders.

    Returns:
        The places.
    """
    return _derived(str(raw), str(inputs))[0]


def _alternates(gaz: Gazetteer) -> dict[str, set[str]]:
    """Other names for each city, in both directions, from the gazetteer's aliases.

    Args:
        gaz: Gazetteer of cities.

    Returns:
        Sets of other names keyed by city name.
    """
    names: dict[str, set[str]] = {}
    for alias, name in gaz.aliases.items():
        names.setdefault(_norm(name), set()).add(_norm(alias))
        names.setdefault(_norm(alias), set()).add(_norm(name))
    return names


def _answered(rows: Iterable[ModelAnswer]) -> dict[RegionKey, ModelAnswer]:
    """Model answers keyed by vendor and region.

    Args:
        rows: Recorded model answers.

    Returns:
        The answers that give a city.
    """
    return {(r["provider"], r["region_id"]): r for r in rows}


def _answers(name: str) -> list[ModelAnswer]:
    """The paper model's recorded answers for one method, or none when the file is absent.

    Args:
        name: Method name, which is also the file name.

    Returns:
        The answers.
    """
    path = ANSWERS / f"{name}.csv"
    return cast(list[ModelAnswer], data.read_csv(path)) if path.exists() else []


def _capital_as_last_resort(
    rules: Iterable[PlacerRow], regions: Iterable[Region], gaz: Gazetteer
) -> list[PlacerRow]:
    """Place any region still unplaced at the capital of the country its label ends with.

    Args:
        rules: The rules' rows.
        regions: Published regions.
        gaz: Gazetteer of cities.

    Returns:
        The rows, with capitals filled in.
    """
    labels = {(r.provider, r.region_id): r.stated_location for r in regions}
    placed: list[PlacerRow] = []
    for rule in rules:
        capital = (
            _as_signal(capital_of_tail(labels[(rule["provider"], rule["region_id"])], gaz))
            if rule["basis"] == "none"
            else None
        )
        placed.append(
            {
                **rule,
                "basis": "capital",
                "city": capital["name"],
                "country": capital.get("country", ""),
                "lat": capital["lat"],
                "lon": capital["lon"],
            }
            if capital
            else rule
        )
    return placed


def _clli(city: str, country: str, facilities: Iterable[Facility], point: Point | None = None) -> str:
    """The most common six-letter CLLI prefix on PeeringDB facilities in a city, or blank.

    Args:
        city: City name.
        country: ISO country code.
        facilities: PeeringDB facilities.
        point: Latitude and longitude, if known.

    Returns:
        The prefix, or blank.
    """
    prefixes = Counter(
        (f.get("clli") or "").strip().upper()[:6]
        for f in facilities
        if f.get("country") == country and _norm(f.get("city", "")) == _norm(city) and _clli_near(f, point)
    )
    usable = sorted((-count, prefix) for prefix, count in prefixes.items() if CLLI_PREFIX.fullmatch(prefix))
    return usable[0][1] if usable else ""


def _clli_near(facility: Facility, point: Point | None) -> bool:
    """Whether a facility lies in the area of a point, or has no coordinates to check.

    Args:
        facility: PeeringDB facility.
        point: Latitude and longitude, if known.

    Returns:
        True when the facility is near or cannot be checked.
    """
    if not (point and facility.get("latitude") and facility.get("longitude")):
        return True
    return (
        haversine_km(point, (float(facility.get("latitude") or 0), float(facility.get("longitude") or 0)))
        <= MATCH_KM
    )


@lru_cache(maxsize=4)
def _derived(raw: str, inputs: str) -> tuple[list[Place], dict[RegionKey, PlaceMapping]]:
    """Apply the placement order to every region and derive one place per town and one mapping per region.

    Args:
        raw: The data/raw folder, as text so the result can be cached.
        inputs: Repository root that holds the input folders.

    Returns:
        The places and the mappings keyed by region.
    """
    regions, gaz = data.load_regions(Path(raw)), load_gazetteer(raw)
    rules = place_regions(regions, gaz, feed_points(raw))
    docs = (
        {r["doc_id"]: r for r in read_with_types(MATERIALS, ANSWERS / DOCUMENT_TYPES)}
        if MATERIALS.exists()
        else {}
    )
    rules = apply_model_answers(rules, _answers("rag"), _answers("memory"), regions, docs, gaz.areas)
    rules = apply_named_places(rules, regions, gaz, _vendor_texts(raw, docs))
    rules = _capital_as_last_resort(rules, regions, gaz)
    ranks = airport_ranks((Path(raw) / "opentraveldata" / "optd_por_public.csv").read_text(encoding="utf-8"))
    return derive_places(
        rules,
        list(data.load_airports(Path(raw)).values()),
        ranks,
        data.load_locodes(Path(raw)),
        data.load_facilities(Path(raw)),
        _alternates(gaz),
        gaz.index,
    )


def _display(city: str) -> str:
    """A city name in title case when the source gave it in capitals.

    Args:
        city: City name.

    Returns:
        The name to show.
    """
    return city.title() if city.isupper() else city


def _locode_names(name: str) -> tuple[str, set[str]]:
    """A code's own name, then the names it holds in brackets or after a comma, such as a district's city.

    Args:
        name: Name of the UN/LOCODE entry.

    Returns:
        The main name and the set of names held inside it.
    """
    parts = re.split(r"[(),]", name)
    return _norm(parts[0]), {_norm(re.sub(r"^\s*ex\s+", "", part)) for part in parts[1:] if part.strip()}


def _locode_near(row: LocodeRow, point: Point | None) -> bool:
    """A code with coordinates must lie in the area; many codes have none, and those are taken on their name.

    Args:
        row: UN/LOCODE row.
        point: Latitude and longitude, if known.

    Returns:
        True when the code is in the area or has no coordinates.
    """
    coordinates = parse_locode_coordinates(row.get("Coordinates", ""))
    return not (coordinates and point) or haversine_km(coordinates, point) <= MATCH_KM


def _locode_order(code: str, row: LocodeRow, state: str) -> tuple[bool, bool, str]:
    """Sort key among codes with the same name: the city's state first, then codes with coordinates.

    Args:
        code: The UN/LOCODE.
        row: Its row.
        state: The city's state, or blank when it is not known.

    Returns:
        The key; the smallest is the code to take.
    """
    return (not state or row.get("Subdivision") != state, not row.get("Coordinates"), code)


def _mapping(rule: PlacerRow, place: Place | None) -> PlaceMapping:
    """The mapping of one region to its place, with whether the label states the city and the precision.

    Args:
        rule: The rules' row for the region.
        place: Place of the region, if it has one.

    Returns:
        The mapping.
    """
    stated = bool(place) and bool(rule.get("label_city")) and rule["basis"] == "label"
    return {
        "provider": rule["provider"],
        "region_id": rule["region_id"],
        "place_id": place["place_id"] if place else "",
        "stated_as": "city" if stated else "area" if place else "none",
        "precision": ("city" if stated else "metro") if place else "",
        "basis": rule["basis"],
        "cross_check": rule["cross_check"],
        "note": rule.get("basis_note") or BASIS_NOTES.get(step(rule), ""),
    }


def _place_of(rule: PlacerRow, chosen: Chosen) -> Place | None:
    """Regions in one town share a place; a namesake further than the cap is another town.

    A town reached under another of its names has the same point, and shares the place too.

    Args:
        rule: The rules' row for the region.
        chosen: Decision the rules made for the region.

    Returns:
        The existing place for the town, or None.
    """
    point = (float(rule["lat"]), float(rule["lon"]))
    for place in chosen.get((rule["country"], _norm(rule["city"])), []):
        if haversine_km(point, (float(place["lat"]), float(place["lon"]))) <= MATCH_KM:
            return place
    same_point = (
        place
        for group in chosen.values()
        for place in group
        if place["country"] == rule["country"] and (float(place["lat"]), float(place["lon"])) == point
    )
    return next(same_point, None)


def _read_in_document(answer: ModelAnswer, doc: TypedDocument | None, areas: Areas) -> bool:
    """The model read the passage and code found its quote; a listing, a network page or a US state does not place a region.

    Args:
        answer: Recorded model answer for the region.
        doc: Document the answer quotes, if known.
        areas: First-level area names per country.

    Returns:
        True when the answer counts as read in a document.
    """
    if answer["status"] != "answered" or not doc or areas.get(_norm(answer["city"])) == "US":
        return False
    return doc["kind"] != "directory" and not _network_page(doc)


def _replaces(
    rule: PlacerRow, answer: ModelAnswer, region: Region, doc: TypedDocument | None, areas: Areas
) -> bool:
    """A capital or nothing gives way to any answer read in a document; the vendor's feed only to a quote naming the region.

    Args:
        rule: The rules' row for the region.
        answer: Recorded model answer for the region.
        region: Published region.
        doc: Document the answer quotes, if known.
        areas: First-level area names per country.

    Returns:
        True when the answer replaces the rule's placement.
    """
    if not doc or not _read_in_document(answer, doc, areas):
        return False
    if rule["basis"] in OPEN_TO_ANY_DOCUMENT:
        return True
    return scripted_record(answer, doc, region, answer, "rag") is not None


def _state(city: str, country: str, point: Point | None, cities: Cities) -> str:
    """The state of the nearest gazetteer city with this name, where the gazetteer uses UN/LOCODE's codes.

    Args:
        city: City name.
        country: ISO country code.
        point: Latitude and longitude, if known.
        cities: Gazetteer cities keyed by normalised name.

    Returns:
        The state code, or blank.
    """
    if country not in SAME_STATE_CODES or not point:
        return ""
    same = [c for c in cities.get(_norm(city), []) if c.country == country]
    nearest = min(same, key=lambda c: haversine_km((c.lat, c.lon), point), default=None)
    return nearest.admin1 if nearest and haversine_km((nearest.lat, nearest.lon), point) <= MATCH_KM else ""


def _unlocode(
    city: str,
    country: str,
    locodes: Mapping[Locode, LocodeRow],
    alternates: Alternates,
    point: Point | None = None,
    state: str = "",
) -> str:
    """The UN/LOCODE for a city: a code with that name in the country and area, or one that lists the name.

    A code with the city's own name comes before one with another of its names. Of several, one in the
    city's state comes first, then one with coordinates.

    Args:
        city: City name.
        country: ISO country code.
        locodes: UN/LOCODE rows keyed by code.
        alternates: Other names for each city.
        point: Latitude and longitude, if known.
        state: The city's state, where the gazetteer and UN/LOCODE use the same codes.

    Returns:
        The five-character code, or blank.
    """
    names = {_norm(city)} | alternates.get(_norm(city), set())
    near = {
        code: _locode_names(row["NameWoDiacritics"])
        for code, row in locodes.items()
        if row["Country"] == country and _locode_near(row, point)
    }
    exact = [code for code, (first, _rest) in near.items() if first == _norm(city)]
    own = [code for code, (first, _rest) in near.items() if first in names]
    other = [code for code, (_first, rest) in near.items() if names & rest]
    found = exact or own or other
    return min(found, key=lambda code: _locode_order(code, locodes[code], state)) if found else ""


def _vendor_texts(raw: str, docs: Mapping[str, TypedDocument]) -> TextsOf:
    """The vendor's own documents, read once per vendor; listings, audits, press and network pages are left out.

    Args:
        raw: The data/raw folder, as text so the result can be cached.
        docs: Corpus documents keyed by URL.

    Returns:
        A function from vendor to document texts.
    """
    usable = {doc_id for doc_id, doc in docs.items() if doc["kind"] == "vendor" and not _network_page(doc)}

    @cache
    def texts_of(provider: str) -> list[str]:
        """The text of every usable vendor document for one vendor, read once.

        Args:
            provider: Vendor identifier.

        Returns:
            The texts.
        """
        return [corpus_text(path) for path in corpus_paths(raw, provider) if path.stem in usable]

    return texts_of


def _with_answer(rule: PlacerRow, answer: ModelAnswer, basis: Basis) -> PlacerRow:
    """A copy of a rule row placed at a model answer's city, with the given basis.

    Args:
        rule: The rules' row for the region.
        answer: Recorded model answer for the region.
        basis: Basis to record for the placement.

    Returns:
        The new row.
    """
    return {
        **rule,
        "basis": basis,
        "cross_check": "",
        "city": answer["city"],
        "country": answer["country"],
        "lat": float(answer["lat"]),
        "lon": float(answer["lon"]),
    }
