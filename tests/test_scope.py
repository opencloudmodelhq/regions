# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0


import pytest


class Fake:
    def __init__(self, partition, region_id="r"):
        self.partition, self.region_id, self.provider = partition, region_id, "x"


def test_restricted_regions_are_dropped_and_open_ones_kept():
    from ocm_regions.common.data import open_regions

    kept = open_regions(
        [
            Fake("public", "a"),
            Fake("aws-cn", "b"),
            Fake("aws-eusc", "c"),
            Fake("aws-us-gov", "d"),
            Fake("tencent-finance", "e"),
        ]
    )
    assert [r.region_id for r in kept] == ["a", "b", "c"]


def test_an_unknown_partition_stops_the_build():
    from ocm_regions.common.data import open_regions

    with pytest.raises(ValueError):
        open_regions([Fake("new-sovereign-partition")])


def test_a_region_dropped_from_scope_keeps_its_code_reserved():
    from ocm_regions.tables.ordinals import assign_codes

    _, registry = assign_codes([("aws", "a", "iad"), ("aws", "b", "iad")], {})
    assigned, registry = assign_codes([("aws", "b", "iad"), ("aws", "c", "iad")], registry)
    assert registry[("aws", "a")] == "iad"
    assert assigned == {("aws", "b"): "iad2", ("aws", "c"): "iad3"}


def test_every_excluded_partition_gives_a_reason_and_the_vendor_mark_it_rests_on():
    from ocm_regions.common.data import EXCLUDED_PARTITIONS

    for partition, scope in EXCLUDED_PARTITIONS.items():
        assert scope.reason and scope.marked_by, partition


def test_classified_aws_partitions_are_excluded_not_skipped():
    from ocm_regions.common.data import open_regions

    assert open_regions([Fake(p) for p in ("aws-iso", "aws-iso-b", "aws-iso-e", "aws-iso-f")]) == []
