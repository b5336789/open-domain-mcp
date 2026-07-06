"""Turn a chunk's extracted KnowledgeUnit into graph nodes and edges.

Entities declared in ``knowledge.entities`` carry an explicit type; any
relation endpoint not declared as an entity is added as a ``Concept`` so the
edge always connects two real nodes.
"""

from __future__ import annotations

import json

from ..models import KnowledgeUnit
from .models import Edge, Entity
from .normalize import normalize_name


def build_graph(knowledge: KnowledgeUnit, chunk_id: str) -> tuple[list[Entity], list[Edge]]:
    entities: dict[str, Entity] = {}

    # Pre-filter to verified evidence entries only.
    verified = [e for e in knowledge.evidence if e.get("verified")]

    def _evidence_for(names: list[str]) -> str:
        """JSON of verified entries whose claim mentions any of the given names."""
        claim_lower = {n.lower() for n in names if n}
        matched = [
            e for e in verified
            if any(n in e.get("claim", "").lower() for n in claim_lower)
        ]
        return json.dumps(matched) if matched else ""

    def _add(name: str, type_: str) -> str:
        norm = normalize_name(name)
        if not norm:
            return ""
        if norm not in entities:
            entities[norm] = Entity(normalized_name=norm, display_name=name.strip(),
                                    type=type_, chunk_id=chunk_id,
                                    confidence=knowledge.confidence or 1.0)
        return norm

    for ent in knowledge.entities:
        _add(ent.get("name", ""), ent.get("type", "Concept"))

    edges: list[Edge] = []
    for rel in knowledge.typed_relations:
        src_name = rel.get("src", "")
        dst_name = rel.get("dst", "")
        src = _add(src_name, "Concept")
        dst = _add(dst_name, "Concept")
        if src and dst:
            edges.append(Edge(src=src, dst=dst, relation_type=rel.get("type", "related_to"),
                              chunk_id=chunk_id, confidence=knowledge.confidence or 1.0,
                              evidence=_evidence_for([src_name, dst_name])))

    # Thread evidence onto entities (after all entities are registered).
    for entity in entities.values():
        entity.evidence = _evidence_for([entity.display_name])

    return list(entities.values()), edges
