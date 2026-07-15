# src/opendomainmcp/graph/store.py
"""Graph persistence. ``MariaGraphStore`` is the production backend (MariaDB via
PyMySQL); ``NullGraphStore`` is a no-op used where the graph is not wired."""

from __future__ import annotations

import json
from typing import Iterable, Optional, Protocol

from .models import Edge, Entity


def _parse_evidence_json(raw: str) -> list[dict]:
    """Parse an evidence_json column value; returns [] on absence/corruption."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


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
    def upsert_workflow(self, workflow_name: str, chunk_id: str, chunk_index: int,
                        steps: list, prerequisites: list[str]) -> None: ...
    def get_workflow(self, name: str) -> Optional[dict]: ...
    def list_workflows(self, q: Optional[str] = None, limit: int = 50) -> list[dict]: ...
    def export_graph(self) -> dict: ...


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

    def upsert_workflow(self, workflow_name, chunk_id, chunk_index, steps, prerequisites):
        pass

    def get_workflow(self, name):
        return None

    def list_workflows(self, q=None, limit=50):
        return []

    def export_graph(self) -> dict:
        return {"entities": [], "edges": [], "entity_chunks": []}

    def upsert_functions(self, functions: list[dict]) -> None:
        pass

    def get_function(self, qualified_name: str) -> Optional[dict]:
        return None

    def delete_codegraph(self) -> None:
        pass


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS entities (
        collection      VARCHAR(255) NOT NULL,
        normalized_name VARCHAR(255) NOT NULL,
        display_name    VARCHAR(512) NOT NULL,
        type            VARCHAR(64)  NOT NULL,
        confidence      FLOAT        NOT NULL DEFAULT 1.0,
        evidence_json   TEXT         NULL,
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
        evidence_json TEXT         NULL,
        -- Prefix lengths on the varchar key columns keep the composite key
        -- under InnoDB's 3072-byte index limit for utf8mb4 (4 bytes/char):
        -- (150+150+150+64+128)*4 = 2568 bytes. Mirrors the prefix-index pattern
        -- used by workflow_prereqs below.
        PRIMARY KEY (collection(150), src(150), dst(150), relation_type, chunk_id)
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_steps (
        collection    VARCHAR(255) NOT NULL,
        workflow_key  VARCHAR(255) NOT NULL,
        workflow_name VARCHAR(512) NOT NULL,
        chunk_id      VARCHAR(128) NOT NULL,
        chunk_index   INT          NOT NULL DEFAULT 0,
        step_order    INT          NOT NULL,
        text          TEXT         NOT NULL,
        precondition  TEXT,
        PRIMARY KEY (collection, workflow_key, chunk_id, step_order)
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_prereqs (
        collection   VARCHAR(255) NOT NULL,
        workflow_key VARCHAR(255) NOT NULL,
        chunk_id     VARCHAR(128) NOT NULL,
        prerequisite VARCHAR(512) NOT NULL,
        -- Prefix lengths keep the composite key under InnoDB's 3072-byte limit
        -- for utf8mb4: (150+150+128+150)*4 = 2312 bytes. The prior single-column
        -- prefix on prerequisite alone was insufficient given the other columns.
        PRIMARY KEY (collection(150), workflow_key(150), chunk_id, prerequisite(150))
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS code_functions (
        collection     VARCHAR(255) NOT NULL,
        qualified_name VARCHAR(512) NOT NULL,
        file           VARCHAR(1024) NOT NULL,
        start_line     INT NOT NULL,
        end_line       INT NOT NULL,
        language       VARCHAR(32) NOT NULL,
        signature      VARCHAR(1024) NOT NULL DEFAULT '',
        kind           VARCHAR(32) NOT NULL DEFAULT 'function',
        PRIMARY KEY (collection(150), qualified_name(300))
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
            # Additive migrations — safe to run repeatedly on existing installs.
            cur.execute(
                "ALTER TABLE entities ADD COLUMN IF NOT EXISTS evidence_json TEXT NULL")
            cur.execute(
                "ALTER TABLE edges ADD COLUMN IF NOT EXISTS evidence_json TEXT NULL")

    def upsert_entities(self, entities: list[Entity]) -> None:
        if not entities:
            return
        with self._connect() as conn, conn.cursor() as cur:
            for e in entities:
                cur.execute(
                    "INSERT INTO entities "
                    "(collection, normalized_name, display_name, type, confidence, evidence_json) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
                    "display_name=VALUES(display_name), type=VALUES(type), "
                    "confidence=GREATEST(confidence, VALUES(confidence)), "
                    "evidence_json=IF(VALUES(evidence_json)='', evidence_json, VALUES(evidence_json))",
                    (self._collection, e.normalized_name, e.display_name, e.type, e.confidence,
                     e.evidence or ""))
                cur.execute(
                    "INSERT IGNORE INTO entity_chunks (collection, normalized_name, chunk_id) "
                    "VALUES (%s, %s, %s)", (self._collection, e.normalized_name, e.chunk_id))

    def upsert_edges(self, edges: list[Edge]) -> None:
        if not edges:
            return
        with self._connect() as conn, conn.cursor() as cur:
            for e in edges:
                cur.execute(
                    "INSERT INTO edges "
                    "(collection, src, dst, relation_type, chunk_id, confidence, evidence_json) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
                    "confidence=GREATEST(confidence, VALUES(confidence)), "
                    "evidence_json=IF(VALUES(evidence_json)='', evidence_json, VALUES(evidence_json))",
                    (self._collection, e.src, e.dst, e.relation_type, e.chunk_id, e.confidence,
                     e.evidence or ""))

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
            cur.execute(
                f"DELETE FROM workflow_steps WHERE collection=%s AND chunk_id IN ({placeholders})",
                [self._collection] + ids)
            cur.execute(
                f"DELETE FROM workflow_prereqs WHERE collection=%s AND chunk_id IN ({placeholders})",
                [self._collection] + ids)

    def delete_collection(self, name: str) -> None:
        """Delete all graph data for the named collection (used by the API drop path)."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM edges WHERE collection=%s", (name,))
            cur.execute("DELETE FROM entity_chunks WHERE collection=%s", (name,))
            cur.execute("DELETE FROM entities WHERE collection=%s", (name,))
            cur.execute("DELETE FROM workflow_steps WHERE collection=%s", (name,))
            cur.execute("DELETE FROM workflow_prereqs WHERE collection=%s", (name,))

    def _get_entity_with_cur(self, cur, normalized_name: str) -> Optional[dict]:
        """Fetch an entity using an already-open cursor (no new connection)."""
        cur.execute("SELECT normalized_name, display_name, type, confidence, evidence_json "
                    "FROM entities WHERE collection=%s AND normalized_name=%s",
                    (self._collection, normalized_name))
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("SELECT chunk_id FROM entity_chunks "
                    "WHERE collection=%s AND normalized_name=%s",
                    (self._collection, normalized_name))
        chunk_ids = [r["chunk_id"] for r in cur.fetchall()]
        evidence = _parse_evidence_json(row.get("evidence_json") or "")
        return {"name": row["display_name"], "normalized_name": row["normalized_name"],
                "type": row["type"], "confidence": row["confidence"],
                "aliases": [], "chunk_ids": chunk_ids, "evidence": evidence}

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
                        sql = (f"SELECT {other} AS other, relation_type, evidence_json "
                               f"FROM edges WHERE collection=%s AND {col}=%s")
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
                                edge_evidence = _parse_evidence_json(
                                    r.get("evidence_json") or "")
                                collected.append({"entity": ent,
                                                  "relation_type": r["relation_type"],
                                                  "direction": direction,
                                                  "edge_evidence": edge_evidence})
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
            cur.execute("SELECT normalized_name, display_name, type, evidence_json FROM entities"
                        f"{where} ORDER BY normalized_name LIMIT %s", params)
            return [{"name": r["display_name"], "normalized_name": r["normalized_name"],
                     "type": r["type"],
                     "evidence": _parse_evidence_json(r.get("evidence_json") or "")}
                    for r in cur.fetchall()]

    def upsert_workflow(self, workflow_name, chunk_id, chunk_index, steps, prerequisites):
        if not workflow_name or (not steps and not prerequisites):
            return
        from .normalize import normalize_name
        key = normalize_name(workflow_name)
        with self._connect() as conn, conn.cursor() as cur:
            for s in steps:
                cur.execute(
                    "INSERT INTO workflow_steps (collection, workflow_key, workflow_name, "
                    "chunk_id, chunk_index, step_order, text, precondition) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
                    "workflow_name=VALUES(workflow_name), chunk_index=VALUES(chunk_index), "
                    "text=VALUES(text), precondition=VALUES(precondition)",
                    (self._collection, key, workflow_name, chunk_id, chunk_index,
                     s.step_order, s.text, s.precondition))
            for p in prerequisites:
                cur.execute(
                    "INSERT IGNORE INTO workflow_prereqs (collection, workflow_key, "
                    "chunk_id, prerequisite) VALUES (%s, %s, %s, %s)",
                    (self._collection, key, chunk_id, p))

    def get_workflow(self, name):
        from .normalize import normalize_name
        key = normalize_name(name)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT workflow_name, step_order, text, precondition, chunk_id "
                "FROM workflow_steps WHERE collection=%s AND workflow_key=%s "
                "ORDER BY chunk_index, step_order", (self._collection, key))
            rows = cur.fetchall()
            cur.execute(
                "SELECT DISTINCT prerequisite FROM workflow_prereqs "
                "WHERE collection=%s AND workflow_key=%s", (self._collection, key))
            prereqs = [r["prerequisite"] for r in cur.fetchall()]
        if not rows and not prereqs:
            return None
        display = rows[0]["workflow_name"] if rows else name
        steps = [{"order": r["step_order"], "text": r["text"],
                  "precondition": r["precondition"] or "", "chunk_id": r["chunk_id"]}
                 for r in rows]
        return {"workflow_name": display, "prerequisites": prereqs, "steps": steps}

    def list_workflows(self, q=None, limit=50):
        clauses, params = ["collection=%s"], [self._collection]
        if q:
            clauses.append("workflow_key LIKE %s")
            params.append(f"%{q.lower().strip()}%")
        params.append(max(1, min(500, limit)))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT workflow_key, MAX(workflow_name) AS workflow_name FROM workflow_steps "
                f"WHERE {' AND '.join(clauses)} GROUP BY workflow_key "
                "ORDER BY workflow_name LIMIT %s", params)
            return [{"name": r["workflow_name"]} for r in cur.fetchall()]

    def export_graph(self) -> dict:
        """Bulk read of the collection's graph for offline quality metrics."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT normalized_name, display_name, type FROM entities "
                        "WHERE collection=%s", (self._collection,))
            entities = list(cur.fetchall())
            cur.execute("SELECT src, dst, relation_type, chunk_id, confidence "
                        "FROM edges WHERE collection=%s", (self._collection,))
            edges = list(cur.fetchall())
            cur.execute("SELECT normalized_name, chunk_id FROM entity_chunks "
                        "WHERE collection=%s", (self._collection,))
            entity_chunks = list(cur.fetchall())
        return {"entities": entities, "edges": edges, "entity_chunks": entity_chunks}

    def upsert_functions(self, functions: list[dict]) -> None:
        if not functions:
            return
        with self._connect() as conn, conn.cursor() as cur:
            for fn in functions:
                cur.execute(
                    "INSERT INTO code_functions "
                    "(collection, qualified_name, file, start_line, end_line, language, signature, kind) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
                    "file=VALUES(file), start_line=VALUES(start_line), end_line=VALUES(end_line), "
                    "signature=VALUES(signature), kind=VALUES(kind)",
                    (self._collection, fn["qualified_name"], fn["file"],
                     fn["start_line"], fn["end_line"], fn["language"],
                     fn.get("signature", ""), fn.get("kind", "function")))

    def get_function(self, qualified_name: str) -> Optional[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT qualified_name, file, start_line, end_line, language, signature, kind "
                "FROM code_functions WHERE collection=%s AND qualified_name=%s",
                (self._collection, qualified_name))
            row = cur.fetchone()
            return dict(row) if row else None

    def delete_codegraph(self) -> None:
        """Remove codegraph rows: synthetic cg:* links and all function provenance."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM edges WHERE collection=%s AND chunk_id LIKE 'cg:%%'",
                    (self._collection,))
                cur.execute(
                    "DELETE FROM entity_chunks WHERE collection=%s AND chunk_id LIKE 'cg:%%'",
                    (self._collection,))
                cur.execute(
                    "DELETE FROM code_functions WHERE collection=%s",
                    (self._collection,))
            conn.commit()
