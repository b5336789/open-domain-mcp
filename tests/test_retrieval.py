from opendomainmcp.retrieval import LexicalIndex, rrf_fuse, tokenize
from opendomainmcp.store import build_where


def test_tokenize_keeps_identifiers():
    assert tokenize("def get_ids_for_source(self):") == [
        "def", "get_ids_for_source", "self"
    ]


def test_lexical_finds_exact_token():
    idx = LexicalIndex()
    idx.build(
        ["a", "b", "c"],
        ["vector similarity search", "build_where helper function", "recursive text splitter"],
    )
    assert idx.search("build_where", top_k=1) == ["b"]
    assert idx.search("nonexistenttoken", top_k=3) == []  # zero-score dropped


def test_rrf_rewards_agreement():
    vector = ["x", "y", "z"]
    lexical = ["y", "w", "x"]
    fused = rrf_fuse([vector, lexical], top_k=4)
    ids = [i for i, _ in fused]
    # 'y' (rank 2 + rank 1) and 'x' (rank 1 + rank 3) appear in both -> ahead of z/w.
    assert ids[0] in {"x", "y"}
    assert set(ids[:2]) == {"x", "y"}


def test_build_where_combines_conditions():
    assert build_where(None) is None
    assert build_where({"kind": "code"}) == {"kind": "code"}
    where = build_where({"kind": "code", "language": "python"})
    assert where == {"$and": [{"kind": "code"}, {"language": "python"}]}
