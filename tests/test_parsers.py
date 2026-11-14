# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from ocm_regions.regions.parsers import (
    parse_alibaba,
    parse_aws,
    parse_azure,
    parse_digitalocean,
    parse_gcp,
    parse_hetzner,
    parse_oracle,
    parse_vultr,
)


def table(*rows):
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table>{body}</table>"


def test_parse_aws_reads_code_and_name():
    html = table(
        ("Code", "Name", "AZs", "Geography", "Opt-in status"),
        ("us-east-1", "US East (N. Virginia)", "6", "USA", "No"),
    )
    assert parse_aws(html)[0].region_id == "us-east-1"
    assert parse_aws(html)[0].stated_location == "US East (N. Virginia)"


def test_parse_azure_reads_location_and_deduplicates_geography_tables():
    header = ("Region", "AZ", "Pair", "Physical location", "Geography", "Programmatic name")
    row = ("East US", "3", "West US", "Virginia", "United States", "eastus")
    html = table(header, row) + table(header, row)
    regions = parse_azure(html)
    assert [r.region_id for r in regions] == ["eastus"]
    assert regions[0].stated_location == "Virginia"


def test_parse_gcp_rolls_zones_into_regions():
    html = table(
        ("Zone", "Location"),
        ("us-east4-a", "Ashburn, Virginia, North America"),
        ("us-east4-b", "Ashburn, Virginia, North America"),
    )
    regions = parse_gcp(html)
    assert [r.region_id for r in regions] == ["us-east4"]
    assert regions[0].stated_location == "Ashburn, Virginia, North America"


def test_parse_oracle_keeps_vendor_key():
    html = table(
        ("Region Name", "Region Identifier", "Region Location", "Region Key", "Realm Key", "ADs"),
        ("Morocco West (Casablanca)", "af-casablanca-1", "Casablanca, Morocco", "LEJ", "OC1", "1"),
    )
    region = parse_oracle(html)[0]
    assert (region.region_id, region.vendor_code) == ("af-casablanca-1", "LEJ")


def test_parse_alibaba_marks_finance_partition_and_skips_zone_rows():
    public = table(
        ("Region name", "Region ID", "Number of zones", "Zone name", "Zone ID"),
        ("Singapore", "ap-southeast-1", "4", "Zone A", "ap-southeast-1a"),
        ("Zone B", "ap-southeast-1b"),
    )
    finance = table(
        ("Region name", "Region ID", "Region", "Number of zones", "Zone name", "Zone ID"),
        (
            "China East 1 Finance",
            "cn-hangzhou-finance",
            "China (Hangzhou)",
            "7",
            "Zone B",
            "cn-hangzhou-finance-b",
        ),
    )
    regions = parse_alibaba(public + finance)
    assert [(r.region_id, r.partition) for r in regions] == [
        ("ap-southeast-1", "public"),
        ("cn-hangzhou-finance", "alibaba-finance"),
    ]


def test_parse_vultr_reads_json():
    regions = parse_vultr('{"regions":[{"id":"sgp","city":"Singapore","country":"SG"}]}')
    assert (regions[0].region_id, regions[0].vendor_code) == ("sgp", "SGP")


def test_parse_digitalocean_lowercases_slug():
    html = table(("Datacenter", "Region", "Slug"), ("NYC1", "New York City, United States", "nyc1"))
    assert parse_digitalocean(html)[0].region_id == "nyc1"


def test_parse_hetzner_reads_location_cells():
    html = table(("eu-central", "us-east"), ("DE Falkenstein fsn1", "US Ashburn, VA ash"))
    assert {r.region_id: r.stated_location for r in parse_hetzner(html)} == {
        "fsn1": "Falkenstein",
        "ash": "Ashburn, VA",
    }
