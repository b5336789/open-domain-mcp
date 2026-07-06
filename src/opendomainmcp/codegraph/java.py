"""Java symbol extraction for the code graph (tree-sitter).

Walks the AST once: collects package/imports, class stack, method
declarations (with Spring route annotations -> endpoints), and per-method
call sites (method_invocation). DB-call strings inside method bodies become
db_call CallSites via models.scan_db_calls. Dynamic dispatch is not resolved
here — the resolver decides what a callee name means."""

from __future__ import annotations

from .models import CallSite, FunctionDef, RawSymbols, scan_db_calls

_ROUTE_ANNOTATIONS = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
    "DeleteMapping": "DELETE", "PatchMapping": "PATCH", "RequestMapping": "ANY",
}

_parser = None


def _get_parser():
    global _parser
    if _parser is None:
        import tree_sitter as ts
        import tree_sitter_java

        _parser = ts.Parser(ts.Language(tree_sitter_java.language()))
    return _parser


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def _first_string_literal(node, src: bytes) -> str:
    if node.type == "string_literal":
        return _text(node, src).strip('"')
    for child in node.children:
        s = _first_string_literal(child, src)
        if s:
            return s
    return ""


def _annotation_route(node, src: bytes) -> tuple[str, str] | None:
    """(METHOD, path) if ``node`` is a route annotation, else None."""
    if node.type not in ("annotation", "marker_annotation"):
        return None
    name_node = node.child_by_field_name("name")
    name = _text(name_node, src) if name_node else ""
    method = _ROUTE_ANNOTATIONS.get(name)
    if method is None:
        return None
    return (method, _first_string_literal(node, src))


def _string_texts(node, src: bytes) -> list[str]:
    """Recursively collect the text of every string_literal node under *node*.

    Used to scope db-call scanning to actual string values so that comments
    like ``// execute nightly batch`` do not produce phantom db_call edges."""
    texts = []
    if node.type == "string_literal":
        texts.append(_text(node, src).strip('"'))
    for child in node.children:
        texts.extend(_string_texts(child, src))
    return texts


def _collect_calls(node, src: bytes, caller: str, file: str, out: list[CallSite]):
    if node.type == "method_invocation":
        obj = node.child_by_field_name("object")
        name = node.child_by_field_name("name")
        callee = _text(name, src) if name else ""
        if obj is not None:
            callee = f"{_text(obj, src)}.{callee}"
        out.append(CallSite(caller=caller, callee_text=callee, file=file,
                            line=node.start_point[0] + 1))
    for child in node.children:
        _collect_calls(child, src, caller, file, out)


def extract_java(source: str, file: str) -> RawSymbols:
    src = source.encode("utf-8")
    tree = _get_parser().parse(src)
    syms = RawSymbols()
    _walk(tree.root_node, src, file, syms, package="", classes=[], class_route="")
    return syms


def _walk(node, src: bytes, file: str, syms: RawSymbols,
          package: str, classes: list[str], class_route: str):
    for child in node.children:
        t = child.type
        if t == "package_declaration":
            ids = [c for c in child.children if c.type in ("scoped_identifier", "identifier")]
            if ids:
                package = _text(ids[0], src)
        elif t == "import_declaration":
            ids = [c for c in child.children if c.type in ("scoped_identifier", "identifier")]
            if ids:
                syms.imports.append(_text(ids[0], src))
        elif t in ("class_declaration", "interface_declaration", "enum_declaration"):
            name_node = child.child_by_field_name("name")
            cls = _text(name_node, src) if name_node else "?"
            route_prefix = ""
            for sib in child.children:
                if sib.type == "modifiers":
                    for ann in sib.children:
                        r = _annotation_route(ann, src)
                        if r:
                            route_prefix = r[1]
            body = child.child_by_field_name("body")
            if body is not None:
                _walk(body, src, file, syms, package, classes + [cls],
                      route_prefix or class_route)
        elif t in ("method_declaration", "constructor_declaration"):
            _method(child, src, file, syms, package, classes, class_route)
        else:
            _walk(child, src, file, syms, package, classes, class_route)


def _method(node, src: bytes, file: str, syms: RawSymbols,
            package: str, classes: list[str], class_route: str):
    name_node = node.child_by_field_name("name")
    name = _text(name_node, src) if name_node else "?"
    prefix = ".".join(([package] if package else []) + classes)
    qualified = f"{prefix}.{name}" if prefix else name

    route = None
    exported = False
    for child in node.children:
        if child.type == "modifiers":
            exported = "public" in _text(child, src)
            for ann in child.children:
                r = _annotation_route(ann, src)
                if r:
                    route = (r[0], (class_route.rstrip("/") + "/" + r[1].lstrip("/"))
                             if class_route else r[1])
    params = node.child_by_field_name("parameters")
    signature = f"{name}{_text(params, src) if params else '()'}"

    syms.functions.append(FunctionDef(
        qualified_name=qualified, file=file,
        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        language="java", signature=signature,
        kind="endpoint" if route else "function",
        route=route, exported=exported,
    ))
    body = node.child_by_field_name("body")
    if body is not None:
        _collect_calls(body, src, qualified, file, syms.calls)
        for s_text in _string_texts(body, src):
            for proc in scan_db_calls(s_text):
                syms.calls.append(CallSite(
                    caller=qualified, callee_text=proc, file=file,
                    line=body.start_point[0] + 1, kind="db_call", detail=proc))
