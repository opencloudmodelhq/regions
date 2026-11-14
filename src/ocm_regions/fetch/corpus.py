# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""The corpus is the URL index. Files a run cannot download come from a folder of a person's own downloads."""

import csv
import hashlib
import shutil
import time
import urllib.request
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final, Literal, TypeAlias, TypedDict, cast

from ocm_regions.fetch.materials import IndexRow, text_hash
from ocm_regions.fetch.materials import media_from_suffix as _media_from_suffix
from ocm_regions.fetch.materials import suffix_for as _suffix
from ocm_regions.fetch.sources import ManifestRow, StrPath, client_headers

Origin: TypeAlias = Literal["document", "dataset"]
InstallStatus: TypeAlias = Literal["invalid", "present", "restored", "copied", "changed", "missing"]

ARCHIVE_FIELDS: Final = ["url", "status", "detail"]
ARCHIVE_HOST: Final = "web.archive.org"
ARCHIVE_PAUSE: Final = 15.0
NEEDS_A_PERSON: Final[tuple[InstallStatus, ...]] = ("missing", "changed")
REPORT_FIELDS: Final = ["path", "status", "origin", "url", "archive_url"]
SAVE_PAGE: Final = "https://web.archive.org/save/"


class CorpusRow(TypedDict):
    """One file the corpus expects under data/raw, with its source and pinned hashes."""

    path: str
    url: str
    sha256: str
    text_sha256: str
    origin: Origin
    archive_url: str


class InstallReport(TypedDict):
    """What happened to one corpus file when installing: present, copied, changed or missing."""

    path: str
    status: InstallStatus
    origin: Origin
    url: str
    archive_url: str


class ArchiveReport(TypedDict):
    """The outcome of asking the web archive to capture one URL."""

    url: str
    status: Literal["requested", "failed"]
    detail: str


def archive_requests(
    materials: Iterable[IndexRow], get: Callable[[str], object] | None = None, pause: float = ARCHIVE_PAUSE
) -> list[ArchiveReport]:
    """Ask for a capture of each indexed document with none; a refusal is reported and the rest continue.

    Args:
        materials: Rows of the URL index.
        get: Function that requests a URL; ``_save_page`` when None.
        pause: Seconds to wait between requests.

    Returns:
        One report row per document asked for.
    """
    get = get or _save_page
    wanted = [
        m["url"]
        for m in materials
        if m["fetch"] == "yes"
        and not m["archive_url"]
        and m.get("kind") != "directory"
        and ARCHIVE_HOST not in m["url"]
    ]
    report: list[ArchiveReport] = []
    for url in wanted:
        report.append(_archive_request(url, get))
        time.sleep(pause)
    return report


def corpus_rows(materials: Iterable[IndexRow], datasets: Iterable[ManifestRow]) -> list[CorpusRow]:
    """List every file the corpus expects: fetchable documents first, then datasets.

    Args:
        materials: Rows of the URL index.
        datasets: Rows of the dataset list.

    Returns:
        One row per expected file.
    """
    documents: list[CorpusRow] = [
        {
            "path": f"trust_docs/{m['provider']}/fetched/{m['doc_id']}{_suffix(m['media_type'], m['url'])}",
            "url": m["url"],
            "sha256": m["sha256"],
            "text_sha256": m.get("text_sha256", ""),
            "origin": "document",
            "archive_url": m["archive_url"],
        }
        for m in materials
        if m["fetch"] == "yes"
    ]
    listed: list[CorpusRow] = [
        {
            "path": d["path"],
            "url": d["url"],
            "sha256": d["sha256"],
            "text_sha256": "",
            "origin": "dataset",
            "archive_url": "",
        }
        for d in datasets
    ]
    return documents + listed


def export_downloads(
    rows: Iterable[CorpusRow], raw: StrPath, folder: StrPath, paths: Collection[str]
) -> None:
    """Stand in for a person downloading files: one folder, with names the corpus does not expect.

    Args:
        rows: Corpus rows.
        raw: The data/raw folder.
        folder: Folder to copy the files into.
        paths: Relative paths to export.
    """
    target = Path(folder)
    target.mkdir(parents=True, exist_ok=True)
    for row in rows:
        saved = _saved(row, Path(raw) / row["path"]) if _inside(raw, row["path"]) else None
        if row["path"] in paths and saved:
            _ = shutil.copyfile(
                saved, target / f"{(row['sha256'] or _sha256(saved))[:12]}-{Path(row['path']).name}"
            )


def install_missing(
    rows: Iterable[CorpusRow], raw: StrPath, downloads_folder: StrPath
) -> list[InstallReport]:
    """Match by hash, so a file's name in the downloads does not matter; a pinned copy wins over a changed page.

    Args:
        rows: Corpus rows.
        raw: The data/raw folder.
        downloads_folder: Folder that holds hand-downloaded files.

    Returns:
        One report row per corpus file.
    """
    downloads = _downloads_by_hash(downloads_folder)
    return [
        {
            "path": r["path"],
            "status": _install_one(r, raw, downloads),
            "origin": r["origin"],
            "url": r["url"],
            "archive_url": r["archive_url"],
        }
        for r in rows
    ]


def missing_notice(report: Iterable[InstallReport], refused: Collection[str] = ()) -> list[str]:
    """What the reader must fetch by hand, and where a copy can be had.

    Args:
        report: Results of installing hand-downloaded files.
        refused: URLs a robots.txt file disallows; these are never asked of a person.

    Returns:
        Lines to print; empty when nothing is missing.
    """
    return [
        f"{r['status']}: {r['url']} (archive: {r['archive_url'] or 'no archive copy'})"
        for r in report
        if r["status"] in NEEDS_A_PERSON and r["url"] not in refused
    ]


def wanted_by_person(report: Iterable[InstallReport]) -> set[str]:
    """Paths of files only a person can supply: missing or changed.

    Args:
        report: Results of installing hand-downloaded files.

    Returns:
        Relative paths of the files to supply.
    """
    return {r["path"] for r in report if r["status"] in NEEDS_A_PERSON}


def write_rows(rows: Iterable[Mapping[str, object]], path: StrPath, fields: Sequence[str]) -> None:
    """Write rows to a CSV file with the given columns.

    Args:
        rows: Rows to write or read.
        path: CSV file to write.
        fields: Column names, in order.
    """
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _archive_request(url: str, get: Callable[[str], object]) -> ArchiveReport:
    """Request one capture and report whether the request was accepted.

    Args:
        url: URL of the document.
        get: Function that requests a URL.

    Returns:
        The report row for the request.
    """
    try:
        return {"url": url, "status": "requested", "detail": str(get(SAVE_PAGE + url))}
    except OSError as exc:
        return {"url": url, "status": "failed", "detail": str(exc)[:120]}


def _copy(source: Path, target: Path) -> None:
    """Copy a file to its target, creating the folder first.

    Args:
        source: File to copy.
        target: File to write.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(source, target)


def _downloads_by_hash(folder: StrPath) -> dict[str, Path]:
    """Index every file in a downloads folder by its SHA-256.

    Args:
        folder: Folder that holds hand-downloaded files.

    Returns:
        File paths keyed by hex digest.
    """
    root = Path(folder)
    if not root.is_dir():
        return {}
    return {_sha256(p): p for p in sorted(root.rglob("*")) if p.is_file()}


def _inside(raw: StrPath, relative: str) -> bool:
    """Whether a relative path stays inside the raw folder, so a row cannot write elsewhere.

    Args:
        raw: The data/raw folder.
        relative: Path relative to the folder.

    Returns:
        True when the path stays inside.
    """
    root = Path(raw).resolve()
    return root in (root / relative).resolve().parents


def _install_one(row: CorpusRow, raw: StrPath, downloads: Mapping[str, Path]) -> InstallStatus:
    """Install one corpus file from the downloads if needed, and say what was done.

    Args:
        row: Corpus row.
        raw: The data/raw folder.
        downloads: Hand-downloaded files keyed by hash.

    Returns:
        What was done with the file.
    """
    if not _inside(raw, row["path"]):
        return "invalid"
    target = Path(raw) / row["path"]
    saved = _saved(row, target)
    source = downloads.get(row["sha256"])
    if saved and _matches_pin(row, saved):
        return "present"
    if source:
        _copy(source, saved or target)
        return "restored" if saved else "copied"
    return "changed" if saved else "missing"


def _matches_pin(row: CorpusRow, saved: Path) -> bool:
    """Whether a saved file matches its pinned hash, or its pinned text hash.

    Args:
        row: Corpus row.
        saved: Saved file.

    Returns:
        True when either hash matches.
    """
    if not row["sha256"]:
        return True
    body = saved.read_bytes()
    if hashlib.sha256(body).hexdigest() == row["sha256"]:
        return True
    return bool(row.get("text_sha256")) and text_hash(body, _media_from_suffix(saved)) == row["text_sha256"]


def _save_page(url: str) -> int:
    """Ask the web archive to capture a URL and return the HTTP status.

    Args:
        url: URL of the document.

    Returns:
        The HTTP status code.
    """
    request = urllib.request.Request(url, headers=client_headers())
    with urllib.request.urlopen(request, timeout=180) as response:
        return cast(int, response.status)


def _saved(row: CorpusRow, target: Path) -> Path | None:
    """The saved copy of a corpus file, allowing a document to carry any suffix.

    Args:
        row: Corpus row.
        target: File to write.

    Returns:
        The path of the saved copy, or None when there is none.
    """
    if target.exists():
        return target
    if row["origin"] != "document" or not target.parent.is_dir():
        return None
    return next(iter(sorted(target.parent.glob(f"{target.stem}.*"))), None)


def _sha256(path: Path) -> str:
    """SHA-256 of a file's bytes, as hex.

    Args:
        path: File to hash.

    Returns:
        The hex digest.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()
