# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""The author's review of every quoted evidence record, and how the paper reports it."""


def test_a_verdict_agrees_when_it_matches_the_stance_the_record_carries():
    from ocm_regions.analysis.quote_review import review_macros

    evidence = [
        {"evidence_id": "E1", "stance": "supports", "source_type": "vendor"},
        {"evidence_id": "E2", "stance": "contradicts", "source_type": "vendor"},
        {"evidence_id": "E3", "stance": "contradicts", "source_type": "vendor"},
        {"evidence_id": "E4", "stance": "partial", "source_type": "certification"},
        {"evidence_id": "E5", "stance": "supports", "source_type": "press"},
        {"evidence_id": "E6", "stance": "supports", "source_type": "vendor_feed"},
    ]
    verdicts = [
        {"evidence_id": "E1", "verdict": "supports"},
        {"evidence_id": "E2", "verdict": "wrong-place"},
        {"evidence_id": "E3", "verdict": "area-only"},
        {"evidence_id": "E4", "verdict": "supports"},
        {"evidence_id": "E5", "verdict": "cannot-open"},
    ]
    macros = review_macros(verdicts, evidence)
    assert macros == {
        "reviewRecords": 5,
        "reviewCannotOpen": 1,
        "reviewOpened": 4,
        "reviewOnPage": 4,
        "reviewAgree": 3,
        "reviewDisagree": 1,
        "reviewWithdrawn": 0,
    }
    assert all(name.isalpha() for name in macros)


def test_words_missing_from_the_page_are_not_counted_as_found():
    from ocm_regions.analysis.quote_review import review_macros

    evidence = [{"evidence_id": "E1", "stance": "supports", "source_type": "vendor"}]
    macros = review_macros([{"evidence_id": "E1", "verdict": "not-on-page"}], evidence)
    assert macros["reviewOnPage"] == 0 and macros["reviewDisagree"] == 1


def test_a_review_that_misses_a_quoted_record_is_refused():
    import pytest

    from ocm_regions.analysis.quote_review import review_macros

    evidence = [{"evidence_id": "E1", "stance": "supports", "source_type": "vendor"}]
    with pytest.raises(ValueError, match="E1"):
        _ = review_macros([], evidence)


def test_a_reviewed_record_no_longer_in_the_table_counts_as_withdrawn_and_as_a_disagreement():
    from ocm_regions.analysis.quote_review import review_macros

    evidence = [{"evidence_id": "E1", "stance": "supports", "source_type": "vendor"}]
    verdicts = [
        {"evidence_id": "E1", "verdict": "supports"},
        {"evidence_id": "E9", "verdict": "area-only"},
    ]
    macros = review_macros(verdicts, evidence)
    assert (macros["reviewRecords"], macros["reviewOnPage"]) == (2, 2)
    assert (macros["reviewAgree"], macros["reviewDisagree"], macros["reviewWithdrawn"]) == (1, 1, 1)
