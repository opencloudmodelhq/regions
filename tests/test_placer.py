# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import inspect
from pathlib import Path

import pytest


def city(name, cc, admin, pop, lat, lon):
    return {"name": name, "country": cc, "admin1": admin, "population": pop, "lat": lat, "lon": lon}


def gazetteer():
    from ocm_regions.place.placer import Gazetteer

    return Gazetteer(
        cities=[
            city("Dallas", "US", "TX", 1300000, 32.78, -96.80),
            city("Dallas", "US", "OR", 17000, 44.92, -123.31),
            city("Virginia", "US", "MN", 8000, 47.52, -92.53),
            city("Ashburn", "US", "VA", 43000, 39.04, -77.49),
            city("Tokyo", "JP", "40", 8000000, 35.69, 139.69),
            city(
                "Frankfurt am Main",
                "DE",
                "05",
                650000,
                50.11,
                8.68,
            ),
            city("Singapore", "SG", "00", 5600000, 1.29, 103.85),
            city("Washington", "US", "DC", 680000, 38.90, -77.04),
            city("Johor Bahru", "MY", "01", 800000, 1.47, 103.76),
            city("Tinytown", "FR", "11", 900, 48.0, 2.0),
        ],
        aliases={"frankfurt": "Frankfurt am Main"},
        areas={
            "virginia": "US",
            "oregon": "US",
            "saudi arabia": "SA",
            "singapore": "SG",
            "germany": "DE",
            "washington": "US",
        },
        countries={
            "us": "US",
            "uk": "GB",
            "de": "DE",
            "germany": "DE",
            "singapore": "SG",
            "saudi arabia": "SA",
            "japan": "JP",
        },
        states={"tx": "TX", "or": "OR", "va": "VA"},
    )


def test_label_prefers_text_in_brackets():
    from ocm_regions.place.placer import label_city

    assert label_city("Asia Pacific (Tokyo)", gazetteer()).name == "Tokyo"


def test_label_uses_a_state_code_to_choose_between_namesakes():
    from ocm_regions.place.placer import label_city

    assert label_city("Dallas, OR", gazetteer()).lat == 44.92
    assert label_city("Dallas, TX", gazetteer()).lat == 32.78
    assert label_city("Dallas", gazetteer()).lat == 32.78


def test_label_ignores_state_and_country_names_but_keeps_large_namesake_cities():
    from ocm_regions.place.placer import label_city

    assert label_city("Virginia", gazetteer()) is None
    assert label_city("Saudi Arabia", gazetteer()) is None
    assert label_city("Singapore", gazetteer()).name == "Singapore"


def test_label_finds_an_alias_and_ignores_digits():
    from ocm_regions.place.placer import label_city

    assert label_city("Frankfurt 2, DE", gazetteer()).name == "Frankfurt am Main"
    assert label_city("EU-Frankfurt4", gazetteer()).name == "Frankfurt am Main"


def test_label_rejects_tiny_places_unless_they_lead_a_comma_address():
    from ocm_regions.place.placer import label_city

    assert label_city("Europe Tinytown central", gazetteer()) is None
    assert label_city("Tinytown, FR", gazetteer()).name == "Tinytown"


def test_region_id_words_name_a_city_even_when_run_together():
    from ocm_regions.place.placer import id_city

    assert id_city("na-ashburn", gazetteer()).name == "Ashburn"
    assert id_city("ap-johorbahru", gazetteer()).name == "Johor Bahru"
    assert id_city("us-east-1", gazetteer()) is None


def signal(name, lat, lon):
    return {"name": name, "lat": lat, "lon": lon}


@pytest.mark.parametrize(
    "label,region,feed,basis,cross_check",
    [
        (signal("A", 1, 1), None, signal("A", 1, 1), "label", "agrees"),
        (signal("A", 1, 1), signal("A", 1, 1), None, "label", "agrees"),
        (signal("A", 1, 1), None, None, "label", ""),
        (None, signal("A", 1, 1), None, "label", ""),
        (None, None, signal("A", 1, 1), "feed", ""),
        (signal("A", 1, 1), None, signal("B", 9, 9), "label", "conflicts"),
        (None, None, None, "none", ""),
    ],
)
def test_basis_says_what_gave_the_place_and_cross_check_whether_a_second_signal_agrees(
    label, region, feed, basis, cross_check
):
    from ocm_regions.place.placer import decide

    decision = decide(label, region, feed)
    assert (decision["basis"], decision["cross_check"]) == (basis, cross_check)


def test_a_step_names_the_basis_and_its_cross_check_together():
    from ocm_regions.place.placer import RULE_STEPS, step

    assert (
        step({"basis": "label", "cross_check": "agrees"}) == "label, agrees"
        and step({"basis": "feed", "cross_check": ""}) == "feed"
    )
    assert RULE_STEPS == (
        "label, agrees",
        "label",
        "label, conflicts",
        "document",
        "feed",
        "capital",
        "memory",
        "none",
    )


def test_a_conflict_keeps_the_vendor_label_and_a_none_has_no_place():
    from ocm_regions.place.placer import decide

    assert decide(signal("A", 1, 1), None, signal("B", 9, 9))["name"] == "A"
    assert decide(None, None, None)["name"] == ""


def test_holdout_is_stable_and_about_thirty_percent():
    from ocm_regions.place.placer import in_holdout

    keys = [("aws", f"r{i}") for i in range(1000)]
    assert in_holdout("aws", "us-east-1") == in_holdout("aws", "us-east-1")
    assert 240 < sum(in_holdout(*k) for k in keys) < 360


def test_placer_never_reads_curated_placements():
    from ocm_regions.place import placer

    source = inspect.getsource(placer.place_regions)
    assert "region_places" not in source and "mappings" not in source


def test_scoring_splits_development_and_holdout_and_reports_by_step():
    from ocm_regions.place.placer import score_placements

    rows = [
        {
            "provider": "a",
            "region_id": "x",
            "basis": "label",
            "cross_check": "agrees",
            "matches_curated": "yes",
            "split": "development",
            "curated_city": "C",
        },
        {
            "provider": "a",
            "region_id": "y",
            "basis": "label",
            "cross_check": "agrees",
            "matches_curated": "no",
            "split": "holdout",
            "curated_city": "C",
        },
        {
            "provider": "b",
            "region_id": "z",
            "basis": "none",
            "cross_check": "",
            "matches_curated": "",
            "split": "holdout",
            "curated_city": "C",
        },
    ]
    summary = score_placements(rows, [("a", "y"), ("q", "q")])
    assert (
        summary["holdout"]["placed"] == 1
        and summary["holdout"]["matched"] == 0
        and summary["holdout"]["abstained"] == 1
    )
    assert summary["by_step"]["label, agrees"] == {"regions": 2, "matched": 1}
    assert summary["overturned"]["total"] == 1


def test_gazetteer_keeps_an_alias_for_a_large_city_when_a_small_town_shares_the_name(tmp_path):
    import zipfile

    from ocm_regions.place.placer import label_city, load_gazetteer

    row = "{gid}\t{name}\t{name}\t{alt}\t{lat}\t{lon}\tP\tPPL\t{cc}\t\t{adm}\t\t\t\t{pop}\t\t\t\t\t"
    lines = [
        row.format(
            gid=1,
            name="Santiago de Queretaro",
            alt="Queretaro",
            lat=20.6,
            lon=-100.4,
            cc="MX",
            adm="22",
            pop=626000,
        ),
        row.format(gid=2, name="Queretaro", alt="", lat=27.0, lon=-109.0, cc="MX", adm="26", pop=600),
    ]
    (tmp_path / "geonames").mkdir()
    with zipfile.ZipFile(tmp_path / "geonames" / "cities500.zip", "w") as archive:
        archive.writestr("cities500.txt", "\n".join(lines))
    (tmp_path / "geonames" / "admin1CodesASCII.txt").write_text("", encoding="utf-8")
    (tmp_path / "geonames" / "countryInfo.txt").write_text("", encoding="utf-8")
    assert label_city("Queretaro", load_gazetteer(tmp_path)).lat == 20.6


def test_placer_macros_use_letter_names(tmp_path):
    from ocm_regions.place.placer import placer_macros

    summary = {
        "development": {"regions": 3, "placed": 2, "matched": 2, "abstained": 1, "accuracy": 1.0},
        "holdout": {"regions": 2, "placed": 2, "matched": 1, "abstained": 0, "accuracy": 0.5},
        "all": {"regions": 5, "placed": 4, "matched": 3, "abstained": 1, "accuracy": 0.75},
        "by_step": {"label, agrees": {"regions": 2, "matched": 2}},
        "overturned": {"total": 8, "flagged": 5, "accepted_matching": 2, "accepted_as_label": 1},
    }
    macros = placer_macros(summary)
    assert macros["placerHoldoutAccuracy"] == "50.0" and macros["placerAgreedRegions"] == 2
    assert all(name.isalpha() for name in macros)


def test_a_two_letter_code_that_is_both_a_state_and_a_country_tries_each_meaning():
    from ocm_regions.place.placer import Gazetteer, label_city

    gaz = Gazetteer(
        cities=[
            city("Ashburn", "US", "VA", 43000, 39.04, -77.49),
            city("Frankfurt", "DE", "05", 650000, 50.11, 8.68),
        ],
        countries={"va": "VA", "de": "DE"},
        states={"va": "VA", "de": "DE"},
    )
    assert label_city("Ashburn, VA", gaz).name == "Ashburn"
    assert label_city("Frankfurt, DE", gaz).name == "Frankfurt"


def test_only_a_conflict_a_feed_a_capital_or_nothing_counts_as_flagged_for_review():
    from ocm_regions.place.placer import score_placements

    rows = [
        {
            "provider": "a",
            "region_id": r,
            "basis": b,
            "cross_check": c,
            "matches_curated": m,
            "split": "development",
            "curated_city": "C",
        }
        for r, b, c, m in [
            ("1", "label", "", "no"),
            ("2", "label", "", "yes"),
            ("3", "label", "agrees", "yes"),
            ("4", "label", "conflicts", "no"),
            ("5", "feed", "", "yes"),
            ("6", "none", "", ""),
        ]
    ]
    overturned = score_placements(rows, [("a", str(i)) for i in range(1, 7)])["overturned"]
    assert overturned == {"total": 6, "flagged": 3, "accepted_matching": 2, "accepted_as_label": 1}


def test_placer_rows_carry_the_country_of_the_chosen_city():
    from ocm_regions.place.placer import decide

    label = {"name": "Tokyo", "lat": 35.0, "lon": 139.0, "country": "JP"}
    assert decide(label, None, None)["country"] == "JP"
    assert decide(None, None, None)["country"] == ""


def _real_gazetteer():
    from pathlib import Path

    from ocm_regions.place.placer import load_gazetteer

    return load_gazetteer(Path("data/raw"))


@pytest.mark.skipif(not Path("data/raw/geonames/cities500.zip").exists(), reason="run `make fetch` first")
class TestLabelsOnRealPlaces:
    gaz = None

    @classmethod
    def setup_class(cls):
        cls.gaz = _real_gazetteer()

    def _city(self, label):
        from ocm_regions.place.placer import label_city

        item = label_city(label, self.gaz)
        return item.name if item else None

    def test_a_label_that_is_only_a_small_town_names_that_town(self):
        assert self._city("Falkenstein") == "Falkenstein" and self._city("Gravelines") == "Gravelines"

    def test_a_two_letter_country_code_settles_the_namesake(self):
        from ocm_regions.place.placer import label_city

        item = label_city("Manchester, GB", self.gaz)
        assert item.name == "Manchester" and item.country == "GB"

    def test_saint_can_be_abbreviated_in_a_label(self):
        assert self._city("St. Ghislain, Belgium, Europe") == "Saint-Ghislain"

    def test_a_label_that_is_only_a_us_state_names_no_city(self):
        assert self._city("Washington") is None and self._city("Virginia") is None

    def test_a_state_hint_settles_a_city_that_shares_a_province_name(self):
        assert self._city("San Jose, CA") == "San Jose"

    def test_a_label_for_a_state_is_not_a_city_even_with_a_city_of_a_similar_name(self):
        assert self._city("Querétaro State") is None

    def test_washington_dc_is_still_a_city(self):
        assert self._city("Washington, DC") == "Washington"

    def test_a_label_naming_only_a_country_falls_back_to_its_capital(self):
        from ocm_regions.place.placer import capital_of_label

        assert capital_of_label("Middle East (Bahrain)", self.gaz).name == "Manama"
        assert capital_of_label("Asia Pacific (Thailand)", self.gaz).name == "Bangkok"
        assert capital_of_label("US East (Ohio)", self.gaz) is None


def test_a_region_with_no_reference_is_not_counted_as_abstained():
    from ocm_regions.place.placer import _split_score

    rows = [
        {"matches_curated": "yes", "curated_city": "X"},
        {"matches_curated": "", "curated_city": "Y"},
        {"matches_curated": "", "curated_city": ""},
    ]
    assert _split_score(rows)["abstained"] == 1 and _split_score(rows)["regions"] == 2


def test_a_capital_standing_in_for_a_country_label_is_recorded_as_a_capital_not_a_feed():
    from ocm_regions.place.placer import Gazetteer, place_regions
    from ocm_regions.regions.parsers import Region

    gaz = Gazetteer(
        [{"name": "Manama", "country": "BH", "admin1": "", "population": 150000, "lat": 26.2, "lon": 50.6}],
        countries={"bahrain": "BH"},
        capitals={"BH": "Manama"},
    )
    row = place_regions(
        [Region("aws", "me-south-1", "Middle East (Bahrain)", "Middle East (Bahrain)")], gaz, {}
    )[0]
    assert (row["basis"], row["cross_check"], row["city"], row["from_capital"]) == (
        "capital",
        "",
        "Manama",
        "yes",
    )


def test_region_rows_publish_the_basis_and_cross_check_not_a_tier():
    from ocm_regions.tables.build import GRADED_FIELDS, REGION_FIELDS

    for fields in (REGION_FIELDS, GRADED_FIELDS):
        assert "place_basis" in fields and "cross_check" in fields and "place_tier" not in fields
