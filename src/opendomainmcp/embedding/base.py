"""Embedder interface. Backends produce dense vectors for a list of texts."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Embedder(ABC):
    """Maps texts to fixed-dimension dense vectors."""

    name: str = "embedder"

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns one vector per input text."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Dimensionality of the produced vectors."""
