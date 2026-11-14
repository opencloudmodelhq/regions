# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from ocm_regions.analysis.sensitivity import SETTINGS, overridden, sensitivity_table, summarise_run
from ocm_regions.place import feeds, placer


def test_a_setting_is_changed_only_while_it_is_being_tested():
    before = placer.AGREE_KM
    with overridden(placer, "AGREE_KM", 25):
        assert placer.AGREE_KM == 25
    assert before == placer.AGREE_KM


def test_a_setting_is_restored_after_a_failure():
    before = feeds.MIN_SHARE
    try:
        with overridden(feeds, "MIN_SHARE", 0.9):
            raise RuntimeError
    except RuntimeError:
        pass
    assert before == feeds.MIN_SHARE


def test_every_round_number_is_tested_including_the_one_we_use():
    assert {name for name, *_ in SETTINGS} == {
        "agreement radius km",
        "minimum population",
        "minimum feed share",
    }
    used = {"agreement radius km": 50, "minimum population": 20000, "minimum feed share": 0.5}
    for name, _module, _attribute, values in SETTINGS:
        assert used[name] in values and len(values) >= 3


def test_a_run_is_summarised_with_an_error_range():
    rows = [
        {"city": "A", "matches_curated": "yes", "split": "holdout"},
        {"city": "B", "matches_curated": "no", "split": "development"},
        {"city": "", "matches_curated": "", "split": "holdout"},
    ]
    row = summarise_run("x", 1, rows)
    assert (
        row["placed"],
        row["matched"],
        row["abstained"],
        row["holdout_placed"],
        row["holdout_matched"],
    ) == (2, 1, 1, 1, 1)
    assert 0 <= row["low"] <= 0.5 <= row["high"] <= 1


def test_table_marks_the_value_in_use():
    rows = [
        {
            "setting": "minimum population",
            "value": 20000,
            "placed": 2,
            "matched": 1,
            "abstained": 0,
            "low": 0.1,
            "high": 0.9,
            "holdout_placed": 1,
            "holdout_matched": 1,
        }
    ]
    table = sensitivity_table(rows)
    assert "\\toprule" in table and "20000*" in table
