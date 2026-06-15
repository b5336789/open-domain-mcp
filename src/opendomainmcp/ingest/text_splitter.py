"""Recursive character text splitter.

A compact in-house implementation (Simplicity First) that splits on the
coarsest separator that fits, recursing into oversized pieces, then merges
small pieces into chunks of ~``chunk_size`` with ``chunk_overlap`` carryover.
"""

from __future__ import annotations

_DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


class RecursiveTextSplitter:
    def __init__(self, chunk_size: int = 1200, chunk_overlap: int = 150,
                 separators: list[str] | None = None):
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or _DEFAULT_SEPARATORS

    def split(self, text: str) -> list[str]:
        text = text.strip()
        if not text:
            return []
        return self._split(text, self.separators)

    def _split(self, text: str, separators: list[str]) -> list[str]:
        separator = separators[-1]
        remaining: list[str] = []
        for i, sep in enumerate(separators):
            if sep == "":
                separator = ""
                break
            if sep in text:
                separator = sep
                remaining = separators[i + 1:]
                break

        pieces = list(text) if separator == "" else text.split(separator)
        chunks: list[str] = []
        buffer: list[str] = []
        for piece in pieces:
            if len(piece) < self.chunk_size:
                buffer.append(piece)
            else:
                if buffer:
                    chunks.extend(self._merge(buffer, separator))
                    buffer = []
                if remaining:
                    chunks.extend(self._split(piece, remaining))
                else:
                    chunks.append(piece)
        if buffer:
            chunks.extend(self._merge(buffer, separator))
        return chunks

    def _merge(self, pieces: list[str], separator: str) -> list[str]:
        sep_len = len(separator)
        out: list[str] = []
        current: list[str] = []
        total = 0
        for piece in pieces:
            addition = len(piece) + (sep_len if current else 0)
            if current and total + addition > self.chunk_size:
                out.append(separator.join(current))
                # Drop from the front until under the overlap budget.
                while current and total > self.chunk_overlap:
                    removed = current.pop(0)
                    total -= len(removed) + (sep_len if current else 0)
            current.append(piece)
            total += len(piece) + (sep_len if len(current) > 1 else 0)
        if current:
            out.append(separator.join(current))
        return [c for c in out if c.strip()]
