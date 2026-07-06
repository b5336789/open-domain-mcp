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


def test_vbnet_and_plsql_extensions_load_as_code(tmp_path):
    from opendomainmcp.ingest.loader import load_file

    vb = tmp_path / "Billing.vb"
    vb.write_text("Module M\n  Sub Ping()\n  End Sub\nEnd Module\n")
    doc = load_file(vb)
    assert doc.kind == "code" and doc.language == "vbnet"

    for ext in (".sql", ".pks", ".pkb", ".pls"):
        f = tmp_path / f"pkg{ext}"
        f.write_text("CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;\n")
        doc = load_file(f)
        assert doc.kind == "code" and doc.language == "plsql", ext
