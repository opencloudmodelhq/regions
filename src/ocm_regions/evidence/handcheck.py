# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Blank sheets for a hand check of the tables, made without showing the answers."""

import csv
import hashlib
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final, TypeAlias

from ocm_regions.fetch.sources import StrPath

SheetRow: TypeAlias = dict[str, str]
SheetKey: TypeAlias = tuple[str, str]

REGION_FIELDS: Final = ["sheet_id", "group", "provider", "region_id", "region_name", "stated_location"]

PASS_ONE_FIELDS: Final = [
    *REGION_FIELDS,
    "corpus_urls",
    "city",
    "country",
    "url_used",
    "quote",
    "status",
    "notes",
]

PASS_TWO_FIELDS: Final = [
    *REGION_FIELDS,
    "search_queries",
    "city",
    "country",
    "url",
    "quote",
    "source_kind",
    "status",
    "notes",
]

SEED: Final = "ocm-regions-handcheck-v1"
SIZE: Final = 100


def pass_one_rows(chosen: Iterable[Mapping[str, str]]) -> list[SheetRow]:
    """Blank rows for checking each sampled region against the corpus.

    Args:
        chosen: Sampled regions.

    Returns:
        The blank rows.
    """
    blank = dict.fromkeys(PASS_ONE_FIELDS[len(REGION_FIELDS) + 1 :], "")
    return [
        _region_part(r)
        | {"corpus_urls": " ".join(filter(None, r.get("evidence_urls", "").split(";")))}
        | blank
        for r in chosen
    ]


def pass_two_rows(chosen: Iterable[Mapping[str, str]]) -> list[SheetRow]:
    """Blank rows for an independent search on each sampled region.

    Args:
        chosen: Sampled regions.

    Returns:
        The blank rows.
    """
    return [_region_part(r) | dict.fromkeys(PASS_TWO_FIELDS[len(REGION_FIELDS) :], "") for r in chosen]


def sample(
    regions: Iterable[SheetRow], weak: Collection[SheetKey], size: int = SIZE, seed: str = SEED
) -> list[SheetRow]:
    """Every weak region, then the rest in a fixed hash order, so any prefix of the random group is random.

    Args:
        regions: Region rows.
        weak: Regions flagged for review.
        size: Number of regions to draw beyond the weak ones.
        seed: Seed text for the hash order.

    Returns:
        The sampled rows, each marked with its group.
    """
    listed = list(regions)
    chosen = [r | {"group": "weak"} for r in sorted(listed, key=_key) if _key(r) in weak]
    rest = sorted((r for r in listed if _key(r) not in weak), key=lambda r: _draw(r, seed))
    chosen += [r | {"group": "random"} for r in rest[: max(size - len(chosen), 0)]]
    return [r | {"sheet_id": f"hc{n:03d}"} for n, r in enumerate(chosen, 1)]


def write_sheets(
    regions_csv: StrPath, weak_csv: StrPath, out_dir: StrPath, size: int = SIZE, seed: str = SEED
) -> list[SheetRow]:
    """Draw the sample and write both blank review sheets.

    Args:
        regions_csv: Path of regions.csv.
        weak_csv: Path of weak_evidence.csv.
        out_dir: Folder to write into.
        size: Number of regions to draw beyond the weak ones.
        seed: Seed text for the hash order.

    Returns:
        The sampled rows.
    """
    weak = {(r["provider"], r["region_id"]) for r in _read(weak_csv)}
    chosen = sample(_read(regions_csv), weak, size, seed)
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / "pass1_corpus.csv", PASS_ONE_FIELDS, pass_one_rows(chosen))
    _write(folder / "pass2_independent.csv", PASS_TWO_FIELDS, pass_two_rows(chosen))
    return chosen


def _draw(row: Mapping[str, str], seed: str) -> str:
    """A fixed hash that orders the random part of the sample.

    Args:
        row: Sheet row.
        seed: Seed text for the hash order.

    Returns:
        The hex digest used as the sort key.
    """
    return hashlib.sha256(f"{seed}|{row['provider']}|{row['region_id']}".encode()).hexdigest()


def _key(row: Mapping[str, str]) -> SheetKey:
    """The vendor and region of a sheet row.

    Args:
        row: Sheet row.

    Returns:
        The vendor and region ID.
    """
    return row["provider"], row["region_id"]


def _read(path: StrPath) -> list[SheetRow]:
    """Read a CSV sheet as rows.

    Args:
        path: CSV sheet to read.

    Returns:
        The sheet rows.
    """
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _region_part(row: Mapping[str, str]) -> SheetRow:
    """The columns of a sheet row that identify the region.

    Args:
        row: Sheet row.

    Returns:
        The identifying columns.
    """
    return {field: row.get(field, "") for field in REGION_FIELDS}


def _write(path: Path, fields: Sequence[str], rows: Iterable[SheetRow]) -> None:
    """Write sheet rows to CSV with the given columns.

    Args:
        path: CSV sheet to write.
        fields: Column names, in order.
        rows: Sheet rows.
    """
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
