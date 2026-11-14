# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""W3C PROV-O export, as JSON-LD, of how each placement was reached."""

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final, TypeAlias, TypedDict

from ocm_regions.common.schema import EvidenceRecord
from ocm_regions.fetch.sources import StrPath
from ocm_regions.model.llm_clients import ModelConfig

Node: TypeAlias = dict[str, object]
Ref: TypeAlias = dict[str, str] | list[dict[str, str]]
Row: TypeAlias = Mapping[str, object]

BY_NOTE: Final[dict[str, str]] = {"rag:": "rag", "quote-search:": "quote-search"}

BY_SOURCE: Final[dict[str, str]] = {
    "vendor_region_list": "parse-region-list",
    "vendor_feed": "parse-feed",
    "code_join": "code-join",
    "peeringdb": "parse-peeringdb",
    "certification": "parse-certificate",
}

CONTEXT: Final[dict[str, str]] = {
    "prov": "http://www.w3.org/ns/prov#",
    "schema": "https://schema.org/",
    "ocm": "https://github.com/opencloudmodelhq/regions/terms#",
    "@base": "https://github.com/opencloudmodelhq/regions/id/",
}

Graph = TypedDict("Graph", {"@context": dict[str, str], "@graph": list[Node]})
MODEL_STEPS: Final = frozenset({"rag"})

REGION_FIELDS: Final[dict[str, str]] = {
    "region_name": "schema:name",
    "city": "schema:addressLocality",
    "country": "schema:addressCountry",
    "provider": "ocm:provider",
    "region_id": "ocm:regionId",
    "grade": "ocm:grade",
    "precision": "ocm:precision",
    "code_nearby_hub": "ocm:codeNearbyHub",
}

SOFTWARE: Final = "agent/ocm-regions"
STEP_PROMPTS: Final[dict[str, str]] = {"rag": "place_rag_v1.txt"}


def build_graph(
    regions: Iterable[Row],
    evidence: Sequence[EvidenceRecord],
    materials: Sequence[Row],
    manifest: Sequence[Row],
    models: Mapping[str, ModelConfig],
    prompts: Mapping[str, str],
    code: str,
    version: str,
) -> Graph:
    """Build the provenance graph of regions, evidence, documents, steps and agents.

    Args:
        regions: Region rows.
        evidence: Evidence records.
        materials: Rows of the URL index.
        manifest: Rows of the dataset list.
        models: Settings per model.
        prompts: SHA-256 per frozen prompt.
        code: Digest of the source code.
        version: Version of the software.

    Returns:
        The JSON-LD graph with its context.
    """
    steps = sorted({method_of(r) for r in evidence} | {"build"})
    publishers = {_text(r, "provider") for r in materials if r.get("provider")}
    graph = _agents(publishers, models, prompts, code, version)
    graph.extend(_step(step, models, prompts) for step in steps)
    graph += _documents(materials, manifest)
    graph += _evidence_nodes(evidence)
    graph.extend(_region(r) for r in regions)
    return {"@context": CONTEXT, "@graph": graph}


def code_digest(folder: StrPath) -> str:
    """SHA-256 over the source files in a folder, in sorted order.

    Args:
        folder: Source folder to hash.

    Returns:
        The hex digest.
    """
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(folder).rglob("*") if p.suffix in {".py", ".html"}):
        digest.update(path.relative_to(folder).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def method_of(record: EvidenceRecord) -> str:
    """The pipeline step that produced an evidence record, from its note or source type.

    Args:
        record: Evidence record.

    Returns:
        The step name.

    Raises:
        ValueError: If no step is known for the record.
    """
    for prefix, step in BY_NOTE.items():
        if record.get("note", "").startswith(prefix):
            return step
    source = record.get("source_type", "")
    if source in BY_SOURCE:
        return BY_SOURCE[source]
    raise ValueError(f"no step known for evidence {record.get('evidence_id')} of type {source}")


def _agents(
    publishers: Iterable[str],
    models: Mapping[str, ModelConfig],
    prompts: Mapping[str, str],
    code: str,
    version: str,
) -> list[Node]:
    """The agent nodes: the software, each vendor and each model with its prompt hashes.

    Args:
        publishers: Vendor identifiers that publish documents.
        models: Settings per model.
        prompts: SHA-256 per frozen prompt.
        code: Digest of the source code.
        version: Version of the software.

    Returns:
        The JSON-LD nodes.
    """
    nodes: list[Node] = [
        {
            "@id": SOFTWARE,
            "@type": ["prov:Agent", "prov:SoftwareAgent", "schema:SoftwareSourceCode"],
            "schema:name": "OpenCloudModel Regions",
            "schema:softwareVersion": version,
            "ocm:codeSha256": code,
        }
    ]
    nodes.extend(
        [
            {"@id": f"agent/vendor/{p}", "@type": ["prov:Agent", "prov:Organization"], "schema:name": p}
            for p in sorted(publishers)
        ]
    )
    nodes.extend(
        [
            {
                "@id": f"agent/model/{name}",
                "@type": ["prov:Agent", "prov:SoftwareAgent"],
                "schema:name": cfg["chat_model"],
                "ocm:modelId": cfg["chat_model_id"],
            }
            for name, cfg in sorted(models.items())
        ]
    )
    nodes.extend(
        [
            {"@id": f"prompt/{name}", "@type": ["prov:Entity", "prov:Plan"], "ocm:sha256": sha}
            for name, sha in sorted(prompts.items())
        ]
    )
    return nodes


def _document(
    url: str, sha256: str, retrieved: str, publisher: str = "", archive: str = "", access: str = ""
) -> Node:
    """The provenance node of a source document, with its hash, date and archive link where known.

    Args:
        url: URL of the document.
        sha256: SHA-256 of the document.
        retrieved: Date the document was retrieved.
        publisher: Publisher of the document.
        archive: Archive link of the document.
        access: How the document can be reached.

    Returns:
        The JSON-LD node.
    """
    node: Node = {"@id": url, "@type": "prov:Entity"}
    node |= {
        key: value
        for key, value in (("ocm:sha256", sha256), ("ocm:retrieved", retrieved), ("ocm:access", access))
        if value
    }
    if publisher:
        node["prov:wasAttributedTo"] = _ref(f"agent/vendor/{publisher}")
    if archive:
        node["prov:alternateOf"] = _ref(archive)
    return node


def _documents(materials: Sequence[Row], manifest: Iterable[Row]) -> list[Node]:
    """One node per source document, from the URL index and the dataset list.

    Args:
        materials: Rows of the URL index.
        manifest: Rows of the dataset list.

    Returns:
        The JSON-LD nodes.
    """
    nodes = {
        _text(r, "url"): _document(
            _text(r, "url"),
            _text(r, "sha256"),
            _text(r, "retrieved"),
            _text(r, "provider"),
            _text(r, "archive_url"),
            _text(r, "access"),
        )
        for r in materials
    }
    for row in manifest:
        _ = nodes.setdefault(
            _text(row, "url"), _document(_text(row, "url"), _text(row, "sha256"), _text(row, "retrieved"))
        )
    archives = sorted({_text(r, "archive_url") for r in materials if r.get("archive_url")} - set(nodes))
    listed: list[Node] = [{"@id": url, "@type": "prov:Entity"} for url in archives]
    return list(nodes.values()) + listed


def _evidence(record: EvidenceRecord, steps: Sequence[str]) -> Node:
    """The provenance node of an evidence record, linked to the steps that found it.

    Args:
        record: Evidence record.
        steps: Pipeline steps that found the record.

    Returns:
        The JSON-LD node.
    """
    node: Node = {
        "@id": f"evidence/{record.get('evidence_id', '')}",
        "@type": "prov:Entity",
        "prov:hadPrimarySource": _ref(record.get("url", "")),
        "prov:wasGeneratedBy": _ref(f"activity/{steps[0]}"),
        "prov:value": record.get("quote", ""),
        "ocm:claim": record.get("claim", ""),
        "ocm:claimValue": record.get("value", ""),
        "ocm:stance": record.get("stance", ""),
        "ocm:sourceType": record.get("source_type", ""),
    }
    if steps[1:]:
        node["ocm:alsoFoundBy"] = _ref(*(f"activity/{s}" for s in steps[1:]))
    if record.get("quote_check"):
        node["ocm:quoteCheck"] = record.get("quote_check", "")
    return node


def _evidence_nodes(evidence: Iterable[EvidenceRecord]) -> list[Node]:
    """One entity per evidence ID; a quote two steps found is made by the first and also found by the rest.

    Args:
        evidence: Evidence records.

    Returns:
        The JSON-LD nodes.
    """
    first: dict[str, EvidenceRecord] = {}
    steps: dict[str, set[str]] = {}
    for record in evidence:
        key = record.get("evidence_id", "")
        _ = first.setdefault(key, record)
        steps.setdefault(key, set()).add(method_of(record))
    return [_evidence(record, sorted(steps[key])) for key, record in first.items()]


def _ref(*targets: str) -> Ref:
    """A JSON-LD reference to one target, or a list of references to several.

    Args:
        targets: Node identifiers to point at.

    Returns:
        The reference or list of references.
    """
    refs = [{"@id": t} for t in targets]
    return refs[0] if len(refs) == 1 else refs


def _region(row: Row) -> Node:
    """The provenance node of a region row.

    Args:
        row: Region row.

    Returns:
        The JSON-LD node.
    """
    node: Node = {
        "@id": f"region/{row['provider']}/{row['region_id']}",
        "@type": "prov:Entity",
        "prov:wasGeneratedBy": _ref("activity/build"),
    }
    node |= {key: row[field] for field, key in REGION_FIELDS.items() if row.get(field)}
    evidence_ids = [e for e in str(row.get("evidence_ids", "")).split(";") if e]
    if evidence_ids:
        node["prov:wasDerivedFrom"] = _ref(*(f"evidence/{e}" for e in evidence_ids))
    return node


def _step(step: str, models: Mapping[str, ModelConfig], prompts: Mapping[str, str]) -> Node:
    """The provenance node of a pipeline step, with the software and any models that ran it.

    Args:
        step: Name of the pipeline step.
        models: Settings per model.
        prompts: SHA-256 per frozen prompt.

    Returns:
        The JSON-LD node.
    """
    agents = [SOFTWARE] + ([f"agent/model/{name}" for name in models] if step in MODEL_STEPS else [])
    node: Node = {
        "@id": f"activity/{step}",
        "@type": "prov:Activity",
        "prov:wasAssociatedWith": _ref(*agents),
    }
    prompt = STEP_PROMPTS.get(step)
    if prompt and prompt in prompts:
        node["prov:used"] = _ref(f"prompt/{prompt}")
    return node


def _text(row: Row, field: str) -> str:
    """A row field as text; blank when missing.

    Args:
        row: Row to read.
        field: Column to read.

    Returns:
        The text.
    """
    return str(row.get(field) or "")
