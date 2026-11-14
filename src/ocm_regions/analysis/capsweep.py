# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Sweep the distance cap that blends nearest-airport and market-hub codes."""

import csv
import io
import json
import random
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Final, cast

from ocm_regions.analysis.captypes import (
    Band,
    BootstrapRange,
    Bounds,
    CapSummary,
    MergeInterval,
    MetroStats,
    NamedRow,
    Pair,
    PairGroup,
    RadiusRow,
    ResampleSummary,
    RuleStability,
    Selection,
    SiteRecord,
    SiteRow,
    Split,
    StabilityRow,
    StableRange,
    SweepRow,
)
from ocm_regions.common import data
from ocm_regions.common.geo import Airport, haversine_km
from ocm_regions.common.schema import IataCode, Kilometres, Partition, Precision, ProviderId, RegionId
from ocm_regions.fetch.sources import StrPath

BOOTSTRAP_RUNS: Final = 1000
RADII: Final = (50, 75, 100, 150, 200)
RESTRICTED_PARTITIONS: Final[frozenset[Partition]] = frozenset(data.EXCLUDED_PARTITIONS)
RULE_RUNS: Final = 200
SELECTION_RADIUS: Final = 100


@dataclass(frozen=True)
class Site:
    """A placed region with its nearest airport, its major hub and the distance to each."""

    provider: ProviderId
    region_id: RegionId
    city: str
    precision: Precision
    lat: float
    lon: float
    nearest: str
    hub: str
    nearest_km: float
    hub_km: float
    country: str = ""


def bands(
    sites: Sequence[Site], pairs: Sequence[Pair], rho: int, width: int = 25, top: int = 350
) -> list[Band]:
    """Count regions moved to their hub in each band of hub distance, with the merges each band adds.

    Args:
        sites: Placed sites.
        pairs: Cross-vendor site pairs with distances.
        rho: Market radius in kilometres.
        width: Width of each band in kilometres.
        top: Upper end of the last band in kilometres.

    Returns:
        One band per width step up to the top.
    """
    result: list[Band] = []
    previous = 0.0
    was = {(i, j) for i, j, _ in pairs if code_at(sites[i], 0.0) == code_at(sites[j], 0.0)}
    for high in range(width, top + 1, width):
        now = {(i, j) for i, j, _ in pairs if code_at(sites[i], high) == code_at(sites[j], high)}
        new = [km for i, j, km in pairs if (i, j) in now - was]
        moved = sum(previous < s.hub_km <= high and s.nearest != s.hub for s in sites)
        result.append(
            {
                "low": previous,
                "high": high,
                "moved": moved,
                "good": sum(km <= rho for km in new),
                "bad": sum(km > rho for km in new),
            }
        )
        was, previous = now, float(high)
    return result


def best_cap(rows: Iterable[SweepRow], rho: int) -> Kilometres:
    """The cap with the highest F1 at a market radius; the lowest such cap on a tie.

    Args:
        rows: Sweep rows.
        rho: Market radius in kilometres.

    Returns:
        The cap in kilometres.
    """
    return max(rows, key=lambda r: (r[f"f1{rho}"], -r["cap"]))["cap"]


def bootstrap_best(sites: Sequence[Site], rho: int, runs: int = 1000, seed: int = 20261104) -> BootstrapRange:
    """The 5th, 50th and 95th percentile of the best cap over bootstrap resamples of the sites.

    Args:
        sites: Placed sites.
        rho: Market radius in kilometres.
        runs: Number of resamples.
        seed: Seed of the random number generator.

    Returns:
        The three percentiles.
    """
    pairs = cross_pairs(sites)
    all_near, all_groups = _shared_groups(sites, pairs, rho)
    keep = set(all_near).union(*(shared for shared, _ in all_groups))
    index = {old: new for new, old in enumerate(sorted(keep))}
    kept = [pairs[old] for old in sorted(keep)]
    near = {index[k] for k in all_near}
    groups: list[PairGroup] = [(frozenset(index[k] for k in shared), cap) for shared, cap in all_groups]
    generator = random.Random(seed)
    picks: list[float] = []
    for _ in range(runs):
        counts = Counter(generator.choices(range(len(sites)), k=len(sites)))
        picks.append(_resample_best(counts, kept, near, groups))
    ordered = sorted(picks)
    return {
        "p5": ordered[int(0.05 * (runs - 1))],
        "p50": ordered[runs // 2],
        "p95": ordered[int(0.95 * (runs - 1))],
    }


def border_split_count(sites: Sequence[Site], pairs: Iterable[Pair], cap: Kilometres, rho: int) -> int:
    """The number of site pairs within the market radius that get different codes.

    Args:
        sites: Placed sites.
        pairs: Cross-vendor site pairs with distances.
        cap: Distance cap in kilometres for using the hub.
        rho: Market radius in kilometres.

    Returns:
        The count.
    """
    return sum(
        km <= rho
        and code_at(sites[i], cap) != code_at(sites[j], cap)
        and sites[i].country != sites[j].country
        for i, j, km in pairs
    )


def candidate_caps(sites: Iterable[Site]) -> list[Kilometres]:
    """Zero and every hub distance where a site's hub differs from its nearest airport, sorted.

    Args:
        sites: Placed sites.

    Returns:
        The caps in kilometres, ascending.
    """
    return sorted({0.0} | {s.hub_km for s in sites if s.nearest != s.hub})


def city_sample(sites: Iterable[Site], rng: random.Random) -> list[Site]:
    """Draw whole cities with replacement, since regions in one city are not independent.

    Args:
        sites: Placed sites.
        rng: Random number generator.

    Returns:
        The resampled sites.
    """
    cities: dict[tuple[str, str], list[Site]] = {}
    for site in sites:
        cities.setdefault((site.country, site.city), []).append(site)
    names = sorted(cities)
    return [site for name in rng.choices(names, k=len(names)) for site in cities[name]]


def code_at(site: Site, cap: Kilometres) -> str:
    """The code a site gets under a cap: the hub within the cap, else the nearest airport.

    Args:
        site: Placed site.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The IATA code.
    """
    return site.hub if site.hub_km <= cap else site.nearest


def cross_pairs(sites: Sequence[Site]) -> list[Pair]:
    """Every pair of sites from different vendors, with the distance between them.

    Args:
        sites: Placed sites.

    Returns:
        Each pair as two site indexes and a distance.
    """
    return [
        (i, j, haversine_km((a.lat, a.lon), (b.lat, b.lon)))
        for i, a in enumerate(sites)
        for j, b in enumerate(sites)
        if i < j and a.provider != b.provider
    ]


def interval_holds(interval: Bounds, cap: float) -> bool:
    """Whether a cap falls in the interval, low end included.

    Args:
        interval: Interval of caps.
        cap: Cap to test, in kilometres.

    Returns:
        True when the cap is inside.
    """
    return interval["low"] <= cap < interval["high"]


def km_to_code(site: Site, cap: Kilometres) -> Kilometres:
    """The distance from a site to the code it gets under a cap.

    Args:
        site: Placed site.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The distance in kilometres.
    """
    return site.hub_km if site.hub_km <= cap else site.nearest_km


def load_sites(rows: Iterable[SiteRow], airports: Mapping[IataCode, Airport]) -> list[Site]:
    """Build a site for every row that has coordinates and both airport codes.

    Args:
        rows: Region rows with coordinates and airport codes.
        airports: Airports keyed by IATA code.

    Returns:
        The sites.
    """
    sites: list[Site] = []
    for row in rows:
        if row["lat"] and row["iata_nearest_airport"] and row["iata_major_hub"]:
            point = (float(row["lat"]), float(row["lon"]))
            sites.append(
                Site(
                    row["provider"],
                    row["region_id"],
                    row["city"],
                    row["precision"],
                    *point,
                    row["iata_nearest_airport"],
                    row["iata_major_hub"],
                    haversine_km(point, airports[row["iata_nearest_airport"]].point),
                    haversine_km(point, airports[row["iata_major_hub"]].point),
                    row["country"],
                )
            )
    return sites


def merge_interval(rows: Sequence[SweepRow], rho: int, share: float = 0.99) -> MergeInterval:
    """Caps that add no false merge and keep at least this share of the most correct pairs they can reach.

    A row holds from its cap up to the next one, so the interval ends at the cap that adds the first false merge.

    Args:
        rows: Sweep rows, ordered by cap.
        rho: Market radius in kilometres.
        share: Share of the most correct pairs the interval must keep.

    Returns:
        The interval with its pair counts.
    """
    base = rows[0][f"fp{rho}"]
    added = next((i for i, row in enumerate(rows) if row[f"fp{rho}"] > base), len(rows))
    pairs = rows[added - 1][f"tp{rho}"]
    bottom = next(r for r in rows if r[f"tp{rho}"] >= share * pairs)
    high = rows[added]["cap"] if added < len(rows) else rows[-1]["cap"]
    return {
        "low": bottom["cap"],
        "high": high,
        "pairs_low": bottom[f"tp{rho}"],
        "pairs_high": pairs,
        "false_merges": base,
    }


def metro_link_distances(text: str) -> list[Kilometres]:
    """Distance from each current airport to its OpenTravelData city in the same country.

    Args:
        text: OpenTravelData airport file as text.

    Returns:
        The distances in kilometres.
    """
    rows = list(csv.DictReader(io.StringIO(text), delimiter="^"))
    cities = {r["iata_code"]: r for r in rows if "C" in r["location_type"] and r["latitude"]}
    links: dict[tuple[str, str], Kilometres] = {}
    for row in rows:
        if "A" not in row["location_type"] or not row["latitude"] or row["date_until"]:
            continue
        for code in row["city_code_list"].split(","):
            city = cities.get(code)
            if city and code != row["iata_code"] and city["country_code"] == row["country_code"]:
                links[(row["iata_code"], code)] = haversine_km(
                    (float(row["latitude"]), float(row["longitude"])),
                    (float(city["latitude"]), float(city["longitude"])),
                )
    return list(links.values())


def off_hub(sites: Iterable[Site], cap: Kilometres) -> list[Site]:
    """Sites whose hub lies beyond the cap, so they keep their nearest airport.

    Args:
        sites: Placed sites.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The sites, farthest hub first.
    """
    return sorted((s for s in sites if s.nearest != s.hub and s.hub_km > cap), key=lambda s: -s.hub_km)


def open_rows[Row: Mapping[str, object]](rows: Iterable[Row]) -> list[Row]:
    """Rows outside the restricted partitions.

    Args:
        rows: Rows to filter.

    Returns:
        The rows kept.
    """
    return [r for r in rows if r["partition"] not in RESTRICTED_PARTITIONS]


def percentile(values: Iterable[float], q: float) -> float:
    """The q-th percentile of the values, by linear interpolation.

    Args:
        values: Numbers to summarise.
        q: Percentile between 0 and 100.

    Returns:
        The percentile value.
    """
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def rule_stability(
    sites: Sequence[Site], rho: int, runs: int = RULE_RUNS, seed: int = 20261104
) -> RuleStability:
    """Rerun the selection rule, resampling regions and whole cities, and with each vendor dropped.

    Args:
        sites: Placed sites.
        rho: Market radius in kilometres.
        runs: Number of resamples.
        seed: Seed of the random number generator.

    Returns:
        The stability results.
    """
    full = sweep(sites, (rho,))
    selected, interval = select_cap(full, rho)["cap"], merge_interval(full, rho)
    rng = random.Random(seed)
    regions = _resample_summary(
        [rng.choices(sites, k=len(sites)) for _ in range(runs)], rho, selected, interval
    )
    cities = _resample_summary([city_sample(sites, rng) for _ in range(runs)], rho, selected, interval)
    without = {
        v: select_cap(sweep([s for s in sites if s.provider != v], (rho,)), rho)["cap"]
        for v in sorted({s.provider for s in sites})
    }
    return {
        "selected": selected,
        "runs": regions["runs"],
        "caps": regions["caps"],
        "share_selected": regions["share_selected"],
        "median": regions["median"],
        "low": regions["low"],
        "high": regions["high"],
        "regions": regions,
        "city": cities,
        "interval": interval,
        "without": without,
    }


def run_caps(
    regions_csv: StrPath, raw: StrPath, output_dir: StrPath, paper_dir: StrPath, runs: int = BOOTSTRAP_RUNS
) -> CapSummary:
    """Run the cap analysis on the built regions and write its CSV, summary and paper files.

    Args:
        regions_csv: Path of regions.csv.
        raw: The data/raw folder.
        output_dir: Output folder.
        paper_dir: The paper folder.
        runs: Number of resamples.

    Returns:
        The summary.
    """
    from ocm_regions.analysis.capfigures import write_cap_inputs

    raw, output_dir = Path(raw), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sites = load_sites(open_rows(cast(list[SiteRow], data.read_csv(regions_csv))), data.load_airports(raw))
    distances = metro_link_distances((raw / "opentraveldata/optd_por_public.csv").read_text(encoding="utf-8"))
    result = summarise(sites, distances, runs=runs)
    _write_sweep_csv(output_dir / "cap_sweep.csv", result["sweep"])
    _ = (output_dir / "cap_summary.json").write_text(
        json.dumps(result, indent=1, allow_nan=False) + "\n", encoding="utf-8"
    )
    write_cap_inputs(paper_dir, result)
    return result


def score(sites: Sequence[Site], pairs: Sequence[Pair], cap: Kilometres, radii: Iterable[int]) -> SweepRow:
    """Score one cap: code overlap, distances, and pair measures at each market radius.

    Args:
        sites: Placed sites.
        pairs: Cross-vendor site pairs with distances.
        cap: Distance cap in kilometres for using the hub.
        radii: Market radii in kilometres.

    Returns:
        The sweep row for the cap.
    """
    codes = [code_at(s, cap) for s in sites]
    same = [codes[i] == codes[j] for i, j, _ in pairs]
    result: SweepRow = {"cap": cap, **_overlap(sites, codes), **_distances(sites, cap), "sharing": sum(same)}
    for rho in radii:
        result.update(_radius_scores(same, [km <= rho for _, _, km in pairs], rho))
    return result


def select_cap(rows: Sequence[SweepRow], rho: int, tolerance: float = 0.01, step: int = 10) -> Selection:
    """Take the midpoint of the widest range of near-best caps with unchanged pair counts, rounded to step.

    Args:
        rows: Sweep rows, ordered by cap.
        rho: Market radius in kilometres.
        tolerance: Share of the best F1 a cap may fall short by.
        step: Kilometres the selected cap is rounded to.

    Returns:
        The selected cap with its range and the F1 peak.
    """
    best = max(r[f"f1{rho}"] for r in rows)
    last: StableRange = {"signature": (0.0, 0.0), "low": rows[-1]["cap"], "high": rows[-1]["cap"]}
    widest = max(_stable_ranges(rows, rho, best - tolerance) or [last], key=lambda r: r["high"] - r["low"])
    midpoint = (widest["low"] + widest["high"]) / 2
    rounded = round(midpoint / step) * step
    cap = rounded if widest["low"] <= rounded < widest["high"] else midpoint
    return {
        "cap": cap,
        "low": widest["low"],
        "high": widest["high"],
        "midpoint": midpoint,
        "peak": best_cap(rows, rho),
        "peak_f1": best,
    }


def splits_at(sites: Sequence[Site], pairs: Iterable[Pair], cap: Kilometres, rho: int) -> list[Split]:
    """Pairs of codes that divide sites within the market radius of each other, with counts.

    Args:
        sites: Placed sites.
        pairs: Cross-vendor site pairs with distances.
        cap: Distance cap in kilometres for using the hub.
        rho: Market radius in kilometres.

    Returns:
        The splits, most frequent first.
    """
    counts: Counter[tuple[tuple[str, str], tuple[str, str]]] = Counter()
    for i, j, km in pairs:
        a, b = code_at(sites[i], cap), code_at(sites[j], cap)
        if km <= rho and a != b:
            first, second = sorted([(a, sites[i].city), (b, sites[j].city)])
            counts[(first, second)] += 1
    return sorted(
        ((x[0], x[1], y[0], y[1], n) for (x, y), n in counts.items()), key=lambda row: (-row[4], row)
    )


def stability(sites: Sequence[Site], rho: int) -> list[StabilityRow]:
    """The best cap for all regions, for city-level regions, and with each vendor left out.

    Args:
        sites: Placed sites.
        rho: Market radius in kilometres.

    Returns:
        One row per test.
    """
    rows = [
        _best_for("All regions", sites, rho),
        _best_for("City-level locations only", [s for s in sites if s.precision == "city"], rho),
    ]
    for vendor in sorted({s.provider for s in sites}):
        rows.append(_best_for(f"Without {vendor}", [s for s in sites if s.provider != vendor], rho))
    return rows


def summarise(
    sites: Sequence[Site],
    distances: Sequence[float],
    radii: Sequence[int] = RADII,
    runs: int = BOOTSTRAP_RUNS,
    rho: int = SELECTION_RADIUS,
) -> CapSummary:
    """Sweep the caps, select one, and collect every figure the paper reports on the selection.

    Args:
        sites: Placed sites.
        distances: Lengths of metro links in kilometres.
        radii: Market radii in kilometres.
        runs: Number of resamples.
        rho: Market radius in kilometres.

    Returns:
        The summary.
    """
    pairs = cross_pairs(sites)
    rows = sweep(sites, radii)
    selection = select_cap(rows, rho)
    cap = selection["cap"]
    return {
        "rho": rho,
        "regions": len(sites),
        "sweep": rows,
        "selection": selection,
        "named": [_named_row(sites, pairs, value, label, radii) for value, label in _named_caps(selection)],
        "bands": bands(sites, pairs, rho),
        "radii": _radius_rows(sites, rows, radii, runs),
        "stability": stability(sites, rho),
        "rule_stability": rule_stability(sites, rho, min(runs, RULE_RUNS)),
        "metro": _metro_stats(distances),
        "off_hub": [cast(SiteRecord, asdict(s)) for s in off_hub(sites, cap)],
        "splits": [list(row) for row in splits_at(sites, pairs, cap, rho)],
        "border_splits": border_split_count(sites, pairs, cap, rho),
    }


def sweep(sites: Sequence[Site], radii: Sequence[int]) -> list[SweepRow]:
    """Score every candidate cap.

    Args:
        sites: Placed sites.
        radii: Market radii in kilometres.

    Returns:
        One sweep row per cap, ascending.
    """
    pairs = cross_pairs(sites)
    return [score(sites, pairs, cap, radii) for cap in candidate_caps(sites)]


def table_cap(sites: Sequence[Site]) -> Kilometres:
    """The cap the nearby-hub rule uses: the rule's pick on the open regions' nearest-airport and major-hub codes.

    Args:
        sites: Placed sites.

    Returns:
        The cap in kilometres.
    """
    return select_cap(sweep(sites, RADII), SELECTION_RADIUS)["cap"]


def _best_for(label: str, sites: Sequence[Site], rho: int) -> StabilityRow:
    """The best cap for one subset of sites, as a stability row.

    Args:
        label: Name of the subset.
        sites: Placed sites.
        rho: Market radius in kilometres.

    Returns:
        The stability row.
    """
    return {"test": label, "regions": len(sites), "cap": best_cap(sweep(sites, (rho,)), rho)}


def _distances(sites: Iterable[Site], cap: Kilometres) -> dict[str, float]:
    """The mean and 95th percentile distance from a site to its code under a cap.

    Args:
        sites: Placed sites.
        cap: Distance cap in kilometres for using the hub.

    Returns:
        The two distances, keyed by name.
    """
    values = [km_to_code(s, cap) for s in sites]
    return {"mean_km": sum(values) / len(values), "p95_km": percentile(values, 95), "max_km": max(values)}


def _metro_stats(distances: Sequence[float]) -> MetroStats:
    """The number of metro links and the 95th and 99th percentile of their length.

    Args:
        distances: Lengths of metro links in kilometres.

    Returns:
        The statistics.
    """
    return {"links": len(distances), "p95": percentile(distances, 95), "p99": percentile(distances, 99)}


def _named_caps(selection: Selection) -> list[tuple[float, str]]:
    """The caps the paper names: nearest airport, 50 km, the selected cap, the F1 peak and major hub.

    Args:
        selection: Selected cap with its interval.

    Returns:
        Each cap in kilometres with its label.
    """
    peak = selection["peak"]
    named = [
        (0.0, "0 (nearest airport)"),
        (50.0, "50 km"),
        (selection["cap"], f"{selection['cap']:.0f} km (rule)"),
        (peak, f"{peak:.0f} km (peak)"),
        (150.0, "150 km"),
        (200.0, "200 km"),
        (float("inf"), "None (major hub)"),
    ]
    return sorted(named, key=lambda item: item[0])


def _named_row(
    sites: Sequence[Site], pairs: Sequence[Pair], value: float, label: str, radii: Iterable[int]
) -> NamedRow:
    """The scores of one named cap.

    Args:
        sites: Placed sites.
        pairs: Cross-vendor site pairs with distances.
        value: Cap in kilometres; infinity for the major hub.
        label: Name of the cap.
        radii: Market radii in kilometres.

    Returns:
        The row.
    """
    row: NamedRow = {"label": label, **score(sites, pairs, value, radii)}
    row["cap"] = value if value != float("inf") else None
    return row


def _overlap(sites: Iterable[Site], codes: Sequence[str]) -> dict[str, int]:
    """The number of distinct codes and of codes shared by more than one vendor.

    Args:
        sites: Placed sites.
        codes: Codes, in the same order as the sites or as audited.

    Returns:
        The two counts, keyed by name.
    """
    vendors: defaultdict[str, set[str]] = defaultdict(set)
    for site, code in zip(sites, codes, strict=True):
        vendors[code].add(site.provider)
    multi = {code for code, names in vendors.items() if len(names) >= 2}
    return {"distinct": len(vendors), "multi": len(multi), "shared": sum(code in multi for code in codes)}


def _radius_rows(
    sites: Sequence[Site], rows: Sequence[SweepRow], radii: Iterable[int], runs: int
) -> list[RadiusRow]:
    """The F1 peak, selected cap and bootstrap range for each market radius.

    Args:
        sites: Placed sites.
        rows: Sweep rows, ordered by cap.
        radii: Market radii in kilometres.
        runs: Number of resamples.

    Returns:
        One row per radius.
    """
    result: list[RadiusRow] = []
    for rho in radii:
        interval = bootstrap_best(sites, rho, runs=runs)
        result.append(
            {
                "rho": rho,
                "peak": best_cap(rows, rho),
                "selected": select_cap(rows, rho)["cap"],
                "p5": interval["p5"],
                "p50": interval["p50"],
                "p95": interval["p95"],
            }
        )
    return result


def _radius_scores(same: Sequence[bool], near_flags: Sequence[bool], rho: int) -> dict[str, float]:
    """Pair counts, recall, precision and F1 at one market radius.

    Args:
        same: Whether each pair shares a code.
        near_flags: Whether each pair lies within the market radius.
        rho: Market radius in kilometres.

    Returns:
        The measures, keyed by name with the radius as suffix.
    """
    tp = sum(1 for s, n in zip(same, near_flags, strict=True) if s and n)
    fp = sum(1 for s, n in zip(same, near_flags, strict=True) if s and not n)
    fn = sum(near_flags) - tp
    recall = tp / (tp + fn) if tp + fn else 1.0
    precision = tp / (tp + fp) if tp + fp else 1.0
    f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
    return {
        f"tp{rho}": tp,
        f"fp{rho}": fp,
        f"recall{rho}": recall,
        f"precision{rho}": precision,
        f"f1{rho}": f1,
    }


def _resample_best(
    weights: Mapping[int, int], pairs: Sequence[Pair], near: Collection[int], groups: Iterable[PairGroup]
) -> Kilometres:
    """The best cap for one bootstrap resample, given how often each site was drawn.

    Args:
        weights: Number of times each site was drawn.
        pairs: Cross-vendor site pairs with distances.
        near: Indexes of pairs within the market radius.
        groups: Groups of pairs that share a hub distance.

    Returns:
        The cap in kilometres.
    """
    pair_weight = {k: weights[i] * weights[j] for k, (i, j, _) in enumerate(pairs)}
    total_near = sum(pair_weight[k] for k in near)
    best, chosen = -1.0, 0.0
    for shared, cap in groups:
        tp = sum(pair_weight[k] for k in shared if k in near)
        fp = sum(pair_weight[k] for k in shared if k not in near)
        f1 = 2 * tp / (tp + fp + total_near) if tp else 0.0
        if f1 > best + 1e-12:
            best, chosen = f1, cap
    return chosen


def _resample_summary(
    samples: Sequence[Sequence[Site]], rho: int, selected: float, interval: Bounds
) -> ResampleSummary:
    """How often resamples select the same cap, and how often their cap stays in the merge interval.

    Args:
        samples: Resampled sets of sites.
        rho: Market radius in kilometres.
        selected: Cap selected on all regions.
        interval: Interval of caps.

    Returns:
        The summary.
    """
    sweeps = [sweep(sample, (rho,)) for sample in samples]
    caps = [select_cap(rows, rho)["cap"] for rows in sweeps]
    edges = [merge_interval(rows, rho) for rows in sweeps]
    lows, highs = [e["low"] for e in edges], [e["high"] for e in edges]
    return {
        "runs": len(samples),
        "caps": caps,
        "share_selected": sum(c == selected for c in caps) / len(caps),
        "median": percentile(caps, 50),
        "low": percentile(caps, 5),
        "high": percentile(caps, 95),
        "lower_median": percentile(lows, 50),
        "lower_p5": percentile(lows, 5),
        "lower_p95": percentile(lows, 95),
        "upper_median": percentile(highs, 50),
        "upper_p5": percentile(highs, 5),
        "upper_p95": percentile(highs, 95),
        "inside_interval": sum(interval_holds(interval, c) for c in caps) / len(caps),
        "interval_holds_selected": sum(interval_holds(e, selected) for e in edges) / len(edges),
    }


def _shared_groups(
    sites: Sequence[Site], pairs: Sequence[Pair], rho: int
) -> tuple[set[int], list[PairGroup]]:
    """Group caps that share the same pairs, keeping the lowest cap, so resampling repeats less work.

    Args:
        sites: Placed sites.
        pairs: Cross-vendor site pairs with distances.
        rho: Market radius in kilometres.

    Returns:
        The indexes of pairs within the radius, and the groups.
    """
    near = {k for k, (_, _, km) in enumerate(pairs) if km <= rho}
    groups: dict[frozenset[int], float] = {}
    for cap in candidate_caps(sites):
        shared = frozenset(
            k for k, (i, j, _) in enumerate(pairs) if code_at(sites[i], cap) == code_at(sites[j], cap)
        )
        _ = groups.setdefault(shared, cap)
    return near, sorted(groups.items(), key=lambda item: item[1])


def _stable_ranges(rows: Sequence[SweepRow], rho: int, floor: float) -> list[StableRange]:
    """Runs of consecutive caps that give the same pair counts.

    Args:
        rows: Sweep rows, ordered by cap.
        rho: Market radius in kilometres.
        floor: Lowest score a range must keep.

    Returns:
        The ranges, in cap order.
    """
    ranges: list[StableRange] = []
    for row, after in pairwise(rows):
        signature = (row[f"tp{rho}"], row[f"fp{rho}"])
        if row[f"f1{rho}"] < floor:
            continue
        if ranges and ranges[-1]["signature"] == signature and ranges[-1]["high"] == row["cap"]:
            ranges[-1]["high"] = after["cap"]
        else:
            ranges.append({"signature": signature, "low": row["cap"], "high": after["cap"]})
    return ranges


def _write_sweep_csv(path: StrPath, rows: Sequence[SweepRow]) -> None:
    """Write the sweep rows to CSV.

    Args:
        path: CSV file to write.
        rows: Sweep rows, ordered by cap.
    """
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(
            {k: round(v, 4) if isinstance(v, float) else v for k, v in row.items()} for row in rows
        )
