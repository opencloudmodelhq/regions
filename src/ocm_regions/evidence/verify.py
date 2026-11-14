# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Re-check that collected evidence quotes still appear at their URLs."""

import csv
import html
import http.client
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from functools import cache
from pathlib import Path
from typing import Final, Literal, TypeAlias, TypedDict, cast

from ocm_regions.common import data
from ocm_regions.common.schema import EvidenceId, EvidenceRecord, QuoteCheck, SourceType
from ocm_regions.fetch import robots
from ocm_regions.fetch.sources import StrPath, client_headers, ungzipped

Fetched: TypeAlias = tuple[str, bytes]
Fetch: TypeAlias = Callable[[str], Fetched]
SavedText: TypeAlias = Callable[[EvidenceRecord], str]
PageState: TypeAlias = Literal["unreachable", "format-not-checked", "read"]

GENERATED_SOURCES: Final[tuple[SourceType, ...]] = ("vendor_region_list", "peeringdb")
HTML_TYPES: Final = ("html", "text", "json", "csv")


class CheckResult(TypedDict):
    """The outcome of checking one quote."""

    status: QuoteCheck
    detail: str


class VerificationRow(CheckResult):
    """The check result for one evidence record, as written to CSV."""

    evidence_id: EvidenceId
    url: str
    quote_chars: int


def check_record(record: EvidenceRecord, fetch: Fetch, saved: SavedText) -> CheckResult:
    """Check a quote against the live page, then against the saved copy of the document it cites.

    Args:
        record: Evidence record.
        fetch: Function that fetches a URL.
        saved: Function that returns the saved text behind a record.

    Returns:
        The check result.
    """
    quote = record.get("quote", "")
    if not quote.strip():
        return {"status": "no-quote", "detail": ""}
    status, detail = _live_status(record, fetch)
    text = saved(record)
    if status != "quote-found" and quote_found(quote, text) and _holds_also_found(record, _normalize(text)):
        return {"status": "quote-found-saved-copy", "detail": status}
    return {"status": status, "detail": detail}


def pdf_both_orders(path: StrPath) -> str:
    """Columns break quotes in one reading order or the other, so a check reads both.

    Args:
        path: PDF file to read.

    Returns:
        The text in layout order followed by the text in raw order.
    """
    return "\n".join(
        subprocess.run(
            ["pdftotext", *flags, str(path), "-"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        ).stdout
        for flags in (cast(list[str], []), ["-layout"])
    )


def quote_found(quote: str, page: str) -> bool:
    """Whether a quote appears in a page after both are normalised.

    Args:
        quote: Quoted text.
        page: Normalised text of the page.

    Returns:
        True when the quote is found.
    """
    return _normalize(quote) in _normalize(page)


def readable_text(kind: str, body: bytes) -> str | None:
    """Return page text, or None for a format that cannot be read.

    Args:
        kind: Media type of the body.
        body: Raw bytes of the response.

    Returns:
        The text, or None.
    """
    body = ungzipped(body)
    if "pdf" in kind or body[:5] == b"%PDF-":
        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            _ = handle.write(body)
            handle.flush()
            return pdf_both_orders(handle.name)
    known_text = any(t in kind for t in HTML_TYPES) or b"\x00" not in body[:4096]
    return body.decode("utf-8", errors="replace") if known_text else None


def verify_evidence(
    paths: Iterable[StrPath], out_csv: StrPath, fetch: Fetch | None = None, saved: SavedText | None = None
) -> list[VerificationRow]:
    """Check every collected record; a miss can reflect blocking, so it needs a human look.

    Args:
        paths: Evidence CSV files.
        out_csv: CSV file to write.
        fetch: Function that fetches a URL; ``_fetch`` when None.
        saved: Function that returns the saved text behind a record; ``_no_saved_copy`` when None.

    Returns:
        One result row per checked record.
    """
    fetch = fetch or _fetch
    saved = saved or _no_saved_copy
    read = [cast(EvidenceRecord, r) for path in paths for r in data.read_csv(path)]
    records = [r for r in read if r.get("source_type") not in GENERATED_SOURCES]
    rows: list[VerificationRow] = []
    for r in records:
        result = check_record(r, fetch, saved)
        rows.append(
            {
                "evidence_id": r.get("evidence_id", EvidenceId("")),
                "url": r.get("url", ""),
                "quote_chars": len(r.get("quote", "")),
                "status": result["status"],
                "detail": result["detail"],
            }
        )
    with Path(out_csv).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["evidence_id", "url", "quote_chars", "status", "detail"])
        writer.writeheader()
        writer.writerows(rows)
    return rows


@cache
def _fetch(url: str) -> Fetched:
    """Fetch a URL and return its content type and body; results are cached.

    Args:
        url: URL of the document.

    Returns:
        The content type and body.
    """
    headers = client_headers()
    robots.require(url, headers)
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        fetched = cast(str, response.headers.get("Content-Type", "")), cast(bytes, response.read())
    time.sleep(robots.crawl_delay(url, headers))
    return fetched


def _holds_also_found(record: EvidenceRecord, page: str) -> bool:
    """Whether the page also contains every extra term the record requires.

    Args:
        record: Evidence record.
        page: Normalised text of the page.

    Returns:
        True when every extra term is found.
    """
    return all(_normalize(part) in page for part in filter(None, record.get("also_found", "").split("|")))


@cache
def _live_page(fetch: Fetch, url: str) -> tuple[PageState, str, str | None]:
    """The normalised text of a live page, or why it could not be read.

    Args:
        fetch: Function that fetches a URL.
        url: URL of the document.

    Returns:
        The page state, a detail and the normalised text or None.
    """
    try:
        kind, body = fetch(url)
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, OSError) as exc:
        return "unreachable", str(exc)[:120], None
    text = readable_text(kind, body)
    return ("format-not-checked", kind, None) if text is None else ("read", "", _normalize(text))


def _live_status(record: EvidenceRecord, fetch: Fetch) -> tuple[QuoteCheck, str]:
    """Check a record's quote against its live page.

    Args:
        record: Evidence record.
        fetch: Function that fetches a URL.

    Returns:
        The check status and a detail.
    """
    state, detail, page = _live_page(fetch, record.get("url", ""))
    if state != "read" or page is None:
        return ("unreachable" if state == "read" else state), detail
    if _normalize(record.get("quote", "")) not in page:
        return "quote-not-found", ""
    return ("quote-found" if _holds_also_found(record, page) else "also-not-found"), ""


def _no_saved_copy(_record: EvidenceRecord) -> str:
    """The default saved text: none.

    Returns:
        An empty string.
    """
    return ""


def _normalize(text: str) -> str:
    """Text as compared for quote checks: unescaped, tags and typographic quotes removed, lower case.

    Args:
        text: Page or quote text.

    Returns:
        The normalised text.
    """
    text = html.unescape(text)
    text = re.sub(r"</?(?:a|b|code|em|i|span|strong)\b[^>]*>", "", text)
    text = re.sub(r"<[a-zA-Z/!][^<>]*>", " ", text)
    text = text.replace("|", " ").replace("`", "")
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+([,.;:)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"\s+", " ", text).strip().lower()


normalize = _normalize
