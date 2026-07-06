"""Candidate pairing for consensus workflows.

Finds pairs of RuleUnits that may conflict or duplicate each other via three
orthogonal signals:

- **embedding**: cosine similarity of claim text embeddings >= threshold.
  All claims are embedded in a single batch; pure-Python dot/norm is used.
  O(N²) float math — fine for thousands of units from one corpus pass.
- **chain**: a chunk unit and a chain unit share chunk_ids, or two chunk units
  both appear in the same chain unit's member ids.
- **entity**: two units whose chunk_ids overlap an entity's chunk set in the
  graph store (optional; skipped when graph is None or raises).

Priority when multiple signals fire for the same pair: entity > chain > embedding.
The embedding similarity value is preserved in the output even when a higher-
priority signal name is recorded.
"""

import logging
import math
from dataclasses import dataclass

from opendomainmcp.consensus.units import RuleUnit

logger = logging.getLogger(__name__)

# Signal priority: higher number = stronger signal.
_PRIORITY = {"embedding": 0, "chain": 1, "entity": 2}


@dataclass
class CandidatePair:
    a: RuleUnit
    b: RuleUnit
    signal: str        # "embedding" | "chain" | "entity"
    similarity: float = 0.0


def _cosine(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity. Returns 0.0 for zero vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_candidates(
    units: list[RuleUnit],
    embedder,
    graph=None,
    threshold: float = 0.80,
    entity_scan_limit: int = 1000,
) -> list[CandidatePair]:
    """Return deduplicated, deterministically ordered candidate pairs.

    Args:
        units: RuleUnits from a corpus pass (thousands at most).
        embedder: Embedder protocol — ``embed(texts) -> list[vec]``.
        graph: Optional graph store. None or any exception silently skips the
            entity signal (NullGraphStore returns empties — also safe).
        threshold: Minimum cosine similarity for the embedding signal.
        entity_scan_limit: Cap on entities scanned; a warning is logged when
            the scan reaches this limit.

    Returns:
        Sorted list of CandidatePair (by unordered key pair), one entry per
        unique pair regardless of how many signals fired.
    """
    if not units:
        return []

    key_to_unit = {u.key: u for u in units}

    # candidates: sorted(key_a, key_b) -> [signal, similarity]
    # Updated in place to prefer higher-priority signals.
    candidates: dict[tuple[str, str], list] = {}

    def _add(ua: RuleUnit, ub: RuleUnit, signal: str, sim: float = 0.0) -> None:
        if ua.key == ub.key:
            return
        pair_key = tuple(sorted([ua.key, ub.key]))
        existing = candidates.get(pair_key)
        if existing is None:
            candidates[pair_key] = [signal, sim]
            return
        ex_sig, ex_sim = existing
        new_prio = _PRIORITY[signal]
        ex_prio = _PRIORITY[ex_sig]
        if new_prio > ex_prio:
            # Higher-priority signal wins; keep the best similarity seen.
            candidates[pair_key] = [signal, max(sim, ex_sim)]
        elif new_prio == ex_prio:
            # Same signal: keep higher similarity.
            if sim > ex_sim:
                existing[1] = sim
        else:
            # Existing signal has higher priority; preserve its similarity but
            # record the embedding value if that was better.
            if sim > ex_sim:
                existing[1] = sim

    # --- Embedding signal ---
    vecs = embedder.embed([u.claim for u in units])
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            sim = _cosine(vecs[i], vecs[j])
            if sim >= threshold:
                _add(units[i], units[j], "embedding", sim)

    # --- Chain signal ---
    # Index: chunk_id -> list of chain units that include it as a member.
    chunk_to_chains: dict[str, list[RuleUnit]] = {}
    for u in units:
        if u.origin == "chain":
            for cid in u.chunk_ids:
                chunk_to_chains.setdefault(cid, []).append(u)

    # Pair each non-chain unit with any chain unit sharing a chunk_id.
    for u in units:
        if u.origin == "chain":
            continue
        for cid in u.chunk_ids:
            for chain_u in chunk_to_chains.get(cid, []):
                _add(u, chain_u, "chain")

    # Pair two non-chain units that both appear in the same chain unit's members.
    for chain_u in units:
        if chain_u.origin != "chain":
            continue
        member_set = set(chain_u.chunk_ids)
        members_in_corpus = [
            u for u in units
            if u.origin != "chain" and any(c in member_set for c in u.chunk_ids)
        ]
        for i in range(len(members_in_corpus)):
            for j in range(i + 1, len(members_in_corpus)):
                _add(members_in_corpus[i], members_in_corpus[j], "chain")

    # --- Entity signal ---
    if graph is not None:
        try:
            entities = graph.list_entities(limit=entity_scan_limit)
            if len(entities) >= entity_scan_limit:
                logger.warning(
                    "Entity scan hit cap (%d); some candidate pairs may be missed.",
                    entity_scan_limit,
                )

            # Index: chunk_id -> units with that chunk_id.
            chunk_to_units: dict[str, list[RuleUnit]] = {}
            for u in units:
                for cid in u.chunk_ids:
                    chunk_to_units.setdefault(cid, []).append(u)

            for ent_row in entities:
                entity = graph.get_entity(ent_row["name"])
                if not entity:
                    continue
                entity_chunk_ids: list[str] = entity.get("chunk_ids", [])

                # Collect distinct units whose chunk_ids overlap this entity.
                seen_keys: set[str] = set()
                matching: list[RuleUnit] = []
                for cid in entity_chunk_ids:
                    for u in chunk_to_units.get(cid, []):
                        if u.key not in seen_keys:
                            seen_keys.add(u.key)
                            matching.append(u)

                for i in range(len(matching)):
                    for j in range(i + 1, len(matching)):
                        _add(matching[i], matching[j], "entity")

        except Exception:
            # NullGraphStore returns empties or raises — skip entity signal.
            pass

    # Build deterministic output sorted by unordered key pair.
    result: list[CandidatePair] = []
    for pair_key in sorted(candidates.keys()):
        signal, sim = candidates[pair_key]
        ua = key_to_unit[pair_key[0]]
        ub = key_to_unit[pair_key[1]]
        result.append(CandidatePair(a=ua, b=ub, signal=signal, similarity=sim))

    return result
