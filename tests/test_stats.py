# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import pytest

from ocm_regions.common.stats import wilson


def test_wilson_matches_a_known_interval():
    low, high = wilson(9, 10)
    assert low == pytest.approx(0.596, abs=0.001) and high == pytest.approx(0.982, abs=0.001)


def test_wilson_stays_inside_zero_and_one_at_the_edges():
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == 1.0
    assert wilson(10, 10)[0] < 1.0


def test_wilson_with_no_data_says_nothing():
    assert wilson(0, 0) == (0.0, 1.0)


def test_more_data_narrows_the_interval():
    small, large = wilson(9, 10), wilson(90, 100)
    assert large[1] - large[0] < small[1] - small[0]
