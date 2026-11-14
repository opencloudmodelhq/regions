# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0


def test_date_span_reads_one_day_or_a_range():
    from ocm_regions.publish.paper import date_span

    assert date_span(["2026-11-05", "2026-11-05", ""]) == "5 November 2026"
    assert date_span(["2026-11-09", "2026-11-05"]) == "5 to 9 November 2026"
    assert date_span(["2026-10-30", "2026-11-02"]) == "30 October 2026 to 2 November 2026"


def test_release_macros_carry_the_dates_and_the_version():
    from ocm_regions.publish.paper import release_macros

    macros = release_macros(
        [{"retrieved": "2026-11-05"}],
        [{"retrieved": "2026-11-09"}, {"retrieved": ""}],
        'title: x\nversion: "1.1.0"\n',
    )
    assert macros == {
        "listsRetrieved": "5 November 2026",
        "documentsRetrieved": "9 November 2026",
        "datasetVersion": "1.1.0",
    }


def test_generated_tables_carry_no_held_out_column():
    from ocm_regions.analysis.sensitivity import sensitivity_table
    from ocm_regions.model.llm import llm_table

    score = {"regions": 2, "answered": 2, "matched": 1}
    report = {
        "scores": {
            "rules": {"all": score, "holdout": score, "review": score, "overturned": score, "rescued": []}
        }
    }
    row = {
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
    assert "Held-out" not in llm_table(report) and "Held-out" not in sensitivity_table([row])
