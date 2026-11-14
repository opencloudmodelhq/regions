# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Where the project keeps its inputs: sources, settings, scoring files and code registers."""

from pathlib import Path
from typing import Literal

from ocm_regions.fetch.sources import StrPath

Rule = Literal["nearest_airport", "major_hub", "nearby_hub"]


def datasets(root: StrPath) -> Path:
    """Path of the list of outside datasets with their pinned hashes.

    Args:
        root: Repository root.

    Returns:
        The path under the sources folder.
    """
    return Path(root) / "sources" / "datasets.csv"


def documents(root: StrPath) -> Path:
    """Path of the list of source documents with their pinned hashes.

    Args:
        root: Repository root.

    Returns:
        The path under the sources folder.
    """
    return Path(root) / "sources" / "documents.csv"


def model_settings(root: StrPath) -> Path:
    """Path of the settings shared by every model: temperature, seed and token limit.

    Args:
        root: Repository root.

    Returns:
        The path under the config folder.
    """
    return Path(root) / "config" / "llm_config.json"


def models(root: StrPath) -> Path:
    """Path of the named model configurations.

    Args:
        root: Repository root.

    Returns:
        The path under the config folder.
    """
    return Path(root) / "config" / "llm_models.json"


def operators(root: StrPath) -> Path:
    """Path of the table that names each vendor's operating company.

    Args:
        root: Repository root.

    Returns:
        The path under the config folder.
    """
    return Path(root) / "config" / "operators.csv"


def overturned(root: StrPath) -> Path:
    """Path of the placements that evidence overturned, used for scoring.

    Args:
        root: Repository root.

    Returns:
        The path under the evaluation folder.
    """
    return Path(root) / "evaluation" / "overturned_placements.csv"


def reference(root: StrPath) -> Path:
    """Path of the reference placements used for scoring.

    Args:
        root: Repository root.

    Returns:
        The path under the evaluation folder.
    """
    return Path(root) / "evaluation" / "reference_placements.csv"


def register(root: StrPath, rule: Rule) -> Path:
    """Path of the append-only code register for one naming rule.

    Args:
        root: Repository root.
        rule: Table rule the register belongs to.

    Returns:
        The path under the registers folder.
    """
    return Path(root) / "registers" / f"codes_{rule}.csv"
