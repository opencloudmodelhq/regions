# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import csv

from ocm_regions.fetch.snapshots import collect_urls, latest_capture, write_snapshots

CDX = "20261013031626 https://a.test/x 200 BDBF\n20261018174520 https://a.test/x 200 EDSV\n"


def test_urls_are_collected_once_from_evidence_and_documents_with_their_vendors():
    evidence = [
        {"provider": "aws", "url": "https://a.test/x"},
        {"provider": "aws", "url": "https://a.test/x"},
    ]
    documents = [{"provider": "ibm", "url": "https://b.test/y"}, {"provider": "ibm", "url": ""}]
    assert collect_urls(evidence, documents) == [("aws", "https://a.test/x"), ("ibm", "https://b.test/y")]


def test_latest_capture_takes_the_newest_with_its_digest():
    assert latest_capture("https://a.test/x", lambda _url: CDX) == {
        "timestamp": "20261018174520",
        "digest": "EDSV",
    }


def test_a_page_with_no_capture_says_so():
    assert latest_capture("https://a.test/x", lambda _url: "") == {"timestamp": "", "digest": ""}


def test_a_failed_lookup_is_recorded_and_does_not_stop_the_run(tmp_path):
    def broken(_url):
        raise TimeoutError("slow")

    rows = write_snapshots([("aws", "https://a.test/x")], tmp_path / "s.csv", broken, pause=0)
    assert rows[0]["status"] == "lookup-failed"
    saved = list(csv.DictReader((tmp_path / "s.csv").open(encoding="utf-8")))
    assert saved[0]["url"] == "https://a.test/x" and saved[0]["status"] == "lookup-failed"


def test_captured_and_missing_pages_are_told_apart(tmp_path):
    rows = write_snapshots(
        [("aws", "u1"), ("aws", "u2")], tmp_path / "s.csv", lambda url: CDX if "u1" in url else "", pause=0
    )
    assert [r["status"] for r in rows] == ["captured", "not-captured"]
    assert "web.archive.org/web/20261018174520id_/u1" in rows[0]["archive_url"]


def test_a_rerun_only_retries_failed_lookups_and_keeps_the_rest(tmp_path):
    from ocm_regions.fetch.snapshots import retry_failed

    previous = [
        {
            "provider": "aws",
            "url": "u1",
            "status": "captured",
            "timestamp": "2026",
            "digest": "D",
            "archive_url": "a",
        },
        {
            "provider": "aws",
            "url": "u2",
            "status": "lookup-failed",
            "timestamp": "",
            "digest": "",
            "archive_url": "",
        },
    ]
    asked = []

    def lookup(url):
        asked.append(url)
        return CDX

    rows = retry_failed(previous, tmp_path / "s.csv", lookup, pause=0)
    assert asked == ["u2"]
    assert [r["status"] for r in rows] == ["captured", "captured"] and rows[0]["archive_url"] == "a"


def test_a_failed_lookup_keeps_the_capture_already_known(tmp_path):
    known = [
        {
            "provider": "aws",
            "url": "u1",
            "status": "captured",
            "timestamp": "2026",
            "digest": "D",
            "archive_url": "a",
        }
    ]

    def broken(_url):
        raise TimeoutError("throttled")

    rows = write_snapshots(
        [("aws", "u1"), ("aws", "u2")], tmp_path / "s.csv", broken, pause=0, previous=known
    )
    assert rows[0]["status"] == "captured" and rows[0]["archive_url"] == "a"
    assert rows[1]["status"] == "lookup-failed"


def test_lookup_retries_waiting_longer_each_time_when_throttled():
    from ocm_regions.fetch.snapshots import patient_lookup

    calls, waits = [], []

    def lookup(url):
        calls.append(url)
        if len(calls) < 3:
            raise OSError("429")
        return CDX

    assert patient_lookup(lookup, sleep=waits.append)("u") == CDX
    assert len(calls) == 3 and waits[0] < waits[1]


def test_a_rerun_also_looks_again_at_sources_with_no_capture(tmp_path):
    from ocm_regions.fetch.snapshots import retry_failed

    previous = [
        {
            "provider": "aws",
            "url": "u1",
            "status": "captured",
            "timestamp": "2026",
            "digest": "D",
            "archive_url": "a",
        },
        {
            "provider": "aws",
            "url": "u2",
            "status": "not-captured",
            "timestamp": "",
            "digest": "",
            "archive_url": "",
        },
    ]
    asked = []

    def lookup(url):
        asked.append(url)
        return CDX

    rows = retry_failed(previous, tmp_path / "s.csv", lookup, pause=0)
    assert asked == ["u2"] and rows[1]["status"] == "captured"
