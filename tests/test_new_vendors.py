# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import json


def table(*rows):
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table>{body}</table>"


def test_tencent_reads_one_row_per_region_with_its_first_zone_and_partition():
    from ocm_regions.regions.parsers import parse_tencent

    html = table(
        ("Region", "Region ID", "Number of AZs", "AZ", "AZ ID"),
        (
            "Asia Pacific and the Middle East (Saudi Arabia)Coverage: Middle East",
            "me-saudi-arabia",
            "2",
            "Riyadh Zone 1",
            "1",
        ),
        ("﻿", "﻿", "﻿", "Riyadh Zone 2", "2"),
        (
            "Beijing FinanceAvailable only to finance institutions",
            "ap-beijing-fsi",
            "2",
            "Beijing Finance Zone 1*",
            "3",
        ),
        ("Shanghai Self-driving Cloud", "ap-shanghai-adc", "5", "Shanghai Self-driving Cloud Zone 1*", "4"),
    )
    parsed = parse_tencent(html)
    assert [(r.region_id, r.partition) for r in parsed] == [
        ("me-saudi-arabia", "public"),
        ("ap-beijing-fsi", "tencent-finance"),
        ("ap-shanghai-adc", "tencent-autonomous"),
    ]
    assert parsed[0].name == "Asia Pacific and the Middle East (Saudi Arabia)"
    assert parsed[0].stated_location == "Asia Pacific and the Middle East (Saudi Arabia); Riyadh Zone 1"


def test_ovh_pairs_cities_with_codes_once_and_strips_markers():
    from ocm_regions.regions.parsers import parse_ovhcloud

    section = table(
        ("", "FRANCE", "UNITED KINGDOM"),
        ("", "Strasbourg", "3-AZ Paris", "London", ""),
        ("", "SBG *", "PAR", "ERI", ""),
    )
    parsed = parse_ovhcloud(section + section)
    assert [(r.region_id, r.stated_location, r.vendor_code) for r in parsed] == [
        ("SBG", "Strasbourg", "SBG"),
        ("PAR", "Paris", "PAR"),
        ("ERI", "London", "ERI"),
    ]


def test_ibm_reads_city_and_identifier_once():
    from ocm_regions.regions.parsers import parse_ibm

    section = table(("Region", "Zone"), ("Chennai - Airtel (in-che)", "in-che-1 in-che-2"))
    parsed = parse_ibm(section + section)
    assert [(r.region_id, r.stated_location) for r in parsed] == [("in-che", "Chennai - Airtel")]


def test_akamai_reads_the_public_regions_api():
    from ocm_regions.regions.parsers import parse_akamai

    text = json.dumps({"data": [{"id": "us-east", "label": "Newark, NJ", "country": "us"}]})
    parsed = parse_akamai(text)
    assert [(r.provider, r.region_id, r.stated_location) for r in parsed] == [
        ("akamai", "us-east", "Newark, NJ")
    ]


def test_huawei_merges_two_documents_and_records_each_source():
    from ocm_regions.regions.parsers import parse_huawei

    first = table(
        ("Region Name", "Region", "Endpoint"),
        ("AP-Bangkok", "ap-southeast-2", "cts.example"),
        ("CN South-Guangzhou-InvitationOnly", "cn-south-4", "cts.example"),
    )
    second = table(
        ("Region Name", "Region Code"), ("AP-Bangkok", "ap-southeast-2"), ("AP-Manila", "ap-southeast-5")
    )
    parsed = {r.region_id: r for r in parse_huawei(first, second)}
    assert sorted(parsed) == ["ap-southeast-2", "ap-southeast-5", "cn-south-4"]
    assert parsed["cn-south-4"].partition == "huawei-invitation"
    assert (parsed["ap-southeast-2"].source, parsed["ap-southeast-5"].source) == ("", "huawei-scp")


def test_sweep_leaves_out_restricted_partitions():
    from ocm_regions.analysis.capsweep import open_rows

    rows = [
        {"partition": "public"},
        {"partition": "aws-cn"},
        {"partition": "tencent-finance"},
        {"partition": "alibaba-gov"},
        {"partition": "huawei-invitation"},
    ]
    assert [r["partition"] for r in open_rows(rows)] == ["public", "aws-cn"]


def test_cap_figures_scale_to_their_data():
    from ocm_regions.analysis.capfigures import measures_figure

    row = {"cap": 0.0, "recall100": 0.5, "fp100": 40, "shared": 400, "max_km": 500.0}
    summary = {"rho": 100, "selection": {"cap": 100, "low": 84.0, "high": 121.0}, "sweep": [row]}
    text = measures_figure(summary)
    assert "ymax=500" in text or "ymax=450" in text
    assert "ymin=350" in text or "ymin=400" in text


def test_ovh_ignores_a_product_row_that_looks_like_a_code():
    from ocm_regions.regions.parsers import parse_ovhcloud

    html = table(
        ("", "Strasbourg", ""), ("", "SBG", ""), ("GPU", "✓", "✓"), ("Memory", "✓", "✓"), ("GPU", "", "")
    )
    assert [r.region_id for r in parse_ovhcloud(html)] == ["SBG"]


def test_ibm_accepts_two_letter_city_identifiers():
    from ocm_regions.regions.parsers import parse_ibm

    html = table(("Region", "Zone"), ("Frankfurt (eu-de)", "eu-de-1"), ("London (eu-gb)", "eu-gb-1"))
    assert [r.region_id for r in parse_ibm(html)] == ["eu-de", "eu-gb"]
