"""Runtime configuration.

Settings are read from environment variables (prefix ``ODM_``) and an optional
``.env`` file. A subset of settings is also persisted as JSON under the data
directory so the web UI can edit them at runtime (see ``Settings.load`` /
``Settings.save``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# API-key spec separators (see ``Settings.parsed_api_keys``).
_API_KEY_ENTRY_SEP = ","
_API_KEY_FIELD_SEP = ":"
_API_KEY_VIEW_SEP = "|"
_API_KEY_ALL_VIEWS = "*"
_API_KEY_FIELD_COUNT = 3

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
    "rerank_enabled",
    "answer_model",
    "review_mode",
    "retrieve_approved_only",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ODM_", env_file=".env", extra="ignore"
    )

    # Storage
    data_dir: Path = Path(".opendomain")
    collection_name: str = "domain_knowledge"

    # Security: when set, ingestion is confined to this directory tree. Paths
    # that resolve outside it (including via symlinks) are rejected. Unset means
    # no restriction (trusted local use); set it when exposing the web/MCP server.
    ingest_root: Optional[Path] = None

    # Security: reject uploads larger than this (megabytes) to bound memory use.
    max_upload_mb: int = 50

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

    # Optional cross-encoder re-ranking of candidates after fusion. Off by
    # default (the model is downloaded on first use); when on it produces a
    # unified relevance score for every result, including lexical-only hits.
    rerank_enabled: bool = False
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"

    # RAG answer synthesis (Anthropic)
    answer_model: str = "claude-sonnet-4-6"

    # Knowledge review workflow. When ``review_mode`` is on, newly extracted
    # knowledge is marked "pending" so it must be approved before it counts as
    # reviewed. When ``retrieve_approved_only`` is on, search/MCP views return
    # only approved knowledge. Both default off so existing behaviour is intact.
    review_mode: bool = False
    retrieve_approved_only: bool = False

    # Resilience for external API calls (Anthropic): per-request timeout in
    # seconds and the number of automatic retries on transient errors.
    request_timeout: float = 60.0
    max_retries: int = 2

    # Multi-tenancy: when on, every request must carry a tenant id (``X-Tenant``
    # header) and each tenant's data is isolated by namespacing the collection as
    # ``<tenant>::<collection>``. Off by default — single-tenant local use is
    # unchanged. Data isolation rides on the existing per-collection separation in
    # both the vector store (Chroma) and the graph store (keyed by collection).
    multi_tenant: bool = False

    # --- Knowledge graph store (MariaDB, required platform-wide) ---
    graph_db_host: str = "localhost"
    graph_db_port: int = 3306
    graph_db_user: str = "opendomain"
    graph_db_password: str = ""
    graph_db_name: str = "opendomain_graph"

    # --- Access control (RBAC / API keys) ---
    # Env-only (never UI-editable): credentials must come from the environment.
    # Auth defaults OFF so trusted local use and existing tests are unaffected;
    # set ``ODM_AUTH_ENABLED=true`` and ``ODM_API_KEYS=...`` to enforce it.
    auth_enabled: bool = False
    # Compact spec parsed by ``parsed_api_keys``. See that method for the format.
    api_keys: str = ""

    def parsed_api_keys(self) -> dict[str, dict]:
        """Parse :attr:`api_keys` into ``{key: {"role", "views"}}``.

        Format: comma-separated entries, each ``key:role:views`` where ``views``
        is ``*`` (all views) or a ``|``-separated list of view names, e.g.::

            secret1:admin:*,secret2:dev:developer|architecture

        Fail-Loud: raises ``ValueError`` on a malformed entry (wrong field count,
        empty field, or an unknown view name).
        """
        return _parse_api_keys(self.api_keys)

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


def _parse_api_keys(spec: str) -> dict[str, dict]:
    """Parse the ``key:role:views`` API-key spec. See ``Settings.parsed_api_keys``."""
    from .views import VIEW_NAMES  # local import avoids a config<-views cycle

    result: dict[str, dict] = {}
    for raw in spec.split(_API_KEY_ENTRY_SEP):
        entry = raw.strip()
        if not entry:
            continue  # tolerate trailing/empty separators
        fields = entry.split(_API_KEY_FIELD_SEP)
        if len(fields) != _API_KEY_FIELD_COUNT:
            raise ValueError(
                f"Malformed API key entry (expected key:role:views): {entry!r}"
            )
        key, role, views_spec = (f.strip() for f in fields)
        if not key or not role or not views_spec:
            raise ValueError(f"API key entry has an empty field: {entry!r}")

        if views_spec == _API_KEY_ALL_VIEWS:
            views: tuple[str, ...] = (_API_KEY_ALL_VIEWS,)
        else:
            views = tuple(
                v.strip() for v in views_spec.split(_API_KEY_VIEW_SEP) if v.strip()
            )
            if not views:
                raise ValueError(f"API key entry has no views: {entry!r}")
            unknown = [v for v in views if v not in VIEW_NAMES]
            if unknown:
                raise ValueError(
                    f"API key entry names unknown view(s) {unknown}: {entry!r}"
                )

        result[key] = {"role": role, "views": views}
    return result


def get_settings() -> Settings:
    """Load settings from env/.env, then apply persisted runtime overrides."""
    return Settings().apply_overrides()
