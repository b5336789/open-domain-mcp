from opendomainmcp.ingest.code_splitter import split_code

PY = '''\
import os


def add(a, b):
    return a + b


class Calculator:
    def multiply(self, a, b):
        return a * b
'''

JS = '''\
function greet(name) {
  return `hi ${name}`;
}

class Widget {
  render() {
    return null;
  }
}
'''


def test_python_splits_at_definitions_with_symbols():
    chunks = split_code(PY, "python", "calc.py", max_chars=80)
    symbols = {c.symbol for c in chunks if c.symbol}
    assert "add" in symbols
    assert "Calculator" in symbols
    add_chunk = next(c for c in chunks if c.symbol == "add")
    assert add_chunk.kind == "code"
    assert add_chunk.language == "python"
    assert add_chunk.start_line == 4  # def add is on line 4
    assert "return a + b" in add_chunk.text


def test_javascript_splits_functions_and_classes():
    chunks = split_code(JS, "javascript", "w.js", max_chars=60)
    symbols = {c.symbol for c in chunks if c.symbol}
    assert "greet" in symbols
    assert "Widget" in symbols


def test_unsupported_language_line_fallback():
    src = "line1\nline2\nline3\nline4\n"
    chunks = split_code(src, "cobol", "legacy.cob", max_chars=12)
    assert chunks  # produced something
    assert all(c.node_type == "lines" for c in chunks)
    # round-trips the original content
    assert "".join(c.text for c in chunks) == src


def test_large_class_recurses_into_methods():
    body = "\n\n".join(
        f"    def method_{i}(self):\n        return {i} * 1000000"
        for i in range(6)
    )
    src = f"class Big:\n{body}\n"
    chunks = split_code(src, "python", "big.py", max_chars=60)
    method_symbols = [c.symbol for c in chunks if c.symbol and c.symbol.startswith("method_")]
    assert len(method_symbols) >= 2  # split into individual methods
