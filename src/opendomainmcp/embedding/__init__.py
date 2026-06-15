"""Embedder factory. Selects a backend from settings."""

from __future__ import annotations

from ..config import Settings
from .base import Embedder


def get_embedder(settings: Settings) -> Embedder:
    backend = settings.embedder_backend.lower()
    if backend == "local":
        from .local import LocalEmbedder

        return LocalEmbedder(settings.embedder_model)
    if backend == "openai":
        from .cloud import OpenAIEmbedder

        return OpenAIEmbedder(settings.embedder_model)
    if backend == "voyage":
        from .cloud import VoyageEmbedder

        return VoyageEmbedder(settings.embedder_model)
    raise ValueError(f"Unknown embedder backend: {settings.embedder_backend!r}")


__all__ = ["Embedder", "get_embedder"]
