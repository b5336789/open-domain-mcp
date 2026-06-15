"""Local embedder backed by fastembed (ONNX, CPU, no torch).

The model is downloaded from HuggingFace on first use. If that download fails
(e.g. no outbound network), ``embed`` raises loudly rather than returning empty
vectors (Fail Loud).
"""

from __future__ import annotations

from .base import Embedder


class LocalEmbedder(Embedder):
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        self.name = f"local:{model_name}"
        self._model_name = model_name
        self._model = None
        self._dim: int | None = None

    def _ensure_model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self._model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._ensure_model()
        vectors = [vec.tolist() for vec in self._model.embed(texts)]
        if self._dim is None and vectors:
            self._dim = len(vectors[0])
        return vectors

    @property
    def dim(self) -> int:
        if self._dim is None:
            # Probe with a tiny input to discover the dimensionality.
            self.embed(["_"])
        assert self._dim is not None
        return self._dim
