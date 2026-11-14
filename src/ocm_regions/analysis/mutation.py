# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Measure the quote check: break quotes on purpose and count how many still pass."""

import random
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Final, TypedDict

from ocm_regions.common.stats import wilson
from ocm_regions.evidence.verify import normalize as _normalize
from ocm_regions.place.placer import norm as _norm

LENGTHS: Final = ((20, "under 20"), (50, "20 to 49"), (10**9, "50 or more"))
Outcome = tuple[str, bool]


class LengthTally(TypedDict):
    """Records and false passes for quotes in one length band."""

    records: int
    false_pass: int


class Tally(TypedDict):
    """False passes among mutated records, with the Wilson interval."""

    records: int
    false_pass: int
    share: float
    low: float
    high: float
    by_length: dict[str, LengthTally]


class Control(TypedDict):
    """Unchanged records and how many pass the quote check."""

    records: int
    passed: int


class MutationResult(TypedDict):
    """The control and the tally for each kind of mutation."""

    control: Control
    other_quote: Tally
    other_city: Tally


def run_mutation(
    records: Sequence[Mapping[str, str]], read_page: Callable[[str], str | None], seed: int = 20261105
) -> MutationResult:
    """Control: real quotes. Other quote: a quote from another source. Other city: the right quote with the wrong city.

    Args:
        records: Evidence records.
        read_page: Function that returns the saved page for a URL.
        seed: Seed of the random number generator.

    Returns:
        The control and both tallies.
    """
    rng = random.Random(seed)
    pages = {r["url"]: _normalize(text) for r in records if (text := read_page(r["url"]))}
    usable = [r for r in records if r["url"] in pages and r["quote"].strip()]
    control = [r for r in usable if _found(r["quote"], pages[r["url"]])]
    cities = sorted({_city_of(r) for r in control if _city_of(r)})
    other_quote: list[Outcome] = []
    other_city: list[Outcome] = []
    for record in control:
        pool = [
            r for r in control if r["url"] != record["url"] and _norm(_city_of(r)) != _norm(_city_of(record))
        ]
        if pool:
            quote = rng.choice(pool)["quote"]
            other_quote.append((quote, _found(quote, pages[record["url"]])))
        names = [c for c in cities if _norm(c) != _norm(_city_of(record))]
        swapped = _swap_city(record, rng.choice(names)) if names else None
        if swapped:
            other_city.append((swapped, _found(swapped, pages[record["url"]])))
    return {
        "control": {"records": len(usable), "passed": len(control)},
        "other_quote": _tally(other_quote),
        "other_city": _tally(other_city),
    }


def _bucket(quote: str) -> str:
    """The length band a quote falls in.

    Args:
        quote: Quoted text.

    Returns:
        The band's label.
    """
    return next(label for limit, label in LENGTHS if len(quote) < limit)


def _city_of(record: Mapping[str, str]) -> str:
    """The city an evidence record claims, from the start of its value.

    Args:
        record: Evidence record.

    Returns:
        The city name.
    """
    return record["value"].split(",")[0].strip()


def _empty() -> LengthTally:
    """A length tally at zero.

    Returns:
        The tally.
    """
    return {"records": 0, "false_pass": 0}


def _found(quote: str, page: str) -> bool:
    """Whether a non-empty quote appears in an already normalised page.

    Args:
        quote: Quoted text.
        page: Normalised text of the page.

    Returns:
        True when the quote is found.
    """
    return bool(_normalize(quote)) and _normalize(quote) in page


def _swap_city(record: Mapping[str, str], others: str) -> str | None:
    """Replace the record's city in its quote, or return None if the quote doesn't name it.

    Args:
        record: Evidence record.
        others: Text to take the replacement city from.

    Returns:
        The changed quote, or None.
    """
    city = _city_of(record)
    start = record["quote"].lower().find(city.lower()) if city else -1
    if start < 0 or not others:
        return None
    return record["quote"][:start] + others + record["quote"][start + len(city) :]


def _tally(results: Sequence[Outcome]) -> Tally:
    """Count the mutated records that passed, with the Wilson interval.

    Args:
        results: Quote and pass flag for each mutated record.

    Returns:
        The tally.
    """
    passed = sum(hit for _quote, hit in results)
    low, high = wilson(passed, len(results))
    by_length: defaultdict[str, LengthTally] = defaultdict(_empty)
    for quote, hit in results:
        by_length[_bucket(quote)]["records"] += 1
        by_length[_bucket(quote)]["false_pass"] += hit
    return {
        "records": len(results),
        "false_pass": passed,
        "share": passed / len(results) if results else 0.0,
        "low": low,
        "high": high,
        "by_length": dict(by_length),
    }
