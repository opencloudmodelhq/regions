# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from ocm_regions.evidence.verify import check_record, readable_text
from ocm_regions.tables.grades import best_grade, grade_of

RECORD = {"provider": "aws", "url": "https://example.test/a", "quote": "in Ashburn"}


def page(text, kind="text/html"):
    return lambda _url: (kind, text.encode())


def no_saved(_provider):
    return ""


def test_quote_on_the_live_page_is_found():
    assert check_record(RECORD, page("<p>in  Ashburn</p>"), no_saved)["status"] == "quote-found"


def test_quote_missing_live_but_in_a_saved_copy_counts_and_says_so():
    result = check_record(RECORD, page("nothing"), lambda _p: "region in ashburn")
    assert result["status"] == "quote-found-saved-copy"


def test_quote_in_neither_place_is_not_found():
    assert check_record(RECORD, page("nothing"), no_saved)["status"] == "quote-not-found"


def test_an_empty_quote_never_matches():
    assert check_record(RECORD | {"quote": ""}, page("anything"), no_saved)["status"] == "no-quote"


def test_unreachable_page_can_still_pass_on_a_saved_copy():
    def fail(_url):
        raise TimeoutError("blocked")

    assert check_record(RECORD, fail, no_saved)["status"] == "unreachable"
    assert check_record(RECORD, fail, lambda _p: "in ashburn")["status"] == "quote-found-saved-copy"


def test_pdf_bodies_are_read_as_text():
    stream = b"BT /F1 12 Tf 72 720 Td (Located in Ashburn) Tj ET"
    pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream endobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n"
    )
    assert "Located in Ashburn" in readable_text("application/pdf", pdf)


def test_a_failed_quote_check_caps_the_grade_at_partial():
    record = {"source_type": "vendor", "stance": "supports"}
    assert grade_of(record | {"quote_check": "quote-found"}) == "vendor-document"
    assert grade_of(record | {"quote_check": "quote-found-saved-copy"}) == "vendor-document"
    for failed in ("quote-not-found", "unreachable", "format-not-checked", "no-quote", "not-checked"):
        assert grade_of(record | {"quote_check": failed}) == "partial"


def test_a_failed_record_cannot_lift_a_region_above_a_passing_one():
    records = [
        {"source_type": "vendor", "stance": "supports", "quote_check": "quote-not-found"},
        {"source_type": "news", "stance": "supports", "quote_check": "quote-found"},
    ]
    assert best_grade(records) == "reported"


def test_build_attaches_checks_only_to_collected_records():
    from ocm_regions.tables.build import attach_quote_checks

    collected = {"source_type": "vendor", "evidence_id": "E1"}
    unknown = {"source_type": "news", "evidence_id": "E9"}
    generated = {"source_type": "vendor_region_list", "evidence_id": "E2"}
    attach_quote_checks([collected, unknown, generated], {"E1": "quote-found"})
    assert collected["quote_check"] == "quote-found" and unknown["quote_check"] == "not-checked"
    assert "quote_check" not in generated
    fresh = {"source_type": "vendor", "evidence_id": "E1"}
    attach_quote_checks([fresh], {})
    assert "quote_check" not in fresh


def test_a_text_file_served_with_a_generic_type_is_read():
    assert (
        readable_text("application/octet-stream", b"8.8.8.0/24,US,US-VA,Ashburn,")
        == "8.8.8.0/24,US,US-VA,Ashburn,"
    )
    assert readable_text("application/octet-stream", b"\x00\x01binary") is None


def test_markdown_table_marks_do_not_hide_a_quote():
    from ocm_regions.evidence.verify import quote_found

    assert quote_found("| Paris 2, France | `fr-par-2` |", "<td>Paris 2, France</td> <td>fr-par-2</td>")
    assert quote_found("Paris 2, France fr-par-2", "| Paris 2, France | `fr-par-2` |")


def test_every_readable_saved_format_counts_as_a_saved_copy(tmp_path):
    from ocm_regions.model.llm import saved_copies

    root = tmp_path / "trust_docs" / "akamai"
    (root / "fetched").mkdir(parents=True)
    (root / "fetched" / "a.html").write_text("<p>London, UK</p>")
    (root / "fetched" / "b.md").write_text("Stockholm, SE")
    assert sorted(p.name for p in saved_copies(tmp_path, "akamai")) == ["a.html", "b.md"]


def _page_with(text):
    return lambda _url: ("text/plain", text.encode())


def test_operator_and_city_strings_must_appear_in_the_same_document():
    record = {
        "provider": "azure",
        "url": "https://a.test/one",
        "quote": "at LVL01, LVL02",
        "also_found": "Microsoft Corp|Boydton, VA",
    }
    assert (
        check_record(record, _page_with("Microsoft Corp at LVL01, LVL02 in Boydton, VA"), lambda _p: "")[
            "status"
        ]
        == "quote-found"
    )


def test_a_missing_operator_string_fails_the_check():
    record = {
        "provider": "azure",
        "url": "https://a.test/two",
        "quote": "at LVL01, LVL02",
        "also_found": "Microsoft Corp|Boydton, VA",
    }
    assert (
        check_record(record, _page_with("Another Operator at LVL01, LVL02 in Boydton, VA"), lambda _p: "")[
            "status"
        ]
        == "also-not-found"
    )


def test_a_saved_copy_must_also_hold_the_operator_string():
    record = {
        "provider": "azure",
        "url": "https://a.test/three",
        "quote": "at LVL01, LVL02",
        "also_found": "Microsoft Corp",
    }
    outcome = check_record(record, _page_with("nothing here"), lambda _p: "Another Operator at LVL01, LVL02")
    assert outcome["status"] == "quote-not-found"


def test_saved_copies_are_only_the_documents_in_the_url_index(tmp_path):
    from ocm_regions.model.llm import saved_copies

    root = tmp_path / "trust_docs" / "aws"
    (root / "fetched").mkdir(parents=True)
    (root / "fetched" / "abc.html").write_text("indexed")
    (root / "hand_saved.html").write_text("not indexed")
    assert [p.name for p in saved_copies(tmp_path, "aws")] == ["abc.html"]


def test_html_entities_do_not_hide_a_quote():
    outcome = check_record(
        {**RECORD, "quote": "Amsterdam & Frankfurt in 2024"},
        page("Amsterdam &amp; Frankfurt&nbsp;in 2024"),
        lambda _p: "",
    )
    assert outcome["status"] == "quote-found"


def test_a_gzip_body_is_read_as_its_text():
    import gzip

    assert readable_text("text/html", gzip.compress(b"in Ashburn")) == "in Ashburn"


def test_a_saved_gzip_document_is_read_as_its_text(tmp_path):
    import gzip

    from ocm_regions.model.llm import _document_text, corpus_text

    path = tmp_path / "a.html"
    path.write_bytes(gzip.compress(b"<p>in Ashburn</p>"))
    assert "in Ashburn" in _document_text(path) and "in Ashburn" in corpus_text(path)


def test_a_pdf_is_checked_in_both_reading_orders(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    def run(command, **_kwargs):
        return SimpleNamespace(stdout="columns" if "-layout" in command else "plain")

    monkeypatch.setattr(subprocess, "run", run)
    text = readable_text("application/pdf", b"%PDF-1.7")
    assert "plain" in text and "columns" in text


def test_a_saved_pdf_is_checked_in_both_reading_orders_but_read_once_for_the_index(monkeypatch, tmp_path):
    import subprocess
    from types import SimpleNamespace

    from ocm_regions.model.llm import checkable_text, corpus_text

    def run(command, **_kwargs):
        return SimpleNamespace(stdout="columns" if "-layout" in command else "plain")

    monkeypatch.setattr(subprocess, "run", run)
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.7")
    assert "plain" in checkable_text(path) and "columns" in checkable_text(path)
    assert corpus_text(path) == "plain"


def test_a_stray_space_before_punctuation_does_not_hide_a_quote():
    outcome = check_record(
        {**RECORD, "quote": "zones in Bangkok , Frankfurt and Tokyo"},
        page("zones in <span>Bangkok</span>, Frankfurt and Tokyo"),
        lambda _p: "",
    )
    assert outcome["status"] == "quote-found"


def test_a_saved_copy_is_looked_up_for_the_record_it_checks():
    asked = []
    check_record(
        {**RECORD, "url": "https://example.test/saved-lookup"},
        page("nothing"),
        lambda record: asked.append(record["url"]) or "",
    )
    assert asked == ["https://example.test/saved-lookup"]


def test_the_saved_text_of_a_record_is_its_own_document(tmp_path):
    import hashlib

    from ocm_regions.model.llm import saved_text_of

    url = "https://a.test/cited"
    folder = tmp_path / "trust_docs" / "aws" / "fetched"
    folder.mkdir(parents=True)
    (folder / f"{hashlib.sha256(url.encode()).hexdigest()[:12]}.html").write_text("in Ashburn")
    (folder / "000000000000.html").write_text("in Dublin")
    text = saved_text_of({"provider": "aws", "url": url}, tmp_path)
    assert "Ashburn" in text and "Dublin" not in text


def test_a_less_than_sign_in_a_pdf_does_not_swallow_the_text_after_it():
    outcome = check_record(
        {**RECORD, "quote": "generators MWH01 run"},
        page("limit < 5 tons of generators MWH01 run and more than > 3"),
        lambda _p: "",
    )
    assert outcome["status"] == "quote-found"


def test_a_quote_finds_a_page_whose_bracket_holds_an_inline_tag():
    from ocm_regions.evidence.verify import quote_found

    assert quote_found("Washington DC ( us-east )", "<td>Washington DC (<code>us-east</code>)</td>")


def test_a_truncated_live_response_counts_as_unreachable():
    import http.client

    def cut(_url):
        raise http.client.IncompleteRead(b"partial")

    record = RECORD | {"url": "https://example.test/truncated"}
    assert check_record(record, cut, no_saved)["status"] == "unreachable"
    assert check_record(record, cut, lambda _p: "in ashburn")["status"] == "quote-found-saved-copy"
