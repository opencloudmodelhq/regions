# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Find the latest Internet Archive capture of every source, so a rerun can read a fixed copy."""

import csv
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final, Literal, TypeAlias, TypedDict, cast

from ocm_regions.fetch.sources import StrPath, client_headers

Lookup: TypeAlias = Callable[[str], str]
SourcePair: TypeAlias = tuple[str, str]

CDX: Final = "https://web.archive.org/cdx/search/cdx?"
FIELDS: Final = ("provider", "url", "status", "timestamp", "digest", "archive_url")


class Capture(TypedDict):
    """The timestamp and digest of one web archive capture."""

    timestamp: str
    digest: str


class SnapshotRow(Capture):
    """The latest archive capture found for one source URL."""

    provider: str
    url: str
    status: Literal["captured", "not-captured", "lookup-failed"]
    archive_url: str


def cdx_lookup(url: str) -> str:
    """Ask the web archive's CDX index for the successful captures of a URL.

    Args:
        url: URL of the document.

    Returns:
        The index reply as text.
    """
    query = urllib.parse.urlencode(
        {"url": url, "fl": "timestamp,original,statuscode,digest", "filter": "statuscode:200", "limit": -1}
    )
    request = urllib.request.Request(CDX + query, headers=client_headers())
    with urllib.request.urlopen(request, timeout=60) as response:
        return cast(bytes, response.read()).decode("utf-8", errors="replace")


def collect_urls(
    evidence: Iterable[Mapping[str, object]], documents: Iterable[Mapping[str, object]]
) -> list[SourcePair]:
    """Every distinct vendor and URL pair cited by evidence or listed as a document.

    Args:
        evidence: Evidence rows.
        documents: Indexed documents.

    Returns:
        The pairs, without repeats.
    """
    pairs: dict[SourcePair, None] = {
        (str(r["provider"]), str(r["url"])): None for r in [*evidence, *documents] if r.get("url")
    }
    return list(pairs)


def latest_capture(url: str, lookup: Lookup) -> Capture:
    """The newest capture of a URL, or blank fields when there is none.

    Args:
        url: URL of the document.
        lookup: Function that looks up the latest archive capture.

    Returns:
        The capture's timestamp and URL.
    """
    lines = [line.split() for line in lookup(url).splitlines() if line.strip()]
    if not lines:
        return {"timestamp": "", "digest": ""}
    newest = max(lines, key=lambda parts: parts[0])
    return {"timestamp": newest[0], "digest": newest[-1]}


def patient_lookup(
    lookup: Lookup = cdx_lookup,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 4,
    wait: float = 15.0,
) -> Lookup:
    """Wait longer after each failure, since the archive throttles scripted lookups.

    Args:
        lookup: Function that looks up the latest archive capture.
        sleep: Function that waits a number of seconds.
        attempts: Number of tries before giving up.
        wait: Seconds to wait before the first retry.

    Returns:
        A lookup that retries.
    """

    def run(url: str) -> str:
        """Look up one URL, retrying with a longer wait after each failure.

        Args:
            url: URL of the document.

        Returns:
            The index reply as text.
        """
        for attempt in range(attempts - 1):
            try:
                return lookup(url)
            except OSError:
                sleep(wait * 2**attempt)
        return lookup(url)

    return run


def retry_failed(
    previous: Iterable[SnapshotRow], output: StrPath, lookup: Lookup | None = None, pause: float = 5.0
) -> list[SnapshotRow]:
    """Look up again only the sources that have no capture yet.

    Args:
        previous: Rows from the last lookup.
        output: Output folder.
        lookup: Function that looks up the latest archive capture; the default when None.
        pause: Seconds to wait between requests.

    Returns:
        Every snapshot row, with the retried ones updated.
    """
    lookup = lookup or patient_lookup()
    rows: list[SnapshotRow] = []
    for row in previous:
        if row["status"] != "captured":
            row = _snapshot_row(row["provider"], row["url"], lookup)
            time.sleep(pause)
        rows.append(row)
    return _write(rows, output)


def write_snapshots(
    pairs: Iterable[SourcePair],
    output: StrPath,
    lookup: Lookup | None = None,
    pause: float = 3.0,
    previous: Sequence[SnapshotRow] = (),
) -> list[SnapshotRow]:
    """Look up every source; a failed lookup keeps the capture already known.

    Args:
        pairs: URL and vendor pairs to look up.
        output: Output folder.
        lookup: Function that looks up the latest archive capture; the default when None.
        pause: Seconds to wait between requests.
        previous: Rows from the last lookup.

    Returns:
        One snapshot row per source.
    """
    lookup = lookup or patient_lookup()
    known = {r["url"]: r for r in previous if r["status"] == "captured"}
    rows: list[SnapshotRow] = []
    for provider, url in pairs:
        row = _snapshot_row(provider, url, lookup)
        rows.append(known.get(url, row) if row["status"] == "lookup-failed" else row)
        time.sleep(pause)
    return _write(rows, output)


def _snapshot_row(provider: str, url: str, lookup: Lookup) -> SnapshotRow:
    """Look up one URL and report whether it was captured.

    Args:
        provider: Vendor identifier.
        url: URL of the document.
        lookup: Function that looks up the latest archive capture.

    Returns:
        The snapshot row.
    """
    try:
        capture = latest_capture(url, lookup)
    except (OSError, ValueError):
        return {
            "provider": provider,
            "url": url,
            "status": "lookup-failed",
            "timestamp": "",
            "digest": "",
            "archive_url": "",
        }
    if not capture["timestamp"]:
        return {
            "provider": provider,
            "url": url,
            "status": "not-captured",
            "timestamp": capture["timestamp"],
            "digest": capture["digest"],
            "archive_url": "",
        }
    return {
        "provider": provider,
        "url": url,
        "status": "captured",
        "timestamp": capture["timestamp"],
        "digest": capture["digest"],
        "archive_url": f"https://web.archive.org/web/{capture['timestamp']}id_/{url}",
    }


def _write(rows: list[SnapshotRow], output: StrPath) -> list[SnapshotRow]:
    """Write snapshot rows to CSV and return them.

    Args:
        rows: Snapshot rows.
        output: Output folder.

    Returns:
        The rows written.
    """
    with Path(output).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter[str](handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows
