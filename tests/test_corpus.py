# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""The corpus is the URL index; a fresh checkout fills what it could not download from a folder of user downloads."""

import hashlib

from ocm_regions.fetch.corpus import (
    archive_requests,
    corpus_rows,
    export_downloads,
    install_missing,
    missing_notice,
    wanted_by_person,
)

URL = "https://a.test/report.pdf"
DOC_ID = hashlib.sha256(URL.encode()).hexdigest()[:12]
TARGET = f"trust_docs/aws/fetched/{DOC_ID}.pdf"


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _material(body=b"one", **extra):
    return {
        "doc_id": DOC_ID,
        "provider": "aws",
        "url": URL,
        "fetch": "yes",
        "sha256": _sha(body),
        "media_type": "application/pdf",
        "archive_url": "",
    } | extra


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _one(tmp_path, material=None):
    raw, drop = tmp_path / "raw", tmp_path / "downloads"
    return raw, drop, corpus_rows([material or _material()], [])


def test_each_indexed_document_keeps_its_url_and_lands_in_the_fetched_folder(tmp_path):
    assert corpus_rows([_material()], []) == [
        {
            "path": TARGET,
            "url": URL,
            "sha256": _sha(b"one"),
            "text_sha256": "",
            "origin": "document",
            "archive_url": "",
        }
    ]


def test_listed_only_documents_are_not_part_of_the_corpus():
    assert corpus_rows([_material(fetch="no")], []) == []


def test_datasets_come_from_the_source_manifest():
    rows = corpus_rows(
        [], [{"path": "ourairports/airports.csv", "url": "https://b.test/a.csv", "sha256": "abc"}]
    )
    assert rows == [
        {
            "path": "ourairports/airports.csv",
            "url": "https://b.test/a.csv",
            "sha256": "abc",
            "text_sha256": "",
            "origin": "dataset",
            "archive_url": "",
        }
    ]


def test_a_missing_document_is_copied_by_its_hash_whatever_its_name(tmp_path):
    raw, drop, rows = _one(tmp_path)
    _write(drop / "Report (1).pdf", b"one")
    assert install_missing(rows, raw, drop)[0]["status"] == "copied"
    assert (raw / TARGET).read_bytes() == b"one"


def test_a_saved_document_with_the_pinned_hash_is_left_alone(tmp_path):
    raw, drop, rows = _one(tmp_path)
    _write(raw / TARGET, b"one")
    assert install_missing(rows, raw, drop)[0]["status"] == "present"


def test_a_saved_document_under_another_suffix_still_counts(tmp_path):
    raw, drop, rows = _one(tmp_path)
    _write(raw / f"trust_docs/aws/fetched/{DOC_ID}.txt", b"one")
    assert install_missing(rows, raw, drop)[0]["status"] == "present"


def test_a_changed_document_is_replaced_when_the_pinned_copy_was_downloaded(tmp_path):
    raw, drop, rows = _one(tmp_path)
    _write(raw / TARGET, b"page changed since")
    _write(drop / "x.pdf", b"one")
    assert install_missing(rows, raw, drop)[0]["status"] == "restored"
    assert (raw / TARGET).read_bytes() == b"one"


def test_a_changed_document_without_a_pinned_copy_is_reported_and_kept(tmp_path):
    raw, drop, rows = _one(tmp_path)
    _write(raw / TARGET, b"page changed since")
    assert install_missing(rows, raw, drop)[0]["status"] == "changed"
    assert (raw / TARGET).read_bytes() == b"page changed since"


def test_a_document_nobody_has_is_reported_missing_with_its_url(tmp_path):
    raw, drop, rows = _one(tmp_path)
    assert install_missing(rows, raw, drop)[0] == {
        "path": TARGET,
        "status": "missing",
        "origin": "document",
        "url": URL,
        "archive_url": "",
    }


def test_a_path_outside_the_raw_folder_is_refused(tmp_path):
    raw, drop = tmp_path / "raw", tmp_path / "downloads"
    _write(drop / "x", b"one")
    rows = [{"path": "../evil", "url": URL, "sha256": _sha(b"one"), "origin": "dataset", "archive_url": ""}]
    assert install_missing(rows, raw, drop)[0]["status"] == "invalid" and not (tmp_path / "evil").exists()


def test_a_person_is_asked_only_for_what_a_fresh_run_lacks(tmp_path):
    report = [
        {"path": "a", "status": "present"},
        {"path": "b", "status": "missing"},
        {"path": "c", "status": "changed"},
    ]
    assert wanted_by_person(report) == {"b", "c"}


def test_exported_downloads_install_back_into_a_fresh_checkout(tmp_path):
    raw, drop, fresh = tmp_path / "raw", tmp_path / "downloads", tmp_path / "fresh"
    _write(raw / TARGET, b"one")
    rows = corpus_rows([_material()], [])
    export_downloads(rows, raw, drop, {TARGET})
    assert [p.name for p in drop.iterdir()] != [f"{DOC_ID}.pdf"]
    assert install_missing(rows, fresh, drop)[0]["status"] == "copied"


def test_export_refuses_a_path_outside_the_raw_folder(tmp_path):
    raw, drop = tmp_path / "raw", tmp_path / "downloads"
    raw.mkdir()
    _write(tmp_path / "secret", b"one")
    rows = [{"path": "../secret", "url": URL, "sha256": _sha(b"one"), "origin": "dataset", "archive_url": ""}]
    export_downloads(rows, raw, drop, {"../secret"})
    assert not list(drop.glob("*"))


def test_the_notice_names_each_document_the_reader_must_get_and_where():
    report = [
        {"path": "a", "status": "present", "url": "https://a.test/ok", "archive_url": ""},
        {
            "path": "b",
            "status": "missing",
            "url": "https://a.test/blocked",
            "archive_url": "https://web.archive.org/x",
        },
        {"path": "c", "status": "changed", "url": "https://a.test/moved", "archive_url": ""},
    ]
    lines = missing_notice(report)
    assert (
        len(lines) == 2 and "https://a.test/blocked" in lines[0] and "https://web.archive.org/x" in lines[0]
    )
    assert "no archive copy" in lines[1]


def test_an_empty_notice_when_nothing_is_lacking():
    assert missing_notice([{"path": "a", "status": "present", "url": "u", "archive_url": ""}]) == []


def _indexed(url, **extra):
    return {"url": url, "fetch": "yes", "archive_url": "", "kind": "certificate"} | extra


def test_only_indexed_documents_without_an_archive_copy_are_sent_to_the_archive():
    rows = [
        _indexed("https://a.test/1"),
        _indexed("https://a.test/2", archive_url="https://web.archive.org/y"),
        _indexed("https://a.test/3", fetch="no"),
        _indexed("https://web.archive.org/web/1id_/https://a.test/4"),
        _indexed("https://a.test/5", kind="directory"),
    ]
    asked = []
    report = archive_requests(rows, lambda url: asked.append(url) or 200, pause=0)
    assert asked == ["https://web.archive.org/save/https://a.test/1"]
    assert report == [{"url": "https://a.test/1", "status": "requested", "detail": "200"}]


def test_a_refused_archive_request_is_reported_and_the_rest_continue():
    def get(url):
        if url.endswith("/1"):
            raise OSError("HTTP Error 523")
        return 200

    report = archive_requests([_indexed("https://a.test/1"), _indexed("https://a.test/2")], get, pause=0)
    assert [r["status"] for r in report] == ["failed", "requested"] and "523" in report[0]["detail"]


def test_an_ordinary_fetch_keeps_the_pinned_dataset_hashes(tmp_path, monkeypatch):
    from ocm_regions.fetch import sources

    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "name,url,path,bytes,sha256,retrieved,license_note\nd,u,d.csv,1,pinned,2026-01-01,x\n",
        encoding="utf-8",
    )
    source = sources.Source("d", "u", "d.csv", "x")
    (tmp_path / "d.csv").write_text("newer data", encoding="utf-8")
    monkeypatch.setattr(sources, "SOURCES", [source])
    monkeypatch.setattr(sources, "download", lambda _source, raw, _force: raw / "d.csv")
    drift = sources.fetch_all(tmp_path, manifest)
    assert drift["changed"] == ["d"] and "pinned" in manifest.read_text(encoding="utf-8")


def test_a_forced_fetch_moves_the_pins(tmp_path, monkeypatch):
    from ocm_regions.fetch import sources

    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "name,url,path,bytes,sha256,retrieved,license_note\nd,u,d.csv,1,pinned,2026-01-01,x\n",
        encoding="utf-8",
    )
    (tmp_path / "d.csv").write_text("newer data", encoding="utf-8")
    monkeypatch.setattr(sources, "SOURCES", [sources.Source("d", "u", "d.csv", "x")])
    monkeypatch.setattr(sources, "download", lambda _source, raw, _force: raw / "d.csv")
    sources.fetch_all(tmp_path, manifest, force=True)
    assert "pinned" not in manifest.read_text(encoding="utf-8")


def test_a_page_whose_words_match_the_pin_counts_as_present(tmp_path):
    from ocm_regions.fetch.materials import text_hash

    html = b"<html><script>var a=1</script><p>in Ashburn</p></html>"
    other = b"<html><script>var a=2</script><p>in Ashburn</p></html>"
    raw = tmp_path / "raw"
    target = f"trust_docs/aws/fetched/{DOC_ID}.html"
    _write(raw / target, other)
    rows = [
        {
            "path": target,
            "url": URL,
            "sha256": _sha(html),
            "text_sha256": text_hash(html, "text/html"),
            "origin": "document",
            "archive_url": "",
        }
    ]
    assert install_missing(rows, raw, tmp_path / "none")[0]["status"] == "present"


def test_a_failed_dataset_download_is_reported_and_the_rest_continue(tmp_path, monkeypatch):
    from ocm_regions.fetch import sources

    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "name,url,path,bytes,sha256,retrieved,license_note\nd,u,d.csv,1,pinned,2026-01-01,x\ne,u,e.csv,1,pinned,2026-01-01,x\n",
        encoding="utf-8",
    )
    (tmp_path / "e.csv").write_text("data", encoding="utf-8")

    def download(source, raw, _force):
        if source.name == "d":
            raise OSError("HTTP Error 429")
        return raw / "e.csv"

    monkeypatch.setattr(
        sources, "SOURCES", [sources.Source("d", "u", "d.csv", "x"), sources.Source("e", "u", "e.csv", "x")]
    )
    monkeypatch.setattr(sources, "download", download)
    assert sources.fetch_all(tmp_path, manifest, force=True)["failed"] == ["d"]
    assert "d,u,d.csv" in manifest.read_text(encoding="utf-8")
