# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Shapes of the cap sweep's rows and summary."""

from typing import NotRequired, TypeAlias, TypedDict

from ocm_regions.common.schema import IataCode, Kilometres, Precision, ProviderId, RegionId

Pair: TypeAlias = tuple[int, int, Kilometres]
SweepRow: TypeAlias = dict[str, float]
NamedRow: TypeAlias = dict[str, float | str | None]
PairGroup: TypeAlias = tuple[frozenset[int], float]
Split: TypeAlias = tuple[str, str, str, str, int]


class SiteRow(TypedDict):
    """A region row as the cap sweep reads it."""

    provider: ProviderId
    region_id: RegionId
    city: str
    precision: Precision
    lat: float | str
    lon: float | str
    iata_nearest_airport: IataCode
    iata_major_hub: IataCode
    country: str


class StableRange(TypedDict):
    """A run of caps that give the same pair counts."""

    signature: tuple[float, float]
    low: float
    high: float


class Selection(TypedDict):
    """The selected cap, the interval it was taken from, and the F1 peak."""

    cap: float
    low: float
    high: float
    midpoint: float
    peak: float
    peak_f1: float


class Band(TypedDict):
    """Regions moved in one band of hub distance, with the correct and false merges added."""

    low: float
    high: int
    moved: int
    good: int
    bad: int


class BootstrapRange(TypedDict):
    """Percentiles of the best cap over bootstrap resamples."""

    p5: float
    p50: float
    p95: float


class StabilityRow(TypedDict):
    """The best cap for one subset of regions."""

    test: str
    regions: int
    cap: float


class Bounds(TypedDict):
    """The low and high end of an interval of caps."""

    low: float
    high: float


class MergeInterval(Bounds):
    """The interval of caps that add no false merge, with its pair counts."""

    pairs_low: float
    pairs_high: float
    false_merges: float


class ResampleSummary(TypedDict):
    """How the selected cap behaves over resamples."""

    runs: int
    caps: list[float]
    share_selected: float
    median: float
    low: float
    high: float
    lower_median: float
    lower_p5: float
    lower_p95: float
    upper_median: float
    upper_p5: float
    upper_p95: float
    inside_interval: float
    interval_holds_selected: float


class SiteRecord(TypedDict):
    """A site as written to the summary."""

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
    country: str


class RuleStability(TypedDict):
    """How the selection rule holds when vendors are left out or regions resampled."""

    selected: float
    runs: int
    caps: list[float]
    share_selected: float
    median: float
    low: float
    high: float
    regions: ResampleSummary
    city: ResampleSummary
    interval: NotRequired[MergeInterval]
    without: dict[ProviderId, float]


class RadiusRow(BootstrapRange):
    """The F1 peak, selected cap and bootstrap range at one market radius."""

    rho: int
    peak: float
    selected: float


class MetroStats(TypedDict):
    """The number of metro links and percentiles of their length."""

    links: int
    p95: float
    p99: float


class CapSummary(TypedDict):
    """Everything the cap analysis reports."""

    rho: int
    regions: int
    sweep: list[SweepRow]
    selection: Selection
    named: list[NamedRow]
    bands: list[Band]
    radii: list[RadiusRow]
    stability: list[StabilityRow]
    rule_stability: RuleStability
    metro: MetroStats
    off_hub: list[SiteRecord]
    splits: list[list[str | int]]
    border_splits: int
