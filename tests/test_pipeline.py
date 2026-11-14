# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import json

from ocm_regions.fetch.sources import SOURCES, drift, sha256_file
from ocm_regions.regions.parsers import parse_aws_partitions


def test_partitions_keep_classified_ones_labelled_and_leave_commercial_to_the_region_page():
    text = json.dumps(
        {
            "partitions": [
                {"partition": "aws", "regions": {"us-east-1": {"description": "x"}}},
                {"partition": "aws-iso", "regions": {"us-iso-east-1": {"description": "x"}}},
                {"partition": "aws-cn", "regions": {"cn-north-1": {"description": "China (Beijing)"}}},
            ]
        }
    )
    assert [(r.region_id, r.partition) for r in parse_aws_partitions(text)] == [
        ("us-iso-east-1", "aws-iso"),
        ("cn-north-1", "aws-cn"),
    ]


def test_every_source_declares_a_license_note():
    assert all(s.license_note for s in SOURCES)


def test_sha256_file(tmp_path):
    path = tmp_path / "x"
    path.write_bytes(b"abc")
    assert sha256_file(path).startswith("ba7816bf")


def test_drift_reports_changed_hash_only():
    old = {"a": "1", "b": "2"}
    new = {"a": "1", "b": "3", "c": "4"}
    assert drift(old, new) == {"changed": ["b"], "added": ["c"], "removed": []}


def test_evidence_ids_tell_two_quotes_apart():
    from ocm_regions.tables.build import evidence_id

    one = {
        "provider": "a",
        "region_id": "r",
        "claim": "city",
        "value": "X",
        "url": "u",
        "source_type": "vendor",
        "quote": "first",
    }
    assert evidence_id(one) != evidence_id({**one, "quote": "second"})
