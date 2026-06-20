import pytest

from opendomainmcp.ingest.loader import load_file
from opendomainmcp.ingest.wiki import (
    looks_like_mediawiki,
    mediawiki_to_text,
)

# A minimal MediaWiki export: one real page, one redirect page.
_MEDIAWIKI_XML = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.10/" version="0.10">
  <siteinfo>
    <sitename>Example Wiki</sitename>
  </siteinfo>
  <page>
    <title>Getting Started</title>
    <revision>
      <text>Welcome to the project. Install with pip.</text>
    </revision>
  </page>
  <page>
    <title>Old Page</title>
    <redirect title="Getting Started" />
    <revision>
      <text>#REDIRECT [[Getting Started]]</text>
    </revision>
  </page>
  <page>
    <title>Architecture</title>
    <revision>
      <text>The system uses a vector store backend.</text>
    </revision>
  </page>
</mediawiki>
"""


def test_looks_like_mediawiki_detects_export():
    # Arrange / Act
    result = looks_like_mediawiki(_MEDIAWIKI_XML)

    # Assert
    assert result is True


def test_looks_like_mediawiki_rejects_plain_xml():
    # Arrange
    plain = "<config><value>1</value></config>"

    # Act
    result = looks_like_mediawiki(plain)

    # Assert
    assert result is False


def test_mediawiki_to_text_includes_pages_excludes_redirect():
    # Act
    text = mediawiki_to_text(_MEDIAWIKI_XML)

    # Assert: real page titles and bodies present
    assert "= Getting Started =" in text
    assert "Welcome to the project. Install with pip." in text
    assert "= Architecture =" in text
    assert "The system uses a vector store backend." in text
    # Redirect page excluded
    assert "Old Page" not in text
    assert "#REDIRECT" not in text


def test_mediawiki_to_text_fails_loud_on_malformed_xml():
    # Arrange
    broken = "<mediawiki><page><title>Oops</title>"

    # Act / Assert
    with pytest.raises(ValueError):
        mediawiki_to_text(broken)


def test_load_file_mediawiki_export(tmp_path):
    # Arrange
    p = tmp_path / "wiki-export.xml"
    p.write_text(_MEDIAWIKI_XML)

    # Act
    doc = load_file(p)

    # Assert
    assert doc.kind == "text"
    assert doc.language == "mediawiki"
    assert "= Getting Started =" in doc.text
    assert "= Architecture =" in doc.text


def test_load_file_plain_xml_is_plain_text(tmp_path):
    # Arrange
    p = tmp_path / "config.xml"
    p.write_text("<config><value>42</value></config>")

    # Act
    doc = load_file(p)

    # Assert
    assert doc.kind == "text"
    assert doc.language is None
    assert "<value>42</value>" in doc.text
