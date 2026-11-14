# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Place regions with a local language model and check every quote in code."""

import csv
import hashlib
import json
import re
import subprocess
import urllib.error
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Final, Literal, NotRequired, Protocol, TypeAlias, TypedDict, cast

from ocm_regions.common.geo import haversine_km
from ocm_regions.common.schema import Point, ProviderId, RegionId, RegionKey
from ocm_regions.common.stats import wilson
from ocm_regions.evidence.verify import normalize as _normalize
from ocm_regions.evidence.verify import pdf_both_orders, quote_found
from ocm_regions.fetch.materials import visible_words as _visible_words
from ocm_regions.fetch.sources import StrPath, ungzipped
from ocm_regions.model.document_types import read_with_types
from ocm_regions.model.llm_clients import Client as Client
from ocm_regions.model.llm_clients import ModelConfig
from ocm_regions.model.prompts import PROMPTS as PROMPTS
from ocm_regions.model.prompts import Answer, PromptName, fill, prompt_hash
from ocm_regions.model.prompts import parse_answer as parse_answer
from ocm_regions.model.prompts import prompt_text as prompt_text
from ocm_regions.place.placer import AGREE_KM, City, Gazetteer, PlacerRow, Split, in_holdout, needs_review
from ocm_regions.place.placer import allowed as _allowed
from ocm_regions.place.placer import norm as _norm
from ocm_regions.place.placer import usable as _usable
from ocm_regions.publish.paper import macros_tex
from ocm_regions.regions.parsers import Region

AnswerStatus: TypeAlias = Literal["abstained", "unsupported", "unresolved", "answered"]
MethodName: TypeAlias = Literal["label", "memory", "rag"]
Score: TypeAlias = dict[str, int]
Embed: TypeAlias = Callable[[list[str]], list[list[float]]]

CHUNK_OVERLAP: Final = 150
CHUNK_SIZE: Final = 900
DOCUMENT_TYPES: Final = "document_types.csv"
FETCHED_DIR: Final = "fetched"

FIELDS: Final = (
    "provider",
    "region_id",
    "split",
    "status",
    "city",
    "country",
    "lat",
    "lon",
    "quote",
    "quote_ok",
    "quote_source",
    "document",
)

LOCATION_WORDS: Final = re.compile(
    r"data ?cent(?:er|re)|region|availability zone|campus|facilit|located", re.I
)

MAX_DROPPED_SHARE: Final = 0.05

METHOD_NAMES: Final[dict[str, str]] = {
    "rules": "Rules",
    "label": "Model, label only",
    "memory": "Model, no documents",
    "rag": "RAG",
}

MIN_NAME_CHARS: Final = 4
PAPER_MODEL: Final = "chain-qwen3.8"
RERANK_POOL: Final = 30
ROOT: Final = Path(__file__).resolve().parents[3]
SAVED_SUFFIXES: Final = frozenset({".txt", ".pdf", ".html", ".md", ".json", ".csv"})
SKIP_DIRS: Final = frozenset({"blocked_responses"})
SOURCE_INDEX: Final = ROOT / "sources" / "documents.csv"

STREET_ADDRESS: Final = re.compile(
    r"\b\d{5}\b|\b\d+\s+\w+\s+(?:Street|Road|Drive|Avenue|Way|Plaza)\b|\b\d+\s+\w+\s+\w+\s+(?:Street|Road|Drive|Avenue|Way|Plaza)\b",
    re.I,
)

TOP_K: Final = 6
WITHHELD_DOCUMENTS: Final = frozenset({"iso27001_2018_trisotech.pdf"})
_fill = fill
_NO_SKIP: Final[frozenset[str]] = frozenset()


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


class Embedder(Protocol):
    """A client that turns texts into vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per text.

        Args:
            texts: Texts to embed.

        Returns:
            The vectors, in text order.
        """
        ...


class Reranker(Protocol):
    """A client that scores documents against a query."""

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Return one relevance score per document, in document order.

        Args:
            query: Query text.
            documents: Documents to score.

        Returns:
            The scores, in document order.
        """
        ...


class RagClient(Chat, Embedder, Reranker, Protocol):
    """A client that can chat, embed and rerank."""


class JudgedRow(TypedDict):
    """A model answer after the checks in code: its status, city, coordinates and quote."""

    status: AnswerStatus
    city: str
    country: str
    lat: float | str
    lon: float | str
    quote: str
    quote_ok: str
    quote_source: str
    document: NotRequired[str]


class MethodRow(JudgedRow):
    """A judged answer for one region, with the split it belongs to."""

    provider: ProviderId
    region_id: RegionId
    split: Split


class ScoredRow(TypedDict):
    """A placement as the comparison reads it, from the rules or from a method's saved answers."""

    provider: ProviderId
    region_id: RegionId
    status: AnswerStatus
    lat: float | str
    lon: float | str
    city: str
    quote_source: NotRequired[str]


class Index(TypedDict):
    """The embedded passages of one vendor's documents, with the document each came from."""

    texts: list[str]
    vectors: list[list[float]]
    dropped: NotRequired[int]
    sources: NotRequired[dict[str, str]]
    corpus_sha256: NotRequired[str]


class MethodScores(TypedDict):
    """One method's scores on each group of regions, and the regions it matched that the rules did not."""

    development: Score
    holdout: Score
    all: Score
    review: Score
    overturned: Score
    rescued: list[list[str]]


class Comparison(TypedDict):
    """The saved comparison of methods: settings, prompt hashes and scores."""

    config: Mapping[str, object]
    prompts: dict[PromptName, str]
    scores: dict[str, MethodScores]


class Repeatability(TypedDict):
    """How many placements were identical between two runs, and which changed."""

    regions: int
    identical: int
    share: float
    changed: list[list[str]]


def build_index(raw: StrPath, vendor: str, client: Embedder, index_dir: StrPath, batch: int = 64) -> Index:
    """Embed a vendor's passages, or reuse the saved index when the corpus has not changed.

    Args:
        raw: The data/raw folder.
        vendor: Vendor identifier.
        client: Embedding client.
        index_dir: Folder that holds embedding indexes.
        batch: Number of texts per embedding request.

    Returns:
        The index.

    Raises:
        RuntimeError: If embedding fails for any passage.
    """
    config = _config(client)
    target = index_folder(index_dir, config) / f"{vendor}.json"
    sources = _index_pieces(raw, vendor)
    fingerprint = hashlib.sha256(json.dumps(sorted(sources)).encode()).hexdigest()
    if target.exists():
        saved = cast(Index, json.loads(target.read_text()))
        if saved.get("corpus_sha256") == fingerprint:
            return saved
    kept, vectors, dropped = embed_all(list(sources), lambda chunk: embed_documents(client, chunk), batch)
    if dropped > len(sources) * MAX_DROPPED_SHARE:
        raise RuntimeError(
            f"embedding failed for {dropped} of {len(sources)} passages from {vendor}; is the model out of memory?"
        )
    index: Index = {
        "texts": kept,
        "vectors": vectors,
        "dropped": dropped,
        "sources": {t: sources[t] for t in kept},
        "corpus_sha256": fingerprint,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_text(json.dumps(index))
    return index


def checkable_text(path: Path) -> str:
    """The text a quote is checked against; a PDF is read in both text orders.

    Args:
        path: Saved document.

    Returns:
        The text to check quotes against.
    """
    return pdf_both_orders(path) if path.suffix == ".pdf" else _document_text(path)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Cut text into overlapping passages, keeping those that mention a location word.

    Args:
        text: Text of the document.
        size: Characters per passage.
        overlap: Characters shared by consecutive passages.

    Returns:
        The passages kept.
    """
    clean = re.sub(r"\s+", " ", text).strip()
    step = max(size - overlap, 1)
    pieces = (clean[start : start + size] for start in range(0, len(clean), step))
    return [p for p in pieces if LOCATION_WORDS.search(p)]


def city_level_quote(quote: str, city: str) -> str:
    """The quote, or only the city when the quote holds a street address.

    Args:
        quote: Quoted text.
        city: City name.

    Returns:
        The quote to record.
    """
    return city if STREET_ADDRESS.search(quote) else quote


def compare_methods(
    rules: Sequence[PlacerRow],
    methods: Mapping[str, Sequence[ScoredRow]],
    places: Mapping[RegionKey, Point],
    overturned: Iterable[RegionKey],
) -> dict[str, MethodScores]:
    """Score every method on the same regions: each split, the hard review set and the overturned placements.

    Args:
        rules: The rules' rows.
        methods: Answers per method.
        places: Reference coordinates per region.
        overturned: Regions whose first placement evidence overturned.

    Returns:
        Scores keyed by method name.
    """
    split = {_key(r): r["split"] for r in rules}
    review = {_key(r) for r in rules if needs_review(r)}
    rule_rows = _rule_rows(rules)
    by_rules = {_key(r) for r in rule_rows if _matches(r, places)}
    development, holdout, turned = (
        {k for k, v in split.items() if v == "development"},
        {k for k, v in split.items() if v == "holdout"},
        set(overturned),
    )
    result: dict[str, MethodScores] = {}
    for name, rows in {"rules": rule_rows, **methods}.items():

        def within(keys: Collection[RegionKey], rows: Sequence[ScoredRow] = rows) -> Score:
            """Score the method on the regions in one group.

            Args:
                keys: Regions in the group.
                rows: Answer rows of the method.

            Returns:
                The score on that group.
            """
            return _score([r for r in rows if _key(r) in keys], places)

        result[name] = {
            "development": within(development),
            "holdout": within(holdout),
            "all": within(set(split)),
            "review": within(review),
            "overturned": within(turned),
            "rescued": sorted(
                list(_key(r))
                for r in rows
                if _key(r) in split and _matches(r, places) and _key(r) not in by_rules
            ),
        }
    return result


def corpus_paths(raw: StrPath, vendor: str, skip: Collection[str] = _NO_SKIP) -> list[Path]:
    """The corpus holds only what a reader's own make fetch downloads.

    Args:
        raw: The data/raw folder.
        vendor: Vendor identifier.
        skip: File names to leave out.

    Returns:
        The paths of the vendor's corpus files.
    """
    return sorted(
        p
        for p in (Path(raw) / "trust_docs" / vendor / FETCHED_DIR).glob("*")
        if p.is_file() and p.stem not in skip
    )


def corpus_text(path: Path) -> str:
    """The text the model reads from a document: visible words for HTML, extracted text for a PDF.

    Args:
        path: Saved document.

    Returns:
        The readable text.
    """
    if path.suffix == ".pdf":
        return _document_text(path)
    text = _document_text(path)
    return " ".join(_visible_words(text) or []) if path.suffix == ".html" else text


def directory_documents(index: StrPath = SOURCE_INDEX) -> set[str]:
    """Third-party listings: their claims are not the vendor's, so the model does not read them.

    Args:
        index: Path of the URL index.

    Returns:
        The URLs of documents typed as listings.
    """
    return {r["doc_id"] for r in read_with_types(index, _types_path()) if r["kind"] == "directory"}


def embed_all(
    texts: Sequence[str], embed: Embed, batch: int = 64
) -> tuple[list[str], list[list[float]], int]:
    """Embed texts, splitting any failed batch, and return what succeeded plus how many were dropped.

    Args:
        texts: Texts to embed.
        embed: Function that embeds one batch of texts.
        batch: Number of texts per embedding request.

    Returns:
        The texts embedded, their vectors, and the number dropped.
    """
    kept: list[str] = []
    vectors: list[list[float]] = []
    dropped = 0
    pending = [list(texts[i : i + batch]) for i in range(0, len(texts), batch)]
    while pending:
        group = pending.pop(0)
        try:
            vectors += embed(group)
            kept += group
        except OSError:
            if len(group) == 1:
                dropped += 1
            else:
                middle = len(group) // 2
                pending[:0] = [group[:middle], group[middle:]]
    return kept, vectors, dropped


def embed_documents(client: Embedder, texts: Iterable[str]) -> list[list[float]]:
    """Embed passages with the model's document template.

    Args:
        client: Embedding client.
        texts: Texts to search or embed.

    Returns:
        One vector per passage.
    """
    return client.embed([_templated(client, "embed_document_template", t) for t in texts])


def embed_query(client: Embedder, text: str) -> list[float]:
    """Only the query carries the model's instruction or prefix, as each model's card specifies.

    Args:
        client: Embedding client.
        text: Query text.

    Returns:
        The query vector.
    """
    return client.embed([_templated(client, "embed_query_template", text)])[0]


def index_folder(index_dir: StrPath, config: ModelConfig) -> Path:
    """The folder for a model's embedding index, named by its settings where they give a name.

    Args:
        index_dir: Folder that holds embedding indexes.
        config: Model settings.

    Returns:
        The folder path.
    """
    name = config.get("index_name")
    return Path(index_dir) / name if name else Path(index_dir)


def judge(answer: Answer, source: str, gaz: Gazetteer, needs_quote: bool, label: str = "") -> JudgedRow:
    """Turn a model answer into a placement only if it survives the checks in code.

    Args:
        answer: Parsed model answer.
        source: Passage text shown to the model.
        gaz: Gazetteer of cities.
        needs_quote: Whether the answer must carry a quote.
        label: The vendor's label for the region.

    Returns:
        The judged answer, with a status that says why it was kept or dropped.
    """
    city, country, quote = (
        str(answer.get("city") or "").strip(),
        str(answer.get("country") or "").strip(),
        str(answer.get("quote") or "").strip(),
    )
    row: JudgedRow = {
        "status": "abstained",
        "city": city,
        "country": country,
        "lat": "",
        "lon": "",
        "quote": quote,
        "quote_ok": "",
        "quote_source": "",
    }
    if not city:
        return row
    if needs_quote:
        origin = _quote_source(quote, source, label)
        supported = origin and _names_city(city, quote)
        row["quote_ok"], row["quote_source"] = "yes" if supported else "no", origin
        if not supported:
            return {**row, "status": "unsupported"}
    item = _resolve(city, country, gaz)
    if not item or not _usable(
        item, _norm(city), gaz, True, bool(country) and gaz.areas.get(_norm(city)) != country.upper()
    ):
        return {**row, "status": "unresolved"}
    return {**row, "status": "answered", "lat": item.lat, "lon": item.lon}


def llm_macros(report: Comparison) -> dict[str, int]:
    """Paper macros for each method's scores.

    Args:
        report: Saved comparison of methods.

    Returns:
        Macro values keyed by name.
    """
    values: dict[str, int] = {}
    for name, score in report["scores"].items():
        prefix = f"llm{name.capitalize()}"
        every, held, review, turned = score["all"], score["holdout"], score["review"], score["overturned"]
        values |= {
            f"{prefix}Answered": every["answered"],
            f"{prefix}Matched": every["matched"],
            f"{prefix}Regions": every["regions"],
            f"{prefix}Rescued": len(score["rescued"]),
            f"{prefix}FromPassage": every.get("from_passage", 0),
            f"{prefix}Unsupported": every["unsupported"],
            f"{prefix}Unresolved": every["unresolved"],
            f"{prefix}HoldoutAnswered": held["answered"],
            f"{prefix}HoldoutMatched": held["matched"],
            f"{prefix}ReviewAnswered": review["answered"],
            f"{prefix}ReviewMatched": review["matched"],
            f"{prefix}ReviewRegions": review["regions"],
            f"{prefix}TurnedMatched": turned["matched"],
            f"{prefix}TurnedAnswered": turned["answered"],
            f"{prefix}TurnedRegions": turned["regions"],
        }
    return values


def llm_table(report: Comparison) -> str:
    """The paper's table of methods: answers, matches, review set, overturned placements and regions rescued.

    Args:
        report: Saved comparison of methods.

    Returns:
        The LaTeX source of the table.
    """
    rows: list[str] = []
    for name, score in report["scores"].items():
        every, review, turned = score["all"], score["review"], score["overturned"]
        rows.append(
            f"{METHOD_NAMES.get(name, name)} & {every['answered']} of {every['regions']} & {every['matched']} of {every['answered']} & {_range(every)} & "
            + f"{review['matched']} of {review['answered']} & {turned['matched']} of {turned['answered']} & {len(score['rescued'])} \\\\"
        )
    header = "Method & Answers & Matched & 95\\% & Review & Overturned & Rules missed \\\\"
    return "\n".join(
        [
            "\\begin{tabular}{lrrrrrr}",
            "\\toprule",
            header,
            "\\midrule",
            *rows,
            "\\bottomrule",
            "\\end{tabular}",
            "",
        ]
    )


def method_result_files(paths: Iterable[Path]) -> list[Path]:
    """A model folder also holds chain files, which have no status column and are not method results.

    Args:
        paths: Candidate files.

    Returns:
        The files that hold method results.
    """
    return [p for p in paths if "status" in p.read_text(encoding="utf-8").split("\n", 1)[0].split(",")]


def model_config(
    base: Mapping[str, object], models: Mapping[str, Mapping[str, object]], name: str
) -> ModelConfig:
    """The settings for one named model: the shared settings with that model's own on top.

    Args:
        base: Settings shared by every model.
        models: Settings per model.
        name: Model name in the settings file.

    Returns:
        The merged settings.
    """
    return cast(ModelConfig, {**base, **models[name]})


def model_output(output: StrPath, name: str) -> Path:
    """The folder that holds one model's recorded results.

    Args:
        output: Output folder.
        name: Model name.

    Returns:
        The folder path.
    """
    return Path(output) / name


def place_with_label(region: Region, client: Chat, gaz: Gazetteer) -> JudgedRow:
    """Ask the model to place a region from the vendor's label alone, and check its quote against that label.

    Args:
        region: Published region.
        client: Chat client.
        gaz: Gazetteer of cities.

    Returns:
        The judged answer.
    """
    text = _label_text(region)
    reply = client.chat(_fill("label", vendor=region.provider, region_id=region.region_id, text=text))
    return judge(parse_answer(reply), "", gaz, needs_quote=True, label=f"{text} {region.region_id}")


def place_with_memory(region: Region, client: Chat, gaz: Gazetteer) -> JudgedRow:
    """Ask the model to place a region with no documents; nothing can check the answer but the gazetteer.

    Args:
        region: Published region.
        client: Chat client.
        gaz: Gazetteer of cities.

    Returns:
        The judged answer.
    """
    reply = client.chat(
        _fill("memory", vendor=region.provider, region_id=region.region_id, text=_label_text(region))
    )
    return judge(parse_answer(reply), "", gaz, needs_quote=False)


def place_with_rag(region: Region, index: Index, client: RagClient, gaz: Gazetteer) -> JudgedRow:
    """Retrieve passages that name the region, ask the model, and check its quote against those passages.

    Args:
        region: Published region.
        index: Embedded passages of the vendor's documents.
        client: Client that can chat, embed and rerank.
        gaz: Gazetteer of cities.

    Returns:
        The judged answer.
    """
    passages = top_passages(region, index, client)
    if not passages:
        return {**judge({}, "", gaz, needs_quote=True), "document": ""}
    block = "\n\n".join(f"[{n + 1}] {p}" for n, p in enumerate(passages))
    label = _label_text(region)
    reply = client.chat(
        _fill("rag", vendor=region.provider, region_id=region.region_id, text=label, passages=block)
    )
    row = judge(parse_answer(reply), " ".join(passages), gaz, needs_quote=True, label=label)
    return {**row, "document": _quote_document(row["quote"], passages, index.get("sources", {}))}


def region_passages(region: Region, texts: Iterable[str]) -> list[str]:
    """The passages that name a region.

    Args:
        region: Published region.
        texts: Texts to search or embed.

    Returns:
        The matching passages.
    """
    return [t for t in texts if _names_region(region, t)]


def repeat_sample(regions: Iterable[Region], size: int) -> list[Region]:
    """A fixed sample of regions for asking again without the cache.

    Args:
        regions: Published regions.
        size: Number of regions to take.

    Returns:
        The sampled regions.
    """
    return sorted(regions, key=_repeat_order)[:size]


def repeatability(
    first: Iterable[Mapping[str, object]], second: Iterable[Mapping[str, object]]
) -> Repeatability:
    """Compare two runs on the placements they make, ignoring the wording of quotes.

    Args:
        first: Rows of the first run.
        second: Rows of the second run.

    Returns:
        The counts and the regions that changed.
    """
    a, b = _placements(first), _placements(second)
    changed = sorted(list(k) for k in a if a[k] != b.get(k))
    return {
        "regions": len(a),
        "identical": len(a) - len(changed),
        "share": (len(a) - len(changed)) / len(a) if a else 0.0,
        "changed": changed,
    }


def run_method(
    name: MethodName,
    regions: Iterable[Region],
    client: RagClient,
    gaz: Gazetteer,
    raw: StrPath,
    index_dir: StrPath,
    output: StrPath,
    only_split: Split | None = None,
) -> list[MethodRow]:
    """Place every region with one method, in parallel where the settings allow, and write the answers.

    Args:
        name: Placement method.
        regions: Published regions.
        client: Client that can chat, embed and rerank.
        gaz: Gazetteer of cities.
        raw: The data/raw folder.
        index_dir: Folder that holds embedding indexes.
        output: Output folder.
        only_split: Split to run; every region when None.

    Returns:
        One answer row per region.
    """
    chosen = [
        r
        for r in regions
        if not only_split
        or ("holdout" if in_holdout(r.provider, r.region_id) else "development") == only_split
    ]
    indexes = (
        {v: build_index(raw, v, client, index_dir) for v in {r.provider for r in chosen}}
        if name == "rag"
        else {}
    )
    with ThreadPoolExecutor(max_workers=_config(client).get("workers", 1)) as pool:

        def place(region: Region) -> MethodRow:
            """Place one region with the chosen method.

            Args:
                region: Published region.

            Returns:
                The answer row.
            """
            return _place_one(name, region, client, gaz, indexes)

        rows = list(pool.map(place, chosen))
    Path(output).mkdir(parents=True, exist_ok=True)
    with (Path(output) / f"{name}.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter[str](handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def saved_copies(raw: StrPath, vendor: str) -> list[Path]:
    """Every readable document from the URL index for a vendor, for rechecking quotes.

    Args:
        raw: The data/raw folder.
        vendor: Vendor identifier.

    Returns:
        The paths of the saved documents.
    """
    root = Path(raw) / "trust_docs" / vendor / FETCHED_DIR
    return [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and p.suffix in SAVED_SUFFIXES
        and not SKIP_DIRS & set(p.parts)
        and p.name not in WITHHELD_DOCUMENTS
    ]


def saved_text_of(record: Mapping[str, str], raw: StrPath) -> str:
    """The saved text of the one document a record cites, so a quote cannot pass on another document.

    Args:
        record: Evidence record.
        raw: The data/raw folder.

    Returns:
        The document's text; blank when it is not saved.
    """
    doc_id = hashlib.sha256(record["url"].encode()).hexdigest()[:12]
    folder = Path(raw) / "trust_docs" / record["provider"] / FETCHED_DIR
    return " ".join(checkable_text(p) for p in sorted(folder.glob(f"{doc_id}.*")))


def top_passages(region: Region, index: Index, client: RagClient, k: int = TOP_K) -> list[str]:
    """The passages most similar to the region query among those that name it, reranked where a reranker is set.

    Args:
        region: Published region.
        index: Embedded passages of the vendor's documents.
        client: Client that can chat, embed and rerank.
        k: Number of passages to return.

    Returns:
        At most k passages, best first.
    """
    import numpy as np

    keep = [i for i, t in enumerate(index["texts"]) if _names_region(region, t)]
    if not keep:
        return []
    matrix = np.array([index["vectors"][i] for i in keep], dtype=float)
    matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
    query = np.array(
        embed_query(client, f"{region.provider} {region.region_id} {region.name} data center location city"),
        dtype=float,
    )
    query /= max(np.linalg.norm(query), 1e-9)
    reranker = _config(client).get("rerank_model")
    order = np.argsort(-(matrix @ query))[: RERANK_POOL if reranker else k]
    found = [index["texts"][keep[int(i)]] for i in order]
    return _reranked(region, found, client)[:k] if reranker else found


def write_comparison(
    output: StrPath,
    rules: Sequence[PlacerRow],
    methods: Mapping[str, Sequence[ScoredRow]],
    places: Mapping[RegionKey, Point],
    overturned: Iterable[RegionKey],
    config: Mapping[str, object],
) -> Path:
    """Score the rules and every method on the same regions and save the comparison.

    Args:
        output: Output folder.
        rules: The rules' rows.
        methods: Answers per method.
        places: Reference coordinates per region.
        overturned: Regions whose first placement evidence overturned.
        config: Model settings.

    Returns:
        The path of the saved comparison.
    """
    report: Comparison = {
        "config": config,
        "prompts": {name: prompt_hash(name) for name in PROMPTS},
        "scores": compare_methods(rules, methods, places, overturned),
    }
    path = Path(output) / "comparison.json"
    _ = path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return path


def write_manifest(
    folder: StrPath,
    config: Mapping[str, object],
    rows_by_method: Mapping[str, Sequence[Mapping[str, object]]],
    cost: float,
    commit: str,
    tool_version: str,
) -> Path:
    """Record what a rerun needs.

    Args:
        folder: Folder that holds the model's results.
        config: Model settings.
        rows_by_method: Result rows per method.
        cost: Cost in US dollars.
        commit: Git commit of the code.
        tool_version: Version of the text extraction tool.

    Returns:
        The path of the manifest.
    """
    manifest = {
        **config,
        "commit": commit,
        "tool_version": tool_version,
        "prompts": {name: prompt_hash(name) for name in PROMPTS},
        "cost_usd": round(cost, 4),
        "counts": {
            m: dict(Counter(r["status"] for r in rows))
            for m, rows in rows_by_method.items()
            if rows and "status" in rows[0]
        },
    }
    path = Path(folder) / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_paper_files(report: Comparison, generated: StrPath) -> None:
    """Write the paper's model macros and model table.

    Args:
        report: Saved comparison of methods.
        generated: The paper's generated folder.
    """
    folder = Path(generated)
    _ = (folder / "llm_macros.tex").write_text(macros_tex(llm_macros(report)), encoding="utf-8")
    _ = (folder / "llm_table.tex").write_text(llm_table(report), encoding="utf-8")


def _config(client: object) -> ModelConfig:
    """The model settings a client carries, or none for a client without any.

    Args:
        client: Any client; its settings are read when it has them.

    Returns:
        The settings; empty when the client has none.
    """
    config: object = getattr(client, "config", None)
    return cast(ModelConfig, config) if isinstance(config, dict) else cast(ModelConfig, {})


def _document_text(path: Path) -> str:
    """The text of a saved document, through pdftotext for a PDF.

    Args:
        path: Saved document.

    Returns:
        The document's text.
    """
    if path.suffix == ".pdf":
        return subprocess.run(
            ["pdftotext", str(path), "-"], capture_output=True, text=True, errors="replace", check=False
        ).stdout
    return ungzipped(path.read_bytes()).decode("utf-8", errors="replace")


def _index_pieces(raw: StrPath, vendor: str) -> dict[str, str]:
    """Passages with the document each came from.

    Args:
        raw: The data/raw folder.
        vendor: Vendor identifier.

    Returns:
        Source document keyed by passage.
    """
    return {
        piece: path.stem
        for path in corpus_paths(raw, vendor, directory_documents())
        for piece in chunk_text(corpus_text(path))
    }


def _key(row: ScoredRow | PlacerRow) -> RegionKey:
    """The vendor and region of a row.

    Args:
        row: Answer or placer row.

    Returns:
        The vendor and region ID.
    """
    return row["provider"], row["region_id"]


def _label_text(region: Region) -> str:
    """The vendor's text for a region: its name and stated location, once if they are the same.

    Args:
        region: Published region.

    Returns:
        The label text.
    """
    return (
        region.stated_location
        if region.stated_location == region.name
        else f"{region.name}; {region.stated_location}"
    )


def _matches(row: ScoredRow, places: Mapping[RegionKey, Point]) -> bool:
    """Whether an answer lies within the agreement radius of the reference place.

    Args:
        row: Answer row.
        places: Reference coordinates per region.

    Returns:
        True when the answer agrees with the reference.
    """
    place = places.get((row["provider"], row["region_id"]))
    return bool(
        row["status"] == "answered"
        and place
        and row["lat"] != ""
        and haversine_km((float(row["lat"]), float(row["lon"])), place) <= AGREE_KM
    )


def _names_city(city: str, quote: str) -> bool:
    """Whether a quote contains the city as whole words.

    Args:
        city: City name.
        quote: Quoted text.

    Returns:
        True when the city is in the quote.
    """
    return f" {_norm(city)} " in f" {_norm(_normalize(quote))} "


def _names_region(region: Region, text: str) -> bool:
    """Whether text names the region by its ID, name or stated location as whole words.

    Args:
        region: Published region.
        text: Text to search.

    Returns:
        True when the region is named.
    """
    names = [region.region_id] + [
        n for n in (region.name, region.stated_location) if len(n) >= MIN_NAME_CHARS
    ]
    return any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text, re.I) for n in names)


def _place_one(
    name: MethodName, region: Region, client: RagClient, gaz: Gazetteer, indexes: Mapping[ProviderId, Index]
) -> MethodRow:
    """Place one region with one method and record its split, with any street address cut from the quote.

    Args:
        name: Placement method.
        region: Published region.
        client: Client that can chat, embed and rerank.
        gaz: Gazetteer of cities.
        indexes: Embedding index per vendor.

    Returns:
        The answer row.
    """
    if name == "label":
        row = place_with_label(region, client, gaz)
    elif name == "memory":
        row = place_with_memory(region, client, gaz)
    else:
        row = place_with_rag(region, indexes[region.provider], client, gaz)
    split: Split = "holdout" if in_holdout(region.provider, region.region_id) else "development"
    return {
        "provider": region.provider,
        "region_id": region.region_id,
        "split": split,
        **row,
        "quote": city_level_quote(row["quote"], row["city"]),
    }


def _placements(rows: Iterable[Mapping[str, object]]) -> dict[tuple[str, str], tuple[str, str]]:
    """The status and city each run gave, keyed by vendor and region.

    Args:
        rows: Rows to write or read.

    Returns:
        Status and city keyed by vendor and region.
    """
    return {(str(r["provider"]), str(r["region_id"])): (str(r["status"]), str(r["city"])) for r in rows}


def _quote_document(quote: str, passages: Iterable[str], sources: Mapping[str, str]) -> str:
    """The document a quote came from, by the first passage that contains it.

    Args:
        quote: Quoted text.
        passages: Passages shown to the model.
        sources: Document per passage.

    Returns:
        The document's URL, or blank.
    """
    return next((sources.get(p, "") for p in passages if quote and quote_found(quote, p)), "")


def _quote_source(quote: str, source: str, label: str) -> str:
    """Where a quote was found: the passage, the vendor's label, or nowhere.

    Args:
        quote: Quoted text.
        source: Passage text shown to the model.
        label: The vendor's label for the region.

    Returns:
        ``passage``, ``label`` or blank.
    """
    if quote and quote_found(quote, source):
        return "passage"
    return "label" if quote and label and quote_found(quote, label) else ""


def _range(score: Score) -> str:
    """The Wilson interval of a method's match rate, as LaTeX.

    Args:
        score: Score of the method.

    Returns:
        The interval text.
    """
    low, high = wilson(score["matched"], score["answered"])
    return f"{low * 100:.0f}--{high * 100:.0f}\\%"


def _repeat_order(region: Region) -> str:
    """A fixed hash order for choosing the repeat sample.

    Args:
        region: Published region.

    Returns:
        The hex digest used as the sort key.
    """
    return hashlib.sha256(f"ocm-regions-repeat-v1/{region.provider}/{region.region_id}".encode()).hexdigest()


def _rerank_scores(query: str, passages: list[str], client: Reranker) -> list[float]:
    """Split a refused batch until the passage at fault is found; it scores below everything else.

    Args:
        query: Query text.
        passages: Passages to score.
        client: Reranking client.

    Returns:
        One score per passage.
    """
    try:
        return client.rerank(query, passages)
    except urllib.error.HTTPError:
        if len(passages) == 1:
            return [-1.0]
        middle = len(passages) // 2
        return _rerank_scores(query, passages[:middle], client) + _rerank_scores(
            query, passages[middle:], client
        )


def _reranked(region: Region, passages: list[str], client: Reranker) -> list[str]:
    """Passages ordered by reranker score, best first; raises when the reranker refuses them all.

    Args:
        region: Published region.
        passages: Passages to score.
        client: Reranking client.

    Returns:
        The passages, best first.

    Raises:
        RuntimeError: If the reranker refuses every passage.
    """
    query = f"{region.provider} {region.region_id} {region.name} data center location city"
    scores = _rerank_scores(query, passages, client)
    if passages and all(score < 0 for score in scores):
        raise RuntimeError(
            f"the reranker refused every passage for {region.region_id}; is the model out of memory?"
        )
    return [
        p
        for _score, _rank, p in sorted(
            (-s, n, p) for n, (s, p) in enumerate(zip(scores, passages, strict=False))
        )
    ]


def _resolve(city: str, country: str, gaz: Gazetteer) -> City | None:
    """The gazetteer city a model's city and country name, or None.

    Args:
        city: City name.
        country: Country name or code the model gave.
        gaz: Gazetteer of cities.

    Returns:
        The city, or None when the gazetteer has no match.
    """
    items = gaz.index.get(_norm(city), [])
    return _allowed(items, None, country.upper()) if country else _allowed(items, None, None)


def _rule_rows(rules: Iterable[PlacerRow]) -> list[ScoredRow]:
    """The rules' placements in the shape the comparison scores.

    Args:
        rules: The rules' rows.

    Returns:
        One scored row per region.
    """
    return [
        {
            "provider": r["provider"],
            "region_id": r["region_id"],
            "status": "answered" if r["city"] else "abstained",
            "lat": r["lat"],
            "lon": r["lon"],
            "city": r["city"],
        }
        for r in rules
    ]


def _score(rows: Sequence[ScoredRow], places: Mapping[RegionKey, Point]) -> Score:
    """Count a method's answers by status, its matches, and its answers quoted from a passage.

    Args:
        rows: Answer rows.
        places: Reference coordinates per region.

    Returns:
        The counts.
    """
    score: Score = {
        "regions": len(rows),
        "answered": 0,
        "matched": 0,
        "abstained": 0,
        "unsupported": 0,
        "unresolved": 0,
    }
    for row in rows:
        score[row["status"]] = score.get(row["status"], 0) + 1
        score["matched"] += _matches(row, places)
    if rows and "quote_source" in rows[0]:
        score["from_passage"] = sum(
            r["status"] == "answered" and r.get("quote_source") == "passage" for r in rows
        )
    return score


def _templated(
    client: object, field: Literal["embed_document_template", "embed_query_template"], text: str
) -> str:
    """Wrap text in the model's document or query template, where its settings give one.

    Args:
        client: Any client; its settings are read when it has them.
        field: Setting that names the template.
        text: Text to wrap.

    Returns:
        The wrapped text, or the text unchanged.
    """
    return _config(client).get(field, "{text}").replace("{text}", text)


def _types_path() -> Path:
    """Path of the paper model's recorded document types.

    Returns:
        The file path.
    """
    return SOURCE_INDEX.parents[1] / "output" / "llm" / PAPER_MODEL / DOCUMENT_TYPES


document_text = _document_text
