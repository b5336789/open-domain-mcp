"""JS/TS symbol extraction for the code graph (tree-sitter).

Function declarations, class methods, and named arrow functions become
FunctionDefs (qualified as ``<file>:<name>``). Call expressions attribute to
the nearest named enclosing function. fetch()/axios.*() call sites become
http_call CallSites with a normalized "METHOD /path/{param}" detail so the
resolver can match them against backend route templates."""

from __future__ import annotations

import re

from .models import CallSite, FunctionDef, RawSymbols

_AXIOS_METHODS = {"get", "post", "put", "delete", "patch"}
_parsers: dict[str, object] = {}


def _get_parser(language: str):
    if language not in _parsers:
        import tree_sitter as ts

        if language == "javascript":
            import tree_sitter_javascript as mod
            lang = mod.language()
        else:
            import tree_sitter_typescript as mod
            lang = mod.language_tsx() if language == "tsx" else mod.language_typescript()
        _parsers[language] = ts.Parser(ts.Language(lang))
    return _parsers[language]


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def _url_of(node, src: bytes) -> str:
    """Literal or template URL, ${...} -> {param}. '' if not a string."""
    if node.type == "string":
        return _text(node, src).strip("'\"")
    if node.type == "template_string":
        raw = _text(node, src).strip("`")
        return re.sub(r"\$\{[^}]*\}", "{param}", raw)
    return ""


def extract_jsts(source: str, file: str, language: str) -> RawSymbols:
    src = source.encode("utf-8")
    tree = _get_parser(language).parse(src)
    syms = RawSymbols()
    _walk(tree.root_node, src, file, syms, enclosing=None, exported=False)
    return syms


def _func_name(node, src: bytes) -> str | None:
    name = node.child_by_field_name("name")
    if name is not None:
        return _text(name, src)
    return None


def _register(node, src, file, syms, name: str, exported: bool) -> str:
    qualified = f"{file}:{name}"
    syms.functions.append(FunctionDef(
        qualified_name=qualified, file=file,
        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        language="javascript" if file.endswith((".js", ".jsx", ".mjs")) else "typescript",
        signature=name, exported=exported,
    ))
    return qualified


def _walk(node, src: bytes, file: str, syms: RawSymbols,
          enclosing: str | None, exported: bool):
    for child in node.children:
        t = child.type
        if t == "import_statement":
            source_node = child.child_by_field_name("source")
            if source_node is not None:
                syms.imports.append(_text(source_node, src).strip("'\""))
        elif t == "export_statement":
            _walk(child, src, file, syms, enclosing, exported=True)
            continue
        elif t in ("function_declaration", "generator_function_declaration",
                   "method_definition"):
            name = _func_name(child, src)
            scope = _register(child, src, file, syms, name, exported) if name else enclosing
            body = child.child_by_field_name("body")
            if body is not None:
                _walk(body, src, file, syms, scope, False)
            continue
        elif t in ("lexical_declaration", "variable_declaration"):
            for decl in [c for c in child.children if c.type == "variable_declarator"]:
                value = decl.child_by_field_name("value")
                name_node = decl.child_by_field_name("name")
                if value is not None and value.type in ("arrow_function", "function_expression") \
                        and name_node is not None:
                    scope = _register(value, src, file, syms,
                                      _text(name_node, src), exported)
                    body = value.child_by_field_name("body")
                    if body is not None:
                        _walk(body, src, file, syms, scope, False)
                else:
                    _walk(decl, src, file, syms, enclosing, False)
            continue
        elif t == "call_expression" and enclosing is not None:
            _call(child, src, file, syms, enclosing)
        _walk(child, src, file, syms, enclosing, False)


def _call(node, src: bytes, file: str, syms: RawSymbols, caller: str):
    fn = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if fn is None:
        return
    fn_text = _text(fn, src)
    line = node.start_point[0] + 1
    first_arg = None
    if args is not None:
        actual = [c for c in args.children if c.type not in ("(", ")", ",")]
        first_arg = actual[0] if actual else None

    if fn_text == "fetch" and first_arg is not None:
        url = _url_of(first_arg, src)
        if url:
            method = "GET"
            m = re.search(r"method\s*:\s*['\"](\w+)['\"]", _text(args, src))
            if m:
                method = m.group(1).upper()
            syms.calls.append(CallSite(caller=caller, callee_text="fetch", file=file,
                                       line=line, kind="http_call",
                                       detail=f"{method} {url}"))
            return
    if fn.type == "member_expression":
        obj = fn.child_by_field_name("object")
        prop = fn.child_by_field_name("property")
        if obj is not None and prop is not None and _text(obj, src) == "axios" \
                and _text(prop, src) in _AXIOS_METHODS and first_arg is not None:
            url = _url_of(first_arg, src)
            if url:
                syms.calls.append(CallSite(
                    caller=caller, callee_text=fn_text, file=file, line=line,
                    kind="http_call",
                    detail=f"{_text(prop, src).upper()} {url}"))
                return
    syms.calls.append(CallSite(caller=caller, callee_text=fn_text,
                               file=file, line=line))
