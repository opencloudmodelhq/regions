# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

from ocm_regions.tables.ordinals import assign_codes


def test_first_region_on_a_code_has_no_number():
    assigned, _ = assign_codes([("aws", "a", "iad")], {})
    assert assigned[("aws", "a")] == "iad"


def test_second_region_on_a_code_gets_two():
    assigned, _ = assign_codes([("aws", "a", "iad"), ("aws", "b", "iad")], {})
    assert assigned[("aws", "b")] == "iad2"


def test_other_vendor_does_not_share_numbering():
    assigned, _ = assign_codes([("aws", "a", "iad"), ("gcp", "x", "iad")], {})
    assert assigned[("gcp", "x")] == "iad"


def test_registry_keeps_existing_numbers_when_new_region_sorts_first():
    _, registry = assign_codes([("aws", "m", "iad")], {})
    assigned, _ = assign_codes([("aws", "a", "iad"), ("aws", "m", "iad")], registry)
    assert assigned[("aws", "m")] == "iad"
    assert assigned[("aws", "a")] == "iad2"


def test_region_keeps_its_code_when_its_base_changes_only_with_new_entry():
    _, registry = assign_codes([("aws", "a", "iad")], {})
    assigned, registry = assign_codes([("aws", "a", "pdx")], registry)
    assert assigned[("aws", "a")] == "pdx"
    assert registry[("aws", "a")] == "pdx"
