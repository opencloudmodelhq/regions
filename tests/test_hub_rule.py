# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0


import pytest

from ocm_regions.common.geo import Airport


def airport(code, country):
    return Airport(code, code, code, country, 0.0, 0.0, "large_airport", True)


def test_domestic_hub_is_accepted():
    from ocm_regions.common.geo import require_domestic_hub

    assert require_domestic_hub("kulai", "MY", airport("JHB", "MY")).code == "JHB"


def test_foreign_hub_is_rejected_and_named():
    from ocm_regions.common.geo import require_domestic_hub

    with pytest.raises(ValueError, match="kulai.*SIN.*SG.*MY"):
        require_domestic_hub("kulai", "MY", airport("SIN", "SG"))
