# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from ocm_regions.evidence.passages import PASSAGE_FIELDS, display_text, find_passages, passage_rows
from ocm_regions.evidence.verify import quote_found

PAGE = """<html><head><style>.x{color:red}</style><script>var a = "Ashburn";</script></head><body>
<h1>Regions</h1>
<table><tr><td>us-ashburn-1</td><td>US East (Ashburn)</td><td>Ashburn, VA</td></tr>
<tr><td>us-phoenix-1</td><td>US West (Phoenix)</td><td>Phoenix, AZ</td></tr></table>
<p>Our office in Ashburn hosts a sales team &amp; partners.</p>
</body></html>"""


def test_display_text_drops_markup_scripts_and_styles_but_keeps_case():
    text = display_text(PAGE)
    assert "<" not in text and "color:red" not in text and "var a" not in text
    assert "US East (Ashburn)" in text and "sales team & partners" in text


def test_a_table_row_stays_on_one_line():
    lines = display_text(PAGE).splitlines()
    assert any("us-ashburn-1" in line and "Ashburn, VA" in line for line in lines)


def test_passages_that_name_the_region_come_first():
    found = find_passages(display_text(PAGE), "Ashburn", ["us-ashburn-1"])
    assert "us-ashburn-1" in found[0] and "sales team" in found[1]


def test_every_passage_passes_the_quote_check_on_its_document():
    for passage in find_passages(display_text(PAGE), "Ashburn", ["us-ashburn-1"]):
        assert quote_found(passage, PAGE)


def test_the_city_matches_without_accents_or_case():
    found = find_passages("Region Brazil East is in SÃO PAULO, Brazil.", "Sao Paulo", [])
    assert found == ["Region Brazil East is in SÃO PAULO, Brazil."]


def test_a_long_line_is_cut_to_a_window_around_the_city_on_word_boundaries():
    text = " ".join(["word"] * 200) + " Ashburn " + " ".join(["more"] * 200)
    (passage,) = find_passages(text, "Ashburn", [], width=60)
    assert "Ashburn" in passage and len(passage) <= 2 * 60 + len("Ashburn") + 2
    assert not passage.startswith("ord") and not passage.endswith("mor")


def test_passages_are_capped_and_never_repeat():
    text = "\n".join(["Ashburn site"] * 10)
    (passage,) = find_passages(text, "Ashburn", [], limit=3)
    assert passage.startswith("Ashburn site")


def test_a_city_part_of_a_longer_word_does_not_match():
    assert find_passages("Romeville and Rometown", "Rome", []) == []


def row(**extra):
    return {
        "sheet_id": "hc001",
        "provider": "oracle",
        "region_id": "us-ashburn-1",
        "region_name": "US East (Ashburn)",
        "stated_location": "Ashburn, VA",
        "city": "Ashburn",
        "country": "US",
        "url_used": "https://o.test/r",
        "quote": "x",
        "status": "found",
    } | extra


def test_rows_list_each_passage_with_a_blank_pick_column():
    rows = passage_rows([row()], lambda _url, _provider: PAGE)
    assert [r["passage_no"] for r in rows] == ["1", "2"]
    assert all(list(r) == PASSAGE_FIELDS and r["pick"] == "" and r["flag"] == "" for r in rows)


def test_a_city_missing_from_the_document_is_flagged():
    (only,) = passage_rows([row(city="Chicago")], lambda _url, _provider: PAGE)
    assert only["flag"] == "city-not-in-document" and only["passage"] == ""


def test_a_document_with_no_saved_text_is_flagged():
    (only,) = passage_rows([row()], lambda _url, _provider: "")
    assert only["flag"] == "no-saved-text"


def test_a_row_without_a_city_is_passed_through_for_the_reviewer():
    (only,) = passage_rows([row(city="", status="country-only")], lambda _url, _provider: PAGE)
    assert only["flag"] == "no-city-given"


def test_cited_text_reads_a_saved_document_or_a_dataset_and_nothing_else(tmp_path):
    import hashlib

    from ocm_regions.evidence.passages import corpus_text_of
    from ocm_regions.model.llm import FETCHED_DIR

    doc_url, data_url = "https://o.test/doc", "https://o.test/feed.csv"
    folder = tmp_path / "trust_docs" / "oracle" / FETCHED_DIR
    folder.mkdir(parents=True)
    (folder / f"{hashlib.sha256(doc_url.encode()).hexdigest()[:12]}.html").write_text("<p>Ashburn</p>")
    (tmp_path / "feeds").mkdir()
    (tmp_path / "feeds" / "f.csv").write_text("1.2.3.0/24,US,US-VA,Ashburn,\n")
    text_of = corpus_text_of(
        tmp_path, [{"url": doc_url, "provider": "oracle"}], [{"url": data_url, "path": "feeds/f.csv"}]
    )
    assert "Ashburn" in text_of(doc_url, "oracle") and "US-VA,Ashburn" in text_of(data_url, "oracle")
    assert text_of("https://elsewhere.test/", "oracle") == ""


def test_a_city_inside_an_identifier_does_not_match():
    assert find_passages("us-ashburn-1\nmy_ashburn_zone", "Ashburn", []) == []


def test_a_short_line_takes_its_neighbours_as_context():
    text = "Region\nus-ashburn-1\nUS East\nAshburn, VA\nUS\nnext row"
    (passage,) = find_passages(text, "Ashburn", ["us-ashburn-1"])
    assert "US East" in passage and "Ashburn, VA" in passage and "\n" not in passage


def test_a_city_in_a_hyphenated_name_without_digits_still_matches():
    assert find_passages("AF-Johannesburg af-south-1 region", "Johannesburg", []) == [
        "AF-Johannesburg af-south-1 region"
    ]


def test_feed_lines_never_borrow_their_neighbours():
    feed = "1.0.0.0/24,NO,,OSLO,\n2.0.0.0/24,FR,,PARIS,\n3.0.0.0/24,DE,,BERLIN,\n"
    rows = passage_rows([row(city="Paris", url_used="https://feed.test/x")], lambda _url, _provider: feed)
    assert [r["passage"] for r in rows] == ["2.0.0.0/24,FR,,PARIS,"]


def test_every_listed_passage_passes_the_quote_check_or_falls_back_to_its_own_line():
    page = "<html><head><title>Plans for Hortolandia</title><script>x()</script></head><body><p>Search</p></body></html>"
    rows = passage_rows([row(city="Hortolandia", url_used="https://n.test/a")], lambda _url, _provider: page)
    assert [r["passage"] for r in rows] == ["Plans for Hortolandia"]
    assert quote_found(rows[0]["passage"], page)
