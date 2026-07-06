"""Evidence storage shape on KnowledgeUnit/Chunk/ChainItem (enhancement #2)."""

import json

from opendomainmcp.models import ChainItem, Chunk, KnowledgeUnit, parse_evidence_field

EV = [{"claim": "amount >= 0", "quote": "if (amt < 0) throw", "source": "A.java",
      "start_line": 12, "end_line": 12, "verified": True}]


def test_knowledge_unit_evidence_defaults():
    k = KnowledgeUnit()
    assert k.evidence == [] and k.evidence_status == ""


def test_chunk_metadata_serializes_evidence_json():
    k = KnowledgeUnit(summary="S", evidence=EV, evidence_status="verified")
    c = Chunk(text="code", source="A.java", kind="code", knowledge=k)
    meta = c.metadata()
    assert meta["evidence_status"] == "verified"
    assert json.loads(meta["evidence"]) == EV
    assert all(not isinstance(v, (list, dict)) for v in meta.values())


def test_chunk_metadata_omits_empty_evidence():
    c = Chunk(text="t", source="a.md", knowledge=KnowledgeUnit(summary="S"))
    meta = c.metadata()
    assert "evidence" not in meta and "evidence_status" not in meta


def test_chain_item_evidence_metadata():
    item = ChainItem(entry="e", title="T", body="B", evidence=EV,
                     evidence_status="partial")
    meta = item.metadata()
    assert meta["evidence_status"] == "partial"
    assert json.loads(meta["evidence"])[0]["claim"] == "amount >= 0"


def test_parse_evidence_field_roundtrip_and_corruption():
    assert parse_evidence_field({"evidence": json.dumps(EV)}) == EV
    assert parse_evidence_field({}) == []
    assert parse_evidence_field({"evidence": "{not json"}) == []
