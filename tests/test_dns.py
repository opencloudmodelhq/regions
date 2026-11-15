# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0


import urllib.parse

import pytest

from ocm_regions.fetch import dns

ANSWERS = {
    "A": {
        "Status": 0,
        "Answer": [
            {"name": "a.test", "type": 5, "data": "edge.a.test."},
            {"name": "edge.a.test", "type": 1, "data": "93.184.216.34"},
        ],
    },
    "AAAA": {"Status": 0, "Answer": [{"name": "a.test", "type": 28, "data": "2606:4700:4700::1111"}]},
}


def recorder(answers):
    asked = []

    def ask(url):
        asked.append(url)
        return answers[urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["type"][0]]

    return ask, asked


def test_a_resolver_gives_the_addresses_of_both_families_and_skips_aliases():
    ask, asked = recorder(ANSWERS)
    assert dns.lookup("a.test", "cloudflare", ask) == ["93.184.216.34", "2606:4700:4700::1111"]
    assert [url.split("?")[0] for url in asked] == ["https://cloudflare-dns.com/dns-query"] * 2
    assert all("name=a.test" in url for url in asked)


def test_a_resolver_is_chosen_by_name_or_by_its_https_address():
    assert dns.endpoint("cloudflare") == "https://cloudflare-dns.com/dns-query"
    assert dns.endpoint("google") == "https://dns.google/resolve"
    assert dns.endpoint("https://dns.example.org/query") == "https://dns.example.org/query"
    for bad in ("http://dns.example.org/query", "quad9", "1.1.1.1"):
        with pytest.raises(ValueError):
            dns.endpoint(bad)


def test_a_name_no_resolver_answer_gives_an_address_for_is_an_error():
    missing = {"Status": 3}
    ask, _asked = recorder({"A": missing, "AAAA": missing})
    with pytest.raises(OSError):
        dns.lookup("gone.test", "google", ask)


def test_an_answer_that_is_not_an_address_is_left_out():
    odd = {
        "Status": 0,
        "Answer": [{"type": 1, "data": 7}, {"type": 1}, "text", {"type": 1, "data": "192.0.2.7"}],
    }
    ask, _asked = recorder({"A": odd, "AAAA": {"Status": 0}})
    assert dns.lookup("a.test", "google", ask) == ["192.0.2.7"]
