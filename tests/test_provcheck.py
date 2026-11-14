# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import json

from ocm_regions.publish.provcheck import prov_document, shape_problems
from test_provenance import graph, node


def text(g):
    return json.dumps(g)


def test_a_sound_record_has_no_problems():
    assert shape_problems(text(graph())) == []


def test_the_w3c_prov_library_reads_every_agent_as_an_agent():
    agents = [r for r in prov_document(text(graph())).get_records() if type(r).__name__ == "ProvAgent"]
    assert len(agents) == 3


def test_a_placement_without_evidence_is_a_problem():
    g = graph()
    del node(g, "region/aws/us-east-1")["prov:wasDerivedFrom"]
    assert any("wasDerivedFrom" in p for p in shape_problems(text(g)))


def test_evidence_without_a_primary_source_is_a_problem():
    g = graph()
    del node(g, "evidence/E1")["prov:hadPrimarySource"]
    assert any("hadPrimarySource" in p for p in shape_problems(text(g)))


def test_a_malformed_document_hash_is_a_problem():
    g = graph()
    node(g, "https://docs.example.test/regions")["ocm:sha256"] = "not-a-hash"
    assert any("sha256" in p for p in shape_problems(text(g)))


def test_a_step_with_no_agent_is_a_problem():
    g = graph()
    del node(g, "activity/rag")["prov:wasAssociatedWith"]
    assert any("wasAssociatedWith" in p for p in shape_problems(text(g)))
