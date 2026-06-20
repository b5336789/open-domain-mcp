"""Wiki export parsing (MediaWiki XML, Confluence HTML).

Wiki dumps bundle many pages into one file. A MediaWiki XML export wraps every
page in a ``<page>`` element under a ``<mediawiki>`` root; splitting it by
character windows would mix unrelated pages together. Instead we flatten the
export into clean text, rendering each page as a ``= Title =`` section followed
by its latest revision body, so the normal embed/store flow sees coherent units.

Parsing uses the stdlib :mod:`xml.etree.ElementTree`. Malformed XML raises a
clear ``ValueError`` (Fail Loud) rather than silently returning empty text.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# MediaWiki export root tag (with or without a namespace prefix).
_MEDIAWIKI_ROOT_RE = re.compile(r"<mediawiki[\s>]")
# Quick check for a <page> child without fully parsing first.
_PAGE_TAG_RE = re.compile(r"<page[\s>]")

# Confluence HTML exports carry a confluence-specific <meta> or a main-content
# container. Matching this is best-effort: a full DOM parse is unnecessary for a
# heuristic, so a couple of substring/regex checks keep it dependency-free.
_CONFLUENCE_META_RE = re.compile(r'<meta\s+name="confluence', re.IGNORECASE)
_CONFLUENCE_MAIN_RE = re.compile(r'id="main-content"', re.IGNORECASE)


def looks_like_mediawiki(text: str) -> bool:
    """Heuristic: is ``text`` a MediaWiki XML export with page entries?"""
    if not isinstance(text, str) or not text.strip():
        return False
    return bool(_MEDIAWIKI_ROOT_RE.search(text) and _PAGE_TAG_RE.search(text))


def looks_like_confluence_html(text: str) -> bool:
    """Heuristic: is ``text`` a Confluence HTML export page?

    Best-effort and intentionally simple: rather than building a DOM, we look
    for a confluence-specific ``<meta>`` tag or the ``id="main-content"``
    container that Confluence space exports wrap page bodies in.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    return bool(_CONFLUENCE_META_RE.search(text) or _CONFLUENCE_MAIN_RE.search(text))


def _local_name(tag: str) -> str:
    """Strip any ``{namespace}`` prefix from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


def _find_child(element: ET.Element, name: str) -> ET.Element | None:
    """Return the first direct child whose local name matches ``name``."""
    for child in element:
        if _local_name(child.tag) == name:
            return child
    return None


def _iter_children(element: ET.Element, name: str):
    """Yield direct children whose local name matches ``name``."""
    for child in element:
        if _local_name(child.tag) == name:
            yield child


def _is_redirect(page: ET.Element) -> bool:
    """A page is a redirect if it has a ``<redirect>`` element."""
    return _find_child(page, "redirect") is not None


def _latest_revision_text(page: ET.Element) -> str:
    """Return the body of the last ``<revision>`` in document order, or ""."""
    last_text = ""
    for revision in _iter_children(page, "revision"):
        text_el = _find_child(revision, "text")
        if text_el is not None and text_el.text:
            last_text = text_el.text
    return last_text.strip()


def _page_section(page: ET.Element) -> str | None:
    """Render one ``<page>`` as a ``= Title =`` section, or None to skip.

    Redirect pages and pages with an empty body are skipped.
    """
    if _is_redirect(page):
        return None
    title_el = _find_child(page, "title")
    title = (title_el.text or "").strip() if title_el is not None else ""
    body = _latest_revision_text(page)
    if not title or not body:
        return None
    return f"= {title} =\n{body}"


def mediawiki_to_text(text: str) -> str:
    """Flatten a MediaWiki XML export into clean per-page sections.

    Each non-redirect page with a non-empty latest revision becomes a section::

        = Page Title =
        <raw page body>

    Malformed XML raises ``ValueError`` (Fail Loud).
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"invalid MediaWiki XML: {exc}") from exc

    if _local_name(root.tag) != "mediawiki":
        raise ValueError(
            f"not a MediaWiki export: root element is <{_local_name(root.tag)}>"
        )

    sections: list[str] = []
    for page in _iter_children(root, "page"):
        section = _page_section(page)
        if section is not None:
            sections.append(section)
    return "\n\n".join(sections)
