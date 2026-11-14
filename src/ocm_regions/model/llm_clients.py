# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Clients for the models that place regions, with replies cached so a run can resume."""

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal, Required, TypedDict, cast

from ocm_regions.fetch.sources import StrPath

DEFAULT_BASE_URL: Final = "http://localhost:12434/engines/llama.cpp/v1"
RETRY_WAITS: Final = (5.0, 30.0, 120.0)
SERVER_FAILURES: Final = (500, 502, 503, 504)


class ModelConfig(TypedDict, total=False):
    """Settings for one model run: the chat, embedding and rerank models and how to call them."""

    chat_model: Required[str]
    chat_model_id: Required[str]
    temperature: Required[float]
    seed: Required[int]
    max_tokens: Required[int]
    chat_extra: dict[str, object]
    rerank_model: str
    rerank_model_id: str
    embed_model: str
    embed_model_id: str
    embed_document_template: str
    embed_query_template: str
    index_name: str
    corpus: str
    workers: int


class _Message(TypedDict, total=False):
    """The message in a chat reply."""

    content: str | None


class _Choice(TypedDict, total=False):
    """One choice in a chat reply."""

    finish_reason: str
    message: Required[_Message]


class _ChatReply(TypedDict):
    """A chat completion reply."""

    choices: list[_Choice]


class _RerankResult(TypedDict):
    """One document's score in a rerank reply."""

    index: int
    relevance_score: float


class _RerankReply(TypedDict):
    """A rerank reply."""

    results: list[_RerankResult]


class _Embedding(TypedDict):
    """One vector in an embedding reply."""

    embedding: list[float]


class _EmbedReply(TypedDict):
    """An embedding reply."""

    data: list[_Embedding]


class _Saved(TypedDict, total=False):
    """A cached chat reply with its cost."""

    text: Required[str]
    cost: float


class Client:
    """OpenAI-compatible local endpoint."""

    def __init__(
        self, config: ModelConfig, cache_dir: StrPath, base_url: str | None = None, fresh: bool = False
    ) -> None:
        """Set the model settings, the reply cache folder, the endpoint and whether to ignore the cache.

        Args:
            config: Model settings.
            cache_dir: Folder that holds cached replies.
            base_url: Endpoint of the model server; taken from the environment when None.
            fresh: Ignore cached replies.
        """
        super().__init__()
        self.config: ModelConfig = config
        self.cache: Path = Path(cache_dir)
        self.fresh: bool = fresh
        self.base: str = base_url or os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)
        self.cost: float = 0.0
        self._lock: threading.Lock = threading.Lock()
        self.cache.mkdir(parents=True, exist_ok=True)

    def _post_patiently(self, path: str, payload: Mapping[str, object]) -> object:
        """Send a request, asking again after a wait when the server itself fails.

        Args:
            path: API path under the endpoint.
            payload: JSON body to send.

        Returns:
            The decoded JSON reply.

        Raises:
            urllib.error.HTTPError: If the server rejects the request, or still fails after the last wait.
        """
        for wait in RETRY_WAITS:
            try:
                return self._post(path, payload)
            except urllib.error.HTTPError as error:
                if error.code not in SERVER_FAILURES:
                    raise
            time.sleep(wait)
        return self._post(path, payload)

    def _post(self, path: str, payload: Mapping[str, object]) -> object:
        """Send a JSON request to the endpoint and return the decoded reply.

        Args:
            path: API path under the endpoint.
            payload: JSON body to send.

        Returns:
            The decoded JSON reply.
        """
        base = self.base.removesuffix("/v1") if path == "/rerank" else self.base
        request = urllib.request.Request(
            base + path, json.dumps(payload).encode(), {"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=600) as response:
            return cast(object, json.load(response))

    def _complete(self, prompt: str) -> tuple[str, float]:
        """Ask the chat model once; raises when the reply was cut off at the token limit.

        Args:
            prompt: Prompt text.

        Returns:
            The reply text and its cost in US dollars.

        Raises:
            RuntimeError: If the reply was cut off at the token limit.
        """
        payload: dict[str, object] = {
            "model": self.config["chat_model"],
            "temperature": self.config["temperature"],
            "seed": self.config["seed"],
            "max_tokens": self.config["max_tokens"],
            "messages": [{"role": "user", "content": prompt}],
            **self.config.get("chat_extra", {}),
        }
        reply = cast(_ChatReply, self._post_patiently("/chat/completions", payload))
        choice = reply["choices"][0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError(f"model hit the {self.config['max_tokens']} token limit")
        return choice["message"].get("content") or "", 0.0

    def _add_cost(self, cost: float) -> None:
        """Add to the running cost, safely across threads.

        Args:
            cost: Cost in US dollars.
        """
        with self._lock:
            self.cost += cost

    def chat(self, prompt: str) -> str:
        """Answer a prompt, from the cache when the same model and prompt were asked before.

        Args:
            prompt: Prompt text.

        Returns:
            The model's reply.
        """
        extra = (
            json.dumps(self.config.get("chat_extra"), sort_keys=True) if self.config.get("chat_extra") else ""
        )
        key = hashlib.sha256((self.config["chat_model_id"] + extra + prompt).encode()).hexdigest()
        path = self.cache / f"{key}.json"
        if path.exists() and not self.fresh:
            saved = cast(_Saved, json.loads(path.read_text()))
            self._add_cost(saved.get("cost", 0.0))
            return saved["text"]
        text, cost = self._complete(prompt)
        self._add_cost(cost)
        if not self.fresh:
            _ = path.write_text(json.dumps({"text": text, "cost": cost}))
        return text

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Scores in document order, from the server's rerank route, which sits beside the OpenAI one.

        Args:
            query: Query text.
            documents: Documents to score.

        Returns:
            The scores, in document order.
        """
        reply = cast(
            _RerankReply,
            self._post(
                "/rerank",
                {"model": _setting(self.config, "rerank_model"), "query": query, "documents": documents},
            ),
        )
        scores = [0.0] * len(documents)
        for item in reply["results"]:
            scores[item["index"]] = item["relevance_score"]
        return scores

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts with the embedding model.

        Args:
            texts: Texts to embed.

        Returns:
            The vectors, in text order.
        """
        reply = cast(
            _EmbedReply,
            self._post("/embeddings", {"model": _setting(self.config, "embed_model"), "input": texts}),
        )
        return [item["embedding"] for item in reply["data"]]


def _setting(config: ModelConfig, key: Literal["rerank_model", "embed_model"]) -> str:
    """A model name that a call needs; raises KeyError when the settings lack it.

    Args:
        config: Model settings.
        key: Setting to read.

    Returns:
        The model name.

    Raises:
        KeyError: If the settings lack the model name.
    """
    value = config.get(key)
    if value is None:
        raise KeyError(key)
    return value
