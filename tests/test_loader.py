import pytest

from opendomainmcp.ingest.loader import UnsupportedFileError, load_file


def test_loads_code_with_language(tmp_path):
    p = tmp_path / "mod.py"
    p.write_text("def f():\n    return 1\n")
    doc = load_file(p)
    assert doc.kind == "code"
    assert doc.language == "python"
    assert "def f" in doc.text


def test_loads_markdown_as_text(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text("# Title\nbody")
    doc = load_file(p)
    assert doc.kind == "text"
    assert doc.language is None


def test_strips_html(tmp_path):
    p = tmp_path / "page.html"
    p.write_text("<html><body><p>Hello</p><script>ignore()</script></body></html>")
    doc = load_file(p)
    assert "Hello" in doc.text
    assert "ignore" not in doc.text


def test_unknown_extension_text_is_accepted(tmp_path):
    p = tmp_path / "data.weird"
    p.write_text("just some text")
    doc = load_file(p)
    assert doc.kind == "text"
    assert "just some text" in doc.text


def test_binary_fails_loud(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"\xff\xfe\x00\x01\x80")
    with pytest.raises(UnsupportedFileError):
        load_file(p)
