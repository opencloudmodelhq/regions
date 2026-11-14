# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Verbatim passages from a cited document that name the reviewer's city, for the hand check."""

import html
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final, TypeAlias

from ocm_regions.evidence.verify import normalize as _normalize
from ocm_regions.evidence.verify import readable_text
from ocm_regions.fetch.sources import StrPath
from ocm_regions.model.llm import saved_text_of

SheetRow: TypeAlias = Mapping[str, str]
PassageRow: TypeAlias = dict[str, str]
TextOf: TypeAlias = Callable[[str, str], str]
Check: TypeAlias = Callable[[str], bool]

BLOCK: Final = re.compile(
    r"</?(?:br|p|div|tr|li|ul|ol|table|thead|tbody|section|article|header|footer|h[1-6]|dt|dd|dl|pre|blockquote)\b[^>]*>",
    re.I,
)

CONTEXT: Final = 60
HIDDEN: Final = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>", re.I | re.S)
HTML_HINT: Final = re.compile(r"<(?:html|body|div|p|table|td|li|span)\b", re.I)
INLINE: Final = re.compile(r"</?(?:a|b|code|em|i|span|strong)\b[^>]*>", re.I)
LIMIT: Final = 3
NEIGHBOURS: Final = 2
OTHER: Final = re.compile(r"<[a-zA-Z/!][^<>]*>")

PASSAGE_FIELDS: Final = [
    "sheet_id",
    "provider",
    "region_id",
    "city",
    "country",
    "url_used",
    "passage_no",
    "passage",
    "flag",
    "pick",
]

WIDTH: Final = 120


def corpus_text_of(
    raw: StrPath, materials: Iterable[Mapping[str, object]], manifest: Iterable[Mapping[str, object]]
) -> TextOf:
    """Text of a corpus document or dataset by its URL; any other URL has none.

    Args:
        raw: The data/raw folder.
        materials: Rows of the URL index.
        manifest: Rows of the dataset list.

    Returns:
        A function from URL to saved text.
    """
    providers = {str(r["url"]): str(r["provider"]) for r in materials}
    paths = {str(r["url"]): str(r["path"]) for r in manifest}

    def text_of(url: str, _provider: str) -> str:
        """The saved text behind a URL: a corpus document, a dataset, or nothing.

        Args:
            url: URL of the document.

        Returns:
            The text; blank when there is none.
        """
        if url in providers:
            return saved_text_of({"url": url, "provider": providers[url]}, raw)
        path = Path(raw) / paths.get(url, "")
        if url not in paths or not path.is_file():
            return ""
        return readable_text("text", path.read_bytes()) or ""

    return text_of


def display_text(raw: str) -> str:
    """Readable text with its original case; a table row stays on one line.

    Args:
        raw: Saved HTML or text of the document.

    Returns:
        The readable text.
    """
    if HTML_HINT.search(raw):
        raw = OTHER.sub(" ", BLOCK.sub("\n", INLINE.sub("", HIDDEN.sub(" ", raw))))
        raw = html.unescape(raw)
    lines = (re.sub(r"[ \t\r\f\v\xa0]+", " ", line).strip() for line in raw.split("\n"))
    return "\n".join(line for line in lines if line)


def find_passages(
    text: str,
    city: str,
    hints: Sequence[str],
    limit: int = LIMIT,
    width: int = WIDTH,
    neighbours: bool = True,
    check: Check | None = None,
) -> list[str]:
    """Each distinct passage naming the city, those naming the region first.

    Args:
        text: Readable text of the document.
        city: City name.
        hints: Terms that mark a relevant passage, most important first.
        limit: Most passages to return.
        width: Most characters to keep on each side of the match.
        neighbours: Include the lines before and after.
        check: Test a passage must pass to be kept; none when None.

    Returns:
        The passages, best first.
    """
    folded, index = _folded_with_index(text)
    pattern = re.compile(r"(?<!\w)" + re.escape(_fold(city)) + r"(?!\w)")
    found: list[str] = []
    lines: set[str] = set()
    for match in pattern.finditer(folded):
        if _in_identifier(folded, match.start(), match.end()):
            continue
        start, end = index[match.start()], index[match.end() - 1] + 1
        line = text[text.rfind("\n", 0, start) + 1 : (text.find("\n", end) + 1 or len(text) + 1) - 1]
        passage = _passage(text, start, end, width, neighbours, check)
        if passage and line not in lines and passage not in found:
            lines.add(line)
            found.append(passage)
    return sorted(found, key=lambda p: -_score(p, hints))[:limit]


def passage_rows(sheet_rows: Iterable[SheetRow], text_of: TextOf) -> list[PassageRow]:
    """Passage rows for every reviewed region.

    Args:
        sheet_rows: Rows of the review sheet.
        text_of: Function that returns the saved text behind a URL.

    Returns:
        The passage rows.
    """
    return [out for row in sheet_rows for out in _rows_for(row, text_of)]


def _base(row: SheetRow) -> PassageRow:
    """The columns every passage row shares.

    Args:
        row: Hand-check sheet row.

    Returns:
        The shared columns.
    """
    return {field: row.get(field, "") for field in PASSAGE_FIELDS[:6]}


def _checker(raw: str) -> Check:
    """A check that a passage appears in the document's normalised text.

    Args:
        raw: Saved HTML or text of the document.

    Returns:
        The check function.
    """
    page = _normalize(raw)

    def holds(passage: str) -> bool:
        """Whether the passage appears in the document.

        Args:
            passage: Passage of text.

        Returns:
            True when the passage is found.
        """
        return _normalize(passage) in page

    return holds


def _flagged(row: SheetRow, flag: str) -> PassageRow:
    """A sheet row that carries a flag in place of a passage.

    Args:
        row: Hand-check sheet row.
        flag: Reason no passage is given.

    Returns:
        The sheet row.
    """
    return _base(row) | {"passage_no": "", "passage": "", "flag": flag, "pick": ""}


def _fold(text: str) -> str:
    """Lower-case text with accents removed and one form of apostrophe.

    Args:
        text: Text to fold.

    Returns:
        The folded text.
    """
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.replace("’", "'")) if not unicodedata.combining(c)
    ).lower()


def _folded_with_index(text: str) -> tuple[str, list[int]]:
    """Folded text, with the position in the original of each folded character.

    Args:
        text: Text to fold.

    Returns:
        The folded text and the original offset of each of its characters.
    """
    folded: list[str] = []
    index: list[int] = []
    for position, char in enumerate(text):
        for part in _fold(char):
            folded.append(part)
            index.append(position)
    return "".join(folded), index


def _in_identifier(folded: str, start: int, end: int) -> bool:
    """Whether a match sits inside an identifier that contains a digit, such as a region ID.

    Args:
        folded: Folded text.
        start: Start offset in the text.
        end: End offset in the text.

    Returns:
        True when the match is part of an identifier.
    """
    left, right = start, end
    while left > 0 and (folded[left - 1].isalnum() or folded[left - 1] in "-_"):
        left -= 1
    while right < len(folded) and (folded[right].isalnum() or folded[right] in "-_"):
        right += 1
    return any(c.isdigit() for c in folded[left:right])


def _passage(
    text: str, start: int, end: int, width: int, neighbours: bool, check: Check | None
) -> str | None:
    """The passage for a match that passes the check, with and then without neighbouring lines.

    Args:
        text: Text the match was found in.
        start: Start offset in the text.
        end: End offset in the text.
        width: Most characters to keep on each side of the match.
        neighbours: Include the lines before and after.
        check: Test a passage must pass to be kept; none when None.

    Returns:
        The passage, or None when none passes the check.
    """
    for borrow in (True, False) if neighbours else (False,):
        passage = _window(text, start, end, width, borrow)
        if check is None or check(passage):
            return passage
    return None


def _rows_for(row: SheetRow, text_of: TextOf) -> list[PassageRow]:
    """Passage rows for one reviewed region, or one flagged row when none can be given.

    Args:
        row: Hand-check sheet row.
        text_of: Function that returns the saved text behind a URL.

    Returns:
        The passage rows.
    """
    city = row.get("city", "").strip()
    if not city:
        return [_flagged(row, "no-city-given")]
    raw = text_of(row["url_used"], row["provider"])
    if not raw:
        return [_flagged(row, "no-saved-text")]
    hints = [row.get("region_id", ""), row.get("region_name", ""), row.get("stated_location", "")]
    passages = find_passages(
        display_text(raw), city, hints, neighbours=bool(HTML_HINT.search(raw)), check=_checker(raw)
    )
    if not passages:
        return [_flagged(row, "city-not-in-document")]
    return [
        _base(row) | {"passage_no": str(n), "passage": p, "flag": "", "pick": ""}
        for n, p in enumerate(passages, 1)
    ]


def _score(passage: str, hints: Sequence[str]) -> int:
    """Score a passage by which region hints it contains, earlier hints counting more.

    Args:
        passage: Passage of text.
        hints: Terms that mark a relevant passage, most important first.

    Returns:
        The score; higher is more relevant.
    """
    folded = _fold(passage)
    return sum(len(hints) - rank for rank, hint in enumerate(hints) if hint and _fold(hint) in folded)


def _window(text: str, start: int, end: int, width: int, neighbours: bool) -> str:
    """The passage around a match: its whole line when short, else a window cut at word ends.

    Args:
        text: Text the match was found in.
        start: Start offset in the text.
        end: End offset in the text.
        width: Most characters to keep on each side of the match.
        neighbours: Include the lines before and after.

    Returns:
        The passage text.
    """
    line_start, line_end = text.rfind("\n", 0, start) + 1, text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    if line_end - line_start <= 2 * width + (end - start):
        return (
            _with_neighbours(text, line_start, line_end) if neighbours else text[line_start:line_end].strip()
        )
    left, right = max(line_start, start - width), min(line_end, end + width)
    if left > line_start and not text[left - 1].isspace():
        left = text.find(" ", left, start) + 1 or start
    if right < line_end and not text[right].isspace():
        right = max(text.rfind(" ", end, right), end)
    return text[left:right].strip()


def _with_neighbours(text: str, start: int, end: int) -> str:
    """A short line, such as a table cell, borrows lines on each side until it reads as context.

    Args:
        text: Text the line belongs to.
        start: Start offset in the text.
        end: End offset in the text.

    Returns:
        The line with its neighbours.
    """
    for _ in range(NEIGHBOURS):
        if end - start >= CONTEXT:
            break
        start = text.rfind("\n", 0, max(start - 1, 0)) + 1 if start else 0
        end = len(text) if text.find("\n", end + 1) < 0 else text.find("\n", end + 1)
    return " ".join(text[start:end].split("\n")).strip()
