# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0


import pytest

ROUTING = (
    "OCM_REGIONS_DNS",
    "OCM_REGIONS_REQUIRE_PROXY",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)


@pytest.fixture(autouse=True)
def no_routing_from_the_shell(monkeypatch):
    for name in ROUTING:
        monkeypatch.delenv(name, raising=False)
