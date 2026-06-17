from opendomainmcp.models import Chunk, KnowledgeUnit


def test_content_hash_stable_and_location_sensitive():
    a = Chunk(text="hello", source="f.py", start_line=1, end_line=2)
    b = Chunk(text="hello", source="f.py", start_line=1, end_line=2)
    c = Chunk(text="hello", source="f.py", start_line=3, end_line=4)
    assert a.content_hash == b.content_hash
    assert a.content_hash != c.content_hash
    assert a.id == a.content_hash


def test_embedding_text_enriched_with_knowledge():
    k = KnowledgeUnit(summary="adds two numbers", concepts=["addition", "math"])
    chunk = Chunk(text="def add(a, b): return a + b", source="f.py", knowledge=k)
    enriched = chunk.embedding_text()
    assert "adds two numbers" in enriched
    assert "addition" in enriched
    assert chunk.text in enriched


def test_embedding_text_plain_without_knowledge():
    chunk = Chunk(text="raw text", source="f.txt")
    assert chunk.embedding_text() == "raw text"


def test_metadata_drops_none_and_flattens_knowledge():
    k = KnowledgeUnit(summary="s", concepts=["x", "y"], relations=["a->b"])
    chunk = Chunk(
        text="t", source="f.py", kind="code", language="python",
        symbol="add", start_line=1, end_line=1, knowledge=k,
    )
    meta = chunk.metadata()
    assert meta["language"] == "python"
    assert meta["concepts"] == "x, y"
    assert meta["relations"] == "a->b"
    assert "node_type" not in meta  # None values dropped
    assert all(v is not None for v in meta.values())


def test_metadata_flattens_classification_fields():
    k = KnowledgeUnit(
        summary="s", knowledge_type="Feature",
        audience=["product_manager", "support"], confidence=0.8,
        version="v2", permissions=["admin"], tags=["billing", "core"],
        references=["TICKET-1"],
    )
    chunk = Chunk(text="t", source="f.md", knowledge=k)
    meta = chunk.metadata()
    assert meta["knowledge_type"] == "Feature"
    assert meta["audience"] == "product_manager, support"
    assert meta["confidence"] == 0.8
    assert meta["version"] == "v2"
    assert meta["permissions"] == "admin"
    assert meta["tags"] == "billing, core"
    assert meta["references"] == "TICKET-1"
    assert meta["review_status"] == "approved"


def test_metadata_omits_empty_classification_for_legacy_chunks():
    # A chunk with no extracted knowledge (e.g. NullExtractor / older index)
    # carries none of the new keys, so old filters keep matching.
    chunk = Chunk(text="t", source="f.txt")
    meta = chunk.metadata()
    for key in ("knowledge_type", "audience", "review_status", "tags"):
        assert key not in meta


def test_classification_fields_default_empty_and_backward_compatible():
    k = KnowledgeUnit(summary="s")
    assert k.knowledge_type == ""
    assert k.audience == []
    assert k.confidence == 0.0
    assert k.review_status == "approved"
