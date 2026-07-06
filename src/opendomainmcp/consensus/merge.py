"""Merge rule units into rule items via union-find, trust tiers, conflict marking."""

from opendomainmcp.consensus.units import RuleUnit
from opendomainmcp.models import RuleItem


class UnionFind:
    """Simple union-find for grouping rule units by 'same' verdict."""

    def __init__(self, keys: list[str]):
        self.parent = {key: key for key in keys}

    def find(self, key: str) -> str:
        """Find the canonical representative of key's group."""
        if self.parent[key] != key:
            self.parent[key] = self.find(self.parent[key])  # Path compression
        return self.parent[key]

    def union(self, key_a: str, key_b: str) -> None:
        """Union the groups of key_a and key_b."""
        root_a = self.find(key_a)
        root_b = self.find(key_b)
        if root_a != root_b:
            self.parent[root_b] = root_a


def merge_groups(units: list[RuleUnit],
                 verdicts: list[tuple[str, str, str]],
                 review_mode: bool = False) -> list[RuleItem]:
    """Merge rule units into rule items via union-find over 'same' verdicts.

    Args:
        units: List of RuleUnit to merge
        verdicts: List of (key_a, key_b, verdict) tuples where verdict is one of:
                 "same", "conflict", "related"
        review_mode: If True, all rules get review_status='pending'

    Returns:
        List of RuleItem, sorted by rule id. Only non-singleton groups (or
        singletons with conflict verdicts) produce output.
    """
    if not units:
        return []

    # Build lookup for units by key
    units_by_key = {u.key: u for u in units}
    all_keys = list(units_by_key.keys())

    # Track which keys have conflict verdicts
    conflicted_keys = set()
    for key_a, key_b, verdict in verdicts:
        if verdict == "conflict":
            conflicted_keys.add(key_a)
            conflicted_keys.add(key_b)

    # Union-find over "same" verdicts (skip stale keys not present in units)
    uf = UnionFind(all_keys)
    for key_a, key_b, verdict in verdicts:
        if verdict == "same" and key_a in units_by_key and key_b in units_by_key:
            uf.union(key_a, key_b)

    # Group units by their canonical representative
    groups = {}
    for key in all_keys:
        root = uf.find(key)
        if root not in groups:
            groups[root] = []
        groups[root].append(units_by_key[key])

    # Build RuleItems from groups
    rules = []
    for root, group in groups.items():
        # Only emit if group has >1 member OR has a conflict verdict
        if len(group) == 1 and root not in conflicted_keys:
            continue

        # Determine statement: longest claim (tie → lexicographic min)
        claims = [u.claim for u in group]
        claims.sort(key=lambda c: (-len(c), c))  # Sort by descending length, then lexicographic
        statement = claims[0]

        # Layers (computed once; also used for the trust decision below)
        layers = sorted(set(u.layer for u in group))

        # Determine trust
        if any(u.key in conflicted_keys for u in group):
            trust = "conflicted"
        else:
            non_chain_layers = [l for l in layers if l != "chain"]
            has_chain = "chain" in layers

            # "high" if ≥ 2 distinct non-chain layers OR (≥ 2 members AND chain + ≥1 non-chain)
            if len(non_chain_layers) >= 2:
                trust = "high"
            elif len(group) >= 2 and has_chain and non_chain_layers:
                trust = "high"
            else:
                trust = "normal"

        # Determine review_status
        if any(u.key in conflicted_keys for u in group) or review_mode:
            review_status = "pending"
        else:
            review_status = "approved"

        # Collect and deduplicate evidence by (claim, quote, source)
        seen_evidence = set()
        evidence = []
        for u in group:
            for ev in u.evidence:
                key_tuple = (ev.get("claim"), ev.get("quote"), ev.get("source"))
                if key_tuple not in seen_evidence:
                    seen_evidence.add(key_tuple)
                    evidence.append(ev)

        # Combine evidence_status by entry counts (same rule as extract/verify.py):
        # all verified → "verified", some → "partial", none → "unverified", empty → ""
        verified_count = sum(1 for ev in evidence if ev.get("verified"))
        if not evidence:
            combined_evidence_status = ""
        elif verified_count == len(evidence):
            combined_evidence_status = "verified"
        elif verified_count:
            combined_evidence_status = "partial"
        else:
            combined_evidence_status = "unverified"

        # Collect member_keys, chunk_ids, sources
        member_keys = [u.key for u in group]
        member_chunk_ids = sorted(set(cid for u in group for cid in u.chunk_ids))
        sources = sorted(set(u.source for u in group))

        # Create RuleItem
        rule = RuleItem(
            statement=statement,
            trust=trust,
            corroborations=len(group),
            layers=layers,
            member_keys=member_keys,
            member_chunk_ids=member_chunk_ids,
            sources=sources,
            evidence=evidence,
            evidence_status=combined_evidence_status,
            review_status=review_status,
        )
        rules.append(rule)

    # Sort by rule id (deterministic)
    rules.sort(key=lambda r: r.id)
    return rules
