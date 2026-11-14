# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Pick one city per region from extracted evidence, by several methods, and score them alike."""

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Final, Required, TypedDict, cast

from ocm_regions.analysis.mutation import LengthTally, MutationResult
from ocm_regions.common.geo import haversine_km
from ocm_regions.common.schema import Point, ProviderId, RegionId, RegionKey
from ocm_regions.common.stats import wilson
from ocm_regions.evidence.extract import FoundRow
from ocm_regions.evidence.scripted import MethodGrades
from ocm_regions.fetch.materials import IndexRow
from ocm_regions.model.llm import city_level_quote
from ocm_regions.place.placer import AGREE_KM

SMOOTHING: Final = 0.5


class Pick(TypedDict, total=False):
    """The one city a method chose for a region."""

    provider: Required[ProviderId]
    region_id: Required[RegionId]
    city: Required[str]
    country: str
    lat: Required[float | str]
    lon: Required[float | str]
    document: str
    quote: Required[str]
    matched: str
    score: float | str


class MethodScore(TypedDict):
    """Answers and matches for one extraction method, with the Wilson interval."""

    regions: int
    answered: int
    matched: int
    low: float
    high: float


def contribution_macros(
    grades: MethodGrades, risk: Iterable[Mapping[str, object]], corpus: Sequence[IndexRow]
) -> dict[str, int]:
    """Paper numbers for what the local model adds, how many codes are at risk, and the corpus.

    Args:
        grades: Grades with and without the model's answers.
        risk: Area risk rows.
        corpus: Indexed documents.

    Returns:
        Macro values keyed by name.
    """
    status = {(r["provider"], r["region_id"]): r["status"] for r in risk}
    return {
        "areaRiskSafe": sum(v == "code-safe" for v in status.values()),
        "areaRiskAtRisk": sum(v == "code-at-risk" for v in status.values()),
        "corpusDocuments": len(corpus),
        "corpusFetched": sum(r["fetch"] == "yes" for r in corpus),
        "modelGainRegions": len(grades["regions_gaining"]),
        "vendorDocWithModel": grades["with_model"].get("vendor-document", 0),
        "vendorDocWithoutModel": grades["without_model"].get("vendor-document", 0),
    }


def method_macros(scores: Mapping[str, MethodScore], mutation: MutationResult) -> dict[str, object]:
    """Paper numbers for the extraction methods, the quote check's false passes and the exceptions on record.

    Args:
        scores: Score per method.
        mutation: Result of the mutation test.

    Returns:
        Macro values keyed by name.
    """
    values: dict[str, object] = {
        "mutationControl": mutation["control"]["passed"],
        "mutationControlTotal": mutation["control"]["records"],
    }
    for item, prefix in (
        (mutation["other_quote"], "mutationQuote"),
        (mutation["other_city"], "mutationCity"),
    ):
        values |= {
            f"{prefix}Records": item["records"],
            f"{prefix}False": item["false_pass"],
            f"{prefix}Share": f"{item['share'] * 100:.1f}",
            f"{prefix}Low": f"{item['low'] * 100:.1f}",
            f"{prefix}High": f"{item['high'] * 100:.1f}",
        }
    none: LengthTally = {"records": 0, "false_pass": 0}
    short, long_ = (
        mutation["other_city"]["by_length"].get("under 20", none),
        mutation["other_city"]["by_length"].get("50 or more", none),
    )
    values |= {
        "mutationShortFalse": short.get("false_pass", 0),
        "mutationShortRecords": short.get("records", 0),
        "mutationLongFalse": long_.get("false_pass", 0),
        "mutationLongRecords": long_.get("records", 0),
    }
    for name, prefix in (
        ("traditional", "extractTraditional"),
        ("statistical", "extractStatistical"),
        ("chain-qwen3.8/rag", "extractRag"),
        ("chain-qwen3.6/rag", "extractRagQwenSix"),
        ("chain-glm-4.7/rag", "extractRagGlm"),
        ("chain-qwen3.8/label", "extractLabel"),
    ):
        if name in scores:
            values |= _score_macros(prefix, scores[name])
    return values


def pick_log_odds(rows: Sequence[FoundRow]) -> dict[RegionKey, Pick]:
    """Statistical: the city most over-represented for this region against the vendor's other regions.

    Args:
        rows: Lines that name a region and a city.

    Returns:
        Picks keyed by vendor and region.
    """
    vendor_city: Counter[tuple[str, str]] = Counter()
    vendor_total: Counter[str] = Counter()
    for row in rows:
        vendor_city[(row["provider"], row["city"])] += 1
        vendor_total[row["provider"]] += 1
    picks: dict[RegionKey, Pick] = {}
    for key, found in _by_region(rows).items():
        counts = Counter(r["city"] for r in found)
        total, everything = len(found), vendor_total[key[0]]
        scores = {
            c: _log_odds(n, total, vendor_city[(key[0], c)] - n, everything - total)
            for c, n in counts.items()
        }
        picks[key] = _pick(found, scores)
    return picks


def pick_most_common(rows: Iterable[FoundRow]) -> dict[RegionKey, Pick]:
    """Traditional: the city named most often on lines naming the region.

    Args:
        rows: Lines that name a region and a city.

    Returns:
        Picks keyed by vendor and region.
    """
    return {key: _pick(found, Counter(r["city"] for r in found)) for key, found in _by_region(rows).items()}


def picks_from_model(rows: Iterable[Mapping[str, str]]) -> dict[RegionKey, Pick]:
    """The model's recorded picks, keyed by region.

    Args:
        rows: Rows to read.

    Returns:
        Picks keyed by vendor and region.
    """
    return {
        (ProviderId(r["provider"]), RegionId(r["region_id"])): cast(
            Pick,
            {
                **r,
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "document": r.get("document", ""),
                "score": "",
            },
        )
        for r in rows
        if r["status"] == "answered" and r["lat"] != ""
    }


def public_rows(picks: Mapping[RegionKey, Pick]) -> list[dict[str, object]]:
    """Picks as rows for the published CSV.

    Args:
        picks: City picked per region.

    Returns:
        The rows.
    """
    return [
        {
            "provider": k[0],
            "region_id": k[1],
            "city": p["city"],
            "country": p.get("country", ""),
            "score": p.get("score", ""),
            "document": p.get("document", ""),
            "quote": city_level_quote(p["quote"][:160], p["city"]),
        }
        for k, p in sorted(picks.items())
    ]


def score_method(picks: Mapping[RegionKey, Pick], places: Mapping[RegionKey, Point]) -> MethodScore:
    """Agreement with the current placements, on the regions in scope; agreement is not accuracy.

    Args:
        picks: City picked per region.
        places: Reference coordinates per region.

    Returns:
        The score.
    """
    answered = [k for k in picks if k in places]
    matched = sum(
        haversine_km((float(picks[k]["lat"]), float(picks[k]["lon"])), places[k]) <= AGREE_KM
        for k in answered
    )
    low, high = wilson(matched, len(answered))
    return {"regions": len(places), "answered": len(answered), "matched": matched, "low": low, "high": high}


def _by_region(rows: Iterable[FoundRow]) -> defaultdict[RegionKey, list[FoundRow]]:
    """Found rows grouped by region.

    Args:
        rows: Lines that name a region and a city.

    Returns:
        Rows keyed by vendor and region.
    """
    grouped: defaultdict[RegionKey, list[FoundRow]] = defaultdict(list)
    for row in rows:
        grouped[(row["provider"], row["region_id"])].append(row)
    return grouped


def _log_odds(count: int, region_total: int, other: int, others_total: int) -> float:
    """Smoothed log odds that a city appears with this region and not with the vendor's other regions.

    Args:
        count: Lines that name both the city and the region.
        region_total: Lines that name the region.
        other: Lines that name the city with the vendor's other regions.
        others_total: Lines that name the vendor's other regions.

    Returns:
        The log odds.
    """
    inside = (count + SMOOTHING) / (region_total - count + SMOOTHING)
    outside = (other + SMOOTHING) / (others_total - other + SMOOTHING)
    return math.log(inside / outside)


def _pick(key_rows: Iterable[FoundRow], scores: Mapping[str, float]) -> Pick:
    """The highest-scoring city for a region, with the shortest line that names it.

    Args:
        key_rows: Found rows of one region.
        scores: Score per city.

    Returns:
        The pick.
    """
    best = max(sorted(scores), key=lambda city: scores[city])
    chosen = min((r for r in key_rows if r["city"] == best), key=lambda r: (r["document"], r["quote"]))
    return {**chosen, "score": round(scores[best], 4)}


def _score_macros(prefix: str, score: MethodScore) -> dict[str, object]:
    """Paper macros for one extraction method's score.

    Args:
        prefix: Start of each macro name.
        score: Score of the method.

    Returns:
        Macro values keyed by name.
    """
    return {
        f"{prefix}Answered": score["answered"],
        f"{prefix}Matched": score["matched"],
        f"{prefix}Low": f"{score['low'] * 100:.0f}",
        f"{prefix}High": f"{score['high'] * 100:.0f}",
    }
