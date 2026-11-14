# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Generate LaTeX macros and tables so paper numbers come from the build."""

import csv
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Final, TypedDict

from ocm_regions.common.schema import CallRow, EvidenceRecord, Grade, Precision, RegionId
from ocm_regions.common.vendors import vendor_name
from ocm_regions.fetch.sources import StrPath
from ocm_regions.tables.coverage import Coverage
from ocm_regions.tables.grades import GRADES
from ocm_regions.tables.report import SCHEME_LABELS, ProviderGrade, ReportRow, grades_by_provider

Macros = dict[str, object]

_SPECIALS: Final[dict[str, str]] = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


class PaperRow(ReportRow):
    """A region row as the paper tables read it."""

    region_id: RegionId
    city: str
    country: str
    place_id: str
    precision: Precision
    code_nearest_airport: str
    code_major_hub: str
    code_nearby_hub: str


class DocumentAccess(TypedDict):
    """How a document can be reached."""

    access: str


class Retrieved(TypedDict):
    """The date a source was retrieved."""

    retrieved: str


def build_macros(
    rows: Sequence[PaperRow],
    evidence: Sequence[EvidenceRecord],
    calls: Sequence[CallRow],
    coverage: Coverage,
    documents: Sequence[DocumentAccess],
    verification: Sequence[Mapping[str, str]],
    candidates: Sequence[object] = (),
) -> Macros:
    """Every count the paper quotes from the build, as macros.

    Args:
        rows: Region rows.
        evidence: Evidence records.
        calls: Review calls.
        coverage: Coverage per code scheme.
        documents: Indexed documents with how each is reached.
        verification: Quote check results.
        candidates: Region IDs seen in IP ranges but not in a vendor's list.

    Returns:
        Macro values keyed by name.
    """
    grades = Counter(r["grade"] for r in rows)
    return {
        "candidateCount": len(candidates),
        "feedRegions": grades["vendor-feed"],
        "feedConflicts": sum(c["kind"] == "feed-conflict" for c in calls),
        "regionCount": len(rows),
        "providerCount": len({r["provider"] for r in rows}),
        "placeCount": len({r["place_id"] for r in rows if r["place_id"]}),
        "evidenceCount": len(evidence),
        "curatedEvidenceCount": sum(
            e.get("source_type") not in ("vendor_region_list", "peeringdb", "vendor_feed") for e in evidence
        ),
        "callCount": len(calls),
        "highCallCount": sum(c["severity"] == "high" for c in calls),
        "tableDiffCount": sum(r["iata_nearest_airport"] != r["iata_major_hub"] for r in rows),
        "airportRegions": coverage["iata_nearest_airport"]["regions"],
        "metroRegions": coverage["iata_metro"]["regions"],
        "metroDistinct": coverage["iata_metro"]["distinct"],
        "locodeRegions": coverage["unlocode"]["regions"],
        "clliRegions": coverage["clli"]["regions"],
        "tableADistinct": coverage["iata_nearest_airport"]["distinct"],
        "tableBDistinct": coverage["iata_major_hub"]["distinct"],
        "tableCDistinct": coverage["iata_nearby_hub"]["distinct"],
        **table_c_macros(rows),
        "gradeVendorStated": grades["vendor-stated"],
        "gradeAssumed": grades["assumed"],
        "documentCount": len(documents),
        "documentPublic": sum(d["access"] == "public" for d in documents),
        "documentRestricted": sum(d["access"] != "public" for d in documents),
        "gradeVendorDocument": grades["vendor-document"],
        "gradePublicRecord": grades["public-record"],
        "gradeReported": grades["reported"],
        "gradePartial": grades["partial"],
        "gradeNetwork": grades["network-presence"],
        "precisionCity": sum(r["precision"] == "city" for r in rows),
        "precisionCoarse": sum(r["precision"] not in ("city", "") for r in rows),
        "metroOrAirportDistinct": coverage["metro_or_airport"]["distinct"],
        "excludedCount": len(read_excluded()),
        "verifyTotal": len(verification),
        "verifyFound": sum(v["status"] == "quote-found" for v in verification),
        "verifySaved": sum(v["status"] == "quote-found-saved-copy" for v in verification),
        "verifyNoQuote": sum(v["status"] == "no-quote" for v in verification),
        "verifyShort": sum(
            v["status"].startswith("quote-found") and int(v.get("quote_chars") or 0) < 20
            for v in verification
        ),
        "verifyNotFound": sum(v["status"] == "quote-not-found" for v in verification),
        "placedByDocument": sum(r.get("place_basis") == "document" for r in rows),
        "placedByMemory": sum(r.get("place_basis") == "memory" for r in rows),
        "placedNone": sum(not r["place_id"] for r in rows),
        "verifyFormat": sum(v["status"] == "format-not-checked" for v in verification),
        "verifyUnreachable": sum(v["status"] == "unreachable" for v in verification),
    }


def code_table(rows: Iterable[PaperRow]) -> str:
    """The appendix table of every region and its code in each table.

    Args:
        rows: Region rows.

    Returns:
        The LaTeX source of the table.
    """
    head = r"Vendor & Region & Place & Country & Evidence & Nearest airport & Major hub & Nearby hub \\"
    caption = r"\caption{Every region and its code in each table. Bold marks a nearby-hub code that differs from the major hub.}\label{tab:codes}\\"
    lines = [
        r"\begin{longtable}{llllllll}",
        caption,
        r"\toprule",
        head,
        r"\midrule",
        r"\endfirsthead",
        r"\toprule",
        head,
        r"\midrule",
        r"\endhead",
        r"\bottomrule",
        r"\endlastfoot",
        *(_code_row(row) for row in sorted(rows, key=lambda r: (r["provider"], r["region_id"]))),
        r"\end{longtable}",
        "",
    ]
    return "\n".join(lines)


def date_span(values: Iterable[str]) -> str:
    """The first and last of the dates as one phrase, without repeating a shared month.

    Args:
        values: ISO dates.

    Returns:
        The phrase.
    """
    days = sorted(date.fromisoformat(v) for v in values if v)
    first, last = days[0], days[-1]
    if first == last:
        return _day(first)
    same_month = (first.year, first.month) == (last.year, last.month)
    return f"{_day(first, not same_month)} to {_day(last)}"


def latex_escape(text: object) -> str:
    """Text with LaTeX special characters escaped.

    Args:
        text: Value to escape.

    Returns:
        The escaped text.
    """
    return "".join(_SPECIALS.get(c, c) for c in str(text))


def macros_tex(values: Mapping[str, object]) -> str:
    r"""LaTeX ``\newcommand`` lines for the given names and values.

    Args:
        values: Macro values keyed by name.

    Returns:
        The LaTeX source.
    """
    return "".join(f"\\newcommand{{\\{k}}}{{{v}}}\n" for k, v in values.items())


def read_excluded() -> list[dict[str, str]]:
    """The regions left out of the tables, from the output folder.

    Returns:
        The rows.
    """
    path = Path(__file__).resolve().parents[3] / "output" / "excluded_regions.csv"
    return [dict(row) for row in csv.DictReader(path.open(encoding="utf-8"))] if path.exists() else []


def release_macros(
    manifest: Iterable[Retrieved], materials: Iterable[Retrieved], citation: str
) -> dict[str, str]:
    """Macros for the version and retrieval dates; raises ValueError when the citation file has no version.

    Args:
        manifest: Rows of the dataset list.
        materials: Rows of the URL index.
        citation: Text of the citation file.

    Returns:
        Macro values keyed by name.

    Raises:
        ValueError: If the citation file has no version.
    """
    found = re.search(r'^version: "?([^"\n]+)"?', citation, re.M)
    if not found:
        raise ValueError("citation file has no version")
    version = found[1]
    return {
        "listsRetrieved": date_span(r["retrieved"] for r in manifest),
        "documentsRetrieved": date_span(r["retrieved"] for r in materials),
        "datasetVersion": version,
    }


def table_c_macros(rows: Iterable[PaperRow]) -> dict[str, int]:
    """Counts of regions whose nearby-hub code differs from the nearest-airport and major-hub codes.

    Args:
        rows: Region rows.

    Returns:
        Macro values keyed by name.
    """
    coded = [r for r in rows if r["iata_nearby_hub"]]
    return {
        "tableDiffAC": sum(r["iata_nearest_airport"] != r["iata_nearby_hub"] for r in coded),
        "tableDiffBC": sum(r["iata_major_hub"] != r["iata_nearby_hub"] for r in coded),
    }


def write_paper_inputs(
    paper_dir: StrPath,
    rows: Sequence[PaperRow],
    evidence: Sequence[EvidenceRecord],
    calls: Sequence[CallRow],
    coverage: Coverage,
    documents: Sequence[DocumentAccess],
    verification: Sequence[Mapping[str, str]] = (),
    candidates: Sequence[object] = (),
) -> Macros:
    """Write the paper's macros and tables to its generated folder and return the macros.

    Args:
        paper_dir: The paper folder.
        rows: Region rows.
        evidence: Evidence records.
        calls: Review calls.
        coverage: Coverage per code scheme.
        documents: Indexed documents with how each is reached.
        verification: Quote check results.
        candidates: Region IDs seen in IP ranges but not in a vendor's list.

    Returns:
        Macro values keyed by name.
    """
    out = Path(paper_dir) / "generated"
    out.mkdir(parents=True, exist_ok=True)
    macros = build_macros(rows, evidence, calls, coverage, documents, verification, candidates)
    _ = (out / "macros.tex").write_text(macros_tex(macros), encoding="utf-8")
    _ = (out / "coverage.tex").write_text(_coverage_table(coverage, len(rows)), encoding="utf-8")
    _ = (out / "grades.tex").write_text(_grade_table(rows), encoding="utf-8")
    _ = (out / "calls.tex").write_text(_calls_table(calls), encoding="utf-8")
    _ = (out / "code_table.tex").write_text(code_table(rows), encoding="utf-8")
    return macros


def _calls_table(calls: Iterable[CallRow]) -> str:
    """The LaTeX table of calls by severity and kind.

    Args:
        calls: Review calls.

    Returns:
        The LaTeX source of the table.
    """
    lines = [r"\begin{tabular}{llr}", r"\toprule", r"Severity & Kind & Count \\", r"\midrule"]
    for (severity, kind), n in sorted(Counter((c["severity"], c["kind"]) for c in calls).items()):
        lines.append(f"{severity} & {latex_escape(kind)} & {n} \\\\")
    return "\n".join([*lines, r"\bottomrule", r"\end{tabular}", ""])


def _cell(value: str) -> str:
    """An escaped table cell, with a dash for an empty value.

    Args:
        value: Cell text.

    Returns:
        The cell text.
    """
    return latex_escape(value) or "--"


def _code_row(row: PaperRow) -> str:
    """One line of the code table; the nearby-hub code is bold where it differs from the major hub.

    Args:
        row: Region row.

    Returns:
        The LaTeX source of the line.
    """
    capped = _cell(row["code_nearby_hub"])
    if row["iata_nearby_hub"] != row["iata_major_hub"]:
        capped = rf"\textbf{{{capped}}}"
    return (
        " & ".join(
            [
                latex_escape(vendor_name(row["provider"])),
                _cell(row["region_id"]),
                _cell(row["city"]),
                _cell(row["country"]),
                _cell(row["grade"]),
                _cell(row["code_nearest_airport"]),
                _cell(row["code_major_hub"]),
                capped,
            ]
        )
        + " \\\\"
    )


def _coverage_table(coverage: Coverage, total: int) -> str:
    """The LaTeX table of code scheme coverage.

    Args:
        coverage: Coverage per code scheme.
        total: Number of items in all.

    Returns:
        The LaTeX source of the table.
    """
    lines = [
        r"\begin{tabular}{lrrr}",
        r"\toprule",
        r"Scheme & Regions named & Share & Distinct codes \\",
        r"\midrule",
    ]
    for key, label in SCHEME_LABELS.items():
        item = coverage[key]
        lines.append(
            f"{latex_escape(label)} & {item['regions']} of {total} & {item['share'] * 100:.1f}\\% & {item['distinct']} \\\\"
        )
    return "\n".join([*lines, r"\bottomrule", r"\end{tabular}", ""])


def _day(value: date, with_month: bool = True) -> str:
    """A date as day, month and year, or the day alone.

    Args:
        value: Date to print.
        with_month: Whether to print the month and year.

    Returns:
        The date text.
    """
    return f"{value.day} {value:%B %Y}" if with_month else str(value.day)


def _grade_table(rows: Iterable[ProviderGrade]) -> str:
    """The LaTeX table of grades per vendor.

    Args:
        rows: Vendor and grade of each region.

    Returns:
        The LaTeX source of the table.
    """
    counts = grades_by_provider(rows)
    used: list[Grade] = [g for g in GRADES if any(c[g] for c in counts.values())]
    head = " & ".join(["Vendor", "Regions", *[latex_escape(g) for g in used]])
    lines = [r"\begin{tabular}{l" + "r" * (len(used) + 1) + "}", r"\toprule", head + r" \\", r"\midrule"]
    for provider, count in sorted(counts.items()):
        cells = [
            latex_escape(vendor_name(provider)),
            str(sum(count.values())),
            *[str(count[g]) for g in used],
        ]
        lines.append(" & ".join(cells) + r" \\")
    return "\n".join([*lines, r"\bottomrule", r"\end{tabular}", ""])
