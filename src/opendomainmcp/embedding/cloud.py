"""Optional cloud embedders (OpenAI / Voyage).

These keep the SDK imports lazy so the package works without the optional
dependencies installed. A missing package or API key fails loudly with a clear
message instead of silently degrading.
"""

from __future__ import annotations

import os

from .base import Embedder

_OPENAI_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}
_VOYAGE_DIMS = {
    "voyage-3": 1024,
    "voyage-3-lite": 512,
    "voyage-code-3": 1024,
}


class OpenAIEmbedder(Embedder):
    def __init__(self, model_name: str = "text-embedding-3-small"):
        self.name = f"openai:{model_name}"
        self._model_name = model_name
        self._dim = _OPENAI_DIMS.get(model_name, 1536)
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required for the openai embedder backend")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError("Install the 'openai' package to use the openai backend") from exc
        self._client = OpenAI()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._client.embeddings.create(model=self._model_name, input=texts)
        return [item.embedding for item in resp.data]

    @property
    def dim(self) -> int:
        return self._dim


class VoyageEmbedder(Embedder):
    def __init__(self, model_name: str = "voyage-3-lite"):
        self.name = f"voyage:{model_name}"
        self._model_name = model_name
        self._dim = _VOYAGE_DIMS.get(model_name, 1024)
        if not os.environ.get("VOYAGE_API_KEY"):
            raise RuntimeError("VOYAGE_API_KEY is required for the voyage embedder backend")
        try:
            import voyageai
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError("Install the 'voyageai' package to use the voyage backend") from exc
        self._client = voyageai.Client()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        result = self._client.embed(texts, model=self._model_name)
        return result.embeddings

    @property
    def dim(self) -> int:
        return self._dim
