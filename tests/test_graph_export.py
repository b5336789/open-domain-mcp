"""export_graph(): bulk read used by the graph quality metrics."""
from opendomainmcp.graph.store import NullGraphStore


def test_null_store_export_graph_shape():
    export = NullGraphStore().export_graph()
    assert export == {"entities": [], "edges": [], "entity_chunks": []}
