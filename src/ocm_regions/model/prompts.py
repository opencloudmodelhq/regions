# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Frozen prompts and the parsing of a model's reply."""

import hashlib
import json
import re
from pathlib import Path
from typing import Final, Literal, TypeAlias, cast

PromptName: TypeAlias = Literal["label", "memory", "rag", "document"]
Answer: TypeAlias = dict[str, object]

PROMPTS: Final[dict[PromptName, str]] = {
    "label": "place_label_v1.txt",
    "memory": "place_memory_v1.txt",
    "rag": "place_rag_v1.txt",
    "document": "document_type_v1.txt",
}

ROOT: Final = Path(__file__).resolve().parents[3]


def fill(name: PromptName, **values: str) -> str:
    """A frozen prompt with its placeholders filled in.

    Args:
        name: Name of the frozen prompt.
        values: Placeholder values keyed by name.

    Returns:
        The filled prompt.
    """
    text = prompt_text(name)
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def parse_answer(text: str) -> Answer:
    """The JSON object in a model reply, ignoring any thinking block; empty when there is none.

    Args:
        text: The model's reply.

    Returns:
        The parsed object.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        return {}
    try:
        answer: object = json.loads(match[0])
    except json.JSONDecodeError:
        return {}
    return cast(Answer, answer) if isinstance(answer, dict) else {}


def prompt_hash(name: PromptName) -> str:
    """SHA-256 of a frozen prompt, recorded with every result.

    Args:
        name: Name of the frozen prompt.

    Returns:
        The hex digest.
    """
    return hashlib.sha256(prompt_text(name).encode()).hexdigest()


def prompt_text(name: PromptName) -> str:
    """The text of a frozen prompt.

    Args:
        name: Name of the frozen prompt.

    Returns:
        The prompt text.
    """
    return (ROOT / "prompts" / PROMPTS[name]).read_text(encoding="utf-8")
