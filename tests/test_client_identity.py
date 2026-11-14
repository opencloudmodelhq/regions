# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0


import pytest

from ocm_regions.fetch import sources

NAMES = ("OCM_REGIONS_USER_AGENT", "OCM_REGIONS_FROM", "OCM_REGIONS_REQUIRE_PROXY")
PROXIES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


@pytest.fixture
def bare(monkeypatch):
    for name in (*NAMES, *PROXIES):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_a_reader_without_settings_sends_the_generic_client_name(bare):
    assert sources.client_headers() == {"User-Agent": "ocm-regions-harness/0.1"}


def test_an_operator_names_their_own_client_and_contact_in_the_environment(bare):
    bare.setenv("OCM_REGIONS_USER_AGENT", "ExampleBot/1.0 (+https://example.org/bot)")
    bare.setenv("OCM_REGIONS_FROM", "bot@example.org")
    assert sources.client_headers() == {
        "User-Agent": "ExampleBot/1.0 (+https://example.org/bot)",
        "From": "bot@example.org",
    }


def test_a_run_that_requires_a_proxy_refuses_to_send_without_one(bare):
    bare.setenv("OCM_REGIONS_REQUIRE_PROXY", "1")
    with pytest.raises(RuntimeError, match="proxy"):
        sources.client_headers()
    bare.setenv("HTTPS_PROXY", "http://proxy.example.org:3128")
    bare.setenv("HTTP_PROXY", "http://proxy.example.org:3128")
    assert "User-Agent" in sources.client_headers()


def test_a_gzip_body_that_expands_past_the_limit_is_refused():
    import gzip

    small = gzip.compress(b"plain text")
    assert sources.ungzipped(small) == b"plain text"
    assert sources.ungzipped(b"not gzip") == b"not gzip"
    bomb = gzip.compress(b"\0" * (sources.MAX_UNZIPPED + 1))
    assert len(bomb) < sources.MAX_UNZIPPED // 100
    with pytest.raises(ValueError, match="expands"):
        sources.ungzipped(bomb)
