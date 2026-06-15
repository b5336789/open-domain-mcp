"""Runtime configuration.

Settings are read from environment variables (prefix ``ODM_``) and an optional
``.env`` file. A subset of settings is also persisted as JSON under the data
directory so the web UI can edit them at runtime (see ``Settings.load`` /
``Settings.save``).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Settings that the web UI is allowed to view/update at runtime. Credentials and
# the data directory are deliberately excluded.
EDITABLE_FIELDS = (
    "embedder_backend",
    "embedder_model",
    "extract_knowledge",
    "extraction_model",
    "chunk_size",
    "chunk_overlap",
    "code_max_chunk_chars",
    "extract_concurrency",
    "search_mode",
    "answer_model",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ODM_", env_file=".env", extra="ignore"
    )

    # Storage
    data_dir: Path = Path(".opendomain")
    collection_name: str = "domain_knowledge"

    # Embedding
    embedder_backend: str = "local"  # local | openai | voyage
    embedder_model: str = "BAAI/bge-small-en-v1.5"

    # Domain-knowledge extraction (Anthropic)
    extract_knowledge: bool = True
    extraction_model: str = "claude-sonnet-4-6"
    extract_concurrency: int = 8  # parallel extraction calls per file

    # Text chunking
    chunk_size: int = 1200
    chunk_overlap: int = 150

    # Code (AST) chunking fallback
    code_max_chunk_chars: int = 2000

    # Retrieval: "vector" (dense only) or "hybrid" (dense + BM25 via RRF)
    search_mode: str = "hybrid"

    # RAG answer synthesis (Anthropic)
    answer_model: str = "claude-sonnet-4-6"

    @property
    def overrides_path(self) -> Path:
        return self.data_dir / "settings.json"

    def apply_overrides(self) -> "Settings":
        """Return a copy with persisted runtime overrides applied on top."""
        path = self.overrides_path
        if not path.exists():
            return self
        data = json.loads(path.read_text(encoding="utf-8"))
        clean = {k: v for k, v in data.items() if k in EDITABLE_FIELDS}
        return self.model_copy(update=clean)

    def save_overrides(self, values: dict) -> "Settings":
        """Persist editable overrides and return the updated settings."""
        invalid = set(values) - set(EDITABLE_FIELDS)
        if invalid:
            raise ValueError(f"Not editable: {sorted(invalid)}")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        current = {}
        if self.overrides_path.exists():
            current = json.loads(self.overrides_path.read_text(encoding="utf-8"))
        current.update(values)
        self.overrides_path.write_text(
            json.dumps(current, indent=2), encoding="utf-8"
        )
        return self.model_copy(update=values)

    def editable_dict(self) -> dict:
        return {k: getattr(self, k) for k in EDITABLE_FIELDS}


def get_settings() -> Settings:
    """Load settings from env/.env, then apply persisted runtime overrides."""
    return Settings().apply_overrides()
