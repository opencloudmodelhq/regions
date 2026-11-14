# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import json

import pytest

from ocm_regions.publish.provenance import build_graph, code_digest, method_of

DOC = "https://docs.example.test/regions"
FEED = "https://ip.example.test/feed.csv"


def evidence(**extra):
    return {
        "evidence_id": "E1",
        "provider": "aws",
        "region_id": "us-east-1",
        "claim": "city",
        "value": "Ashburn",
        "source_type": "vendor",
        "publisher": "aws",
        "title": "Regions",
        "url": DOC,
        "quote": "Ashburn, Virginia",
        "stance": "supports",
        "quote_check": "quote-found",
        "note": "rag: read from the corpus by code.",
    } | extra


REGION = {
    "provider": "aws",
    "region_id": "us-east-1",
    "region_name": "US East (N. Virginia)",
    "city": "Ashburn",
    "country": "US",
    "grade": "vendor-document",
    "precision": "city",
    "code_nearby_hub": "IAD",
    "evidence_ids": "E1;E2",
}
MATERIAL = {
    "url": DOC,
    "provider": "aws",
    "sha256": "ab" * 32,
    "retrieved": "2026-11-04",
    "archive_url": "https://web.archive.org/x",
}
DATASET = {"name": "aws-feed", "url": FEED, "sha256": "cd" * 32, "retrieved": "2026-11-04"}
MODEL = {"chain-qwen3.8": {"chat_model": "ai/qwen3.8:27b-q8_0", "chat_model_id": "7f7f60352e7c"}}
PROMPTS = {"place_rag_v1.txt": "ef" * 32}


def graph(evidence_rows=None):
    rows = evidence_rows or [
        evidence(),
        evidence(evidence_id="E2", source_type="vendor_feed", url=FEED, note=""),
    ]
    return build_graph([REGION], rows, [MATERIAL], [DATASET], MODEL, PROMPTS, code="12" * 32, version="0.1.0")


def node(g, node_id):
    return next(n for n in g["@graph"] if n["@id"] == node_id)


def ids(value):
    return {v["@id"] for v in (value if isinstance(value, list) else [value])}


def test_a_placement_derives_from_each_of_its_evidence_records():
    region = node(graph(), "region/aws/us-east-1")
    assert ids(region["prov:wasDerivedFrom"]) == {"evidence/E1", "evidence/E2"}
    assert region["schema:addressLocality"] == "Ashburn" and region["ocm:codeNearbyHub"] == "IAD"


def test_an_evidence_record_quotes_its_document_and_names_the_step_that_made_it():
    record = node(graph(), "evidence/E1")
    assert ids(record["prov:hadPrimarySource"]) == {DOC}
    assert ids(record["prov:wasGeneratedBy"]) == {"activity/rag"}
    assert record["prov:value"] == "Ashburn, Virginia" and record["ocm:stance"] == "supports"


def test_a_document_carries_its_hash_retrieval_date_archive_copy_and_publisher():
    document = node(graph(), DOC)
    assert document["ocm:sha256"] == "ab" * 32 and document["ocm:retrieved"] == "2026-11-04"
    assert ids(document["prov:alternateOf"]) == {"https://web.archive.org/x"}
    assert ids(document["prov:wasAttributedTo"]) == {"agent/vendor/aws"}


def test_a_dataset_from_the_manifest_is_a_document_too():
    assert node(graph(), FEED)["ocm:sha256"] == "cd" * 32


def test_the_model_step_used_its_prompt_and_names_the_model_build():
    rag = node(graph(), "activity/rag")
    assert ids(rag["prov:used"]) == {"prompt/place_rag_v1.txt"}
    assert node(graph(), "prompt/place_rag_v1.txt")["ocm:sha256"] == "ef" * 32
    assert "agent/model/chain-qwen3.8" in ids(rag["prov:wasAssociatedWith"])
    assert node(graph(), "agent/model/chain-qwen3.8")["ocm:modelId"] == "7f7f60352e7c"


def test_every_step_is_run_by_the_code_at_a_stated_version():
    software = node(graph(), "agent/ocm-regions")
    assert software["ocm:codeSha256"] == "12" * 32 and software["schema:softwareVersion"] == "0.1.0"
    assert "agent/ocm-regions" in ids(node(graph(), "activity/parse-feed")["prov:wasAssociatedWith"])


@pytest.mark.parametrize(
    "record, step",
    [
        (evidence(note="rag: read from the corpus by code."), "rag"),
        (evidence(note="quote-search: read from the corpus by code."), "quote-search"),
        (
            evidence(source_type="vendor_region_list", note="Parsed from the saved copy of the vendor list."),
            "parse-region-list",
        ),
        (evidence(source_type="vendor_feed", note=""), "parse-feed"),
        (evidence(source_type="code_join", note="Joined A to B"), "code-join"),
        (evidence(source_type="peeringdb", note="Owned by the vendor"), "parse-peeringdb"),
        (evidence(source_type="certification", note="One of 3 sites"), "parse-certificate"),
    ],
)
def test_each_evidence_record_maps_to_the_step_that_made_it(record, step):
    assert method_of(record) == step


def test_an_unknown_kind_of_evidence_stops_the_export():
    with pytest.raises(ValueError):
        method_of(evidence(source_type="rumour", note=""))


def test_the_export_is_the_same_on_every_run_and_has_no_run_time():
    first, second = json.dumps(graph(), sort_keys=True), json.dumps(graph(), sort_keys=True)
    assert first == second and "generatedAtTime" not in first and "startedAtTime" not in first


def test_the_code_digest_changes_with_the_code_only(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    before = code_digest(tmp_path)
    (tmp_path / "notes.txt").write_text("ignored")
    assert code_digest(tmp_path) == before
    (tmp_path / "a.py").write_text("x = 2\n")
    assert code_digest(tmp_path) != before


def test_the_export_is_valid_rdf_with_one_derivation_per_evidence_link():
    rdflib = pytest.importorskip("rdflib")
    g = rdflib.Graph().parse(data=json.dumps(graph()), format="json-ld")
    derived = rdflib.URIRef("http://www.w3.org/ns/prov#wasDerivedFrom")
    assert len(list(g.triples((None, derived, None)))) == 2


def test_a_document_never_fetched_has_no_hash_and_says_why():
    gated = {
        "url": "https://portal.example.test/audit",
        "provider": "aws",
        "sha256": "",
        "retrieved": "",
        "archive_url": "",
        "access": "gated",
    }
    g = build_graph(
        [REGION], [evidence()], [MATERIAL, gated], [DATASET], MODEL, PROMPTS, code="12" * 32, version="0.1.0"
    )
    document = node(g, "https://portal.example.test/audit")
    assert (
        "ocm:sha256" not in document and "ocm:retrieved" not in document and document["ocm:access"] == "gated"
    )


def test_a_quote_found_by_two_steps_is_one_entity_made_once():
    rows = [evidence(), evidence(note="quote-search: read from the corpus by code.")]
    g = graph(rows)
    records = [n for n in g["@graph"] if n["@id"] == "evidence/E1"]
    assert len(records) == 1
    assert ids(records[0]["prov:wasGeneratedBy"]) == {"activity/quote-search"}
    assert ids(records[0]["ocm:alsoFoundBy"]) == {"activity/rag"}


def test_the_software_agent_carries_the_brand_name():
    assert node(graph(), "agent/ocm-regions")["schema:name"] == "OpenCloudModel Regions"
