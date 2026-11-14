# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"


@pytest.fixture
def work(tmp_path):
    inputs = tmp_path / "inputs"
    for folder in ("config", "evaluation", "registers"):
        shutil.copytree(ROOT / folder, inputs / folder)
    return inputs, tmp_path / "output"


def test_unknown_stage_is_refused():
    from ocm_regions.tables.build import run_until

    with pytest.raises(ValueError, match="stage"):
        run_until("nope", RAW, ROOT, ROOT / "output")
