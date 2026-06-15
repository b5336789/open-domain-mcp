from opendomainmcp.ingest.text_splitter import RecursiveTextSplitter


def test_short_text_single_chunk():
    splitter = RecursiveTextSplitter(chunk_size=100, chunk_overlap=10)
    chunks = splitter.split("a short paragraph")
    assert chunks == ["a short paragraph"]


def test_respects_chunk_size():
    text = "\n\n".join(f"paragraph {i} " + "word " * 20 for i in range(10))
    splitter = RecursiveTextSplitter(chunk_size=200, chunk_overlap=20)
    chunks = splitter.split(text)
    assert len(chunks) > 1
    # Allow modest slack for the separator join but no runaway chunks.
    assert all(len(c) <= 260 for c in chunks)


def test_overlap_between_chunks():
    paras = [f"unique{i} " + "filler " * 30 for i in range(6)]
    text = "\n\n".join(paras)
    splitter = RecursiveTextSplitter(chunk_size=250, chunk_overlap=80)
    chunks = splitter.split(text)
    # Consecutive chunks should share some trailing/leading content.
    overlaps = sum(
        1 for a, b in zip(chunks, chunks[1:])
        if set(a.split()) & set(b.split())
    )
    assert overlaps >= 1


def test_empty_text():
    splitter = RecursiveTextSplitter()
    assert splitter.split("   ") == []


def test_invalid_overlap():
    try:
        RecursiveTextSplitter(chunk_size=100, chunk_overlap=100)
        assert False
    except ValueError:
        pass
