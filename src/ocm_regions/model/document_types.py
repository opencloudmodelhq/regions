# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Each document's kind and publisher, from a frozen prompt at temperature 0; the title comes from the text."""

import csv
import html
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final, Literal, Protocol, TypeAlias, TypedDict, cast, get_args

from ocm_regions.fetch.materials import IndexRow
from ocm_regions.fetch.sources import StrPath
from ocm_regions.model.prompts import fill, parse_answer

DocumentKind: TypeAlias = Literal["vendor", "certification", "government", "news", "directory"]

FIELDS: Final = ("doc_id", "kind", "publisher", "title")
KINDS: Final[tuple[DocumentKind, ...]] = get_args(DocumentKind)
STREET: Final = re.compile(
    r"\b\d+\s+(?:\w+\s+){1,2}(?:Street|St|Road|Rd|Drive|Dr|Avenue|Ave|Lane|Ln|Way|Plaza|Boulevard|Blvd)\b",
    re.I,
)
TEXT_CHARS: Final = 3000
WEAKEST: Final[DocumentKind] = "directory"


class Chat(Protocol):
    """A client that answers a prompt with text."""

    def chat(self, prompt: str) -> str:
        """Answer a prompt with text.

        Args:
            prompt: Prompt text.

        Returns:
            The model's reply.
        """
        ...


class DocumentType(TypedDict):
    """The kind, publisher and title recorded for one document."""

    doc_id: str
    kind: DocumentKind
    publisher: str
    title: str


class TypedDocument(IndexRow):
    """A row of the URL index with the document's recorded type."""

    kind: DocumentKind
    publisher: str
    title: str


def classify_document(row: IndexRow, text: str, client: Chat) -> DocumentType:
    """Ask the model who published a document and what kind it is; an unread document takes the weakest kind.

    Args:
        row: Row of the URL index.
        text: Readable text of the document.
        client: Chat client.

    Returns:
        The recorded kind, publisher and title.
    """
    base: DocumentType = {
        "doc_id": row["doc_id"],
        "kind": WEAKEST,
        "publisher": "",
        "title": document_title(text, row["url"]),
    }
    if not text:
        return base
    answer = parse_answer(
        client.chat(fill("document", vendor=row["provider"], url=row["url"], text=text[:TEXT_CHARS]))
    )
    kind = answer.get("kind")
    return {
        **base,
        "kind": kind if kind in KINDS else WEAKEST,
        "publisher": str(answer.get("publisher") or ""),
    }


def document_title(text: str, url: str) -> str:
    """A document's title: its HTML title, else its first line; its URL when neither exists or one holds a street address.

    Args:
        text: Saved HTML or text of the document.
        url: URL of the document.

    Returns:
        The title text.
    """
    found = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = " ".join(html.unescape(found[1]).split()) if found else "".join(lines[:1])[:200]
    return url if not title or STREET.search(title) else title


def read_with_types(index: StrPath, types: StrPath) -> list[TypedDocument]:
    """The URL index with each document's recorded type; a document not yet read takes the weakest reading.

    Args:
        index: Path of the URL index.
        types: Path of the recorded document types.

    Returns:
        One document per index row, with its type.
    """
    recorded: dict[str, dict[str, str]] = {}
    if Path(types).exists():
        with Path(types).open(encoding="utf-8", newline="") as handle:
            recorded = {r["doc_id"]: dict(r) for r in csv.DictReader(handle)}
    with Path(index).open(encoding="utf-8", newline="") as handle:
        rows = [dict(r) for r in csv.DictReader(handle)]
    merged = [
        {
            "kind": WEAKEST,
            "publisher": "",
            "title": r["url"],
            **r,
            **{k: v for k, v in recorded.get(r["doc_id"], {}).items() if k != "doc_id"},
        }
        for r in rows
    ]
    return cast(list[TypedDocument], merged)


def write_types(rows: Iterable[DocumentType], path: StrPath) -> None:
    """Write the recorded document types to CSV.

    Args:
        rows: Recorded document types.
        path: CSV file to write.
    """
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter[str](handle, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
