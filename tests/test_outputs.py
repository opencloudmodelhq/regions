# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0


from ocm_regions.evidence.verify import quote_found
from ocm_regions.publish.paper import latex_escape, macros_tex
from ocm_regions.publish.site import render_site


def test_quote_found_ignores_case_whitespace_and_markup():
    assert quote_found("Greater  San Antonio", "<p>in the greater\nsan <b>antonio</b> area</p>")


def test_quote_found_tolerates_curly_quotes():
    assert quote_found("Microsoft's site", "Microsoft’s site")


def test_quote_found_rejects_missing_text():
    assert not quote_found("Cheyenne", "<p>Laramie</p>")


def test_latex_escape_handles_specials():
    assert latex_escape("a_b & 5%") == r"a\_b \& 5\%"


def test_macros_only_use_letter_names():
    text = macros_tex({"regionCount": 277})
    assert r"\newcommand{\regionCount}{277}" in text


def test_site_escapes_cell_content():
    html = render_site([{"provider": "x", "region_id": "<b>"}], [], [], {}, {})
    assert '<b>"' not in html.split("const DATA")[0]
    assert "\\u003cb" in html


def test_site_includes_issue_counts():
    html = render_site(
        [],
        [],
        [{"severity": "high", "kind": "gap-iata", "provider": "x", "region_id": "y", "detail": "z"}],
        {},
        {},
    )
    assert "gap-iata" in html


def test_quote_found_accepts_a_table_row_copied_with_cell_separators():
    page = "<table><tr><td>Europe (Poland - Warsaw)</td><td>eu-central-waw</td><td>WAW</td></tr></table>"
    assert quote_found("Europe (Poland - Warsaw) | eu-central-waw | WAW", page)
    assert quote_found("Europe (Poland - Warsaw) eu-central-waw WAW", page)


def test_quote_found_rejects_cells_from_different_rows():
    page = "<table><tr><td>Warsaw</td><td>one</td></tr><tr><td>two</td><td>WAW</td></tr></table>"
    assert not quote_found("Warsaw | WAW", page)


def test_quote_found_reads_inline_markup_as_part_of_a_word():
    assert quote_found("Dallas (us-south)", "<td>Dallas (<code>us-south</code>)</td>")


def test_coverage_share_shows_gaps_that_round_away():
    from ocm_regions.publish.paper import SCHEME_LABELS, _coverage_table

    coverage = {key: {"regions": 391, "share": 391 / 393, "distinct": 131} for key in SCHEME_LABELS}
    assert "99.5\\%" in _coverage_table(coverage, 393)


def test_site_lists_weak_evidence_and_counts_vendors_from_the_data():
    rows = [
        {
            "provider": "x",
            "region_id": "y",
            "stated_location": "S",
            "city": "C",
            "country": "US",
            "grade": "partial",
            "precision": "metro",
            "location_reasoning": "r",
        }
    ]
    html = render_site(rows, [], [], {}, {})
    assert 'id="weak"' in html and '"tier": "partial"' in html
    assert "eight cloud vendors" not in html


def test_changes_list_a_region_removed_and_a_code_in_any_table():
    from ocm_regions.tables.build import region_changes

    base = {
        "provider": "a",
        "city": "X",
        "iata_nearest_airport": "AAA",
        "iata_major_hub": "BBB",
        "iata_nearby_hub": "CCC",
        "grade": "partial",
    }
    changes = region_changes(
        [{**base, "region_id": "gone"}, {**base, "region_id": "kept"}],
        [{**base, "region_id": "kept", "iata_nearest_airport": "DDD"}],
    )
    assert {
        "provider": "a",
        "region_id": "kept",
        "field": "iata_nearest_airport",
        "before": "AAA",
        "after": "DDD",
    } in changes
    assert {"provider": "a", "region_id": "gone", "field": "region", "before": "gone", "after": ""} in changes


def test_writing_no_rows_makes_an_empty_file(tmp_path):
    from ocm_regions.cli import _write_csv

    _write_csv(tmp_path / "x.csv", [])
    assert (tmp_path / "x.csv").read_text() == ""
