"""Plain dataclasses for graph nodes and edges (no business logic)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Entity:
    normalized_name: str
    display_name: str
    type: str
    chunk_id: str
    confidence: float = 1.0


@dataclass
class Edge:
    src: str  # normalized_name of source entity
    dst: str  # normalized_name of destination entity
    relation_type: str
    chunk_id: str
    confidence: float = 1.0


@dataclass
class WorkflowStep:
    step_order: int
    text: str
    precondition: str = ""
