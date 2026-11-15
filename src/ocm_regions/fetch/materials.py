# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""An index of source documents: readers download their own copies, and nothing is redistributed."""

import csv
import hashlib
import html
import http.client
import ipaddress
import os
import re
import socket
import string
import time
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import IO, Final, Literal, NotRequired, TypeAlias, TypedDict, cast, override

from ocm_regions.evidence.verify import Fetched, readable_text
from ocm_regions.fetch import dns, robots
from ocm_regions.fetch.snapshots import SnapshotRow
from ocm_regions.fetch.sources import StrPath, client_headers

Get: TypeAlias = Callable[[str], Fetched]
Download: TypeAlias = tuple[str, bytes, str, str]
PinStatus: TypeAlias = Literal["new", "matched", "text-matched", "changed"]
FetchStatus: TypeAlias = PinStatus | Literal["invalid", "blocked", "listed-only", "robots-disallowed"]
SaveAction: TypeAlias = Literal["hold-invalid", "hold-marked", "hold-unread", "save"]

ASCII_LOWER: Final = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
DOWNLOAD_ERRORS: Final = (OSError, ValueError, http.client.HTTPException)

EMBEDDING_IPV6: Final = [
    ipaddress.ip_network(n)
    for n in ("::/96", "::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
]

FIELDS: Final = [
    "doc_id",
    "provider",
    "url",
    "access",
    "fetch",
    "license_declared",
    "terms_url",
    "redistribution_status",
    "shared_form",
    "sha256",
    "text_sha256",
    "size_bytes",
    "media_type",
    "retrieved",
    "archive_url",
    "archive_timestamp",
]

HIDDEN_TAGS: Final = ("script", "style", "noscript")
MARKING: Final = re.compile(r"confidential|proprietary|not\s+for\s+distribution|internal\s+use\s+only", re.I)
MAX_BYTES: Final = 300 * 1024 * 1024
MAX_GARBLED_SHARE: Final = 0.01
MAX_TEXT_BYTES: Final = 10 * 1024 * 1024

REPORT_FIELDS: Final = [
    "doc_id",
    "provider",
    "url",
    "status",
    "source",
    "sha256",
    "text_sha256",
    "size_bytes",
    "media_type",
    "detail",
]

SAFE_VENDOR: Final = re.compile(r"[a-z0-9_-]+")

SUFFIXES: Final[dict[str, str]] = {
    "pdf": ".pdf",
    "html": ".html",
    "json": ".json",
    "csv": ".csv",
    "markdown": ".md",
    "text": ".txt",
    "xml": ".html",
}

TAG_SPAN: Final = re.compile(r"<[^<>]*>")
TAG_STARTS: Final = frozenset(string.ascii_lowercase + "/!?")
TEXT_TYPES: Final = ("html", "text", "xml", "markdown")


class IndexRow(TypedDict):
    """One source document in the URL index, with its access, hashes and archive copy."""

    doc_id: str
    provider: str
    url: str
    access: str
    fetch: str
    license_declared: str
    terms_url: str
    redistribution_status: str
    shared_form: str
    sha256: str
    text_sha256: str
    size_bytes: int | str
    media_type: str
    retrieved: str
    archive_url: str
    archive_timestamp: str
    note: NotRequired[str]


ReadText: TypeAlias = Callable[[IndexRow], str | None]


class FetchResult(TypedDict):
    """What one fetch found for a document: its status, where it came from and its hashes."""

    doc_id: str
    provider: str
    url: str
    status: FetchStatus
    source: str
    sha256: str
    text_sha256: str
    size_bytes: int | str
    media_type: str
    detail: str


class SaveRow(TypedDict):
    """Whether a document should be saved to a public archive, or held and why."""

    provider: str
    url: str
    action: SaveAction


class CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect handler that applies the public-address check to every redirect target."""

    @override
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Refuse a redirect to a non-public address, then follow it as usual.

        Args:
            req: The original request.
            fp: Body of the redirect response.
            code: HTTP status code of the redirect.
            msg: Status message of the redirect.
            headers: Headers of the redirect response.
            newurl: URL the redirect points to.

        Returns:
            The request to follow, or None to stop.
        """
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def archivable(rows: Iterable[IndexRow], read_text: ReadText) -> list[IndexRow]:
    """A source goes to a public archive only if it was read and carries no confidentiality marking.

    Args:
        rows: Rows of the URL index.
        read_text: Function that returns the saved text of a document.

    Returns:
        The index rows that may be archived.
    """
    return [row for row in rows if _save_action(row, read_text) == "save"]


def check_url(url: str, resolve: Callable[[str], list[str]] | None = None) -> None:
    """Allow only http(s) addresses that resolve to the public internet.

    A name sent through a proxy is never looked up on this machine; the operator names who resolves it.

    Args:
        url: URL of the document.
        resolve: Function that resolves a host name to addresses; chosen by ``_addresses`` when None.

    Raises:
        ValueError: If the URL is not a web address or resolves to a non-public address.
        RuntimeError: If the request goes through a proxy and no one is named to resolve its host.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not a web address: {url[:80]}")
    for address in _addresses(parts.scheme, parts.hostname, resolve):
        if not _is_public(address.split("%")[0]):
            raise ValueError(f"not a public address: {parts.hostname}")


def checked_robots(url: str, headers: Mapping[str, str]) -> str | None:
    """Fetch a robots.txt file under the same public-address checks as a document.

    Args:
        url: URL of the robots.txt file.
        headers: Headers to send.

    Returns:
        The file's text; empty when the site has none; None when it may not or could not be read.
    """
    try:
        check_url(url)
    except ValueError:
        return None
    return robots.read_robots(url, headers, urllib.request.build_opener(CheckedRedirects))


def fetch_materials(
    rows: Iterable[IndexRow], raw: StrPath, get: Get | None = None, pause: float = 1.0, force: bool = False
) -> list[FetchResult]:
    """Download every public source; a failure is reported and never stops the run.

    Args:
        rows: Rows of the URL index.
        raw: The data/raw folder.
        get: Function that downloads a URL; ``http_get`` when None.
        pause: Seconds to wait between requests.
        force: Download again even when a saved copy exists.

    Returns:
        One report row per document.
    """
    get = get or http_get
    return [
        _fetch_one(row, raw, get, pause, force)
        if row["fetch"] == "yes"
        else _result(row, "listed-only", detail=row["access"])
        for row in rows
    ]


def http_get(url: str) -> Fetched:
    """Fetch a public URL and return its content type and body, up to the size limit.

    Args:
        url: URL of the document.

    Returns:
        The content type and body.
    """
    check_url(url)
    headers = client_headers()
    robots.require(url, headers, checked_robots)
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.build_opener(CheckedRedirects).open(request, timeout=120) as response:
        return cast(str, response.headers.get("Content-Type", "")), cast(bytes, response.read(MAX_BYTES))


def read_index(path: StrPath) -> list[IndexRow]:
    """Read the URL index of source documents.

    Args:
        path: Path of the URL index.

    Returns:
        The index rows.
    """
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return cast(list[IndexRow], list(csv.DictReader(handle)))


def read_saved(row: IndexRow, raw: StrPath) -> str | None:
    """Return the text of every saved copy, or None if any copy, or the source itself, couldn't be read.

    Args:
        row: Row of the URL index.
        raw: The data/raw folder.

    Returns:
        The joined text of the saved copies, or None.
    """
    folder = Path(raw) / "trust_docs" / row["provider"] / "fetched"
    copies = sorted(folder.glob(f"{row['doc_id']}.*")) if folder.exists() else []
    texts = [_read_copy(path) for path in copies]
    read = [text for text in texts if text is not None]
    return " ".join(read) if texts and len(read) == len(texts) else None


def record_hashes(rows: Iterable[IndexRow], report: Iterable[FetchResult], today: str) -> list[IndexRow]:
    """Pin what this run downloaded, so later runs can tell whether a source changed.

    Args:
        rows: Rows of the URL index.
        report: Results of the downloads.
        today: Today's date in ISO form.

    Returns:
        The index rows with pins filled in.
    """
    done = {r["doc_id"]: r for r in report if r["status"] in ("new", "changed", "matched") and r["sha256"]}
    return [_pinned(row, done[row["doc_id"]], today) if row["doc_id"] in done else row for row in rows]


def refresh_index(rows: Iterable[Mapping[str, str]], snapshots: Iterable[SnapshotRow] = ()) -> list[IndexRow]:
    """Fill in a row a person added with only a provider and a URL, and record archive copies.

    Args:
        rows: Rows to read.
        snapshots: Snapshot rows.

    Returns:
        The completed index rows.
    """
    captured = {s["url"]: s for s in snapshots if s["status"] == "captured"}
    refreshed: list[IndexRow] = []
    for row in rows:
        filled: dict[str, str] = {
            "doc_id": hashlib.sha256(row["url"].encode()).hexdigest()[:12],
            "access": "public",
            "fetch": "yes",
            "license_declared": "NOASSERTION",
            "redistribution_status": "silent-or-unknown",
            "shared_form": "url,hash,short-quote",
        } | {k: v for k, v in row.items() if v}
        if row["url"] in captured:
            filled.update(
                archive_url=captured[row["url"]]["archive_url"],
                archive_timestamp=captured[row["url"]]["timestamp"],
            )
        refreshed.append(cast(IndexRow, {field: filled.get(field, "") for field in FIELDS}))
    return sorted(refreshed, key=lambda r: (r["provider"], r["url"]))


def save_list(rows: Iterable[IndexRow], read_text: ReadText) -> list[SaveRow]:
    """Public sources with no archive capture; only a source read and found unmarked is listed to save.

    Args:
        rows: Rows of the URL index.
        read_text: Function that returns the saved text of a document.

    Returns:
        One row per document, with the action and reason.
    """
    return [
        {"provider": row["provider"], "url": row["url"], "action": _save_action(row, read_text)}
        for row in rows
        if row["fetch"] == "yes" and not row["archive_url"]
    ]


def text_hash(body: bytes, media_type: str) -> str:
    """Hash a page's words so scripts that change on every request don't count as a change; large files get none.

    Args:
        body: Raw bytes of the response.
        media_type: Media type of the body.

    Returns:
        The hex digest of the words, or blank when no text hash applies.
    """
    if len(body) > MAX_TEXT_BYTES or not any(kind in media_type for kind in TEXT_TYPES):
        return ""
    text = body.decode("utf-8", errors="replace")
    words = _visible_words(text) if "html" in media_type or "xml" in media_type else text.split()
    return hashlib.sha256(" ".join(words).encode()).hexdigest() if words else ""


def write_fetch_report(report: Iterable[FetchResult], path: StrPath) -> None:
    """Write the per-document fetch report as CSV.

    Args:
        report: Results of the downloads.
        path: CSV file to write.
    """
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(report)


def write_index(rows: Iterable[IndexRow], path: StrPath) -> None:
    """Write the URL index of source documents.

    Args:
        rows: Rows of the URL index.
        path: Path of the URL index.
    """
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _addresses(scheme: str, host: str, resolve: Callable[[str], list[str]] | None) -> list[str]:
    """The addresses to check for a host: its own when written out, else from the resolver for this route.

    Args:
        scheme: Scheme of the URL.
        host: Host of the URL.
        resolve: Function that resolves a host name to addresses, when the caller gives one.

    Returns:
        The addresses, as text; empty when the operator states that the proxy resolves the name.
    """
    literal = _literal(host)
    if literal:
        return [literal]
    if resolve:
        return resolve(host)
    resolver = os.environ.get(dns.DNS_VARIABLE, "").strip()
    if _proxied(scheme, host):
        return _proxy_addresses(host, resolver)
    asked = dns.lookup(host, resolver) if resolver and resolver != dns.PROXY_RESOLVES else []
    return _resolve(host) + asked


def _checked(row: IndexRow, body: bytes, media: str, source: str, detail: str) -> FetchResult:
    """Hash a document body and report how it compares with its pins.

    Args:
        row: Row of the URL index.
        body: Raw bytes of the response.
        media: Media type of the response.
        source: Where the bytes came from: the live page, an archive or a saved copy.
        detail: Explanation to record.

    Returns:
        The report row.
    """
    digest, text_digest = hashlib.sha256(body).hexdigest(), text_hash(body, media)
    return _result(
        row, _status(row, digest, text_digest), source, digest, len(body), media, detail, text_digest
    )


def _download(row: IndexRow, get: Get) -> Download:
    """Fetch a document from its live URL, falling back on its archive copy.

    Args:
        row: Row of the URL index.
        get: Function that downloads a URL.

    Returns:
        The body, its media type and where it came from.
    """
    try:
        return (*_get_document(row["url"], row, get), "live", "")
    except robots.RobotsDisallowed:
        raise
    except DOWNLOAD_ERRORS as exc:
        failure = str(exc)[:120]
    if not row["archive_url"]:
        return "", b"", "", failure
    try:
        return (*_get_document(row["archive_url"], row, get), "archive", failure)
    except DOWNLOAD_ERRORS as exc:
        return "", b"", "", f"{failure}; archive: {str(exc)[:80]}"


def _existing(folder: Path, doc_id: str) -> Path | None:
    """The saved copy of a document in a folder, whatever its suffix.

    Args:
        folder: Folder that holds the vendor's saved documents.
        doc_id: Identifier of the document.

    Returns:
        The path of the saved copy, or None when there is none.
    """
    return next(iter(sorted(folder.glob(f"{doc_id}.*"))), None) if folder.exists() else None


def _fetch_one(row: IndexRow, raw: StrPath, get: Get, pause: float, force: bool) -> FetchResult:
    """Use the saved copy of a document or download it, save it, and report the result.

    Args:
        row: Row of the URL index.
        raw: The data/raw folder.
        get: Function that downloads a URL.
        pause: Seconds to wait between requests.
        force: Download again even when a saved copy exists.

    Returns:
        The report row for the document.
    """
    if not _safe_row(row):
        return _result(row, "invalid", detail="doc_id or vendor failed validation")
    folder = Path(raw) / "trust_docs" / row["provider"] / "fetched"
    saved = _existing(folder, row["doc_id"])
    if saved and not force:
        body = saved.read_bytes()
        return _checked(row, body, row["media_type"] or _media_from_suffix(saved), "saved", "")
    try:
        media, body, source, detail = _pinned_download(row, get)
    except robots.RobotsDisallowed as refusal:
        return _result(row, "robots-disallowed", detail=str(refusal)[:120])
    time.sleep(max(pause, robots.crawl_delay(row["url"], client_headers())))
    if not source:
        return _result(row, "blocked", detail=detail)
    folder.mkdir(parents=True, exist_ok=True)
    _ = (folder / f"{row['doc_id']}{_suffix(media, row['url'])}").write_bytes(body)
    return _checked(row, body, media, source, detail)


def _get_document(url: str, row: IndexRow, get: Get) -> Fetched:
    """A PDF link that answers with anything else, such as a landing page, did not deliver the document.

    Args:
        url: URL of the document.
        row: Row of the URL index.
        get: Function that downloads a URL.

    Returns:
        The content type and body.

    Raises:
        ValueError: If a document expected to be a PDF is not one.
    """
    media, body = get(url)
    if row["url"].lower().split("?")[0].endswith(".pdf") and b"%PDF-" not in body[:1024]:
        raise ValueError("not a PDF")
    return media, body


def _is_marked(text: str) -> bool:
    """Whether text carries a confidentiality marking in any of its readable forms.

    Args:
        text: Raw text of the document.

    Returns:
        True when a marking is found.
    """
    return any(MARKING.search(form) for form in _marking_forms(text))


def _is_public(address: str) -> bool:
    """Whether an address is on the public internet, rejecting IPv6 forms that embed an IPv4 address.

    Args:
        address: IP address to test.

    Returns:
        True for a public address.
    """
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and any(ip in net for net in EMBEDDING_IPV6):
        return False
    return ip.is_global and not (
        ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_link_local or ip.is_loopback
    )


def _literal(host: str) -> str | None:
    """The address a host stands for without a lookup: an IP address in any spelling, or a local name.

    Args:
        host: Host of the URL.

    Returns:
        The address, as text; None when the host is a name that needs a lookup.
    """
    name = host.rstrip(".").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return "127.0.0.1"
    try:
        return str(ipaddress.ip_address(host.split("%")[0]))
    except ValueError:
        pass
    try:
        return socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        return None


def _marking_forms(text: str) -> tuple[str, str, str]:
    """The raw text, so a marking in an attribute counts, and the text with tags dropped or spaced, as a reader sees it.

    Args:
        text: Raw text of the document.

    Returns:
        The raw text, the text without tags, and the text with tags as spaces.
    """
    plain = "".join(
        c for c in unicodedata.normalize("NFKC", html.unescape(text)) if unicodedata.category(c) != "Cf"
    )
    joined = re.sub(r"-\s*\n\s*", "", plain)
    return plain, TAG_SPAN.sub("", joined), TAG_SPAN.sub(" ", joined)


def _matches_pin(row: IndexRow, body: bytes, media: str) -> bool:
    """Whether a body matches the document's pins, or the document has none yet.

    Args:
        row: Row of the URL index.
        body: Raw bytes of the response.
        media: Media type of the response.

    Returns:
        True when the body matches or no pin exists.
    """
    return _status(row, hashlib.sha256(body).hexdigest(), text_hash(body, media)) in (
        "new",
        "matched",
        "text-matched",
    )


def _media_from_suffix(path: Path) -> str:
    """Media kind implied by a saved file's suffix.

    Args:
        path: Saved file.

    Returns:
        The media kind.
    """
    return next((kind for kind, suffix in SUFFIXES.items() if suffix == path.suffix), "")


def _pinned(row: IndexRow, result: FetchResult, today: str) -> IndexRow:
    """A copy of an index row with the hashes, size and media type of this fetch.

    Args:
        row: Row of the URL index.
        result: Result of the download.
        today: Today's date in ISO form.

    Returns:
        The updated copy of the row.
    """
    return {
        **row,
        "sha256": result["sha256"],
        "text_sha256": result.get("text_sha256", ""),
        "size_bytes": result["size_bytes"],
        "media_type": result["media_type"] or row["media_type"],
        "retrieved": today,
    }


def _pinned_download(row: IndexRow, get: Get) -> Download:
    """When a live page no longer matches its pin, an archive copy that does match is the document cited.

    Args:
        row: Row of the URL index.
        get: Function that downloads a URL.

    Returns:
        The download that matches the pin where one does, else the live one.
    """
    media, body, source, detail = _download(row, get)
    if source != "live" or not row["archive_url"] or _matches_pin(row, body, media):
        return media, body, source, detail
    try:
        old_media, old_body = _get_document(row["archive_url"], row, get)
    except DOWNLOAD_ERRORS:
        return media, body, source, detail
    return (
        (old_media, old_body, "archive", "live page changed")
        if _matches_pin(row, old_body, old_media)
        else (media, body, source, detail)
    )


def _proxy_addresses(host: str, resolver: str) -> list[str]:
    """The addresses to check for a name sent through a proxy, without a lookup on this machine.

    Args:
        host: Host name to resolve.
        resolver: The operator's choice of resolver; empty when none is made.

    Returns:
        The chosen resolver's addresses; empty when the operator states that the proxy resolves the name.

    Raises:
        RuntimeError: If no one is named to resolve the name.
    """
    if not resolver:
        choices = f"{', '.join(dns.RESOLVERS)}, an https resolver address, or {dns.PROXY_RESOLVES!r}"
        raise RuntimeError(
            f"{dns.DNS_VARIABLE} must name who resolves hosts sent through the proxy: {choices}"
        )
    return [] if resolver == dns.PROXY_RESOLVES else dns.lookup(host, resolver)


def _proxied(scheme: str, host: str) -> bool:
    """Whether a request to a host goes through a proxy.

    Args:
        scheme: Scheme of the URL.
        host: Host of the URL.

    Returns:
        True when a proxy is set for the scheme and the host is not exempt from it.
    """
    return scheme in urllib.request.getproxies() and not urllib.request.proxy_bypass(host)


def _read_copy(path: Path) -> str | None:
    """Text of one saved copy, or None when it is binary, garbled or empty.

    Args:
        path: Saved file to read.

    Returns:
        The text, or None.
    """
    body = path.read_bytes()
    if b"%PDF-" in body[:1024] or path.suffix == ".pdf":
        text = readable_text("application/pdf", body)
    else:
        text = body.decode("utf-8", errors="replace")
        if "\x00" in text or text.count("\ufffd") > len(text) * MAX_GARBLED_SHARE:
            return None
    return text if text and text.strip() else None


def _resolve(host: str) -> list[str]:
    """Resolve a host name to its IP addresses.

    Args:
        host: Host name to resolve.

    Returns:
        The addresses, as text.
    """
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _result(
    row: IndexRow,
    status: FetchStatus,
    source: str = "",
    digest: str = "",
    size: int | str = "",
    media: str = "",
    detail: str = "",
    text_digest: str = "",
) -> FetchResult:
    """Build the report row for one document.

    Args:
        row: Row of the URL index.
        status: Outcome of the download.
        source: Where the bytes came from: the live page, an archive or a saved copy.
        digest: SHA-256 of the saved bytes.
        size: Size in bytes, or blank.
        media: Media type of the response.
        detail: Explanation to record.
        text_digest: SHA-256 of the extracted text.

    Returns:
        The report row.
    """
    return {
        "doc_id": row["doc_id"],
        "provider": row["provider"],
        "url": row["url"],
        "status": status,
        "source": source,
        "sha256": digest,
        "text_sha256": text_digest,
        "size_bytes": size,
        "media_type": media,
        "detail": detail,
    }


def _safe_row(row: IndexRow) -> bool:
    """Whether a row's ID matches its URL and its vendor name is safe to use as a folder.

    Args:
        row: Row of the URL index.

    Returns:
        True when the row is safe to save.
    """
    return row["doc_id"] == hashlib.sha256(row["url"].encode()).hexdigest()[:12] and bool(
        SAFE_VENDOR.fullmatch(row["provider"])
    )


def _save_action(row: IndexRow, read_text: ReadText) -> SaveAction:
    """Decide whether a document may be saved to a public archive.

    Args:
        row: Row of the URL index.
        read_text: Function that returns the saved text of a document.

    Returns:
        Whether to save, with the reason.
    """
    if not _safe_row(row):
        return "hold-invalid"
    if _is_marked(row.get("note", "")):
        return "hold-marked"
    text = read_text(row)
    if text is None:
        return "hold-unread"
    return "hold-marked" if _is_marked(text) else "save"


def _skip_tag(low: str, start: int) -> int:
    """Return where text resumes after the tag at start, or -1 if the page ends first.

    Args:
        low: Lower-case tag text.
        start: Start offset in the text.

    Returns:
        The offset after the tag, or -1.
    """
    if low.startswith("<!--", start):
        end = low.find("-->", start)
        return end + 3 if end >= 0 else -1
    end = low.find(">", start)
    if end < 0:
        return -1
    name = low[start + 1 : end].split(maxsplit=1)[0].rstrip("/") if low[start + 1 : end].strip() else ""
    if name in HIDDEN_TAGS:
        closing = low.find(f"</{name}", end)
        end = low.find(">", closing) if closing >= 0 else -1
    return end + 1 if end >= 0 else -1


def _status(row: IndexRow, digest: str, text_digest: str) -> PinStatus:
    """Compare a document's hashes with its pins: new, matched, text-matched or changed.

    Args:
        row: Row of the URL index.
        digest: SHA-256 of the saved bytes.
        text_digest: SHA-256 of the extracted text.

    Returns:
        The pin status.
    """
    if not row["sha256"]:
        return "new"
    if row["sha256"] == digest:
        return "matched"
    return "text-matched" if text_digest and row.get("text_sha256") == text_digest else "changed"


def _suffix(media_type: str, url: str) -> str:
    """File suffix for a document, from its media type or else its URL.

    Args:
        media_type: Media type of the body.
        url: URL of the document.

    Returns:
        The suffix with its leading dot.
    """
    for key, suffix in SUFFIXES.items():
        if key in media_type:
            return suffix
    tail = Path(url.split("?")[0]).suffix.lower()
    return tail if tail in SUFFIXES.values() else ".txt"


def _visible_words(text: str) -> list[str] | None:
    """Scan once from left to right, so hostile markup can't make this slow; an unclosed construct returns None.

    Args:
        text: HTML to scan.

    Returns:
        The visible words in order, or None for unclosed markup.
    """
    low, position = text.translate(ASCII_LOWER), 0
    words: list[str] = []
    while True:
        tag = low.find("<", position)
        if tag < 0:
            words += text[position:].split()
            return html.unescape(" ".join(words)).split()
        words += text[position:tag].split()
        if low[tag + 1 : tag + 2] not in TAG_STARTS:
            words.append("<")
            position = tag + 1
            continue
        position = _skip_tag(low, tag)
        if position < 0:
            return None


suffix_for = _suffix


visible_words = _visible_words


media_from_suffix = _media_from_suffix
