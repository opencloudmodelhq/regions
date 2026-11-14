# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import re

from tests.test_method_figures import LABELS, REPORT

from ocm_regions.analysis.capfigures import _moved_table
from ocm_regions.analysis.method_figures import coverage_figure
from ocm_regions.publish.paper import _grade_table


def _first_cells(tex):
    body = tex.split(r"\midrule")[1].split(r"\bottomrule")[0]
    return [[c.strip() for c in line.rstrip(" \\").split("&")] for line in body.strip().splitlines()]


def test_grade_table_lists_providers_alphabetically():
    rows = [{"provider": p, "grade": "vendor-stated"} for p in ("vultr", "aws", "oracle", "azure")]
    assert [r[0] for r in _first_cells(_grade_table(rows))] == ["AWS", "Azure", "Oracle", "Vultr"]


def test_moved_table_sorts_by_vendor_then_region_then_place():
    def row(provider, region, city):
        return {
            "provider": provider,
            "region_id": region,
            "city": city,
            "hub": "AAA",
            "hub_km": 150,
            "nearest": "BBB",
            "nearest_km": 10,
        }

    summary = {
        "off_hub": [
            row("ovhcloud", "RBX", "Roubaix"),
            row("azure", "westus", "San Jose"),
            row("azure", "centralus", "Des Moines"),
            row("azure", "centralus", "Ames"),
        ]
    }
    assert [r[:3] for r in _first_cells(_moved_table(summary))] == [
        ["Azure", "centralus", "Ames"],
        ["Azure", "centralus", "Des Moines"],
        ["Azure", "westus", "San Jose"],
        ["OVHcloud", "RBX", "Roubaix"],
    ]


def test_coverage_figure_panels_are_wide_enough_to_read():
    tikz = coverage_figure(REPORT, LABELS, ("rules", "rag", "memory"))
    widths = [float(w) for w in re.findall(r"width=([\d.]+)cm", tikz)]
    assert widths and min(widths) >= 5.5


def test_code_table_sorts_by_vendor_then_region_code():
    from tests.test_nearby_hub import sample_rows

    from ocm_regions.publish.paper import code_table

    rows = sample_rows()
    shuffled = [
        dict(rows[0], provider="vultr", region_id="ewr"),
        dict(rows[0], provider="aws", region_id="us-west-2"),
        dict(rows[0], provider="aws", region_id="ap-east-1"),
    ]
    body = code_table(shuffled).split(r"\endlastfoot")[1]
    keys = [tuple(c.strip() for c in line.split("&")[:2]) for line in body.splitlines() if "&" in line]
    assert keys == [("AWS", "ap-east-1"), ("AWS", "us-west-2"), ("Vultr", "ewr")]


def test_grade_table_heads_its_first_column_vendor():
    assert (
        _grade_table([{"provider": "gcp", "grade": "vendor-stated"}]).splitlines()[2].startswith("Vendor & ")
    )


def test_tables_name_vendors_as_the_text_does():
    from ocm_regions.analysis.capfigures import _stability_table
    from ocm_regions.analysis.method_figures import vendor_table
    from ocm_regions.common.vendors import vendor_name

    assert (vendor_name("gcp"), vendor_name("ovhcloud"), vendor_name("unknown")) == (
        "Google Cloud",
        "OVHcloud",
        "unknown",
    )
    stability = _stability_table(
        {
            "stability": [
                {"test": "All regions", "regions": 3, "cap": 84.0},
                {"test": "Without gcp", "regions": 2, "cap": 84.0},
            ]
        }
    )
    assert "Without Google Cloud & 2" in stability and "All regions & 3" in stability
    assert "\nAWS & " in vendor_table(REPORT, LABELS, ("rules", "rag"))
