# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import json

from ocm_regions.tables.grades import GRADES, best_grade

FEED = "# comment\nIP Range,Country,Region,City,Postal Code\n10.0.0.0/24,us,US-OR,Boardman,\n10.0.1.0/24,US,US-OR,Hillsboro,\n2600::/32,US,US-TX,Dallas,\n"


def test_parse_geofeed_reads_ipv4_rows_and_skips_comments_headers_and_ipv6():
    from ocm_regions.place.feeds import parse_geofeed

    parsed = parse_geofeed(FEED)
    assert [(r.city, r.country, r.subdivision) for r in parsed] == [
        ("Boardman", "US", "US-OR"),
        ("Hillsboro", "US", "US-OR"),
    ]
    assert parsed[0].raw == "10.0.0.0/24,us,US-OR,Boardman,"


def test_dominant_city_is_weighted_by_addresses():
    from ocm_regions.place.feeds import dominant_city, parse_geofeed

    hit = dominant_city(["10.0.0.0/24", "10.0.0.0/25", "10.0.1.0/26"], parse_geofeed(FEED))
    assert hit.city == "Boardman"
    assert round(hit.share, 2) == 0.86


def test_dominant_city_is_none_when_the_feed_has_no_overlap():
    from ocm_regions.place.feeds import dominant_city, parse_geofeed

    assert dominant_city(["192.0.2.0/24"], parse_geofeed(FEED)) is None


def test_range_parsers_group_prefixes_by_region():
    from ocm_regions.place.feeds import aws_ranges, azure_ranges, gcp_ranges

    aws = json.dumps(
        {
            "prefixes": [
                {"ip_prefix": "10.0.0.0/24", "region": "us-west-2"},
                {"ip_prefix": "10.9.0.0/24", "region": "GLOBAL"},
            ]
        }
    )
    gcp = json.dumps(
        {
            "prefixes": [
                {"ipv4Prefix": "10.0.0.0/24", "scope": "asia-east1"},
                {"ipv6Prefix": "2600::/32", "scope": "asia-east1"},
                {"ipv4Prefix": "10.1.0.0/24", "scope": "global"},
            ]
        }
    )
    az = json.dumps(
        {
            "values": [
                {
                    "name": "AzureCloud.norwaye",
                    "properties": {"region": "norwaye", "addressPrefixes": ["10.0.0.0/24", "2600::/32"]},
                },
                {
                    "name": "Storage.norwaye",
                    "properties": {"region": "norwaye", "addressPrefixes": ["10.5.0.0/24"]},
                },
            ]
        }
    )
    assert aws_ranges(aws) == {"us-west-2": ["10.0.0.0/24"]}
    assert gcp_ranges(gcp) == {"asia-east1": ["10.0.0.0/24"]}
    assert azure_ranges(az, {"norwaye": "norwayeast"}) == {"norwayeast": ["10.0.0.0/24"]}


def test_akamai_resolvers_become_host_ranges():
    from ocm_regions.place.feeds import akamai_ranges

    text = json.dumps(
        {"data": [{"id": "us-east", "resolvers": {"ipv4": "10.0.0.1,10.0.0.2", "ipv6": "2600::1"}}]}
    )
    assert akamai_ranges(text) == {"us-east": ["10.0.0.1/32", "10.0.0.2/32"]}


def lookup(key):
    return {
        ("US", "OR", "boardman"): (45.84, -119.70),
        ("AE", "dubai"): (25.07, 55.17),
        ("US", "VA", "quincy"): (0.0, 0.0),
    }.get(key)


def place(city="Boardman", lat="45.84", lon="-119.70", name=""):
    return {"city": city, "lat": lat, "lon": lon, "geonames_name": name}


def test_feed_distance_treats_the_same_name_as_the_same_place():
    from ocm_regions.place.feeds import FeedHit, feed_km

    assert feed_km(FeedHit("QUERETARO", "", "MX", 1.0, ""), place("Querétaro", "20.6", "-100.4"), lookup) == 0
    assert (
        feed_km(
            FeedHit("QUINCY-MOSES LAKE", "US-WA", "US", 1.0, ""), place("Quincy", "47.2", "-119.8"), lookup
        )
        == 0
    )


def test_feed_distance_uses_the_us_state_and_reports_unresolved_names():
    from ocm_regions.place.feeds import FeedHit, feed_km

    assert (
        feed_km(FeedHit("Boardman", "US-OR", "US", 1.0, ""), place("Other", "45.84", "-119.70"), lookup) < 1
    )
    assert feed_km(FeedHit("Nowhere", "", "ZZ", 1.0, ""), place(), lookup) is None


def test_feed_record_supports_only_a_nearby_place_tied_to_the_region():
    from ocm_regions.place.feeds import FeedHit, feed_record

    hit = FeedHit("Boardman", "US-OR", "US", 0.97, "10.0.0.0/24,US,US-OR,Boardman,")
    near = feed_record("aws", "us-west-2", hit, 0, "https://example.test/feed.csv", linked=True)
    far = feed_record("aws", "us-west-2", hit, 200, "https://example.test/feed.csv", linked=True)
    unknown = feed_record("aws", "us-west-2", hit, None, "https://example.test/feed.csv", linked=True)
    loose = feed_record("vultr", "ewr", hit, 0, "https://example.test/feed.csv", linked=False)
    assert (near["source_type"], near["stance"], near["quote"]) == ("vendor_feed", "supports", hit.quote)
    assert far["stance"] == unknown["stance"] == loose["stance"] == "partial"


def test_feed_grade_sits_below_documents_and_above_press():
    assert GRADES.index("audit-document") < GRADES.index("vendor-feed") < GRADES.index("reported")
    record = {"source_type": "vendor_feed", "stance": "supports"}
    assert best_grade([record, {"source_type": "news", "stance": "supports"}]) == "vendor-feed"
    assert (
        best_grade([record, {"source_type": "vendor_region_list", "stance": "supports"}]) == "vendor-stated"
    )


def test_candidates_are_ranges_absent_from_the_vendor_list():
    from ocm_regions.place.feeds import FeedHit, candidate_rows

    hits = {
        ("aws", "us-east-1"): FeedHit("Ashburn", "US-VA", "US", 1.0, "a"),
        ("aws", "new-1"): FeedHit("Dallas", "US-TX", "US", 1.0, "b"),
    }
    result = candidate_rows(hits, {("aws", "us-east-1")})
    assert [(r["region_id"], r["feed_city"]) for r in result] == [("new-1", "Dallas")]


def test_source_resolves_a_dated_download_from_its_page():
    from ocm_regions.fetch.sources import resolve_url

    page = 'x <a href="https://download.microsoft.com/a/ServiceTags_Public_20261102.json"> y'
    assert resolve_url(
        r"https://download\.microsoft\.com/[^\"]*ServiceTags_Public[^\"]*\.json", page
    ).endswith("20261102.json")


def test_feed_calls_are_registered():
    from ocm_regions.tables.calls import SEVERITY, calls_for_region

    assert SEVERITY["feed-conflict"] == "medium" and SEVERITY["candidate-region"] == "medium"
    fields = {
        "grade": "vendor-stated",
        "conflict": False,
        "iata_nearest_airport": "A",
        "iata_major_hub": "A",
        "unlocode": "X",
        "airport_km": 1,
        "vendor_code": "",
        "shared_with": "",
        "feed_conflict": "Dubai, 115 km",
    }
    assert "feed-conflict" in {c.kind for c in calls_for_region(fields)}
