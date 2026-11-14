# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import urllib.error

import pytest

from ocm_regions.evidence import verify
from ocm_regions.fetch import corpus, materials, robots, sources
from ocm_regions.fetch.materials import fetch_materials, refresh_index

BOT = {"User-Agent": "ExampleBot/1.0 (+https://example.org/bot)"}


@pytest.fixture(autouse=True)
def fresh():
    robots.forget()


def serving(text, seen=None):
    def read(url, _headers):
        if seen is not None:
            seen.append(url)
        return text

    return read


def test_the_product_name_is_the_part_of_the_client_name_before_the_version():
    assert robots.product("ExampleBot/1.0 (+https://example.org/bot)") == "ExampleBot"


def test_a_rule_for_this_client_stops_it_and_a_rule_for_another_does_not():
    ours = serving("User-agent: ExampleBot\nDisallow: /private/\n")
    assert not robots.allowed("https://a.test/private/page", BOT, ours)
    assert robots.allowed("https://a.test/open", BOT, ours)
    robots.forget()
    assert robots.allowed("https://a.test/private/page", BOT, serving("User-agent: OtherBot\nDisallow: /\n"))


def test_a_rule_for_every_client_applies():
    assert not robots.allowed("https://a.test/x", BOT, serving("User-agent: *\nDisallow: /\n"))


def test_a_site_with_no_robots_file_allows_everything():
    assert robots.allowed("https://a.test/x", BOT, serving(""))


def test_a_site_whose_robots_file_cannot_be_read_is_left_alone():
    assert not robots.allowed("https://a.test/x", BOT, serving(None))
    with pytest.raises(robots.RobotsDisallowed, match="a.test"):
        robots.require("https://a.test/x", BOT, serving(None))


def test_the_robots_file_is_read_once_for_each_site():
    seen = []
    read = serving("", seen)
    for path in ("one", "two", "three"):
        assert robots.allowed(f"https://a.test/{path}", BOT, read)
    assert robots.allowed("https://b.test/one", BOT, read)
    assert seen == ["https://a.test/robots.txt", "https://b.test/robots.txt"]


def test_a_crawl_delay_is_known_once_the_site_has_been_checked():
    assert robots.crawl_delay("https://a.test/x", BOT) == 0
    assert robots.allowed("https://a.test/x", BOT, serving("User-agent: *\nCrawl-delay: 7\n"))
    assert robots.crawl_delay("https://a.test/y", BOT) == 7


@pytest.mark.parametrize(
    ("code", "expected"), [(404, ""), (410, ""), (401, ""), (403, ""), (429, None), (500, None)]
)
def test_an_unavailable_file_allows_and_a_busy_or_failing_site_does_not(code, expected):
    class Refusing:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, code, "no", None, None)

    assert robots.read_robots("https://a.test/robots.txt", BOT, Refusing()) == expected


def test_every_fetcher_asks_before_it_sends(monkeypatch):
    def refuse(url, *_rest):
        raise robots.RobotsDisallowed(url)

    def no_network(*_args, **_kwargs):
        raise AssertionError("a request was sent")

    monkeypatch.setattr(robots, "require", refuse)
    monkeypatch.setattr(materials, "_resolve", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(materials.urllib.request, "build_opener", no_network)
    monkeypatch.setattr(sources.urllib.request, "urlopen", no_network)
    for fetch in (materials.http_get, sources._plain_get, verify._fetch):
        with pytest.raises(robots.RobotsDisallowed):
            fetch("https://a.test/page")


def test_a_disallowed_document_is_reported_and_its_archive_copy_is_not_used(tmp_path):
    asked = []

    def get(url):
        asked.append(url)
        raise robots.RobotsDisallowed(url)

    row = refresh_index([{"provider": "aws", "url": "https://a.test/news"}], [])[0]
    row = row | {"archive_url": "https://web.archive.org/web/2026id_/https://a.test/news"}
    report = fetch_materials([row], tmp_path, get, pause=0)
    assert report[0]["status"] == "robots-disallowed" and asked == ["https://a.test/news"]


def test_a_disallowed_document_is_not_asked_of_a_person():
    report = [
        {
            "path": "a",
            "status": "missing",
            "origin": "document",
            "url": "https://a.test/1",
            "archive_url": "",
        },
        {
            "path": "b",
            "status": "missing",
            "origin": "document",
            "url": "https://a.test/2",
            "archive_url": "",
        },
    ]
    lines = corpus.missing_notice(report, refused={"https://a.test/2"})
    assert len(lines) == 1 and "https://a.test/1" in lines[0]


def test_a_site_that_asks_for_a_longer_wait_than_the_cap_is_left_unread():
    slow = serving(f"User-agent: *\nCrawl-delay: {robots.MAX_CRAWL_DELAY + 1}\n")
    assert not robots.allowed("https://a.test/x", BOT, slow)
    assert robots.crawl_delay("https://a.test/x", BOT) <= robots.MAX_CRAWL_DELAY
    robots.forget()
    assert robots.allowed(
        "https://a.test/x", BOT, serving(f"User-agent: *\nCrawl-delay: {robots.MAX_CRAWL_DELAY}\n")
    )


def test_a_robots_file_is_followed_to_another_site_for_five_redirects_at_most():
    request = robots.urllib.request.Request("https://a.test/robots.txt")
    guard = robots.FiveRedirects()
    assert (
        guard.redirect_request(request, None, 301, "Moved", {}, "https://www.a.test/robots.txt") is not None
    )
    assert guard.max_redirections == 5


def test_a_document_fetch_never_asks_a_private_address_for_its_robots_file(monkeypatch):
    def no_network(*_args, **_kwargs):
        raise AssertionError("a request was sent")

    monkeypatch.setattr(materials, "_resolve", lambda _host: ["127.0.0.1"])
    monkeypatch.setattr(materials.urllib.request, "build_opener", no_network)
    assert materials.checked_robots("https://a.test/robots.txt", BOT) is None


def test_a_document_fetch_reads_robots_files_through_the_address_check(monkeypatch):
    used = []
    monkeypatch.setattr(robots, "require", lambda _url, _headers, read=None: used.append(read))
    monkeypatch.setattr(materials, "_resolve", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(
        materials.urllib.request, "build_opener", lambda *_a: (_ for _ in ()).throw(OSError("stop"))
    )
    with pytest.raises(OSError, match="stop"):
        materials.http_get("https://a.test/page")
    assert used == [materials.checked_robots]


def test_a_quote_check_waits_the_delay_the_site_asks_for(monkeypatch):
    class Page:
        headers = {"Content-Type": "text/html"}

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return b"<p>page</p>"

    waited = []
    monkeypatch.setattr(robots, "require", lambda _url, _headers: None)
    monkeypatch.setattr(robots, "crawl_delay", lambda _url, _headers: 7.0)
    monkeypatch.setattr(verify.urllib.request, "urlopen", lambda _request, timeout: Page())
    monkeypatch.setattr(verify.time, "sleep", waited.append)

    assert verify._fetch.__wrapped__("https://a.test/page") == ("text/html", b"<p>page</p>")
    assert waited == [7.0]
