"""File loading and type detection.

Routes code files to the AST splitter and extracts text from documents. Unknown
or binary files raise ``UnsupportedFileError`` so the pipeline can report them
explicitly (Fail Loud) rather than silently dropping data.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

# Code file extension -> tree-sitter language name (names match
# tree-sitter-language-pack; see code_splitter.py).
LANGUAGE_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sh": "bash",
    ".bash": "bash",
    ".lua": "lua",
}

# Plain-text document extensions read verbatim.
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".json", ".yaml", ".yml",
    ".csv", ".tsv", ".log", ".ini", ".toml", ".cfg", ".xml", ".css",
}


class UnsupportedFileError(Exception):
    """Raised when a file cannot be loaded as text or code."""


# Structured-spec extensions that may hold an OpenAPI/Swagger document.
_SPEC_EXTENSIONS = {".json", ".yaml", ".yml"}


@dataclass
class LoadedDoc:
    path: str
    kind: str  # "code" | "text" | "api"
    text: str
    language: Optional[str] = None


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip and data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self._parts)


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedFileError(f"{path}: not UTF-8 text (likely binary)") from exc


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_docx(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs)


def _extract_html(path: Path) -> str:
    parser = _TextExtractor()
    parser.feed(_read_utf8(path))
    return parser.text()


def load_file(path: str | Path) -> LoadedDoc:
    path = Path(path)
    if not path.is_file():
        raise UnsupportedFileError(f"{path}: not a file")
    ext = path.suffix.lower()

    if ext in LANGUAGE_BY_EXT:
        return LoadedDoc(str(path), "code", _read_utf8(path), LANGUAGE_BY_EXT[ext])
    if ext == ".pdf":
        return LoadedDoc(str(path), "text", _extract_pdf(path))
    if ext == ".docx":
        return LoadedDoc(str(path), "text", _extract_docx(path))
    if ext in (".html", ".htm"):
        return LoadedDoc(str(path), "text", _extract_html(path))
    if ext in _SPEC_EXTENSIONS:
        # An OpenAPI/Swagger spec is split per-operation; other JSON/YAML is
        # treated as plain text.
        from .openapi import looks_like_openapi, parse_spec

        text = _read_utf8(path)
        if looks_like_openapi(parse_spec(text)):
            return LoadedDoc(str(path), "api", text, "openapi")
        return LoadedDoc(str(path), "text", text)
    if ext in TEXT_EXTENSIONS:
        return LoadedDoc(str(path), "text", _read_utf8(path))

    # Unknown extension: accept if it decodes as UTF-8 text, else fail loud.
    return LoadedDoc(str(path), "text", _read_utf8(path))
