"""Runtime wiring shared by the CLI, MCP server, and web API.

A single ``build_context`` keeps all entry points on the same pipeline/store so
there is exactly one source of truth for ingestion and retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings, get_settings
from .embedding import get_embedder
from .extract import get_extractor
from .graph.store import GraphStoreProtocol, MariaGraphStore, NullGraphStore
from .ingest.pipeline import Pipeline
from .retrieval import get_reranker
from .store import ChromaStore


@dataclass
class Context:
    settings: Settings
    store: ChromaStore
    pipeline: Pipeline
    graph: GraphStoreProtocol


def build_context(settings: Settings | None = None, collection: str | None = None) -> Context:
    settings = settings or get_settings()
    collection_name = collection or settings.collection_name
    graph_backend = settings.graph_store_backend.strip().lower()
    if graph_backend not in {"mariadb", "null", "none", "disabled"}:
        raise ValueError(
            "Unsupported graph_store_backend "
            f"{settings.graph_store_backend!r}; expected 'mariadb' or 'null'"
        )
    embedder = get_embedder(settings)
    store = ChromaStore(
        embedder,
        data_dir=settings.data_dir / "chroma",
        collection_name=collection_name,
        max_retries=settings.max_retries,
        reranker=get_reranker(settings),
    )
    extractor = get_extractor(settings)
    if graph_backend == "mariadb":
        graph = MariaGraphStore(
            host=settings.graph_db_host, port=settings.graph_db_port,
            user=settings.graph_db_user, password=settings.graph_db_password,
            database=settings.graph_db_name,
            collection=collection_name,
        )
        # Fail loud: required production dependency. A clear error beats a late
        # failure deep inside ingestion.
        try:
            graph.ensure_schema()
        except Exception as exc:  # noqa: BLE001 - surface the real cause
            raise RuntimeError(
                f"Cannot connect to MariaDB graph store at "
                f"{settings.graph_db_host}:{settings.graph_db_port}: {exc}"
            ) from exc
    else:
        graph = NullGraphStore()
    pipeline = Pipeline(store, extractor, settings, graph=graph)
    return Context(settings=settings, store=store, pipeline=pipeline, graph=graph)
