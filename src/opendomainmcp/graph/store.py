# src/opendomainmcp/graph/store.py
"""Graph persistence. ``MariaGraphStore`` is the production backend (MariaDB via
PyMySQL); ``NullGraphStore`` is a no-op used where the graph is not wired."""

from __future__ import annotations

from typing import Iterable, Optional, Protocol

from .models import Edge, Entity


class GraphStoreProtocol(Protocol):
    def ensure_schema(self) -> None: ...
    def upsert_entities(self, entities: list[Entity]) -> None: ...
    def upsert_edges(self, edges: list[Edge]) -> None: ...
    def delete_for_chunks(self, chunk_ids: Iterable[str]) -> None: ...
    def delete_collection(self, name: str) -> None: ...
    def get_entity(self, name: str) -> Optional[dict]: ...
    def neighbors(self, name: str, relation_type: Optional[str] = None,
                  depth: int = 1) -> dict: ...
    def list_entities(self, type: Optional[str] = None, q: Optional[str] = None,
                      limit: int = 50) -> list[dict]: ...


class NullGraphStore:
    """No-op store (graph disabled / direct Pipeline construction in tests)."""

    def ensure_schema(self) -> None: pass
    def upsert_entities(self, entities: list[Entity]) -> None: pass
    def upsert_edges(self, edges: list[Edge]) -> None: pass
    def delete_for_chunks(self, chunk_ids: Iterable[str]) -> None: pass
    def delete_collection(self, name: str) -> None: pass
    def get_entity(self, name: str) -> Optional[dict]:
        return None
    def neighbors(self, name: str, relation_type: Optional[str] = None,
                  depth: int = 1) -> dict:
        return {"entity": None, "neighbors": []}

    def list_entities(self, type=None, q=None, limit=50):
        return []


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS entities (
        collection      VARCHAR(255) NOT NULL,
        normalized_name VARCHAR(255) NOT NULL,
        display_name    VARCHAR(512) NOT NULL,
        type            VARCHAR(64)  NOT NULL,
        confidence      FLOAT        NOT NULL DEFAULT 1.0,
        PRIMARY KEY (collection, normalized_name)
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS entity_chunks (
        collection      VARCHAR(255) NOT NULL,
        normalized_name VARCHAR(255) NOT NULL,
        chunk_id        VARCHAR(128) NOT NULL,
        PRIMARY KEY (collection, normalized_name, chunk_id)
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS edges (
        collection    VARCHAR(255) NOT NULL,
        src           VARCHAR(255) NOT NULL,
        dst           VARCHAR(255) NOT NULL,
        relation_type VARCHAR(64)  NOT NULL,
        chunk_id      VARCHAR(128) NOT NULL,
        confidence    FLOAT        NOT NULL DEFAULT 1.0,
        PRIMARY KEY (collection, src, dst, relation_type, chunk_id)
    ) CHARACTER SET utf8mb4
    """,
)


class MariaGraphStore:
    """MariaDB-backed graph store. Connections are short-lived per operation to
    stay safe under FastAPI's threaded request handling."""

    def __init__(self, host: str, port: int, user: str, password: str,
                 database: str, collection: str = "domain_knowledge"):
        import pymysql

        self._pymysql = pymysql
        self._collection = collection
        self._conn_kwargs = dict(host=host, port=port, user=user,
                                 password=password, database=database,
                                 charset="utf8mb4", autocommit=True,
                                 cursorclass=pymysql.cursors.DictCursor)

    @property
    def collection(self) -> str:
        return self._collection

    def _connect(self):
        # Fail loud: a clear error if MariaDB is unreachable.
        return self._pymysql.connect(**self._conn_kwargs)

    def ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            for ddl in _SCHEMA:
                cur.execute(ddl)

    def upsert_entities(self, entities: list[Entity]) -> None:
        if not entities:
            return
        with self._connect() as conn, conn.cursor() as cur:
            for e in entities:
                cur.execute(
                    "INSERT INTO entities (collection, normalized_name, display_name, type, confidence) "
                    "VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
                    "display_name=VALUES(display_name), type=VALUES(type), "
                    "confidence=GREATEST(confidence, VALUES(confidence))",
                    (self._collection, e.normalized_name, e.display_name, e.type, e.confidence))
                cur.execute(
                    "INSERT IGNORE INTO entity_chunks (collection, normalized_name, chunk_id) "
                    "VALUES (%s, %s, %s)", (self._collection, e.normalized_name, e.chunk_id))

    def upsert_edges(self, edges: list[Edge]) -> None:
        if not edges:
            return
        with self._connect() as conn, conn.cursor() as cur:
            for e in edges:
                cur.execute(
                    "INSERT INTO edges (collection, src, dst, relation_type, chunk_id, confidence) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
                    "confidence=GREATEST(confidence, VALUES(confidence))",
                    (self._collection, e.src, e.dst, e.relation_type, e.chunk_id, e.confidence))

    def delete_for_chunks(self, chunk_ids: Iterable[str]) -> None:
        ids = list(chunk_ids)
        if not ids:
            return
        placeholders = ", ".join(["%s"] * len(ids))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM edges WHERE collection=%s AND chunk_id IN ({placeholders})",
                [self._collection] + ids)
            cur.execute(
                f"DELETE FROM entity_chunks WHERE collection=%s AND chunk_id IN ({placeholders})",
                [self._collection] + ids)
            # Drop entities no longer referenced by any chunk in this collection.
            cur.execute(
                "DELETE FROM entities WHERE collection=%s AND normalized_name NOT IN "
                "(SELECT normalized_name FROM entity_chunks WHERE collection=%s)",
                (self._collection, self._collection))

    def delete_collection(self, name: str) -> None:
        """Delete all graph data for the named collection (used by the API drop path)."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM edges WHERE collection=%s", (name,))
            cur.execute("DELETE FROM entity_chunks WHERE collection=%s", (name,))
            cur.execute("DELETE FROM entities WHERE collection=%s", (name,))

    def _get_entity_with_cur(self, cur, normalized_name: str) -> Optional[dict]:
        """Fetch an entity using an already-open cursor (no new connection)."""
        cur.execute("SELECT normalized_name, display_name, type, confidence "
                    "FROM entities WHERE collection=%s AND normalized_name=%s",
                    (self._collection, normalized_name))
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("SELECT chunk_id FROM entity_chunks "
                    "WHERE collection=%s AND normalized_name=%s",
                    (self._collection, normalized_name))
        chunk_ids = [r["chunk_id"] for r in cur.fetchall()]
        return {"name": row["display_name"], "normalized_name": row["normalized_name"],
                "type": row["type"], "confidence": row["confidence"],
                "aliases": [], "chunk_ids": chunk_ids}

    def get_entity(self, name: str) -> Optional[dict]:
        from .normalize import normalize_name
        norm = normalize_name(name)
        with self._connect() as conn, conn.cursor() as cur:
            return self._get_entity_with_cur(cur, norm)

    def neighbors(self, name: str, relation_type: Optional[str] = None,
                  depth: int = 1) -> dict:
        from .normalize import normalize_name
        depth = max(1, min(2, depth))  # clamp per Global Constraints
        norm_root = normalize_name(name)
        collected: list[dict] = []
        with self._connect() as conn, conn.cursor() as cur:
            root = self._get_entity_with_cur(cur, norm_root)
            if root is None:
                return {"entity": None, "neighbors": []}
            seen = {root["normalized_name"]}
            frontier = [root["normalized_name"]]
            for _ in range(depth):
                next_frontier = []
                for norm in frontier:
                    for direction, col, other in (("out", "src", "dst"), ("in", "dst", "src")):
                        sql = (f"SELECT {other} AS other, relation_type FROM edges "
                               f"WHERE collection=%s AND {col}=%s")
                        params = [self._collection, norm]
                        if relation_type:
                            sql += " AND relation_type=%s"
                            params.append(relation_type)
                        cur.execute(sql, params)
                        for r in cur.fetchall():
                            if r["other"] in seen:
                                continue
                            seen.add(r["other"])
                            next_frontier.append(r["other"])
                            ent = self._get_entity_with_cur(cur, r["other"])
                            if ent:
                                collected.append({"entity": ent,
                                                  "relation_type": r["relation_type"],
                                                  "direction": direction})
                frontier = next_frontier
        return {"entity": root, "neighbors": collected}

    def list_entities(self, type=None, q=None, limit=50):
        clauses, params = ["collection=%s"], [self._collection]
        if type:
            clauses.append("type=%s"); params.append(type)
        if q:
            clauses.append("normalized_name LIKE %s")
            params.append(f"%{q.lower().strip()}%")
        where = " WHERE " + " AND ".join(clauses)
        params.append(max(1, min(500, limit)))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT normalized_name, display_name, type FROM entities"
                        f"{where} ORDER BY normalized_name LIMIT %s", params)
            return [{"name": r["display_name"], "normalized_name": r["normalized_name"],
                     "type": r["type"]} for r in cur.fetchall()]
