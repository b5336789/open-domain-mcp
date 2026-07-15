"""export_graph(): bulk read used by the graph quality metrics."""
from opendomainmcp.graph.models import Edge, Entity
from opendomainmcp.graph.store import NullGraphStore


def test_null_store_export_graph_shape():
    export = NullGraphStore().export_graph()
    assert export == {"entities": [], "edges": [], "entity_chunks": []}


def test_fake_store_export_graph_shape(fake_graph):
    fake = fake_graph
    fake.upsert_entities([
        Entity(normalized_name="widget", display_name="Widget", type="Concept",
               confidence=0.9, chunk_id="c1", evidence=""),
        Entity(normalized_name="gadget", display_name="Gadget", type="Concept",
               confidence=0.8, chunk_id="c2", evidence=""),
    ])
    fake.upsert_edges([
        Edge(src="widget", dst="gadget", relation_type="relates_to",
             chunk_id="c1", confidence=0.7, evidence=""),
    ])

    export = fake.export_graph()

    assert sorted(export["entities"], key=lambda e: e["normalized_name"]) == [
        {"normalized_name": "gadget", "display_name": "Gadget", "type": "Concept"},
        {"normalized_name": "widget", "display_name": "Widget", "type": "Concept"},
    ]
    assert export["edges"] == [
        {"src": "widget", "dst": "gadget", "relation_type": "relates_to",
         "chunk_id": "c1", "confidence": 0.7},
    ]
    assert sorted(export["entity_chunks"], key=lambda ec: ec["normalized_name"]) == [
        {"normalized_name": "gadget", "chunk_id": "c2"},
        {"normalized_name": "widget", "chunk_id": "c1"},
    ]
