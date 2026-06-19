"""Runtime wiring shared by the CLI, MCP server, and web API.

A single ``build_context`` keeps all entry points on the same pipeline/store so
there is exactly one source of truth for ingestion and retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings, get_settings
from .embedding import get_embedder
from .extract import get_extractor
from .graph.store import MariaGraphStore
from .ingest.pipeline import Pipeline
from .retrieval import get_reranker
from .store import ChromaStore


@dataclass
class Context:
    settings: Settings
    store: ChromaStore
    pipeline: Pipeline
    graph: MariaGraphStore


def build_context(settings: Settings | None = None, collection: str | None = None) -> Context:
    settings = settings or get_settings()
    embedder = get_embedder(settings)
    store = ChromaStore(
        embedder,
        data_dir=settings.data_dir / "chroma",
        collection_name=collection or settings.collection_name,
        max_retries=settings.max_retries,
        reranker=get_reranker(settings),
    )
    extractor = get_extractor(settings)
    graph = MariaGraphStore(
        host=settings.graph_db_host, port=settings.graph_db_port,
        user=settings.graph_db_user, password=settings.graph_db_password,
        database=settings.graph_db_name,
    )
    # Fail loud: required platform dependency. A clear error beats a late failure
    # deep inside ingestion.
    try:
        graph.ensure_schema()
    except Exception as exc:  # noqa: BLE001 - surface the real cause
        raise RuntimeError(
            f"Cannot connect to MariaDB graph store at "
            f"{settings.graph_db_host}:{settings.graph_db_port}: {exc}"
        ) from exc
    pipeline = Pipeline(store, extractor, settings, graph=graph)
    return Context(settings=settings, store=store, pipeline=pipeline, graph=graph)
