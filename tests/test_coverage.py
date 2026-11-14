# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from ocm_regions.tables.coverage import scheme_coverage


def row(**kw):
    return {
        "iata_nearest_airport": "",
        "iata_major_hub": "",
        "iata_nearby_hub": "",
        "iata_metro": "",
        "unlocode": "",
        "clli": "",
        "grade": "vendor-stated",
    } | kw


def test_coverage_counts_regions_with_a_code():
    result = scheme_coverage(
        [row(iata_nearest_airport="IAD", iata_metro="WAS"), row(iata_nearest_airport="SIN")]
    )
    assert result["iata_nearest_airport"]["regions"] == 2
    assert result["iata_metro"]["regions"] == 1


def test_coverage_reports_distinct_codes():
    result = scheme_coverage([row(iata_nearest_airport="IAD"), row(iata_nearest_airport="IAD")])
    assert result["iata_nearest_airport"]["distinct"] == 1


def test_coverage_counts_metro_or_airport_fallback():
    result = scheme_coverage(
        [row(iata_nearest_airport="IAD", iata_metro="WAS"), row(iata_nearest_airport="SIN")]
    )
    assert result["metro_or_airport"]["regions"] == 2
