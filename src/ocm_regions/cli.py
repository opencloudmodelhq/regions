# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Command line entry point."""

import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from functools import cache
from pathlib import Path
from typing import Final, TypeAlias, cast

from ocm_regions.analysis.arearisk import FIELDS as AREA_RISK_FIELDS
from ocm_regions.analysis.arearisk import RiskRegion, area_risk, derive_rivals
from ocm_regions.analysis.capsweep import run_caps
from ocm_regions.analysis.external import run_external
from ocm_regions.analysis.extract_methods import (
    Pick,
    contribution_macros,
    method_macros,
    pick_log_odds,
    pick_most_common,
    picks_from_model,
    public_rows,
    score_method,
)
from ocm_regions.analysis.heldout import run_heldout, run_memory
from ocm_regions.analysis.method_figures import write_method_figures
from ocm_regions.analysis.method_stats import gather, write_method_stats
from ocm_regions.analysis.mutation import MutationResult, run_mutation
from ocm_regions.analysis.quote_review import run_review
from ocm_regions.analysis.sensitivity import run_sensitivity
from ocm_regions.common import data, layout
from ocm_regions.common.schema import EvidenceRecord, Place, Point, ProviderId, RegionId, RegionKey
from ocm_regions.evidence.extract import FoundRow, extract_from_text
from ocm_regions.evidence.handcheck import write_sheets
from ocm_regions.evidence.joins import REPORT_FIELDS as JOIN_REPORT_FIELDS
from ocm_regions.evidence.joins import JoinReport, provider_joins
from ocm_regions.evidence.passages import corpus_text_of, passage_rows
from ocm_regions.evidence.scripted import Found, method_grades, scripted_evidence
from ocm_regions.evidence.verify import verify_evidence
from ocm_regions.fetch.corpus import (
    ARCHIVE_FIELDS,
    CorpusRow,
    InstallReport,
    archive_requests,
    corpus_rows,
    export_downloads,
    install_missing,
    missing_notice,
    wanted_by_person,
    write_rows,
)
from ocm_regions.fetch.corpus import REPORT_FIELDS as CORPUS_REPORT_FIELDS
from ocm_regions.fetch.materials import (
    FetchResult,
    IndexRow,
    archivable,
    fetch_materials,
    read_index,
    read_saved,
    record_hashes,
    refresh_index,
    save_list,
    write_fetch_report,
    write_index,
)
from ocm_regions.fetch.snapshots import SnapshotRow, collect_urls, retry_failed, write_snapshots
from ocm_regions.fetch.sources import ManifestRow, StrPath, fetch_all
from ocm_regions.model.document_types import TypedDocument, classify_document, read_with_types, write_types
from ocm_regions.model.llm import (
    DOCUMENT_TYPES,
    PAPER_MODEL,
    Client,
    Comparison,
    ScoredRow,
    method_result_files,
    model_config,
    model_output,
    repeat_sample,
    repeatability,
    run_method,
    saved_copies,
    saved_text_of,
    write_comparison,
    write_manifest,
    write_paper_files,
)
from ocm_regions.model.llm import document_text as _document_text
from ocm_regions.model.llm_clients import ModelConfig
from ocm_regions.place.hubs import airport_ranks
from ocm_regions.place.placement import ModelAnswer, load_mappings, load_places
from ocm_regions.place.placer import PlacerRow, load_gazetteer, run_placer
from ocm_regions.publish.paper import Retrieved, macros_tex, release_macros, write_paper_inputs
from ocm_regions.publish.provcheck import prov_document, shape_problems
from ocm_regions.publish.provenance import build_graph, code_digest
from ocm_regions.publish.site import render_site
from ocm_regions.tables.build import build, region_changes, run_until
from ocm_regions.tables.grades import weak_regions
from ocm_regions.tables.report import verification_section, write_report

Command: TypeAlias = Callable[[argparse.Namespace], int]

ROOT: Final = Path(__file__).resolve().parents[2]
MANIFEST: Final = layout.datasets(ROOT)
MATERIALS: Final = layout.documents(ROOT)
OUTPUT: Final = ROOT / "output"
RAW: Final = ROOT / "data" / "raw"

STAT_MODELS: Final[dict[str, str]] = {
    "rag": "RAG, Qwen 3.8",
    "memory": "Model, no documents",
    "label": "Model, label only",
    "rag-qwen3.6": "RAG, Qwen 3.6",
    "rag-glm-4.7": "RAG, GLM 4.7 Flash",
}


def cmd_archive_missing(_args: argparse.Namespace) -> int:
    """Ask the Wayback Machine to save documents that have no archive link, and write the report.

    Returns:
        The process exit status; 0 on success.
    """
    report = archive_requests(archivable(_documents(), _read_saved))
    write_rows(report, OUTPUT / "archive_requests.csv", ARCHIVE_FIELDS)
    print(Counter(r["status"] for r in report))
    return 0


def cmd_area_risk(_args: argparse.Namespace) -> int:
    """For regions placed to an area, test whether a rival town would change a code.

    Returns:
        The process exit status; 0 on success.
    """
    weak = {(r["provider"], r["region_id"]) for r in data.read_csv(OUTPUT / "weak_evidence.csv")}
    regions = [
        r
        for r in data.read_as(OUTPUT / "regions.csv", RiskRegion)
        if (r["provider"], r["region_id"]) in weak or r["grade"] == "public-record"
    ]
    regions = [r for r in regions if r["place_id"]]
    ranks = airport_ranks((RAW / "opentraveldata" / "optd_por_public.csv").read_text(encoding="utf-8"))
    cities = load_gazetteer(RAW).cities
    cap = cast(
        float, json.loads((OUTPUT / "cap_summary.json").read_text(encoding="utf-8"))["selection"]["cap"]
    )
    rivals = derive_rivals(
        regions, data.read_as(OUTPUT / "evidence.csv", EvidenceRecord), load_gazetteer(RAW).areas
    )
    rows = area_risk(regions, rivals, list(data.load_airports(RAW).values()), ranks, cities, cap)
    with (OUTPUT / "area_risk.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter[str](handle, fieldnames=AREA_RISK_FIELDS, restval="")
        writer.writeheader()
        writer.writerows(rows)
    print(Counter(status for status in {(r["provider"], r["region_id"]): r["status"] for r in rows}.values()))
    return 0


def cmd_build(_args: argparse.Namespace) -> int:
    """Build the tables, evidence and calls, then write the report, paper inputs and site.

    Returns:
        The process exit status; 0 on success.
    """
    rows, evidence, calls = build(RAW, ROOT, OUTPUT)
    verification = (
        data.read_csv(OUTPUT / "evidence_verification.csv")
        if (OUTPUT / "evidence_verification.csv").exists()
        else []
    )
    extra = verification_section(verification) if verification else []
    coverage = write_report(rows, calls, OUTPUT, extra)
    documents = _documents()
    _write_csv(OUTPUT / "weak_evidence.csv", weak_regions(rows, calls, evidence))
    _ = write_paper_inputs(
        ROOT / "paper",
        rows,
        evidence,
        calls,
        coverage,
        documents,
        verification,
        data.read_csv(OUTPUT / "candidate_regions.csv"),
    )
    release = release_macros(
        data.read_as(MANIFEST, Retrieved),
        read_index(MATERIALS),
        (ROOT / "CITATION.cff").read_text(encoding="utf-8"),
    )
    _ = (ROOT / "paper" / "generated" / "release_macros.tex").write_text(
        macros_tex(release), encoding="utf-8"
    )
    (ROOT / "site").mkdir(exist_ok=True)
    _ = (ROOT / "site" / "index.html").write_text(
        render_site(rows, evidence, calls, coverage, documents), encoding="utf-8"
    )
    print(
        f"{len(rows)} regions, {len(evidence)} evidence records, {len(calls)} calls, {len(documents)} documents"
    )
    return 0


def cmd_caps(_args: argparse.Namespace) -> int:
    """Run the cap analysis and print the selected cap.

    Returns:
        The process exit status; 0 on success.
    """
    result = run_caps(OUTPUT / "regions.csv", RAW, OUTPUT, ROOT / "paper")
    chosen = result["selection"]
    print(
        f"selected cap {chosen['cap']:.0f} km; stable range {chosen['low']:.1f} to {chosen['high']:.1f} km; F1 peak at {chosen['peak']:.1f} km"
    )
    return 0


def cmd_changes(_args: argparse.Namespace) -> int:
    """Compare the rebuilt regions with the last commit, so a reader sees what a corpus change moved.

    Returns:
        The process exit status; 0 on success.
    """
    shown = subprocess.run(
        ["git", "show", "HEAD:output/regions.csv"], capture_output=True, text=True, cwd=ROOT, check=False
    ).stdout
    rows = region_changes(list(csv.DictReader(io.StringIO(shown))), data.read_csv(OUTPUT / "regions.csv"))
    if rows:
        _write_csv(OUTPUT / "changes.csv", rows)
    else:
        _ = (OUTPUT / "changes.csv").write_text("provider,region_id,field,before,after\n", encoding="utf-8")
    print(len(rows), "changes from the last commit; see output/changes.csv")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Score the rules and one model's methods on the same regions.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The process exit status; 0 on success.
    """
    model = cast(str, args.model)
    folder = model_output(OUTPUT / "llm", model)
    rules = data.read_as(OUTPUT / "placer.csv", PlacerRow)
    methods = {p.stem: data.read_as(p, ScoredRow) for p in method_result_files(sorted(folder.glob("*.csv")))}
    overturned = [
        (ProviderId(r["provider"]), RegionId(r["region_id"])) for r in data.read_csv(layout.overturned(ROOT))
    ]
    path = write_comparison(folder, rules, methods, _reference_points(), overturned, _llm_config(model))
    if model == PAPER_MODEL:
        write_paper_files(
            cast(Comparison, json.loads(path.read_text(encoding="utf-8"))), ROOT / "paper" / "generated"
        )
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_corpus_export(args: argparse.Namespace) -> int:
    """Copy to a folder what a fresh run reported it lacks, as a person would download those files.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The process exit status; 0 on success.
    """
    wanted = wanted_by_person(data.read_as(cast(str, args.report), InstallReport))
    export_downloads(_corpus(), RAW, Path(cast(str, args.to)), wanted)
    print(len(wanted), "files wanted")
    return 0


def cmd_document_types(args: argparse.Namespace) -> int:
    """Ask the model what each saved document is; the answer is frozen by prompt, model and temperature.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The process exit status; 0 on success.
    """
    model = cast(str, args.model)
    client = Client(_llm_config(model), RAW / "llm_cache")
    rows = [
        classify_document(row, saved_text_of({"provider": row["provider"], "url": row["url"]}, RAW), client)
        for row in read_index(MATERIALS)
    ]
    folder = model_output(OUTPUT / "llm", model)
    folder.mkdir(parents=True, exist_ok=True)
    write_types(rows, folder / DOCUMENT_TYPES)
    print(Counter(r["kind"] for r in rows))
    return 0


def cmd_external(_args: argparse.Namespace) -> int:
    """Compare places with an outside dataset and codes with an operator's own; no model is called.

    Returns:
        The process exit status; 0 on success.
    """
    ranks = airport_ranks((RAW / "opentraveldata" / "optd_por_public.csv").read_text(encoding="utf-8"))
    cap = cast(
        float, json.loads((OUTPUT / "cap_summary.json").read_text(encoding="utf-8"))["selection"]["cap"]
    )
    places, codes = run_external(
        data.read_csv(OUTPUT / "regions.csv"),
        RAW,
        OUTPUT,
        ROOT / "paper",
        load_gazetteer(RAW),
        list(data.load_airports(RAW).values()),
        ranks,
        cap,
    )
    print(places | {"only_outside": len(places["only_outside"])}, codes)
    return 0


def cmd_extract(_args: argparse.Namespace) -> int:
    """Extract lines that name a region and a city from the corpus.

    Returns:
        The process exit status; 0 on success.
    """
    gaz = load_gazetteer(RAW)
    found: list[FoundRow] = []
    for region in data.load_regions(RAW):
        for path in saved_copies(RAW, region.provider):
            found += extract_from_text(region, _saved_text(path), gaz, path.name)
    found.sort(key=lambda r: (r["provider"], r["region_id"], r["document"], r["city"], r["quote"]))
    _write_csv(RAW / "extracted.csv", found)
    print(len(found), "lines tie a region to a place")
    return 0


def cmd_extract_methods(_args: argparse.Namespace) -> int:
    """Score the extraction methods against the reference.

    Returns:
        The process exit status; 0 on success.
    """
    reference = _reference_points()
    picks = _extraction_picks(data.read_as(RAW / "extracted.csv", FoundRow))
    scores = {name: score_method(found, reference) for name, found in picks.items()}
    _ = (OUTPUT / "extraction_methods.json").write_text(json.dumps(scores, indent=1) + "\n", encoding="utf-8")
    for name in ("traditional", "statistical"):
        _write_csv(OUTPUT / f"extracted_{name}.csv", public_rows(picks[name]))
    mutation = cast(MutationResult, json.loads((OUTPUT / "quote_mutation.json").read_text(encoding="utf-8")))
    grades = method_grades(data.read_as(OUTPUT / "evidence.csv", EvidenceRecord))
    _ = (OUTPUT / "model_contribution.json").write_text(json.dumps(grades, indent=1) + "\n", encoding="utf-8")
    macros = method_macros(scores, mutation) | contribution_macros(
        grades, data.read_csv(OUTPUT / "area_risk.csv"), read_index(MATERIALS)
    )
    _ = (ROOT / "paper" / "generated" / "method_macros.tex").write_text(macros_tex(macros), encoding="utf-8")
    print({k: (v["answered"], v["matched"]) for k, v in scores.items()})
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """Install hand-downloaded files, then download the datasets and documents.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The process exit status; 0 on success.
    """
    downloads, force = cast(str, args.downloads), cast(bool, args.force)
    _ = _install_corpus(downloads)
    changes = fetch_all(RAW, MANIFEST, force=force)
    print(changes)
    if not args.no_documents:
        report = fetch_materials(read_index(MATERIALS), RAW, force=force)
        write_fetch_report(report, OUTPUT / "source_fetch_report.csv")
        print(Counter(r["status"] for r in report))
    _tell_reader(_install_corpus(downloads))
    return 0


def cmd_handcheck_passages(args: argparse.Namespace) -> int:
    """Fill a hand-check sheet with passages from the corpus.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The process exit status; 0 on success.
    """
    text_of = corpus_text_of(RAW, data.read_csv(MATERIALS), data.read_csv(MANIFEST))
    rows = passage_rows(data.read_csv(Path(cast(str, args.sheet)).expanduser()), text_of)
    (ROOT / "evaluation").mkdir(exist_ok=True)
    _write_csv(ROOT / "evaluation" / "pass1_passages.csv", rows)
    print(Counter(r["flag"] or "passage" for r in rows))
    return 0


def cmd_handcheck_sheets(_args: argparse.Namespace) -> int:
    """Draw the hand-check sample and write the blank sheets.

    Returns:
        The process exit status; 0 on success.
    """
    chosen = write_sheets(OUTPUT / "regions.csv", OUTPUT / "weak_evidence.csv", ROOT / "evaluation")
    print(Counter(r["group"] for r in chosen))
    return 0


def cmd_heldout(_args: argparse.Namespace) -> int:
    """Score the frozen rules on Fastly and Zenlayer; no model is called.

    Returns:
        The process exit status; 0 on success.
    """
    ranks = airport_ranks((RAW / "opentraveldata" / "optd_por_public.csv").read_text(encoding="utf-8"))
    cap = cast(
        float, json.loads((OUTPUT / "cap_summary.json").read_text(encoding="utf-8"))["selection"]["cap"]
    )
    sites, labels = run_heldout(
        RAW, OUTPUT, ROOT / "paper", load_gazetteer(RAW), list(data.load_airports(RAW).values()), ranks, cap
    )
    print(sites, labels)
    return 0


def cmd_heldout_memory(args: argparse.Namespace) -> int:
    """Ask one model where the held-out regions are, showing it only their identifiers.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The process exit status; 0 on success.
    """
    client = Client(_llm_config(cast(str, args.model)), RAW / "llm_cache")
    print(run_memory(RAW, OUTPUT, client, load_gazetteer(RAW)))
    return 0


def cmd_joins(_args: argparse.Namespace) -> int:
    """Derive public-record joins from the corpus; the build reads the result as evidence.

    Returns:
        The process exit status; 0 on success.
    """
    regions, placed, gaz, materials = (
        data.load_regions(RAW),
        _placed_points(),
        load_gazetteer(RAW),
        _documents(),
    )
    records: list[EvidenceRecord] = []
    report: list[JoinReport] = []
    for row in data.read_csv(layout.operators(ROOT)):
        found, seen = provider_joins(
            ProviderId(row["provider"]), regions, placed, materials, row["operator"], RAW, gaz
        )
        records += found
        report += seen
    _ = (OUTPUT / "joins.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    write_rows(report, OUTPUT / "joins_report.csv", JOIN_REPORT_FIELDS)
    print(Counter(r["status"] for r in report), Counter(r.get("source_type") for r in records))
    return 0


def cmd_llm(args: argparse.Namespace) -> int:
    """Run the chosen placement methods with one model.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The process exit status; 0 on success.
    """
    config = _llm_config(cast(str, args.model))
    folder = model_output(OUTPUT / "llm", cast(str, args.model))
    client = Client(config, RAW / "llm_cache")
    rows = run_method(
        args.method,
        data.load_regions(RAW),
        client,
        load_gazetteer(RAW),
        RAW,
        RAW / "rag_index",
        folder,
        args.split,
    )
    _write_run_manifest(folder, config, client)
    print(args.method, Counter(r["status"] for r in rows))
    return 0


def cmd_llm_repeat(args: argparse.Namespace) -> int:
    """Ask again without the cache on a fixed sample, and compare with the recorded run.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The process exit status; 0 on success.
    """
    config, folder = _llm_config(cast(str, args.model)), model_output(OUTPUT / "llm", cast(str, args.model))
    sample = repeat_sample(data.load_regions(RAW), args.sample)
    rows = run_method(
        args.method,
        sample,
        Client(config, RAW / "llm_cache", fresh=True),
        load_gazetteer(RAW),
        RAW,
        RAW / "rag_index",
        folder / "repeat",
    )
    keys = {(str(r["provider"]), str(r["region_id"])) for r in rows}
    recorded = [
        r for r in data.read_csv(folder / f"{args.method}.csv") if (r["provider"], r["region_id"]) in keys
    ]
    result = repeatability(recorded, rows)
    _ = (folder / f"repeatability_{args.method}.json").write_text(
        json.dumps(result, indent=1) + "\n", encoding="utf-8"
    )
    print({"regions": result["regions"], "identical": result["identical"], "share": result["share"]})
    return 0


def cmd_materials(args: argparse.Namespace) -> int:
    """Fill in added rows and archive copies; --record pins hashes from the last fetch report.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The process exit status; 0 on success.
    """
    previous = read_index(MATERIALS)
    snapshots = (
        data.read_as(OUTPUT / "snapshots.csv", SnapshotRow) if (OUTPUT / "snapshots.csv").exists() else []
    )
    rows = refresh_index(cast(list[Mapping[str, str]], previous), snapshots)
    if args.record:
        rows = record_hashes(
            rows, data.read_as(OUTPUT / "source_fetch_report.csv", FetchResult), date.today().isoformat()
        )
    write_index(rows, MATERIALS)
    print(Counter(r["fetch"] for r in rows), sum(bool(r["sha256"]) for r in rows), "hashed")
    return 0


def cmd_method_stats(_args: argparse.Namespace) -> int:
    """Paired tests, agreement and error spread over the answers already saved; no model is called.

    Returns:
        The process exit status; 0 on success.
    """
    llm = OUTPUT / "llm"
    files = {
        "rag": model_output(llm, PAPER_MODEL) / "rag.csv",
        "memory": model_output(llm, PAPER_MODEL) / "memory.csv",
        "label": model_output(llm, PAPER_MODEL) / "label.csv",
        "rag-qwen3.6": model_output(llm, "chain-qwen3.6") / "rag.csv",
        "rag-glm-4.7": model_output(llm, "chain-glm-4.7") / "rag.csv",
    }
    methods = gather(data.read_as(OUTPUT / "placer.csv", PlacerRow), files)
    places: dict[tuple[str, str], Point] = {
        (provider, region_id): point for (provider, region_id), point in _reference_points().items()
    }
    models = tuple(m for m in STAT_MODELS if m in methods)
    labels = {"rules": "Rules", **STAT_MODELS}
    report = write_method_stats(
        methods, places, models, OUTPUT / "method_stats.json", ROOT / "paper" / "generated", labels
    )
    write_method_figures(report, labels, ("rules", *models), ROOT / "paper" / "generated")
    for pair, test in report["mcnemar"].items():
        print(f"{pair}: {test['only_first']} vs {test['only_second']} discordant, p={test['p']:.4f}")
    return 0


def cmd_mutation(_args: argparse.Namespace) -> int:
    """Swap quotes and cities in evidence records and count how many still pass the check.

    Returns:
        The process exit status; 0 on success.
    """
    gen = ("vendor_region_list", "peeringdb", "vendor_feed")
    records = [r for r in data.read_csv(OUTPUT / "evidence.csv") if r["source_type"] not in gen]
    result = run_mutation(records, _saved_page_reader())
    _ = (OUTPUT / "quote_mutation.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    shown = {
        name: {"records": tally["records"], "false_pass": tally["false_pass"]}
        for name, tally in (("other_quote", result["other_quote"]), ("other_city", result["other_city"]))
    }
    print(shown, result["control"])
    return 0


def cmd_place(_args: argparse.Namespace) -> int:
    """Place every region by rule and print the scores.

    Returns:
        The process exit status; 0 on success.
    """
    _rows, summary = run_placer(RAW, ROOT, OUTPUT, ROOT / "paper")
    print(
        f"development {summary['development']}\nholdout {summary['holdout']}\nby step {summary['by_step']}\noverturned {summary['overturned']}"
    )
    return 0


def cmd_provenance(_args: argparse.Namespace) -> int:
    """Write the provenance graph as JSON-LD.

    Returns:
        The process exit status; 0 on success.
    """
    version = cast(str, tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])
    prompts = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / "prompts").glob("*.txt"))
    }
    models = {PAPER_MODEL: cast(ModelConfig, json.loads(layout.models(ROOT).read_text())[PAPER_MODEL])}
    graph = build_graph(
        data.read_csv(OUTPUT / "regions.csv"),
        data.read_as(OUTPUT / "evidence.csv", EvidenceRecord),
        data.read_csv(MATERIALS),
        data.read_csv(MANIFEST),
        models,
        prompts,
        code_digest(ROOT / "src" / "ocm_regions"),
        version,
    )
    _ = (OUTPUT / "provenance.jsonld").write_text(
        json.dumps(graph, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(graph['@graph'])} provenance nodes")
    return 0


def cmd_provenance_check(_args: argparse.Namespace) -> int:
    """Check the provenance file parses as PROV and conforms to the shapes.

    Returns:
        The process exit status; 0 on success.
    """
    text = (OUTPUT / "provenance.jsonld").read_text(encoding="utf-8")
    records = prov_document(text).get_records()
    problems = shape_problems(text)
    for problem in problems:
        print(problem)
    print(f"{len(records)} PROV records read, {len(problems)} shape problems")
    return 1 if problems else 0


def cmd_quote_review(_args: argparse.Namespace) -> int:
    """Score the author's verdicts on every quoted evidence record.

    Returns:
        The process exit status; 0 on success.
    """
    print(run_review(ROOT / "evaluation", OUTPUT, ROOT / "paper"))
    return 0


def cmd_regions(_args: argparse.Namespace) -> int:
    """Write the published region list.

    Returns:
        The process exit status; 0 on success.
    """
    regions = data.load_published_regions(RAW)
    OUTPUT.mkdir(exist_ok=True)
    excluded = [
        {
            "provider": r.provider,
            "region_id": r.region_id,
            "partition": r.partition,
            "reason": data.EXCLUDED_PARTITIONS[r.partition].reason,
            "marked_by": data.EXCLUDED_PARTITIONS[r.partition].marked_by,
        }
        for r in regions
        if r.partition in data.EXCLUDED_PARTITIONS
    ]
    _write_csv(OUTPUT / "excluded_regions.csv", excluded)
    fields = ["provider", "partition", "region_id", "name", "stated_location", "vendor_code"]
    published: list[dict[str, object]] = [{f: cast(object, getattr(r, f)) for f in fields} for r in regions]
    _write_csv(OUTPUT / "published_regions.csv", published)
    print(
        f"{len(regions)} regions published by {len({r.provider for r in regions})} vendors, {len(excluded)} of them out of scope"
    )
    return 0


def cmd_save_list(_args: argparse.Namespace) -> int:
    """Write the list of documents to save by hand.

    Returns:
        The process exit status; 0 on success.
    """
    rows = save_list(read_index(MATERIALS), _read_saved)
    _write_csv(OUTPUT / "archive_save_list.csv", rows)
    print(Counter(r["action"] for r in rows))
    return 0


def cmd_scripted(args: argparse.Namespace) -> int:
    """Read evidence from the corpus by code, with the model's answers where a run has recorded them.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The process exit status; 0 on success.
    """
    docs = {r["doc_id"]: r for r in _documents()}
    found: dict[RegionKey, list[Found]] = {}
    for row in data.read_as(RAW / "extracted.csv", FoundRow):
        doc = docs.get(Path(row["document"]).stem)
        if doc:
            found.setdefault((row["provider"], row["region_id"]), []).append((row, doc))
    rag = model_output(OUTPUT / "llm", cast(str, args.model)) / "rag.csv"
    answers = (
        {
            (r["provider"], r["region_id"]): r
            for r in data.read_as(rag, ModelAnswer)
            if r["status"] == "answered"
        }
        if rag.exists()
        else {}
    )
    records = scripted_evidence(
        data.load_regions(RAW), _placed_points(), answers, found, docs, load_gazetteer(RAW).areas
    )
    _ = (OUTPUT / "scripted_evidence.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    print(Counter((r.get("source_type"), r.get("stance")) for r in records))
    return 0


def cmd_sensitivity(_args: argparse.Namespace) -> int:
    """Rerun the rules under alternative settings.

    Returns:
        The process exit status; 0 on success.
    """
    rows = run_sensitivity(RAW, ROOT, OUTPUT, ROOT / "paper")
    print(f"{len(rows)} settings tested")
    return 0


def cmd_snapshots(args: argparse.Namespace) -> int:
    """Look up archive snapshots for the cited URLs.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The process exit status; 0 on success.
    """
    path = OUTPUT / "snapshots.csv"
    pairs = collect_urls(data.read_csv(OUTPUT / "evidence.csv"), read_index(MATERIALS))
    previous = data.read_as(path, SnapshotRow) if path.exists() else []
    rows = retry_failed(previous, path) if args.retry else write_snapshots(pairs, path, previous=previous)
    print(Counter(r["status"] for r in rows))
    return 0


def cmd_verify(_args: argparse.Namespace) -> int:
    """Check every quote against its saved document and write the results.

    Returns:
        The process exit status; 0 on success.
    """
    results = verify_evidence(
        [OUTPUT / "evidence.csv"], OUTPUT / "evidence_verification.csv", saved=_saved_document
    )
    print(Counter(r["status"] for r in results))
    return 0


def ensure_folders(root: Path) -> None:
    """A clean checkout holds no generated files, so the folders commands write into must exist first.

    Args:
        root: Repository root.
    """
    for folder in (root / "output", root / "paper" / "generated"):
        folder.mkdir(parents=True, exist_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and run the chosen command.

    Args:
        argv: Command-line arguments; the process arguments when None.

    Returns:
        The process exit status; 0 on success.
    """
    parser = argparse.ArgumentParser(prog="ocm-regions")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="install the corpus and report what is missing")
    _ = fetch.add_argument(
        "--force", action="store_true", help="download again even when a local copy exists"
    )
    _ = fetch.add_argument(
        "--downloads",
        default=os.environ.get("OCM_REGIONS_DOWNLOADS", "downloads"),
        help="folder of files downloaded by hand",
    )
    _ = fetch.add_argument(
        "--no-documents", action="store_true", help="skip the source documents in sources/documents.csv"
    )
    fetch.set_defaults(run=cmd_fetch)
    commands.add_parser("build", help="build tables, evidence, calls and report").set_defaults(run=cmd_build)
    for stage, about in (
        ("evidence", "attach evidence to each region"),
        ("grade", "grade evidence and set precision"),
        ("codes", "assign codes and write tables"),
        ("calls", "raise calls and write the master table"),
    ):
        commands.add_parser(stage, help=about).set_defaults(run=_stage(stage))
    commands.add_parser("report", help="write the report, explorer page and paper inputs").set_defaults(
        run=cmd_build
    )
    llm = commands.add_parser("llm", help="place regions with a local language model")
    _ = llm.add_argument("method", choices=["label", "memory", "rag"])
    _ = llm.add_argument("--split", choices=["development", "holdout"])
    _ = llm.add_argument(
        "--model",
        default=PAPER_MODEL,
        help="a named model from config/llm_models.json; results go in its own folder",
    )
    llm.set_defaults(run=cmd_llm)
    repeat = commands.add_parser(
        "llm-repeat", help="rerun a fixed sample without the cache and compare with the recorded run"
    )
    _ = repeat.add_argument("--model", required=True)
    _ = repeat.add_argument("--method", choices=["label", "memory", "rag"], default="label")
    _ = repeat.add_argument("--sample", type=int, default=30)
    repeat.set_defaults(run=cmd_llm_repeat)
    materials = commands.add_parser("materials", help="rebuild the index of source documents")
    _ = materials.add_argument("--record", action="store_true", help="pin hashes from the last fetch report")
    materials.set_defaults(run=cmd_materials)
    commands.add_parser(
        "handcheck-sheets", help="write blank sheets for the two hand-check passes"
    ).set_defaults(run=cmd_handcheck_sheets)
    passages = commands.add_parser(
        "handcheck-passages", help="list verbatim passages naming each reviewer city in its cited document"
    )
    _ = passages.add_argument("--sheet", required=True, help="the filled pass 1 sheet")
    passages.set_defaults(run=cmd_handcheck_passages)
    commands.add_parser("save-list", help="list public sources with no archive capture").set_defaults(
        run=cmd_save_list
    )
    commands.add_parser(
        "sensitivity", help="rerun the placer with other values of its round numbers"
    ).set_defaults(run=cmd_sensitivity)
    commands.add_parser(
        "mutation", help="break quotes on purpose and count how many still pass"
    ).set_defaults(run=cmd_mutation)
    commands.add_parser(
        "archive-missing", help="ask the web archive to save indexed documents it lacks"
    ).set_defaults(run=cmd_archive_missing)
    export = commands.add_parser(
        "corpus-export", help="copy files a fresh run lacks to a folder, as a person's downloads would be"
    )
    _ = export.add_argument("--to", required=True)
    _ = export.add_argument("--report", required=True, help="corpus_report.csv from the fresh run")
    export.set_defaults(run=cmd_corpus_export)
    scripted = commands.add_parser("scripted", help="read evidence from the corpus by code")
    _ = scripted.add_argument("--model", default=PAPER_MODEL)
    scripted.set_defaults(run=cmd_scripted)
    commands.add_parser("changes", help="list what moved since the last commit").set_defaults(run=cmd_changes)
    commands.add_parser("joins", help="derive public-record joins from the corpus").set_defaults(
        run=cmd_joins
    )
    commands.add_parser(
        "external", help="compare places with an outside dataset and codes with an operator's own"
    ).set_defaults(run=cmd_external)
    commands.add_parser(
        "heldout", help="score the frozen rules on vendors that had no part in building them"
    ).set_defaults(run=cmd_heldout)
    asked = commands.add_parser(
        "heldout-memory", help="ask the model where held-out regions are from their identifiers alone"
    )
    _ = asked.add_argument("--model", default=PAPER_MODEL)
    asked.set_defaults(run=cmd_heldout_memory)
    commands.add_parser(
        "quote-review", help="score the author's verdicts on every quoted evidence record"
    ).set_defaults(run=cmd_quote_review)
    commands.add_parser(
        "area-risk", help="sort weak regions by whether a rival city changes the area code"
    ).set_defaults(run=cmd_area_risk)
    commands.add_parser(
        "extract-methods", help="pick a city per region by each method and score them"
    ).set_defaults(run=cmd_extract_methods)
    snapshots = commands.add_parser("snapshots", help="find the latest archive capture of every source")
    _ = snapshots.add_argument(
        "--retry", action="store_true", help="look up only the sources whose lookup failed"
    )
    snapshots.set_defaults(run=cmd_snapshots)
    commands.add_parser(
        "extract", help="find region-to-city statements in saved documents by rule"
    ).set_defaults(run=cmd_extract)
    types = commands.add_parser("document-types", help="ask the model what kind of document each source is")
    _ = types.add_argument("--model", default=PAPER_MODEL)
    types.set_defaults(run=cmd_document_types)
    compare = commands.add_parser("compare", help="score rules and model methods on the same regions")
    _ = compare.add_argument("--model", default=PAPER_MODEL, help="the model whose recorded results to score")
    compare.set_defaults(run=cmd_compare)
    commands.add_parser(
        "method-stats", help="paired tests, agreement and error spread for the placement methods"
    ).set_defaults(run=cmd_method_stats)
    commands.add_parser("regions", help="list every region vendors publish").set_defaults(run=cmd_regions)
    commands.add_parser(
        "place", help="place regions by rule and score them against the reference placements"
    ).set_defaults(run=cmd_place)
    commands.add_parser("caps", help="sweep the hub distance cap and write figures").set_defaults(
        run=cmd_caps
    )
    commands.add_parser("verify", help="re-check evidence URLs and quotes").set_defaults(run=cmd_verify)
    commands.add_parser("provenance", help="write the W3C PROV-O record of every placement").set_defaults(
        run=cmd_provenance
    )
    commands.add_parser(
        "provenance-check", help="read the PROV-O record with the W3C PROV library and check its shapes"
    ).set_defaults(run=cmd_provenance_check)
    args = parser.parse_args(argv)
    ensure_folders(ROOT)
    return cast(Command, args.run)(args)


def _corpus() -> list[CorpusRow]:
    """The corpus: every indexed document and dataset.

    Returns:
        The corpus rows.
    """
    return corpus_rows(read_index(MATERIALS), data.read_as(MANIFEST, ManifestRow))


def _documents() -> list[TypedDocument]:
    """The indexed documents with their recorded types.

    Returns:
        The documents.
    """
    return read_with_types(MATERIALS, OUTPUT / "llm" / PAPER_MODEL / DOCUMENT_TYPES)


def _extraction_picks(found: Sequence[FoundRow]) -> dict[str, dict[RegionKey, Pick]]:
    """The city each extraction method picks per region.

    Args:
        found: Lines that name a region and a city.

    Returns:
        Picks keyed by method, then by vendor and region.
    """
    picks: dict[str, dict[RegionKey, Pick]] = {
        "traditional": pick_most_common(found),
        "statistical": pick_log_odds(found),
    }
    for path in method_result_files(sorted((OUTPUT / "llm").glob("**/*.csv"))):
        if path.parent.name != "repeat":
            name = path.stem if path.parent == OUTPUT / "llm" else f"{path.parent.name}/{path.stem}"
            picks[name] = picks_from_model(data.read_csv(path))
    return picks


def _first_version(text: str) -> str:
    """The first line of a tool's output that mentions a version, else its first line.

    Args:
        text: Output of the tool.

    Returns:
        The line.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return next((line for line in lines if "version" in line.lower()), lines[0] if lines else "")


def _install_corpus(folder: StrPath) -> list[InstallReport]:
    """Run before and after fetching: saved files stay, and a pinned download replaces a page that has changed.

    Args:
        folder: Folder that holds hand-downloaded files.

    Returns:
        One report row per corpus file.
    """
    report = install_missing(_corpus(), RAW, folder)
    write_rows(report, OUTPUT / "corpus_report.csv", CORPUS_REPORT_FIELDS)
    print("corpus", Counter(r["status"] for r in report))
    return report


def _llm_config(name: str) -> ModelConfig:
    """The settings of one named model.

    Args:
        name: Model name in the settings file.

    Returns:
        The settings.
    """
    base = json.loads(layout.model_settings(ROOT).read_text(encoding="utf-8"))
    return model_config(base, json.loads(layout.models(ROOT).read_text(encoding="utf-8")), name)


def _placed_points() -> dict[RegionKey, Place]:
    """The place of every mapped region.

    Returns:
        Places keyed by vendor and region.
    """
    places = {p["place_id"]: p for p in load_places(RAW, ROOT)}
    return {
        key: places[m["place_id"]] for key, m in load_mappings(RAW, ROOT).items() if m["place_id"] in places
    }


def _read_saved(row: IndexRow) -> str | None:
    """The saved text of an indexed document, or None.

    Args:
        row: Row of the URL index.

    Returns:
        The text, or None.
    """
    return read_saved(row, RAW)


def _reference_points() -> dict[RegionKey, Point]:
    """The reference coordinates of each region.

    Returns:
        Points keyed by vendor and region.
    """
    return {k: (float(r["lat"]), float(r["lon"])) for k, r in data.load_reference(ROOT).items()}


def _refused() -> set[str]:
    """URLs the last fetch left unread because a robots.txt file disallows them.

    Returns:
        The URLs; empty before any fetch has written its report.
    """
    path = OUTPUT / "source_fetch_report.csv"
    rows = data.read_csv(path) if path.exists() else []
    return {r["url"] for r in rows if r["status"] == "robots-disallowed"}


def _saved_document(record: EvidenceRecord) -> str:
    """The saved text of the document an evidence record cites.

    Args:
        record: Evidence record.

    Returns:
        The text; blank when it is not saved.
    """
    return saved_text_of({"url": record.get("url", ""), "provider": record["provider"]}, RAW)


def _saved_page_reader() -> Callable[[str], str | None]:
    """A cached reader of saved pages by URL.

    Returns:
        The reader function.
    """
    rows = {r["url"]: r for r in read_index(MATERIALS)}
    cache: dict[str, str | None] = {}

    def read(url: str) -> str | None:
        """The saved page for a URL, or None when it is not in the index.

        Args:
            url: URL of the document.

        Returns:
            The page text, or None.
        """
        if url not in cache:
            cache[url] = read_saved(rows[url], RAW) if url in rows else None
        return cache[url]

    return read


@cache
def _saved_text(path: Path) -> str:
    """The text of a saved file.

    Args:
        path: Saved file to read.

    Returns:
        The text.
    """
    return _document_text(path)


def _stage(name: str) -> Command:
    """A command that runs the build up to the named stage.

    Args:
        name: Last stage to run.

    Returns:
        The command function.
    """

    def run(_args: argparse.Namespace) -> int:
        """Run the build up to the stage and print the counts.

        Returns:
            The process exit status; 0 on success.
        """
        rows, evidence, calls = run_until(name, RAW, ROOT, OUTPUT)
        print(f"{name}: {len(evidence)} evidence records, {len(rows)} rows, {len(calls)} calls")
        return 0

    return run


def _tell_reader(report: Iterable[InstallReport]) -> None:
    """Print the files a person must download by hand.

    Args:
        report: Results of installing hand-downloaded files.
    """
    refused = _refused()
    if refused:
        print(f"{len(refused)} documents are disallowed by robots.txt and were left unread")
    lines = missing_notice(report, refused)
    if lines:
        print(
            f"{len(lines)} files need a person; put downloads in the --downloads folder and run make fetch again:"
        )
        print("\n".join(lines))


def _version(*command: str) -> str:
    """The version a command reports.

    Args:
        command: Command and arguments to run.

    Returns:
        The version line.
    """
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    return _first_version(done.stdout or done.stderr)


def _write_csv(path: StrPath, rows: Sequence[Mapping[str, object]]) -> None:
    """Write rows to CSV, with columns taken from the first row.

    Args:
        path: CSV file to write.
        rows: Rows to write.
    """
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_run_manifest(folder: Path, config: ModelConfig, client: Client) -> None:
    """Write the manifest of a model run: settings, result hashes, cost and tool versions.

    Args:
        folder: Folder that holds the model's results.
        config: Model settings.
        client: Client used for the run.
    """
    rows = {p.stem: data.read_csv(p) for p in method_result_files(sorted(folder.glob("*.csv")))}
    _ = write_manifest(
        folder,
        config,
        rows,
        client.cost,
        _version("git", "rev-parse", "HEAD"),
        _version("docker", "model", "version"),
    )


if __name__ == "__main__":
    sys.exit(main())
