# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import json

import pytest

from ocm_regions.regions.parsers import Region


class FakeClient:
    def __init__(self, answers):
        self.answers, self.prompts = answers, []

    def chat(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0)

    def embed(self, texts):
        return [[1.0 if "Ashburn" in t or "us-east" in t else 0.0, 1.0] for t in texts]


def gaz():
    from ocm_regions.place.placer import Gazetteer

    return Gazetteer(
        cities=[
            {
                "name": "Ashburn",
                "country": "US",
                "admin1": "VA",
                "population": 43000,
                "lat": 39.04,
                "lon": -77.49,
            },
            {
                "name": "Tokyo",
                "country": "JP",
                "admin1": "40",
                "population": 8000000,
                "lat": 35.69,
                "lon": 139.69,
            },
        ]
    )


REGION = Region("aws", "us-east-1", "US East (N. Virginia)", "US East (N. Virginia)")


def test_parse_answer_drops_thinking_and_reads_json():
    from ocm_regions.model.llm import parse_answer

    assert (
        parse_answer(
            '<think>hmm {"city":"x"}</think>\n```json\n{"city":"Tokyo","country":"JP","quote":"Tokyo"}\n```'
        )["city"]
        == "Tokyo"
    )
    assert parse_answer("no json here") == {}
    assert parse_answer('{"city": unfinished') == {}


def test_unsupported_quote_is_never_placed():
    from ocm_regions.model.llm import judge

    row = judge(
        {"city": "Tokyo", "country": "JP", "quote": "Tokyo region"}, "US East", gaz(), needs_quote=True
    )
    assert row["status"] == "unsupported" and row["lat"] == ""


def test_supported_quote_with_a_known_city_is_placed():
    from ocm_regions.model.llm import judge

    row = judge(
        {"city": "Tokyo", "country": "JP", "quote": "tokyo"}, "Asia Pacific (Tokyo)", gaz(), needs_quote=True
    )
    assert row["status"] == "answered" and row["lat"] == 35.69 and row["quote_ok"] == "yes"


def test_empty_city_is_an_abstention_and_an_unknown_city_is_unresolved():
    from ocm_regions.model.llm import judge

    assert (
        judge({"city": "", "country": "", "quote": ""}, "x", gaz(), needs_quote=True)["status"] == "abstained"
    )
    assert judge({}, "x", gaz(), needs_quote=True)["status"] == "abstained"
    assert (
        judge({"city": "Atlantis", "country": "", "quote": "Atlantis"}, "Atlantis", gaz(), needs_quote=True)[
            "status"
        ]
        == "unresolved"
    )


def test_memory_answers_need_no_quote():
    from ocm_regions.model.llm import judge

    assert judge({"city": "Tokyo", "country": "JP"}, "", gaz(), needs_quote=False)["status"] == "answered"


def test_label_method_sends_only_vendor_text_and_scores_it():
    from ocm_regions.model.llm import place_with_label

    client = FakeClient(['{"city":"","country":"","quote":""}'])
    row = place_with_label(REGION, client, gaz())
    assert (
        row["status"] == "abstained"
        and "N. Virginia" in client.prompts[0]
        and "us-east-1" in client.prompts[0]
    )


def test_chunks_overlap_and_keep_only_location_text():
    from ocm_regions.model.llm import chunk_text

    text = ("filler words " * 200) + "Our data center is located in Ashburn. " + ("more filler " * 200)
    chunks = chunk_text(text, size=300, overlap=50)
    assert chunks and all(len(c) <= 300 for c in chunks)
    assert any("Ashburn" in c for c in chunks) and not any(
        "data center" not in c and "located" not in c for c in chunks
    )


def test_retrieval_keeps_only_passages_naming_the_region():
    from ocm_regions.model.llm import top_passages

    index = {
        "texts": ["about billing and region pricing", "us-east-1 sits in Ashburn data center"],
        "vectors": [[0.0, 1.0], [1.0, 1.0]],
    }
    passages = top_passages(REGION, index, FakeClient([]), k=1)
    assert passages == ["us-east-1 sits in Ashburn data center"]


def test_rag_method_checks_the_quote_against_retrieved_passages():
    from ocm_regions.model.llm import place_with_rag

    index = {"texts": ["us-east-1 sits in Ashburn data center"], "vectors": [[1.0, 1.0]]}
    good = place_with_rag(
        REGION, index, FakeClient(['{"city":"Ashburn","country":"US","quote":"sits in Ashburn"}']), gaz()
    )
    bad = place_with_rag(
        REGION, index, FakeClient(['{"city":"Tokyo","country":"JP","quote":"sits in Tokyo"}']), gaz()
    )
    assert good["status"] == "answered" and bad["status"] == "unsupported"


def test_comparison_scores_each_method_on_the_same_regions():
    from ocm_regions.model.llm import compare_methods

    rules = [
        {
            "provider": "a",
            "region_id": "x",
            "basis": "label",
            "cross_check": "agrees",
            "split": "holdout",
            "lat": 1.0,
            "lon": 1.0,
            "city": "P",
            "matches_curated": "yes",
            "curated_city": "P",
        },
        {
            "provider": "a",
            "region_id": "y",
            "basis": "label",
            "cross_check": "conflicts",
            "split": "holdout",
            "lat": 9.0,
            "lon": 9.0,
            "city": "Q",
            "matches_curated": "no",
            "curated_city": "R",
        },
    ]
    places = {("a", "x"): (1.0, 1.0), ("a", "y"): (2.0, 2.0)}
    method = [
        {"provider": "a", "region_id": "x", "status": "answered", "lat": 1.0, "lon": 1.0, "city": "P"},
        {"provider": "a", "region_id": "y", "status": "unsupported", "lat": "", "lon": "", "city": ""},
    ]
    scores = compare_methods(rules, {"label": method}, places, [("a", "y")])
    assert scores["label"]["holdout"] == {
        "regions": 2,
        "answered": 1,
        "matched": 1,
        "abstained": 0,
        "unsupported": 1,
        "unresolved": 0,
    }
    assert scores["label"]["review"]["regions"] == 1 and scores["label"]["overturned"]["matched"] == 0
    assert scores["rules"]["holdout"]["matched"] == 1


def test_run_records_prompt_hashes_and_models_with_the_comparison(tmp_path):
    from ocm_regions.model.llm import write_comparison

    rules = [
        {
            "provider": "a",
            "region_id": "x",
            "basis": "label",
            "cross_check": "agrees",
            "split": "holdout",
            "lat": 1.0,
            "lon": 1.0,
            "city": "P",
        }
    ]
    path = write_comparison(
        tmp_path, rules, {}, {("a", "x"): (1.0, 1.0)}, [], {"chat_model": "m", "embed_model": "e"}
    )
    saved = json.loads(path.read_text())
    assert (
        saved["config"]["chat_model"] == "m"
        and len(saved["prompts"]["rag"]) == 64
        and "rules" in saved["scores"]
    )


def test_a_state_or_country_answered_as_a_city_is_not_placed():
    from ocm_regions.model.llm import judge
    from ocm_regions.place.placer import Gazetteer

    areas = Gazetteer(
        cities=[
            {
                "name": "Virginia",
                "country": "US",
                "admin1": "MN",
                "population": 8000,
                "lat": 47.5,
                "lon": -92.5,
            },
            {
                "name": "Washington",
                "country": "US",
                "admin1": "DC",
                "population": 680000,
                "lat": 38.9,
                "lon": -77.0,
            },
        ],
        areas={"virginia": "US", "washington": "US"},
    )
    assert (
        judge(
            {"city": "Virginia", "country": "US", "quote": "Virginia"}, "Virginia", areas, needs_quote=True
        )["status"]
        == "unresolved"
    )
    assert (
        judge(
            {"city": "Washington", "country": "US", "quote": "Washington"},
            "Washington",
            areas,
            needs_quote=True,
        )["status"]
        == "answered"
    )


def test_embedding_splits_a_failed_batch_and_drops_only_the_text_that_keeps_failing():
    from ocm_regions.model.llm import embed_all

    calls = []

    def flaky(texts):
        calls.append(len(texts))
        if "bad" in texts:
            raise OSError("server error")
        return [[1.0] for _ in texts]

    assert embed_all(["a", "bad", "c", "d"], flaky, batch=4) == (["a", "c", "d"], [[1.0], [1.0], [1.0]], 1)


CHENNAI = "Vultr announces its region in Chennai, India."
ASHBURN = "The us-east-1 region is in Ashburn, Virginia."


def test_a_passage_that_never_names_the_region_is_not_retrieved():
    from ocm_regions.model.llm import region_passages

    assert region_passages(Region("vultr", "ams", "Amsterdam", "Amsterdam"), [CHENNAI]) == []
    assert region_passages(REGION, [CHENNAI, ASHBURN]) == [ASHBURN]


def test_a_region_id_must_match_as_a_whole_word():
    from ocm_regions.model.llm import region_passages

    assert region_passages(Region("vultr", "ams", "NL", "NL"), ["streams flow in Tokyo"]) == []
    assert region_passages(Region("vultr", "ams", "NL", "NL"), ["site AMS is in Amsterdam"]) == [
        "site AMS is in Amsterdam"
    ]


def test_a_full_region_name_counts_as_naming_the_region():
    from ocm_regions.model.llm import region_passages

    region = Region("aws", "ap-northeast-1", "Asia Pacific (Tokyo)", "Asia Pacific (Tokyo)")
    assert region_passages(region, ["Asia Pacific (Tokyo) runs in Tokyo"]) == [
        "Asia Pacific (Tokyo) runs in Tokyo"
    ]


def test_rag_does_not_call_the_model_when_no_passage_names_the_region():
    from ocm_regions.model.llm import place_with_rag

    client = FakeClient([])
    index = {"texts": [CHENNAI], "vectors": [[1.0, 1.0]]}
    row = place_with_rag(Region("vultr", "ams", "Amsterdam", "Amsterdam"), index, client, gaz())
    assert row["status"] == "abstained" and client.prompts == []


def test_a_quote_must_name_the_city_it_supports():
    from ocm_regions.model.llm import judge

    row = judge(
        {"city": "Tokyo", "country": "JP", "quote": "India is home to AI"},
        "India is home to AI",
        gaz(),
        needs_quote=True,
    )
    assert row["status"] == "unsupported"


def test_rag_records_whether_the_quote_came_from_a_passage_or_the_vendor_label():
    from ocm_regions.model.llm import place_with_rag

    region = Region("gcp", "asia-northeast1", "asia-northeast1", "Tokyo, Japan, APAC")
    index = {"texts": ["asia-northeast1 is a region"], "vectors": [[1.0, 1.0]]}
    label = place_with_rag(
        region, index, FakeClient(['{"city":"Tokyo","country":"JP","quote":"Tokyo, Japan"}']), gaz()
    )
    assert label["status"] == "answered" and label["quote_source"] == "label"
    index = {"texts": ["asia-northeast1 is in Tokyo"], "vectors": [[1.0, 1.0]]}
    passage = place_with_rag(
        region, index, FakeClient(['{"city":"Tokyo","country":"JP","quote":"is in Tokyo"}']), gaz()
    )
    assert passage["status"] == "answered" and passage["quote_source"] == "passage"


def test_comparison_counts_answers_that_rest_on_a_passage():
    from ocm_regions.model.llm import compare_methods

    rows = [
        {
            "provider": "aws",
            "region_id": "r",
            "split": "development",
            "status": "answered",
            "lat": 1.0,
            "lon": 1.0,
            "quote_source": "passage",
        },
        {
            "provider": "aws",
            "region_id": "s",
            "split": "development",
            "status": "answered",
            "lat": 1.0,
            "lon": 1.0,
            "quote_source": "label",
        },
    ]
    rules = [
        {
            "provider": "aws",
            "region_id": k,
            "split": "development",
            "basis": "label",
            "cross_check": "",
            "city": "x",
            "lat": 1.0,
            "lon": 1.0,
        }
        for k in "rs"
    ]
    scores = compare_methods(
        rules, {"rag": rows}, {("aws", "r"): (1.0, 1.0), ("aws", "s"): (1.0, 1.0)}, set()
    )
    assert scores["rag"]["development"]["from_passage"] == 1


def test_a_quote_that_normalises_to_nothing_supports_no_city():
    from ocm_regions.model.llm import judge

    row = judge({"city": "Tokyo", "country": "JP", "quote": "<Tokyo>"}, "US East", gaz(), needs_quote=True)
    assert row["status"] == "unsupported"


def test_a_city_inside_a_longer_word_does_not_count():
    from ocm_regions.model.llm import judge

    row = judge(
        {"city": "Tokyo", "country": "JP", "quote": "Tokyoite staff"},
        "Tokyoite staff",
        gaz(),
        needs_quote=True,
    )
    assert row["status"] == "unsupported"


def test_a_stated_country_binds_the_city_lookup():
    from ocm_regions.model.llm import judge

    row = judge({"city": "Tokyo", "country": "US", "quote": "Tokyo"}, "Tokyo", gaz(), needs_quote=True)
    assert row["status"] == "unresolved" and row["lat"] == ""


def test_label_method_records_its_quote_as_coming_from_the_label():
    from ocm_regions.model.llm import place_with_label

    region = Region("aws", "ap-northeast-1", "Asia Pacific (Tokyo)", "Asia Pacific (Tokyo)")
    row = place_with_label(region, FakeClient(['{"city":"Tokyo","country":"JP","quote":"Tokyo"}']), gaz())
    assert row["status"] == "answered" and row["quote_source"] == "label"


def _comparison():
    from ocm_regions.model.llm import compare_methods

    rules = [
        {
            "provider": "a",
            "region_id": "x",
            "basis": "label",
            "cross_check": "agrees",
            "split": "holdout",
            "lat": 1.0,
            "lon": 1.0,
            "city": "P",
        },
        {
            "provider": "a",
            "region_id": "y",
            "basis": "none",
            "cross_check": "",
            "split": "development",
            "lat": "",
            "lon": "",
            "city": "",
        },
    ]
    method = [
        {
            "provider": "a",
            "region_id": "x",
            "status": "answered",
            "lat": 1.0,
            "lon": 1.0,
            "city": "P",
            "quote_source": "passage",
        },
        {
            "provider": "a",
            "region_id": "y",
            "status": "answered",
            "lat": 2.0,
            "lon": 2.0,
            "city": "Q",
            "quote_source": "passage",
        },
    ]
    return compare_methods(rules, {"rag": method}, {("a", "x"): (1.0, 1.0), ("a", "y"): (2.0, 2.0)}, [])


def test_comparison_lists_regions_only_the_method_placed_correctly():
    scores = _comparison()
    assert scores["rag"]["rescued"] == [["a", "y"]]
    assert scores["rules"]["rescued"] == []


def test_comparison_scores_all_regions_together():
    assert _comparison()["rag"]["all"]["regions"] == 2


def test_paper_macros_and_table_come_from_the_recorded_comparison():
    from ocm_regions.model.llm import llm_macros, llm_table

    report = {"scores": _comparison(), "prompts": {"rag": "abc123"}}
    macros = llm_macros(report)
    assert macros["llmRagMatched"] == 2 and macros["llmRagRescued"] == 1 and macros["llmRagFromPassage"] == 2
    assert macros["llmRulesAnswered"] == 1
    table = llm_table(report)
    assert "RAG" in table and "\\toprule" in table and "2 of 2" in table


def test_compare_writes_the_macros_and_table_for_the_paper(tmp_path):
    from ocm_regions.model.llm import write_paper_files

    write_paper_files({"scores": _comparison(), "prompts": {}}, tmp_path)
    assert "llmRagMatched" in (tmp_path / "llm_macros.tex").read_text()
    assert "RAG" in (tmp_path / "llm_table.tex").read_text()


def test_a_published_quote_never_carries_a_street_address():
    from ocm_regions.model.llm import city_level_quote

    assert (
        city_level_quote("Hetzner Online GmbH Am Datacenter-Park 1 08223 Falkenstein", "Falkenstein")
        == "Falkenstein"
    )
    assert city_level_quote("400 Harris Street Ultimo Sydney", "Sydney") == "Sydney"
    assert city_level_quote("Region Location: WuHu", "Wuhu") == "Region Location: WuHu"


MODELS = {"small": {"chat_model": "ai/small", "chat_model_id": "abc"}}
BASE = {
    "chat_model": "ai/base",
    "chat_model_id": "base",
    "embed_model": "e",
    "embed_model_id": "f",
    "temperature": 0,
    "seed": 42,
    "max_tokens": 300,
}


def test_a_named_model_overrides_only_the_chat_model_and_keeps_its_own_results():
    from ocm_regions.model.llm import model_config, model_output

    config = model_config(BASE, MODELS, "small")
    assert (
        config["chat_model"] == "ai/small"
        and config["chat_model_id"] == "abc"
        and config["embed_model"] == "e"
        and config["seed"] == 42
    )
    assert model_output("out/llm", "small").as_posix() == "out/llm/small"


def test_an_unknown_model_is_refused():
    import pytest

    from ocm_regions.model.llm import model_config

    with pytest.raises(KeyError):
        model_config(BASE, MODELS, "missing")


def test_answers_from_different_models_are_cached_apart(tmp_path):
    from ocm_regions.model.llm import Client

    class Fake(Client):
        def __init__(self, config):
            super().__init__(config, tmp_path)
            self.calls = 0

        def _post(self, path, payload):
            self.calls += 1
            return {"choices": [{"message": {"content": payload["model"]}}]}

    one, two = Fake(BASE), Fake(BASE | {"chat_model": "ai/small", "chat_model_id": "abc"})
    assert one.chat("p") == "ai/base" and two.chat("p") == "ai/small"
    assert one.chat("p") == "ai/base" and one.calls == 1


def test_a_fresh_client_asks_again_and_leaves_the_cache_alone(tmp_path):
    from ocm_regions.model.llm import Client

    class Fake(Client):
        replies = iter(["one", "two"])

        def _complete(self, prompt):
            return next(self.replies), 0.0

    assert Fake(BASE, tmp_path).chat("a") == "one"
    assert Fake(BASE, tmp_path, fresh=True).chat("a") == "two"
    assert Fake(BASE, tmp_path).chat("a") == "one"


def test_parallel_runs_keep_region_order(tmp_path):
    from ocm_regions.model.llm import run_method
    from ocm_regions.place.placer import Gazetteer

    class Slow:
        config = {"workers": 4}

        def chat(self, prompt):
            return '{"city":"","country":"","quote":""}'

    regions = [Region("aws", f"r{n}", f"name {n}", f"name {n}") for n in range(12)]
    rows = run_method("label", regions, Slow(), Gazetteer(cities=[]), tmp_path, tmp_path, tmp_path / "o")
    assert [r["region_id"] for r in rows] == [f"r{n}" for n in range(12)]


def test_repeatability_counts_placements_that_stay_the_same():
    from ocm_regions.model.llm import repeatability

    first = [
        {"provider": "a", "region_id": "x", "status": "answered", "city": "P"},
        {"provider": "a", "region_id": "y", "status": "abstained", "city": ""},
    ]
    second = [
        {"provider": "a", "region_id": "x", "status": "answered", "city": "P"},
        {"provider": "a", "region_id": "y", "status": "answered", "city": "Q"},
    ]
    assert repeatability(first, second) == {
        "regions": 2,
        "identical": 1,
        "share": 0.5,
        "changed": [["a", "y"]],
    }


def test_manifest_records_what_is_needed_to_rerun(tmp_path):
    from ocm_regions.model.llm import write_manifest

    path = write_manifest(
        tmp_path, BASE | {"chat_model": "m"}, {"label": [{"status": "answered"}]}, 0.5, "abc123", "tool 1.0"
    )
    local = json.loads(path.read_text())
    assert local["chat_model"] == "m" and local["commit"] == "abc123" and local["tool_version"] == "tool 1.0"
    assert (
        local["counts"] == {"label": {"answered": 1}}
        and local["cost_usd"] == 0.5
        and "label" in local["prompts"]
    )


def test_table_ranges_are_escaped_once_for_latex():
    from ocm_regions.model.llm import _range

    assert _range({"matched": 9, "answered": 10}).endswith("\\%") and "\\\\" not in _range(
        {"matched": 9, "answered": 10}
    )


TASK = "Given a cloud region, retrieve passages that say where its data centres are"
QWEN_EMBED = BASE | {
    "embed_model": "ai/qwen3-embedding:4b-q8_0",
    "embed_model_id": "q",
    "embed_query_template": "Instruct: " + TASK + "\nQuery:{text}",
    "embed_document_template": "{text}",
}


class RecordingEmbed:
    def __init__(self, config):
        self.config, self.seen = config, []

    def embed(self, texts):
        self.seen += texts
        return [[1.0, 1.0] for _ in texts]


def test_only_the_query_carries_the_instruction():
    from ocm_regions.model.llm import embed_documents, embed_query

    client = RecordingEmbed(QWEN_EMBED)
    embed_documents(client, ["a passage"])
    embed_query(client, "aws us-east-1")
    assert client.seen == ["a passage", f"Instruct: {TASK}\nQuery:aws us-east-1"]


def test_nomic_gets_its_required_prefixes_on_both_sides():
    from ocm_regions.model.llm import embed_documents, embed_query

    client = RecordingEmbed(
        BASE
        | {
            "embed_query_template": "search_query: {text}",
            "embed_document_template": "search_document: {text}",
        }
    )
    embed_documents(client, ["p"])
    embed_query(client, "q")
    assert client.seen == ["search_document: p", "search_query: q"]


def test_each_embedding_model_keeps_its_own_index(tmp_path):
    from ocm_regions.model.llm import index_folder

    assert index_folder(tmp_path, BASE) == tmp_path
    assert (
        index_folder(tmp_path, QWEN_EMBED | {"index_name": "qwen-4b-fetched"}) == tmp_path / "qwen-4b-fetched"
    )


def test_a_fetched_corpus_uses_only_what_a_reader_can_download(tmp_path):
    from ocm_regions.model.llm import corpus_paths

    root = tmp_path / "trust_docs" / "ibm"
    (root / "fetched").mkdir(parents=True)
    (root / "manual.txt").write_text("hand saved")
    (root / "fetched" / "a.html").write_text("<p>x</p>")
    (root / "fetched" / "b.pdf").write_text("x")
    assert [p.name for p in corpus_paths(tmp_path, "ibm")] == ["a.html", "b.pdf"]


def test_web_pages_in_the_corpus_are_read_as_visible_text(tmp_path):
    from ocm_regions.model.llm import corpus_text

    page = tmp_path / "a.html"
    page.write_text("<script>var hidden=1;</script><p>Region in Ashburn</p>")
    assert corpus_text(page) == "Region in Ashburn"


def test_reranker_posts_to_the_rerank_route_and_returns_scores_in_document_order(tmp_path):
    from ocm_regions.model.llm import Client

    seen = {}

    class Fake(Client):
        def _post(self, path, payload):
            seen.update(path=path, payload=payload)
            return {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]}

    client = Fake(BASE | {"rerank_model": "ai/qwen3-reranker:4B"}, tmp_path)
    assert client.rerank("q", ["a", "b"]) == [0.1, 0.9]
    assert (
        seen["path"] == "/rerank"
        and seen["payload"]["model"] == "ai/qwen3-reranker:4B"
        and seen["payload"]["documents"] == ["a", "b"]
    )


class RerankClient(RecordingEmbed):
    def __init__(self, scores):
        super().__init__(BASE | {"rerank_model": "r"})
        self.scores, self.pools = scores, []

    def rerank(self, query, documents):
        self.pools.append(list(documents))
        return [self.scores.get(d, 0.0) for d in documents]


def test_reranker_decides_the_final_order_of_the_retrieved_pool():
    from ocm_regions.model.llm import top_passages

    texts = ["us-east-1 alpha", "us-east-1 beta", "us-east-1 gamma"]
    index = {"texts": texts, "vectors": [[1.0, 1.0], [1.0, 1.0], [1.0, 1.0]]}
    client = RerankClient({"us-east-1 gamma": 0.9, "us-east-1 alpha": 0.1})
    assert top_passages(REGION, index, client, k=2) == ["us-east-1 gamma", "us-east-1 alpha"]


def test_without_a_reranker_retrieval_stays_as_recorded():
    from ocm_regions.model.llm import top_passages

    client = RecordingEmbed(BASE)
    index = {"texts": ["us-east-1 alpha"], "vectors": [[1.0, 1.0]]}
    assert top_passages(REGION, index, client, k=1) == ["us-east-1 alpha"]


def test_index_remembers_which_document_each_passage_came_from(tmp_path):
    from ocm_regions.model.llm import build_index

    root = tmp_path / "trust_docs" / "ibm" / "fetched"
    root.mkdir(parents=True)
    (root / "abc123.txt").write_text("region located in Roubaix")
    client = RecordingEmbed(QWEN_EMBED | {"corpus": "fetched", "index_name": "x"})
    index = build_index(tmp_path, "ibm", client, tmp_path / "idx")
    assert index["sources"] == {"region located in Roubaix": "abc123"}


def test_a_rag_answer_names_the_document_its_quote_came_from():
    from ocm_regions.model.llm import place_with_rag

    text = "us-east-1 sits in Ashburn data center"
    index = {"texts": [text], "vectors": [[1.0, 1.0]], "sources": {text: "doc1"}}
    row = place_with_rag(
        REGION, index, FakeClient(['{"city":"Ashburn","country":"US","quote":"sits in Ashburn"}']), gaz()
    )
    assert row["status"] == "answered" and row["document"] == "doc1"


def test_model_settings_reach_the_request_and_separate_the_cache(tmp_path):
    from ocm_regions.model.llm import Client

    bodies = []

    class Fake(Client):
        def _post(self, path, payload):
            bodies.append(payload)
            return {"choices": [{"message": {"content": "x"}}]}

    off = BASE | {"chat_extra": {"chat_template_kwargs": {"enable_thinking": False}}}
    Fake(BASE, tmp_path).chat("p")
    Fake(off, tmp_path).chat("p")
    assert (
        len(bodies) == 2
        and bodies[1]["chat_template_kwargs"] == {"enable_thinking": False}
        and "chat_template_kwargs" not in bodies[0]
    )


def test_an_answer_cut_off_by_the_token_limit_is_not_kept(tmp_path):
    import pytest

    from ocm_regions.model.llm import Client

    class Fake(Client):
        def _post(self, path, payload):
            return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}

    with pytest.raises(RuntimeError):
        Fake(BASE, tmp_path).chat("p")
    assert not list(tmp_path.glob("*.json"))


def test_a_passage_the_reranker_refuses_goes_last_and_the_rest_still_rank():
    import urllib.error

    from ocm_regions.model.llm import _reranked

    class Client(RerankClient):
        def rerank(self, query, documents):
            if "bad" in documents:
                raise urllib.error.HTTPError("u", 500, "too long", {}, None)
            return [self.scores.get(d, 0.0) for d in documents]

    client = Client({"good": 0.9, "fine": 0.5})
    assert _reranked(REGION, ["fine", "bad", "good"], client) == ["good", "fine", "bad"]


def test_an_index_that_lost_most_of_its_passages_stops_the_run(tmp_path):
    import urllib.error

    import pytest

    from ocm_regions.model.llm import build_index

    class Broken(RecordingEmbed):
        def embed(self, texts):
            raise urllib.error.HTTPError("u", 500, "Compute error", {}, None)

    root = tmp_path / "trust_docs" / "ibm" / "fetched"
    root.mkdir(parents=True)
    (root / "a.txt").write_text("region located in Roubaix")
    with pytest.raises(RuntimeError):
        build_index(
            tmp_path, "ibm", Broken(QWEN_EMBED | {"corpus": "fetched", "index_name": "x"}), tmp_path / "idx"
        )
    assert not list((tmp_path / "idx").rglob("*.json"))


def test_a_reranker_that_refuses_everything_stops_the_run():
    import urllib.error

    import pytest

    from ocm_regions.model.llm import _reranked

    class Broken(RerankClient):
        def rerank(self, query, documents):
            raise urllib.error.HTTPError("u", 500, "Compute error", {}, None)

    with pytest.raises(RuntimeError):
        _reranked(REGION, ["a", "b"], Broken({}))


def test_a_fetched_corpus_skips_the_documents_it_is_told_to(tmp_path):
    from ocm_regions.model.llm import corpus_paths

    root = tmp_path / "trust_docs" / "huawei" / "fetched"
    root.mkdir(parents=True)
    (root / "vendor.html").write_text("<p>x</p>")
    (root / "listing.html").write_text("<p>x</p>")
    assert [p.name for p in corpus_paths(tmp_path, "huawei", skip={"listing"})] == ["vendor.html"]


def test_third_party_directories_are_named_for_skipping(tmp_path):
    from ocm_regions.model.llm import directory_documents

    index = tmp_path / "index.csv"
    index.write_text("doc_id,kind,url\nlisting,directory,u\nvendor,vendor,v\n")
    assert directory_documents(index) == {"listing"}


def test_a_manifest_counts_only_method_tables_so_a_second_run_does_not_trip_on_the_chain_table(tmp_path):
    from ocm_regions.model.llm import write_manifest

    tables = {"rag": [{"status": "answered"}], "evidence_chain": [{"provider": "aws"}]}
    path = write_manifest(tmp_path, {}, tables, 0, "commit", "tool")
    assert json.loads(path.read_text())["counts"] == {"rag": {"answered": 1}}


def test_only_method_results_are_read_as_method_results(tmp_path):
    from ocm_regions.model.llm import method_result_files

    (tmp_path / "rag.csv").write_text("provider,status\naws,answered\n", encoding="utf-8")
    (tmp_path / "evidence_chain.csv").write_text("provider,method\naws,rules\n", encoding="utf-8")
    assert [p.name for p in method_result_files([tmp_path / "rag.csv", tmp_path / "evidence_chain.csv"])] == [
        "rag.csv"
    ]


def test_an_index_is_rebuilt_when_the_vendors_documents_change(tmp_path):
    from ocm_regions.model.llm import build_index

    class Fake:
        config = {"corpus": "fetched", "index_name": "x"}
        calls = 0

        def embed(self, texts):
            Fake.calls += 1
            return [[1.0, 0.0] for _ in texts]

    folder = tmp_path / "raw" / "trust_docs" / "aws" / "fetched"
    folder.mkdir(parents=True)
    (folder / "aaa.txt").write_text("the data center region facility in Dublin " * 5)
    build_index(tmp_path / "raw", "aws", Fake(), tmp_path / "index")
    build_index(tmp_path / "raw", "aws", Fake(), tmp_path / "index")
    assert Fake.calls == 1
    (folder / "bbb.txt").write_text("the data center region facility in Paris " * 5)
    index = build_index(tmp_path / "raw", "aws", Fake(), tmp_path / "index")
    assert Fake.calls == 2 and any("Paris" in t for t in index["texts"])


def test_macros_give_the_overturned_placements_each_method_answered():
    from ocm_regions.model.llm import llm_macros

    assert (
        llm_macros({"scores": _comparison()})["llmRagTurnedAnswered"]
        == _comparison()["rag"]["overturned"]["answered"]
    )


def test_a_tool_version_is_the_line_that_names_the_version():
    from ocm_regions.cli import _first_version

    assert _first_version("Client:\n Version:    28.1.0\n Context: default") == "Version:    28.1.0"
    assert _first_version("abc123\n") == "abc123"
    assert _first_version("") == ""


def test_a_chat_request_the_server_fails_is_asked_again_before_giving_up(tmp_path, monkeypatch):
    import urllib.error

    from ocm_regions.model import llm_clients
    from ocm_regions.model.llm import Client

    waits = []
    monkeypatch.setattr(llm_clients.time, "sleep", waits.append)

    class Flaky(Client):
        def __init__(self, failures):
            super().__init__(BASE, tmp_path, fresh=True)
            self.failures = failures

        def _post(self, path, payload):
            if self.failures:
                self.failures -= 1
                raise urllib.error.HTTPError("http://model", 500, "Internal Server Error", None, None)
            return {"choices": [{"message": {"content": "ok"}}]}

    assert Flaky(2).chat("p") == "ok" and waits == list(llm_clients.RETRY_WAITS[:2])
    with pytest.raises(urllib.error.HTTPError):
        Flaky(len(llm_clients.RETRY_WAITS) + 1).chat("p")


def test_a_chat_request_the_server_rejects_is_not_asked_again(tmp_path, monkeypatch):
    import urllib.error

    from ocm_regions.model import llm_clients
    from ocm_regions.model.llm import Client

    monkeypatch.setattr(llm_clients.time, "sleep", lambda _s: pytest.fail("waited"))

    class Rejecting(Client):
        def _post(self, path, payload):
            raise urllib.error.HTTPError("http://model", 400, "Bad Request", None, None)

    with pytest.raises(urllib.error.HTTPError):
        Rejecting(BASE, tmp_path, fresh=True).chat("p")
