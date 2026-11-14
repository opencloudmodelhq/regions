# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from ocm_regions.evidence.handcheck import (
    PASS_ONE_FIELDS,
    PASS_TWO_FIELDS,
    pass_one_rows,
    pass_two_rows,
    sample,
)


def region(provider, region_id, **extra):
    return {
        "provider": provider,
        "region_id": region_id,
        "region_name": f"{region_id} name",
        "stated_location": "Somewhere",
        "city": "Hidden",
        "country": "XX",
        "iata_nearest_airport": "AAA",
        "evidence_urls": "https://a.test/1;https://b.test/2",
    } | extra


def regions(n=30):
    return [region(p, f"r{i}") for p in "abc" for i in range(n)]


def test_the_sample_holds_every_weak_region_then_fills_with_others():
    weak = {("a", "r1"), ("b", "r2")}
    chosen = sample(regions(), weak, size=10, seed="s")
    assert len(chosen) == 10
    assert [(r["provider"], r["region_id"]) for r in chosen[:2]] == sorted(weak)
    assert all(r["group"] == "weak" for r in chosen[:2]) and all(r["group"] == "random" for r in chosen[2:])


def test_the_sample_is_the_same_for_the_same_seed_and_differs_for_another():
    first = sample(regions(), set(), size=10, seed="s")
    assert first == sample(list(reversed(regions())), set(), size=10, seed="s")
    assert first != sample(regions(), set(), size=10, seed="t")


def test_a_prefix_of_the_random_group_is_itself_a_random_sample():
    small = sample(regions(), set(), size=5, seed="s")
    large = sample(regions(), set(), size=10, seed="s")
    assert large[:5] == small


def test_pass_one_lists_corpus_sources_but_no_answer():
    row = pass_one_rows(sample([region("a", "r1")], set(), size=1, seed="s"))[0]
    assert row["corpus_urls"] == "https://a.test/1 https://b.test/2"
    assert "Hidden" not in row.values() and "AAA" not in row.values()
    assert list(row) == PASS_ONE_FIELDS


def test_pass_two_shows_neither_answers_nor_corpus_sources():
    row = pass_two_rows(sample([region("a", "r1")], set(), size=1, seed="s"))[0]
    assert not any("a.test" in value or value in ("Hidden", "AAA") for value in row.values())
    assert list(row) == PASS_TWO_FIELDS
    assert row["status"] == ""


def test_the_command_writes_both_sheets_from_the_tables(tmp_path):
    import csv

    from ocm_regions.evidence.handcheck import write_sheets

    regions_csv, weak_csv = tmp_path / "regions.csv", tmp_path / "weak.csv"
    rows = regions(5)
    with regions_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    weak_csv.write_text("provider,region_id\na,r1\n")
    write_sheets(regions_csv, weak_csv, tmp_path / "out", size=4)
    one = list(csv.DictReader((tmp_path / "out" / "pass1_corpus.csv").open()))
    two = list(csv.DictReader((tmp_path / "out" / "pass2_independent.csv").open()))
    assert len(one) == len(two) == 4 and one[0]["region_id"] == "r1" and one[0]["group"] == "weak"
