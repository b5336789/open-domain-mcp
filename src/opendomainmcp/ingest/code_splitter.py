"""AST-based code splitting.

Borrows the core idea from the open-source ``claude-context`` project: parse the
file with tree-sitter and chunk at semantic boundaries (functions / classes /
methods) instead of arbitrary character windows. Small sibling declarations are
merged; oversized declarations are recursed into; unsupported languages or parse
failures fall back to a line-window splitter (reported, never silently dropped).
"""

from __future__ import annotations

import importlib
import logging

from ..models import Chunk

logger = logging.getLogger(__name__)

# language name -> (module, factory function) for the bundled grammar wheels.
_GRAMMARS = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "java": ("tree_sitter_java", "language"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
    "csharp": ("tree_sitter_c_sharp", "language"),
    "ruby": ("tree_sitter_ruby", "language"),
    "bash": ("tree_sitter_bash", "language"),
}

# Node types that name a declaration worth labelling with its symbol. Each is
# emitted as its own chunk so it carries a symbol name.
_DEFINITION_TYPES = {
    "function_definition", "function_declaration", "function_item",
    "method_definition", "method_declaration", "constructor_declaration",
    "class_definition", "class_declaration", "class_specifier",
    "struct_specifier", "struct_item", "enum_item", "enum_declaration",
    "impl_item", "trait_item", "interface_declaration", "module",
    "type_alias_declaration", "decorated_definition",
}

# Container nodes whose children hold the nested declarations of a definition.
_BODY_TYPES = {
    "block", "statement_block", "declaration_list", "class_body",
    "field_declaration_list", "enum_body",
}

_parser_cache: dict = {}


def _get_parser(language: str):
    if language in _parser_cache:
        return _parser_cache[language]
    spec = _GRAMMARS.get(language)
    if spec is None:
        return None
    import tree_sitter as ts

    module = importlib.import_module(spec[0])
    parser = ts.Parser(ts.Language(getattr(module, spec[1])()))
    _parser_cache[language] = parser
    return parser


def _symbol_of(node, src: bytes):
    name = node.child_by_field_name("name")
    if name is not None:
        return src[name.start_byte:name.end_byte].decode("utf-8", "replace")
    for child in node.named_children:
        if child.type in ("identifier", "type_identifier", "constant"):
            return src[child.start_byte:child.end_byte].decode("utf-8", "replace")
    return None


def _make_chunk(nodes, src, language, path) -> Chunk:
    start, end = nodes[0], nodes[-1]
    text = src[start.start_byte:end.end_byte].decode("utf-8", "replace")
    single = nodes[0] if len(nodes) == 1 else None
    return Chunk(
        text=text,
        source=path,
        kind="code",
        language=language,
        node_type=single.type if single else "block",
        symbol=_symbol_of(single, src) if single else None,
        start_line=start.start_point[0] + 1,
        end_line=end.end_point[0] + 1,
    )


def _split_definition(node, src, language, path, max_chars, out):
    """Emit a definition as one chunk, or recurse into its body if too large."""
    node_len = node.end_byte - node.start_byte
    if node_len <= max_chars:
        out.append(_make_chunk([node], src, language, path))
        return
    body = next((c for c in node.named_children if c.type in _BODY_TYPES), None)
    if body is not None and body.named_children:
        _chunk_siblings(body.named_children, src, language, path, max_chars, out)
    else:
        # No recognizable body; keep it whole rather than losing the symbol.
        out.append(_make_chunk([node], src, language, path))


def _chunk_siblings(nodes, src, language, path, max_chars, out):
    group: list = []  # consecutive non-definition siblings merged by size
    for node in nodes:
        if node.type in _DEFINITION_TYPES:
            if group:
                out.append(_make_chunk(group, src, language, path))
                group = []
            _split_definition(node, src, language, path, max_chars, out)
            continue
        if group and (node.end_byte - group[0].start_byte) > max_chars:
            out.append(_make_chunk(group, src, language, path))
            group = []
        group.append(node)
    if group:
        out.append(_make_chunk(group, src, language, path))


def _line_fallback(source: str, path, max_chars, language=None) -> list[Chunk]:
    chunks: list[Chunk] = []
    lines = source.splitlines(keepends=True)
    buffer: list[str] = []
    start_line = 1
    size = 0
    for i, line in enumerate(lines, start=1):
        buffer.append(line)
        size += len(line)
        if size >= max_chars:
            chunks.append(Chunk(
                text="".join(buffer), source=str(path), kind="code",
                language=language, node_type="lines",
                start_line=start_line, end_line=i,
            ))
            buffer, size, start_line = [], 0, i + 1
    if buffer:
        chunks.append(Chunk(
            text="".join(buffer), source=str(path), kind="code",
            language=language, node_type="lines",
            start_line=start_line, end_line=len(lines),
        ))
    return chunks


def split_code(source: str, language: str, path, max_chars: int = 2000) -> list[Chunk]:
    """Split source code into AST-aware chunks, falling back to line windows."""
    parser = _get_parser(language)
    if parser is None:
        logger.warning("No grammar for %s; line-splitting %s", language, path)
        return _line_fallback(source, path, max_chars, language)
    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Parse failed for %s (%s); line-splitting", path, exc)
        return _line_fallback(source, path, max_chars, language)

    out: list[Chunk] = []
    _chunk_siblings(tree.root_node.named_children, source.encode("utf-8"),
                    language, str(path), max_chars, out)
    if not out:  # e.g. empty file or only trivia
        return _line_fallback(source, path, max_chars, language)
    return out
