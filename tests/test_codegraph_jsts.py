"""JS/TS symbol extraction via tree-sitter (spec 4A, task 3)."""

from opendomainmcp.codegraph.jsts import extract_jsts

CLIENT = """
import { api } from "./base";

export async function fetchOrders(customerId) {
  const res = await fetch(`/api/billing/orders/${customerId}`);
  return normalize(res);
}

function normalize(res) {
  return res.json();
}

export const charge = async (order) => {
  return axios.post("/api/billing/charge", order);
};
"""


def test_functions_and_exports():
    syms = extract_jsts(CLIENT, "src/api/client.js", "javascript")
    by_name = {f.qualified_name: f for f in syms.functions}
    assert "src/api/client.js:fetchOrders" in by_name
    assert "src/api/client.js:normalize" in by_name
    assert "src/api/client.js:charge" in by_name
    assert by_name["src/api/client.js:fetchOrders"].exported
    assert not by_name["src/api/client.js:normalize"].exported
    f = by_name["src/api/client.js:fetchOrders"]
    assert f.start_line > 1 and f.end_line >= f.start_line


def test_plain_call_sites():
    syms = extract_jsts(CLIENT, "src/api/client.js", "javascript")
    plain = {(c.caller, c.callee_text) for c in syms.calls if c.kind == "call"}
    assert ("src/api/client.js:fetchOrders", "normalize") in plain


def test_http_call_sites_with_template_params():
    syms = extract_jsts(CLIENT, "src/api/client.js", "javascript")
    http = {c.detail for c in syms.calls if c.kind == "http_call"}
    assert "GET /api/billing/orders/{param}" in http
    assert "POST /api/billing/charge" in http


def test_imports_and_typescript_language():
    syms = extract_jsts("import x from 'mod';\nexport function f(): void { g(); }",
                        "a.ts", "typescript")
    assert "mod" in syms.imports
    assert [f.qualified_name for f in syms.functions] == ["a.ts:f"]


def test_tsx_language_threaded_to_function_defs():
    syms = extract_jsts(
        "export function App(): JSX.Element { return render(); }",
        "App.tsx", "tsx")
    assert [f.qualified_name for f in syms.functions] == ["App.tsx:App"]
    assert syms.functions[0].language == "tsx"
    plain = {(c.caller, c.callee_text) for c in syms.calls if c.kind == "call"}
    assert ("App.tsx:App", "render") in plain
