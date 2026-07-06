"""Rule unit collection from chunks and chains for consensus workflows."""

from dataclasses import dataclass

from opendomainmcp.models import parse_evidence_field


# Layer mapping: language/kind → semantic layer
LAYER_BY_LANGUAGE = {
    "plsql": "db",
    "java": "service",
    "vbnet": "service",
    "csharp": "service",
    "javascript": "frontend",
    "typescript": "frontend",
    "tsx": "frontend",
}


@dataclass
class RuleUnit:
    """A single claim extracted from a chunk or chain, backing a RuleItem."""

    key: str                 # "{origin}:{origin_id}:{index}"
    claim: str               # The textual claim/rule
    origin: str              # "chunk" | "chain"
    origin_id: str           # chunk id / chain item id
    layer: str               # db|service|frontend|docs|chain
    source: str              # file path / chain entry
    chunk_ids: list[str]     # [chunk_id] for chunk units; member_chunk_ids for chain units
    evidence: list[dict]     # the entries backing this claim


def collect_rule_units(store, page_size: int = 200) -> list[RuleUnit]:
    """Collect rule units from chunks and chains, paginating until exhausted.

    Args:
        store: ChromaStore instance
        page_size: Pagination size for get_items

    Returns:
        List of RuleUnit, in deterministic order (items in pagination order,
        entries in stored order).
    """
    units = []
    offset = 0

    # Collect from main collection chunks
    while True:
        items = store.get_items(offset=offset, limit=page_size)
        if not items:
            break

        for item in items:
            meta = item.get("metadata", {})
            evidence = parse_evidence_field(meta)
            if not evidence:
                continue

            # Determine layer from metadata
            kind = meta.get("kind", "")
            language = meta.get("language", "")

            if kind == "text":
                layer = "docs"
            elif language in LAYER_BY_LANGUAGE:
                layer = LAYER_BY_LANGUAGE[language]
            elif kind == "code":
                # Fallback for code without mapped language
                layer = "service"
            else:
                # Default fallback
                layer = "docs"

            # Create one unit per evidence entry with a non-blank claim
            for i, ev in enumerate(evidence):
                claim = ev.get("claim", "").strip()
                if not claim:
                    continue

                unit = RuleUnit(
                    key=f"chunk:{item['id']}:{i}",
                    claim=claim,
                    origin="chunk",
                    origin_id=item["id"],
                    layer=layer,
                    source=meta.get("source", ""),
                    chunk_ids=[item["id"]],
                    evidence=[ev],
                )
                units.append(unit)

        offset += len(items)
        if len(items) < page_size:
            break

    # Collect from __chains sibling if available
    if hasattr(store, "sibling"):
        collection_name = store.stats()["collection"]
        chains_store = store.sibling(f"{collection_name}__chains")

        # Check if chains collection has items
        chains_stats = chains_store.stats()
        if chains_stats.get("count", 0) > 0:
            chain_offset = 0
            while True:
                chain_items = chains_store.get_items(offset=chain_offset, limit=page_size)
                if not chain_items:
                    break

                for chain_item in chain_items:
                    meta = chain_item.get("metadata", {})

                    # Parse rules from metadata
                    rules_str = meta.get("rules", "")
                    rules = [r.strip() for r in rules_str.split("|") if r.strip()] if rules_str else []

                    # Parse evidence and member_chunk_ids
                    chain_evidence = parse_evidence_field(meta)
                    member_chunk_ids_str = meta.get("member_chunk_ids", "")
                    member_chunk_ids = [c.strip() for c in member_chunk_ids_str.split(",") if c.strip()]

                    # Create one unit per rule
                    for rule_index, rule in enumerate(rules):
                        rule_lower = rule.lower()

                        # Find evidence entries whose claim matches the rule
                        matching_evidence = [
                            ev for ev in chain_evidence
                            if ev.get("claim", "").lower() == rule_lower
                        ]

                        # Fallback to all evidence if no exact matches
                        if not matching_evidence:
                            matching_evidence = chain_evidence

                        unit = RuleUnit(
                            key=f"chain:{chain_item['id']}:{rule_index}",
                            claim=rule,
                            origin="chain",
                            origin_id=chain_item["id"],
                            layer="chain",
                            source=meta.get("entry", ""),
                            chunk_ids=member_chunk_ids,
                            evidence=matching_evidence,
                        )
                        units.append(unit)

                chain_offset += len(chain_items)
                if len(chain_items) < page_size:
                    break

    return units
