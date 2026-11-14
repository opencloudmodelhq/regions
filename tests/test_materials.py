# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import csv
import hashlib

from ocm_regions.fetch.materials import (
    FIELDS,
    fetch_materials,
    read_index,
    record_hashes,
    refresh_index,
    write_index,
)

ROWS = [
    {"provider": "ibm", "url": "https://b.test/soc3.pdf"},
    {"provider": "aws", "url": "https://c.test/artifact", "fetch": "no", "access": "gated"},
    {"provider": "aws", "url": "https://a.test/news"},
]
PDF = b"%PDF-1.4 body"


def index(previous=(), snapshots=()):
    return {r["url"]: r for r in refresh_index(ROWS, snapshots)}


def test_rights_default_to_unknown_rather_than_guessing_a_licence():
    row = index()["https://a.test/news"]
    assert row["license_declared"] == "NOASSERTION" and row["redistribution_status"] == "silent-or-unknown"
    assert row["shared_form"] == "url,hash,short-quote"


def test_archive_capture_is_joined_when_known():
    snap = [
        {
            "url": "https://a.test/news",
            "status": "captured",
            "timestamp": "2026",
            "archive_url": "https://web.archive.org/web/2026id_/https://a.test/news",
        }
    ]
    assert index(snapshots=snap)["https://a.test/news"]["archive_timestamp"] == "2026"


def test_index_round_trips_through_its_file_with_every_field(tmp_path):
    path = tmp_path / "m.csv"
    write_index(list(index().values()), path)
    assert (
        list(csv.DictReader(path.open(encoding="utf-8")))[0].keys() == set(FIELDS)
        or list(read_index(path)[0]) == FIELDS
    )


def _row(**extra):
    return index()["https://b.test/soc3.pdf"] | extra


def test_a_new_document_is_saved_under_its_vendor_with_its_hash(tmp_path):
    report = fetch_materials([_row()], tmp_path, lambda url: ("application/pdf", PDF), pause=0)
    assert report[0]["status"] == "new" and report[0]["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert (tmp_path / "trust_docs" / "ibm" / "fetched" / f"{_row()['doc_id']}.pdf").read_bytes() == PDF


def test_matching_and_changed_hashes_are_told_apart(tmp_path):
    good = hashlib.sha256(PDF).hexdigest()
    assert (
        fetch_materials(
            [_row(sha256=good)], tmp_path, lambda u: ("application/pdf", PDF), pause=0, force=True
        )[0]["status"]
        == "matched"
    )
    assert (
        fetch_materials(
            [_row(sha256="old")], tmp_path, lambda u: ("application/pdf", PDF), pause=0, force=True
        )[0]["status"]
        == "changed"
    )


def test_an_existing_file_is_checked_without_downloading_again(tmp_path):
    fetch_materials([_row()], tmp_path, lambda u: ("application/pdf", PDF), pause=0)

    def never(_url):
        raise AssertionError("downloaded again")

    assert (
        fetch_materials([_row(sha256=hashlib.sha256(PDF).hexdigest())], tmp_path, never, pause=0)[0]["status"]
        == "matched"
    )


def test_a_blocked_source_is_reported_and_the_run_continues(tmp_path):
    def blocked(_url):
        raise OSError("403")

    rows = [_row(), index()["https://a.test/news"]]
    report = fetch_materials(rows, tmp_path, blocked, pause=0)
    assert [r["status"] for r in report] == ["blocked", "blocked"] and "403" in report[0]["detail"]


def test_a_blocked_source_falls_back_to_its_archive_copy_and_says_so(tmp_path):
    def only_archive(url):
        if "archive" not in url:
            raise OSError("403")
        return "application/pdf", PDF

    report = fetch_materials([_row(archive_url="https://web.archive.org/x")], tmp_path, only_archive, pause=0)
    assert report[0]["status"] == "new" and report[0]["source"] == "archive"


def test_gated_documents_report_their_access_route_and_are_never_requested(tmp_path):
    def never(_url):
        raise AssertionError("requested a gated document")

    report = fetch_materials([index()["https://c.test/artifact"]], tmp_path, never, pause=0)
    assert report[0]["status"] == "listed-only"


def test_recording_fills_hashes_only_from_successful_downloads():
    rows = [_row(), index()["https://a.test/news"]]
    report = [
        {
            "doc_id": rows[0]["doc_id"],
            "status": "new",
            "sha256": "abc",
            "size_bytes": 5,
            "media_type": "application/pdf",
            "source": "live",
        },
        {
            "doc_id": rows[1]["doc_id"],
            "status": "blocked",
            "sha256": "",
            "size_bytes": "",
            "media_type": "",
            "source": "",
        },
    ]
    recorded = record_hashes(rows, report, "2026-11-04")
    assert (
        recorded[0]["sha256"] == "abc"
        and recorded[0]["retrieved"] == "2026-11-04"
        and recorded[1]["sha256"] == ""
    )


def test_only_public_web_addresses_can_be_requested():
    import pytest

    from ocm_regions.fetch.materials import check_url

    public = lambda host: ["93.184.216.34"]  # noqa: E731
    check_url("https://a.test/x", public)
    for bad in ("file:///etc/passwd", "ftp://a.test/x", "https:///nohost"):
        with pytest.raises(ValueError):
            check_url(bad, public)
    for address in ("127.0.0.1", "169.254.169.254", "10.0.0.5", "::1"):
        with pytest.raises(ValueError):
            check_url("http://inner.test/", lambda host, a=address: [a])


def test_redirects_are_checked_like_the_first_address():
    import pytest

    from ocm_regions.fetch.materials import CheckedRedirects

    handler = CheckedRedirects()
    with pytest.raises(ValueError):
        handler.redirect_request(None, None, 302, "Found", {}, "file:///etc/passwd")


def test_a_row_with_an_unsafe_id_or_vendor_writes_nothing(tmp_path):
    def never(_url):
        raise AssertionError("requested")

    for bad in ({"doc_id": "../../evil"}, {"provider": "../../evil"}, {"doc_id": "000000000000"}):
        report = fetch_materials([_row(**bad)], tmp_path, never, pause=0)
        assert report[0]["status"] == "invalid"
    assert not list(tmp_path.rglob("*"))


def test_fetched_documents_stay_out_of_the_model_index_unless_asked(tmp_path):
    from ocm_regions.model.llm import build_index as build_model_index

    class Fake:
        def embed(self, texts):
            return [[1.0] for _ in texts]

    root = tmp_path / "trust_docs" / "ibm"
    (root / "fetched").mkdir(parents=True)
    (root / "soc.txt").write_text("region located in Roubaix")
    (root / "fetched" / "new.txt").write_text("region located in Gravelines")
    assert build_model_index(tmp_path, "ibm", Fake(), tmp_path / "a")["texts"] == [
        "region located in Gravelines"
    ]


def test_special_and_embedded_addresses_are_refused():
    import pytest

    from ocm_regions.fetch.materials import check_url

    for address in (
        "224.0.0.1",
        "240.0.0.1",
        "0.0.0.0",
        "::",
        "fe80::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::127.0.0.1",
        "64:ff9b::7f00:1",
        "2002:7f00:1::",
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",
        "ff02::1",
    ):
        with pytest.raises(ValueError):
            check_url("http://inner.test/", lambda host, a=address: [a])
    check_url("https://a.test/", lambda host: ["2606:4700:4700::1111"])


def test_page_text_hash_ignores_scripts_and_markup_but_not_words():
    from ocm_regions.fetch.materials import text_hash

    a = b"<html><script>var t=1;</script><style>x{}</style><!-- 1 --><p>Hello  world</p></html>"
    b = b"<html><script>var t=2;</script><p>Hello world</p><!-- 2 --></html>"
    c = b"<html><p>Hello there</p></html>"
    assert text_hash(a, "text/html") == text_hash(b, "text/html") != text_hash(c, "text/html")
    assert text_hash(PDF, "application/pdf") == ""


def test_a_page_whose_text_is_unchanged_counts_as_matched(tmp_path):
    from ocm_regions.fetch.materials import text_hash

    old, new = b"<script>1</script><p>Same words</p>", b"<script>2</script><p>Same words</p>"
    row = index()["https://a.test/news"] | {
        "sha256": hashlib.sha256(old).hexdigest(),
        "text_sha256": text_hash(old, "text/html"),
    }
    report = fetch_materials([row], tmp_path, lambda u: ("text/html", new), pause=0, force=True)
    assert report[0]["status"] == "text-matched" and report[0]["text_sha256"] == text_hash(new, "text/html")


def test_recording_keeps_the_text_hash():
    row = _row()
    report = [
        {
            "doc_id": row["doc_id"],
            "status": "new",
            "sha256": "a",
            "text_sha256": "b",
            "size_bytes": 1,
            "media_type": "text/html",
            "source": "live",
        }
    ]
    assert record_hashes([row], report, "2026-11-04")[0]["text_sha256"] == "b"


def test_identical_bytes_stay_plainly_matched(tmp_path):
    from ocm_regions.fetch.materials import text_hash

    body = b"<p>Same words</p>"
    row = index()["https://a.test/news"] | {
        "sha256": hashlib.sha256(body).hexdigest(),
        "text_sha256": text_hash(body, "text/html"),
    }
    assert (
        fetch_materials([row], tmp_path, lambda u: ("text/html", body), pause=0, force=True)[0]["status"]
        == "matched"
    )


def test_a_text_match_does_not_replace_the_pinned_bytes():
    row = _row(sha256="pinned")
    report = [
        {
            "doc_id": row["doc_id"],
            "status": "text-matched",
            "sha256": "other",
            "text_sha256": "b",
            "size_bytes": 1,
            "media_type": "text/html",
            "source": "live",
        }
    ]
    assert record_hashes([row], report, "2026-11-04")[0]["sha256"] == "pinned"


def test_only_html_is_stripped_and_empty_or_huge_text_has_no_text_hash():
    from ocm_regions.fetch.materials import MAX_TEXT_BYTES, text_hash

    assert text_hash(b"<b>1</b>", "text/plain") != text_hash(b"<b>2</b>", "text/plain")
    assert text_hash(b"<p>1</p>", "text/csv") != text_hash(b"<i>1</i>", "text/csv")
    assert text_hash(b"<script>only</script>", "text/html") == ""
    assert text_hash(b"x" * (MAX_TEXT_BYTES + 1), "text/html") == ""


def test_hostile_markup_is_hashed_in_reasonable_time():
    import time as clock

    from ocm_regions.fetch.materials import text_hash

    started = clock.monotonic()
    text_hash(b"<script " * 200000 + b"<!--" * 200000, "text/html")
    assert clock.monotonic() - started < 10


def _source(url, **extra):
    return _row(url=url, doc_id=hashlib.sha256(url.encode()).hexdigest()[:12], note="", **extra)


def test_save_list_holds_uncaptured_public_sources_and_holds_back_marked_ones():
    from ocm_regions.fetch.materials import save_list

    clean, in_text = _source("https://a.test/1"), _source("https://a.test/4")
    captured = _source("https://a.test/2") | {"archive_url": "https://web.archive.org/x"}
    noted = _source("https://a.test/3") | {"note": "Marked confidential."}
    gated = index()["https://c.test/artifact"]
    rows = save_list(
        [clean, captured, noted, in_text, gated],
        lambda row: "Company Confidential" if row is in_text else "plain",
    )
    assert {r["url"]: r["action"] for r in rows} == {
        "https://a.test/1": "save",
        "https://a.test/3": "hold-marked",
        "https://a.test/4": "hold-marked",
    }


def test_a_source_we_could_not_read_is_held_not_saved():
    from ocm_regions.fetch.materials import save_list

    assert save_list([_source("https://a.test/1")], lambda row: None)[0]["action"] == "hold-unread"


def test_a_row_with_an_unsafe_id_is_held_and_never_read():
    from ocm_regions.fetch.materials import save_list

    def never(_row):
        raise AssertionError("read an unsafe row")

    bad = _source("https://a.test/1") | {"provider": "../../evil"}
    assert save_list([bad], never)[0]["action"] == "hold-invalid"


def test_markings_are_found_across_line_breaks_and_lookalike_forms():
    from ocm_regions.fetch.materials import save_list

    for text in ("not\nfor   distribution", "Internal\nUse Only", "CONFIDENTIAL", "ｃｏｎｆｉｄｅｎｔｉａｌ"):
        assert save_list([_source("https://a.test/1")], lambda row, t=text: t)[0]["action"] == "hold-marked"


def test_reading_a_saved_source_goes_by_content_not_file_suffix(tmp_path):
    from ocm_regions.fetch.materials import read_saved

    row = _source("https://a.test/1")
    assert read_saved(row, tmp_path) is None
    folder = tmp_path / "trust_docs" / row["provider"] / "fetched"
    folder.mkdir(parents=True)
    path = folder / f"{row['doc_id']}.txt"
    path.write_bytes(b"plain words")
    assert read_saved(row, tmp_path) == "plain words"
    path.write_bytes(b"\x00\x01binary")
    assert read_saved(row, tmp_path) is None
    path.write_bytes(b"%PDF-1.4 not really readable")
    assert read_saved(row, tmp_path) is None


def test_a_changed_word_after_a_special_capital_still_changes_the_hash():
    from ocm_regions.fetch.materials import text_hash

    pad = "İ" * 50
    assert text_hash(f"<p>{pad}</p><p>Dublin</p>".encode(), "text/html") != text_hash(
        f"<p>{pad}</p><p>London</p>".encode(), "text/html"
    )


def test_a_stray_less_than_sign_does_not_hide_the_words_after_it():
    from ocm_regions.fetch.materials import text_hash

    assert text_hash(b"<p>under 2 < 3 ms in Dublin</p>", "text/html") != text_hash(
        b"<p>under 2 < 3 ms in London</p>", "text/html"
    )


def test_a_page_with_an_unclosed_construct_has_no_text_hash():
    from ocm_regions.fetch.materials import text_hash

    for page in (b"<p>Dublin</p><!-- open", b"<p>Dublin</p><script>x", b"<p>Dublin</p><div class='a'"):
        assert text_hash(page, "text/html") == ""


def _saved_folder(row, tmp_path):
    folder = tmp_path / "trust_docs" / row["provider"] / "fetched"
    folder.mkdir(parents=True)
    return folder


def test_every_saved_copy_of_a_source_is_scanned(tmp_path):
    from ocm_regions.fetch.materials import read_saved

    row = _source("https://a.test/1")
    folder = _saved_folder(row, tmp_path)
    (folder / f"{row['doc_id']}.html").write_bytes(b"plain")
    (folder / f"{row['doc_id']}.txt").write_bytes(b"Confidential")
    assert "Confidential" in read_saved(row, tmp_path) and "plain" in read_saved(row, tmp_path)


def test_one_unreadable_copy_makes_the_source_unread(tmp_path):
    from ocm_regions.fetch.materials import read_saved

    row = _source("https://a.test/1")
    folder = _saved_folder(row, tmp_path)
    (folder / f"{row['doc_id']}.html").write_bytes(b"plain")
    (folder / f"{row['doc_id']}.txt").write_bytes(b"\x00binary")
    assert read_saved(row, tmp_path) is None


def test_binary_past_the_first_block_or_garbled_text_counts_as_unread(tmp_path):
    from ocm_regions.fetch.materials import read_saved

    row = _source("https://a.test/1")
    path = _saved_folder(row, tmp_path) / f"{row['doc_id']}.txt"
    path.write_bytes(b"a" * 5000 + b"\x00")
    assert read_saved(row, tmp_path) is None
    path.write_bytes(b"\xff\xfe" * 500 + b"words")
    assert read_saved(row, tmp_path) is None


def test_a_pdf_header_after_leading_bytes_is_still_treated_as_pdf(tmp_path):
    from ocm_regions.fetch.materials import read_saved

    row = _source("https://a.test/1")
    path = _saved_folder(row, tmp_path) / f"{row['doc_id']}.txt"
    path.write_bytes(b"\n\n%PDF-1.4 readable looking words but not a real pdf")
    assert read_saved(row, tmp_path) is None


def test_a_landing_page_served_for_a_pdf_link_is_not_saved_as_the_document(tmp_path):
    result = fetch_materials([_row()], tmp_path, lambda _url: ("text/html", b"<html>landing</html>"), pause=0)
    assert result[0]["status"] == "blocked" and "not a PDF" in result[0]["detail"]
    assert not list(tmp_path.rglob(f"{_row()['doc_id']}*"))


def test_a_landing_page_served_for_a_pdf_link_falls_back_to_the_archive(tmp_path):
    def page(url):
        return ("application/pdf", PDF) if "archive" in url else ("text/html", b"<html>landing</html>")

    report = fetch_materials([_row(archive_url="https://web.archive.org/x")], tmp_path, page, pause=0)
    assert report[0]["source"] == "archive"
    assert (tmp_path / "trust_docs" / "ibm" / "fetched" / f"{_row()['doc_id']}.pdf").read_bytes() == PDF


def _changed_row(**extra):
    return _row(
        sha256=hashlib.sha256(PDF).hexdigest(),
        archive_url="https://web.archive.org/web/1id_/https://a.test/x.pdf",
        **extra,
    )


def _get_by_host(live, archive):
    return lambda url: ("application/pdf", archive if "web.archive.org" in url else live)


def test_a_changed_live_page_is_replaced_by_an_archive_copy_that_matches_the_pin(tmp_path):
    report = fetch_materials([_changed_row()], tmp_path, _get_by_host(PDF + b"changed", PDF), pause=0)
    assert report[0]["status"] == "matched" and report[0]["source"] == "archive"


def test_a_changed_live_page_is_kept_when_the_archive_copy_also_differs(tmp_path):
    report = fetch_materials(
        [_changed_row()], tmp_path, _get_by_host(PDF + b"changed", PDF + b"other"), pause=0
    )
    assert report[0]["status"] == "changed" and report[0]["source"] == "live"


def test_a_failed_archive_copy_leaves_the_live_page(tmp_path):
    def get(url):
        if "web.archive.org" in url:
            raise OSError("HTTP Error 503")
        return "application/pdf", PDF + b"changed"

    report = fetch_materials([_changed_row()], tmp_path, get, pause=0)
    assert report[0]["status"] == "changed" and report[0]["source"] == "live"


def test_a_malformed_archive_response_leaves_the_live_page(tmp_path):
    import http.client

    def get(url):
        if "web.archive.org" in url:
            raise http.client.HTTPException("got more than 100 headers")
        return "application/pdf", PDF + b"changed"

    report = fetch_materials([_changed_row()], tmp_path, get, pause=0)
    assert report[0]["status"] == "changed" and report[0]["source"] == "live"


def test_a_malformed_response_is_reported_and_the_run_goes_on(tmp_path):
    import http.client

    def get(url):
        if url == "https://a.test/1":
            raise http.client.HTTPException("got more than 100 headers")
        return "text/html", b"<p>ok</p>"

    report = fetch_materials(
        [_source("https://a.test/1"), _source("https://a.test/2")], tmp_path, get, pause=0
    )
    assert [r["status"] for r in report][0] == "blocked" and report[1]["status"] != "blocked"


def test_a_row_added_with_a_provider_and_url_is_filled_in():
    row = refresh_index([{"provider": "aws", "url": "https://a.test/new"}])[0]
    assert (row["fetch"], row["access"]) == ("yes", "public") and len(row["doc_id"]) == 12


def test_a_gated_row_stays_unfetched():
    assert index()["https://c.test/artifact"]["fetch"] == "no"


def test_the_save_list_reads_an_index_with_no_note_column():
    from ocm_regions.fetch.materials import save_list

    assert save_list([_source("https://a.test/1")], lambda row: None)[0]["action"] == "hold-unread"


def test_an_archive_request_skips_a_directory_and_reads_an_index_without_kinds():
    from ocm_regions.fetch.corpus import archive_requests

    rows = [
        {"url": "https://a.test/1", "fetch": "yes", "archive_url": "", "kind": "directory"},
        {"url": "https://a.test/2", "fetch": "yes", "archive_url": ""},
    ]
    assert [r["url"] for r in archive_requests(rows, get=lambda url: "", pause=0)] == ["https://a.test/2"]


def test_only_a_source_read_and_found_unmarked_goes_to_the_archive():
    from ocm_regions.fetch.materials import archivable

    rows = [_source("https://a.test/1"), _source("https://a.test/2"), _source("https://a.test/3")]
    text = {
        "https://a.test/1": "plain words",
        "https://a.test/2": "Company Confidential",
        "https://a.test/3": None,
    }
    assert [r["url"] for r in archivable(rows, lambda row: text[row["url"]])] == ["https://a.test/1"]


def test_a_marking_is_found_when_entities_tags_or_soft_hyphens_split_it():
    from ocm_regions.fetch.materials import _is_marked

    for text in (
        "Company &#67;onfidential",
        "Company Confi<b>dential</b>",
        "Company Confi­dential",
        "Company Confi-\ndential",
    ):
        assert _is_marked(text), text
    assert not _is_marked("plain words")


def test_a_marking_is_found_across_cells_and_in_full_width_entities():
    from ocm_regions.fetch.materials import _is_marked

    for text in ("Internal<br>Use Only", "<td>Not</td><td>for distribution</td>", "&#xFF23;onfidential"):
        assert _is_marked(text), text
