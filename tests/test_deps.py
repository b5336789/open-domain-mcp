"""Code dependency extraction (task 4.4)."""

from opendomainmcp.graph.deps import IMPORTS_RELATION, extract_dependencies


def _edge_dsts(edges):
    return {e.dst for e in edges}


def test_python_import_and_from_import():
    src = (
        "import os\n"
        "import sys, json\n"
        "from pkg.sub import thing\n"
        "from . import sibling\n"
    )
    entities, edges = extract_dependencies("python", src, symbol="mymod", chunk_id="c1")

    by_norm = {e.normalized_name: e for e in entities}
    assert by_norm["mymod"].type == "module"
    assert _edge_dsts(edges) >= {"os", "sys", "json", "pkg.sub"}
    assert all(e.relation_type == IMPORTS_RELATION for e in edges)
    assert all(e.src == "mymod" for e in edges)
    assert all(e.chunk_id == "c1" for e in edges)


def test_python_import_with_alias_strips_alias():
    entities, edges = extract_dependencies(
        "python", "import numpy as np\n", symbol="m", chunk_id="c1"
    )
    assert "numpy" in _edge_dsts(edges)
    assert "np" not in _edge_dsts(edges)


def test_javascript_import_require_export():
    src = (
        "import React from 'react';\n"
        "import { foo } from \"./util\";\n"
        "export { bar } from './bar';\n"
        "const fs = require('fs');\n"
    )
    _, edges = extract_dependencies("javascript", src, symbol="app", chunk_id="c2")
    assert _edge_dsts(edges) >= {"react", "./util", "./bar", "fs"}
    assert all(e.relation_type == IMPORTS_RELATION for e in edges)


def test_typescript_supported():
    _, edges = extract_dependencies(
        "typescript", "import { A } from './a';\n", symbol="b", chunk_id="c3"
    )
    assert "./a" in _edge_dsts(edges)


def test_module_entity_uses_symbol_as_display_name():
    entities, _ = extract_dependencies(
        "python", "import os\n", symbol="MyModule", chunk_id="c1"
    )
    src_entity = next(e for e in entities if e.normalized_name == "mymodule")
    assert src_entity.display_name == "MyModule"


def test_falls_back_to_chunk_id_when_no_symbol():
    entities, edges = extract_dependencies(
        "python", "import os\n", symbol=None, chunk_id="chunk-xyz"
    )
    assert any(e.normalized_name == "chunk-xyz" for e in entities)
    assert all(e.src == "chunk-xyz" for e in edges)


def test_no_imports_returns_empty():
    entities, edges = extract_dependencies(
        "python", "x = 1\ndef f():\n    return x\n", symbol="m", chunk_id="c1"
    )
    assert entities == []
    assert edges == []


def test_unsupported_language_returns_empty():
    entities, edges = extract_dependencies(
        "cobol", "IMPORT SOMETHING.\n", symbol="m", chunk_id="c1"
    )
    assert entities == []
    assert edges == []


def test_self_import_is_skipped():
    # An import whose normalized name equals the module's own name produces no edge.
    _, edges = extract_dependencies("python", "import mymod\n", symbol="mymod", chunk_id="c1")
    assert edges == []
