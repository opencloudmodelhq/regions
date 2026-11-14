# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Place regions by fixed rules from vendor labels, region IDs and IP feeds, without hand placements."""

import csv
import hashlib
import json
import re
import unicodedata
import zipfile
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, NotRequired, TypeAlias, TypedDict

from ocm_regions.common import data, layout
from ocm_regions.common.data import ReferencePlacement
from ocm_regions.common.geo import haversine_km
from ocm_regions.common.schema import Basis, CrossCheck, ProviderId, RegionId, RegionKey, Signal
from ocm_regions.common.stats import wilson
from ocm_regions.fetch.sources import StrPath
from ocm_regions.place import feeds
from ocm_regions.publish.paper import macros_tex
from ocm_regions.regions.parsers import Region

Coordinate: TypeAlias = float | str
Split: TypeAlias = Literal["holdout", "development"]
Match: TypeAlias = Literal["yes", "no", ""]

AGREE_KM: int = 50
ALIAS_POPULATION: Final = 200000
AREA_NAMESAKE_POPULATION: Final = 100000
AREA_WORD: Final = re.compile(r"\b(state|province)\b", re.I)
HOLDOUT_PERCENT: Final = 30
MIN_POPULATION: int = 20000
REVIEW_BASES: Final[frozenset[Basis]] = frozenset({"feed", "capital", "none"})

RULE_STEPS: Final = (
    "label, agrees",
    "label",
    "label, conflicts",
    "document",
    "feed",
    "capital",
    "memory",
    "none",
)

SAINT: Final = re.compile(r"^st ")

_STOP: Final = set(
    (
        "central east west north south northeast northwest southeast southwest us usa eu europe asia pacific africa america americas "
        + "middle cloud region zone coverage china mainland the and of new n s e w la af ap tr me cn au ru uk sa na"
    ).split()
)


class GazetteerCity(TypedDict):
    """One GeoNames city: its name, country, first-level area, population and coordinates."""

    name: str
    country: str
    admin1: str
    population: int
    lat: float
    lon: float


class Decision(TypedDict):
    """The place the rules chose for a region, with its basis and cross-check."""

    basis: Basis
    cross_check: CrossCheck
    name: str
    lat: Coordinate
    lon: Coordinate
    country: str


class PlacerRow(TypedDict):
    """One region's placement by rule, with what each signal said and how it compares with the reference."""

    provider: ProviderId
    region_id: RegionId
    basis: Basis
    cross_check: CrossCheck
    basis_note: NotRequired[str]
    city: str
    country: str
    lat: Coordinate
    lon: Coordinate
    label_city: str
    id_city: str
    feed_city: str
    from_capital: Literal["yes", ""]
    split: Split
    curated_city: NotRequired[str]
    km_to_curated: NotRequired[int | Literal[""]]
    matches_curated: NotRequired[Match]


class SplitScore(TypedDict):
    """How many regions in a group were placed and how many match the reference."""

    regions: int
    placed: int
    matched: int
    abstained: int
    accuracy: float | None


class StepScore(TypedDict):
    """Regions and matches for one step of the rules."""

    regions: int
    matched: int


class OverturnedScore(TypedDict):
    """How the rules treat the placements that evidence overturned."""

    total: int
    flagged: int
    accepted_matching: int
    accepted_as_label: int


class PlacerSummary(TypedDict):
    """The rules' scores by split and by step, with the overturned placements."""

    development: SplitScore
    holdout: SplitScore
    all: SplitScore
    by_step: dict[str, StepScore]
    overturned: OverturnedScore


@dataclass(frozen=True)
class City:
    """A city in the gazetteer."""

    name: str
    country: str
    admin1: str
    population: int
    lat: float
    lon: float


@dataclass
class Gazetteer:
    """Cities indexed by name, with aliases, area names, country names, US states and capitals."""

    cities: Sequence[GazetteerCity]
    aliases: dict[str, str] = field(default_factory=dict[str, str])
    areas: dict[str, str] = field(default_factory=dict[str, str])
    countries: dict[str, str] = field(default_factory=dict[str, str])
    states: dict[str, str] = field(default_factory=dict[str, str])
    capitals: dict[str, str] = field(default_factory=dict[str, str])
    index: dict[str, list[City]] = field(init=False, default_factory=dict[str, list[City]])
    squeezed: dict[str, list[City]] = field(init=False, default_factory=dict[str, list[City]])

    def __post_init__(self) -> None:
        """Index cities by normalised name, by alias and by name with spaces removed."""
        for raw in self.cities:
            item = City(raw["name"], raw["country"], raw["admin1"], raw["population"], raw["lat"], raw["lon"])
            self.index.setdefault(_norm(item.name), []).append(item)
        for alias, name in self.aliases.items():
            self.index.setdefault(_norm(alias), []).extend(self.index.get(_norm(name), []))
        for key, items in self.index.items():
            self.squeezed[key.replace(" ", "")] = items


def capital_of_label(text: str, gaz: Gazetteer) -> City | None:
    """A label naming only a country places the region at its capital, the coarsest claim a rule can make.

    Args:
        text: The vendor's label.
        gaz: Gazetteer of cities.

    Returns:
        The capital, or None when the label names more than a country.
    """
    bracket = re.search(r"\(([^)]*)\)", text)
    code = gaz.countries.get(_norm(bracket[1] if bracket else text))
    name = gaz.capitals.get(code, "") if code else ""
    return _allowed(gaz.index.get(_norm(name), []), None, code) if name else None


def capital_of_tail(text: str, gaz: Gazetteer) -> City | None:
    """A label ending in a country names at least that country, so its capital is the last resort.

    Args:
        text: The vendor's label.
        gaz: Gazetteer of cities.

    Returns:
        The capital, or None when the label ends in no country.
    """
    code = gaz.countries.get(_norm(text.split(",")[-1])) if "," in text else None
    name = gaz.capitals.get(code, "") if code else ""
    return _allowed(gaz.index.get(_norm(name), []), None, code) if name else None


def compare_to_reference(
    rows: list[PlacerRow], reference: Mapping[RegionKey, ReferencePlacement]
) -> list[PlacerRow]:
    """Scores only: the reference is an earlier hand placement and feeds no table.

    Args:
        rows: Placer rows; changed in place.
        reference: Reference placement per region.

    Returns:
        The rows, with the comparison columns filled in.
    """
    for row in rows:
        known = reference.get((row["provider"], row["region_id"]))
        row["curated_city"] = known["city"] if known else ""
        row["km_to_curated"], row["matches_curated"] = "", ""
        lat, lon = row["lat"], row["lon"]
        if known and row["city"] and not isinstance(lat, str) and not isinstance(lon, str):
            km = haversine_km((lat, lon), (float(known["lat"]), float(known["lon"])))
            row["km_to_curated"], row["matches_curated"] = round(km), "yes" if km <= AGREE_KM else "no"
    return rows


def decide(label: Signal | None, region: Signal | None, feed: Signal | None) -> Decision:
    """Choose a place and say how far the signals support it.

    Args:
        label: City named by the vendor's label, if any.
        region: City named by the region ID, if any.
        feed: Feed city for the region, if any.

    Returns:
        The decision.
    """
    text = label or region
    signals = [s for s in (label, region, feed) if s]
    chosen = text or feed
    if not signals or chosen is None:
        return {"basis": "none", "cross_check": "", "name": "", "lat": "", "lon": "", "country": ""}
    basis: Basis = "label" if text else "feed"
    cross_check: CrossCheck
    if not all(_close(chosen, s) for s in signals):
        cross_check = "conflicts"
    elif len(signals) >= 2:
        cross_check = "agrees"
    else:
        cross_check = ""
    return {
        "basis": basis,
        "cross_check": cross_check,
        "name": chosen["name"],
        "lat": chosen["lat"],
        "lon": chosen["lon"],
        "country": chosen.get("country", ""),
    }


def feed_points(raw: StrPath) -> dict[RegionKey, Signal]:
    """The feed city of each region as a named point, where it holds at least the minimum share.

    Args:
        raw: The data/raw folder.

    Returns:
        Named points keyed by vendor and region.
    """
    lookup = feeds.make_lookup(raw)
    points: dict[RegionKey, Signal] = {}
    for key, hit in feeds.load_region_hits(raw).items():
        point = feeds.feed_point(hit, lookup)
        if point and hit.share >= feeds.MIN_SHARE:
            points[key] = {"name": hit.city, "lat": point[0], "lon": point[1], "country": hit.country}
    return points


def id_city(region_id: str, gaz: Gazetteer) -> City | None:
    """The city a region ID names, from its words with digits and common terms removed.

    Args:
        region_id: Region identifier.
        gaz: Gazetteer of cities.

    Returns:
        The city, or None.
    """
    words = [
        w
        for w in re.sub(r"\d+", " ", region_id).replace("-", " ").lower().split()
        if w not in _STOP and len(w) >= 4
    ]
    for word in words:
        item = _allowed(gaz.squeezed.get(word, []), None, None)
        if item and word not in gaz.areas and item.population >= MIN_POPULATION:
            return item
    return None


def in_holdout(provider: str, region_id: str) -> bool:
    """Whether a region falls in the fixed 30% split, by a hash of its vendor and ID.

    Args:
        provider: Vendor identifier.
        region_id: Region identifier.

    Returns:
        True for the 30% group.
    """
    digest = hashlib.sha256(f"ocm-regions-holdout-v1/{provider}/{region_id}".encode()).digest()
    return digest[0] * 100 / 256 < HOLDOUT_PERCENT


def label_city(text: str, gaz: Gazetteer) -> City | None:
    """Find the city a vendor label names: brackets first, then the lead of a comma address, then anywhere.

    Args:
        text: The vendor's label.
        gaz: Gazetteer of cities.

    Returns:
        The city, or None.
    """
    if gaz.areas.get(_norm(text)) == "US" or AREA_WORD.search(text):
        return None
    state, country = _hints(text, gaz)
    bracket = re.search(r"\(([^)]*)\)", text)
    if bracket and _norm(bracket[1]) in gaz.areas and _norm(bracket[1]) not in gaz.index:
        return None
    if bracket and (hit := _search(bracket[1], gaz, state, country, False)):
        return hit
    if "," in text and (hit := _search(text.split(",")[0], gaz, state, country, True)):
        return hit
    return _search(text.replace("-", " "), gaz, state, country, not bracket and "," not in text)


def load_gazetteer(raw: StrPath) -> Gazetteer:
    """Load GeoNames cities, aliases of large cities, area names, country names, US states and capitals.

    Args:
        raw: The data/raw folder.

    Returns:
        The gazetteer.
    """
    raw = Path(raw)
    with zipfile.ZipFile(raw / "geonames/cities500.zip") as archive:
        lines = archive.read("cities500.txt").decode().splitlines()
    cities: list[GazetteerCity] = []
    aliases: dict[str, str] = {}
    for line in lines:
        f = line.split("\t")
        population = int(f[14] or 0)
        cities.append(
            {
                "name": f[1],
                "country": f[8],
                "admin1": f[10],
                "population": population,
                "lat": float(f[4]),
                "lon": float(f[5]),
            }
        )
        if population >= ALIAS_POPULATION:
            for alias in f[3].split(","):
                if alias.isascii() and len(alias) >= 4:
                    _ = aliases.setdefault(alias, f[1])
    areas: dict[str, str] = {}
    states: dict[str, str] = {}
    countries: dict[str, str] = {"uk": "GB"}
    capitals: dict[str, str] = {}
    for line in (raw / "geonames/admin1CodesASCII.txt").read_text(encoding="utf-8").splitlines():
        code, _name, ascii_name, _id = line.split("\t")
        cc, admin = code.split(".")
        areas[_norm(ascii_name)] = cc
        if cc == "US":
            states[admin.lower()] = admin
    for line in (raw / "geonames/countryInfo.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            f = line.split("\t")
            areas[_norm(f[4])] = countries[_norm(f[4])] = countries[f[0].lower()] = f[0]
            capitals[f[0]] = f[5]
    return Gazetteer(cities, aliases, areas, countries, states, capitals)


def needs_review(row: Mapping[str, object]) -> bool:
    """A conflict, a feed, a capital or nothing: placements a person should look at first.

    Args:
        row: Placer row.

    Returns:
        True when the placement needs review.
    """
    return row.get("cross_check") == "conflicts" or row["basis"] in REVIEW_BASES


def place_regions(
    regions: Iterable[Region], gaz: Gazetteer, feed_points: Mapping[RegionKey, Signal]
) -> list[PlacerRow]:
    """Place every region by rule from its label, its ID, and its feed or the capital of a country-only label.

    Args:
        regions: Published regions.
        gaz: Gazetteer of cities.
        feed_points: Feed city per region.

    Returns:
        One row per region.
    """
    rows: list[PlacerRow] = []
    for region in regions:
        key = (region.provider, region.region_id)
        label = _as_signal(label_city(region.stated_location, gaz) or label_city(region.name, gaz))
        from_id = _as_signal(id_city(region.region_id, gaz))
        real_feed = feed_points.get(key)
        feed = real_feed or _as_signal(capital_of_label(region.stated_location, gaz))
        result = decide(label, from_id, feed)
        basis: Basis = "capital" if result["basis"] == "feed" and not real_feed else result["basis"]
        rows.append(
            {
                "provider": key[0],
                "region_id": key[1],
                "basis": basis,
                "cross_check": result["cross_check"],
                "city": result["name"],
                "country": result["country"],
                "lat": result["lat"],
                "lon": result["lon"],
                "label_city": label["name"] if label else "",
                "id_city": from_id["name"] if from_id else "",
                "feed_city": feed["name"] if feed else "",
                "from_capital": "yes" if feed and not real_feed else "",
                "split": "holdout" if in_holdout(*key) else "development",
            }
        )
    return rows


def placer_macros(summary: PlacerSummary) -> dict[str, object]:
    """Paper macros for the placer's scores.

    Args:
        summary: Scores of the rules.

    Returns:
        Macro values keyed by name.
    """
    none: StepScore = {"regions": 0, "matched": 0}
    agreed, held, every, overturned = (
        summary["by_step"].get("label, agrees", none),
        summary["holdout"],
        summary["all"],
        summary["overturned"],
    )
    return {
        "placerRegions": every["regions"],
        "placerPlaced": every["placed"],
        "placerAbstained": every["abstained"],
        "placerAccuracy": _percent(every["accuracy"]),
        "placerHoldoutRegions": held["regions"],
        "placerHoldoutPlaced": held["placed"],
        "placerHoldoutAccuracy": _percent(held["accuracy"]),
        "placerDevAccuracy": _percent(summary["development"]["accuracy"]),
        "placerAgreedRegions": agreed["regions"],
        "placerAgreedMatched": agreed["matched"],
        "placerOverturnedTotal": overturned["total"],
        "placerOverturnedFlagged": overturned["flagged"],
        "placerOverturnedAccepted": overturned["accepted_as_label"],
        "placerOverturnedMatching": overturned["accepted_matching"],
        **_range_macros("placerHoldout", held["matched"], held["placed"]),
        **_range_macros("placerAll", every["matched"], every["placed"]),
    }


def run_placer(
    raw: StrPath, inputs: StrPath, output: StrPath, paper: StrPath | None = None
) -> tuple[list[PlacerRow], PlacerSummary]:
    """Place every region by rule, score against the reference, and write the rows, summary and paper macros.

    Args:
        raw: The data/raw folder.
        inputs: Repository root that holds the input folders.
        output: Output folder.
        paper: The paper folder; nothing is written there when None.

    Returns:
        The rows and the summary.
    """
    raw, inputs, output = Path(raw), Path(inputs), Path(output)
    rows = place_regions(data.load_regions(raw), load_gazetteer(raw), feed_points(raw))
    _ = compare_to_reference(rows, data.load_reference(inputs))
    overturned = [
        (ProviderId(r["provider"]), RegionId(r["region_id"]))
        for r in data.read_csv(layout.overturned(inputs))
    ]
    summary = score_placements(rows, overturned)
    _write(output / "placer.csv", rows)
    _ = (output / "placer_summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    if paper:
        _ = (Path(paper) / "generated" / "placer_macros.tex").write_text(
            macros_tex(placer_macros(summary)), encoding="utf-8"
        )
    return rows, summary


def score_placements(rows: Sequence[PlacerRow], overturned: Collection[RegionKey]) -> PlacerSummary:
    """Score the rules by split and by step, and count how they treat overturned placements.

    Args:
        rows: Placer rows.
        overturned: Regions whose first placement evidence overturned.

    Returns:
        The summary.
    """
    steps: dict[str, StepScore] = {}
    for row in rows:
        score = steps.setdefault(step(row), {"regions": 0, "matched": 0})
        score["regions"] += 1
        score["matched"] += row.get("matches_curated") == "yes"
    known = [r for r in rows if (r["provider"], r["region_id"]) in set(overturned)]
    return {
        "development": _split_score([r for r in rows if r["split"] == "development"]),
        "holdout": _split_score([r for r in rows if r["split"] == "holdout"]),
        "all": _split_score(rows),
        "by_step": steps,
        "overturned": {
            "total": len(known),
            "flagged": sum(needs_review(r) for r in known),
            "accepted_matching": sum(
                not needs_review(r) and r.get("matches_curated") == "yes" for r in known
            ),
            "accepted_as_label": sum(not needs_review(r) and r.get("matches_curated") == "no" for r in known),
        },
    }


def step(row: Mapping[str, object]) -> str:
    """The basis with its cross-check, as one name for scoring and ordering.

    Args:
        row: Placer row.

    Returns:
        The step name.
    """
    return f"{row['basis']}, {row['cross_check']}" if row.get("cross_check") else str(row["basis"])


def _allowed(items: Iterable[City], state: str | None, country: str | None) -> City | None:
    """The most populous city among candidates that fits the state and country hints.

    Args:
        items: Candidate cities.
        state: US state code hint, if any.
        country: ISO country code hint, if any.

    Returns:
        The city, or None when no candidate fits.
    """
    chosen = [
        c
        for c in items
        if (not state or (c.country == "US" and c.admin1 == state)) and (not country or c.country == country)
    ]
    return max(chosen, key=lambda c: c.population) if chosen else None


def _as_signal(item: City | Signal | None) -> Signal | None:
    """A city as a named point, leaving a point or None unchanged.

    Args:
        item: City, named point or nothing.

    Returns:
        The named point, or None.
    """
    return (
        {"name": item.name, "lat": item.lat, "lon": item.lon, "country": item.country}
        if isinstance(item, City)
        else item
    )


def _close(a: Signal, b: Signal) -> bool:
    """Whether two signals lie within the agreement radius.

    Args:
        a: First named point.
        b: Second named point.

    Returns:
        True when they agree.
    """
    return haversine_km((a["lat"], a["lon"]), (b["lat"], b["lon"])) <= AGREE_KM


def _hint_pairs(state: str | None, country: str | None) -> list[tuple[str | None, str | None]]:
    """A two-letter code can be a state and a country, so try each meaning in turn.

    Args:
        state: US state code hint, if any.
        country: ISO country code hint, if any.

    Returns:
        State and country pairs to try, in order.
    """
    return [(state, None), (None, country)] if state and country else [(state, country)]


def _hints(text: str, gaz: Gazetteer) -> tuple[str | None, str | None]:
    """The US state and the country named after a comma in a label, if any.

    Args:
        text: The vendor's label.
        gaz: Gazetteer of cities.

    Returns:
        The state code and the country code, each None when absent.
    """
    tail = [_norm(p) for p in text.split(",")[1:]]
    state = next((gaz.states[t] for t in tail if t in gaz.states), None)
    country = next((gaz.countries[t] for t in tail if t in gaz.countries), None)
    return state, country


def _norm(text: str) -> str:
    """Lower-case ASCII letters and single spaces only, so names compare whatever their accents or punctuation.

    Args:
        text: Name to normalise.

    Returns:
        The normalised name.
    """
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z ]+", " ", ascii_text).split())


def _percent(share: float | None) -> str:
    """A share as a percentage with one decimal; raises when nothing was placed.

    Args:
        share: Share between 0 and 1, if any.

    Returns:
        The percentage as text.

    Raises:
        ValueError: If nothing was placed.
    """
    if share is None:
        raise ValueError("no placements to score")
    return f"{share * 100:.1f}"


def _range_macros(name: str, matched: int, placed: int) -> dict[str, str]:
    """Paper macros for the Wilson interval of a match rate.

    Args:
        name: Start of each macro name.
        matched: Number of placements that match the reference.
        placed: Number of regions placed.

    Returns:
        Macro values keyed by name.
    """
    low, high = wilson(matched, placed)
    return {f"{name}Low": f"{low * 100:.1f}", f"{name}High": f"{high * 100:.1f}"}


def _search(
    segment: str, gaz: Gazetteer, state: str | None, country: str | None, leading: bool
) -> City | None:
    """Search a label segment for a city, trying a state hint and a country hint in turn.

    Args:
        segment: Part of the label between commas.
        gaz: Gazetteer of cities.
        state: US state code hint, if any.
        country: ISO country code hint, if any.
        leading: Whether the segment is the first in the label.

    Returns:
        The city, or None.
    """
    for hint_state, hint_country in _hint_pairs(state, country):
        if hit := _search_with(segment, gaz, hint_state, hint_country, leading):
            return hit
    return None


def _search_with(
    segment: str, gaz: Gazetteer, state: str | None, country: str | None, leading: bool
) -> City | None:
    """Find the longest run of up to four words in a segment that names a usable city.

    Args:
        segment: Part of the label between commas.
        gaz: Gazetteer of cities.
        state: US state code hint, if any.
        country: ISO country code hint, if any.
        leading: Whether the segment is the first in the label.

    Returns:
        The city, or None.
    """
    words = _norm(segment).split()
    for size in range(min(4, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            gram = " ".join(words[start : start + size])
            if size == 1 and (gram in _STOP or len(gram) < 4):
                continue
            item = _allowed(
                gaz.index.get(gram) or gaz.index.get(SAINT.sub("saint ", gram), []), state, country
            )
            if item and _usable(
                item,
                gram,
                gaz,
                leading and gram == _norm(segment),
                bool((state or country) and leading and gram == _norm(segment)),
            ):
                return item
    return None


def _split_score(all_rows: Iterable[PlacerRow]) -> SplitScore:
    """Score the regions of one group against the reference.

    Args:
        all_rows: Placer rows of every region.

    Returns:
        The score.
    """
    rows = [r for r in all_rows if r.get("curated_city")]
    placed = [r for r in rows if r.get("matches_curated")]
    matched = sum(r.get("matches_curated") == "yes" for r in placed)
    return {
        "regions": len(rows),
        "placed": len(placed),
        "matched": matched,
        "abstained": len(rows) - len(placed),
        "accuracy": round(matched / len(placed), 3) if placed else None,
    }


def _usable(item: City, gram: str, gaz: Gazetteer, leading: bool, hinted: bool = False) -> bool:
    """Whether a matched city may stand for the words: not a country or area namesake, and large enough.

    Args:
        item: Gazetteer city.
        gram: Words that matched the city.
        gaz: Gazetteer of cities.
        leading: Whether the segment is the first in the label.
        hinted: Whether a state or country hint narrowed the match.

    Returns:
        True when the city may be used.
    """
    area = gaz.areas.get(gram)
    if gram in gaz.countries and _norm(item.name) != gram:
        return False
    if hinted:
        return True
    if area and not (item.country == area and item.population >= AREA_NAMESAKE_POPULATION):
        return False
    return leading or item.population >= MIN_POPULATION


def _write(path: StrPath, rows: Sequence[PlacerRow]) -> None:
    """Write placer rows to CSV.

    Args:
        path: CSV file to write.
        rows: Placer rows.
    """
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


norm = _norm


allowed = _allowed


usable = _usable


as_signal = _as_signal
