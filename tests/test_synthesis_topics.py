from opendomainmcp.synthesis.topics import TopicCandidate, gather_topics


def _item(_id, kind, concepts, ktype="", audience=""):
    return {"id": _id, "text": "",
            "metadata": {"kind": kind, "concepts": concepts,
                         "knowledge_type": ktype, "audience": audience}}


def test_cross_validated_topic_passes_and_ranks_first():
    items = [
        _item("c1", "code", "Billing Engine"),
        _item("d1", "text", "billing engine"),          # same topic, doc side
        _item("c2", "code", "Retry Loop", ktype="Code"),  # code-only, not business
    ]
    topics = gather_topics(items)
    names = [t.name for t in topics]
    assert "billing engine" in names           # normalized, deduped across code+doc
    assert "retry loop" not in names           # code-only single mention → gated out
    top = topics[0]
    assert top.name == "billing engine" and top.cross_validated is True


def test_business_typed_multi_mention_passes_without_cross_validation():
    items = [
        _item("a", "code", "Approval Policy", ktype="Permission"),
        _item("b", "code", "Approval Policy", ktype="Permission"),  # >1 business mention
        _item("e", "text", "One Off", ktype="Feature"),            # single mention → out
    ]
    topics = {t.name: t for t in gather_topics(items)}
    assert "approval policy" in topics
    assert topics["approval policy"].business_hits == 2
    assert "one off" not in topics


def test_extra_topics_from_graph_are_folded_and_deduped():
    items = [_item("c1", "code", "Billing Engine"),
             _item("d1", "text", "billing engine")]
    topics = gather_topics(items, extra_topics=["Billing Engine", "Ledger"])
    names = [t.name for t in topics]
    assert names.count("billing engine") == 1   # deduped against existing concept
    # "ledger" has no chunk support → cannot pass the gate, so it is dropped.
    assert "ledger" not in names


def test_repeated_concept_within_single_chunk_counts_once():
    """Bug fix: a single chunk with repeated concepts should not falsely pass the gate."""
    items = [
        _item("only", "code", "Repeated, Repeated", ktype="Permission")
    ]
    topics = gather_topics(items)
    names = [t.name for t in topics]
    # One chunk can only contribute business_hits=1, so it should be gated out (needs >1).
    assert "repeated" not in names


def test_audience_only_multi_mention_without_business_type_passes():
    """Coverage gap: two chunks with audience but no business knowledge_type should pass."""
    items = [
        _item("a", "code", "SharedThing", audience="product_manager"),
        _item("b", "text", "SharedThing", audience="product_manager"),
    ]
    topics = gather_topics(items)
    names = [t.name for t in topics]
    # business_hits == 2 from audience, should pass gate.
    assert "sharedthing" in names
