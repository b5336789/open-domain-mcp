"""VB.NET lightweight parser (spec 4A, task 4). No tree-sitter grammar exists
for VB.NET; the syntax is line-oriented and regular enough for regex parsing."""

from opendomainmcp.codegraph.vbnet import extract_vbnet

BILLING_VB = """
Imports Acme.Data

Namespace Acme.Billing
    Public Class BillingService

        Public Function ChargeOrder(ByVal order As Order) As Receipt
            ValidateAmount(order)
            Return Repo.Save(order)
        End Function

        Private Sub ValidateAmount(ByVal order As Order)
            Dim cmd As New OracleCommand()
            cmd.CommandText = "BEGIN pkg_billing.validate_amount(:amt); END;"
            If order.Amount < 0 Then
                Throw New ArgumentException("negative")
            End If
        End Sub

    End Class
End Namespace
"""


def test_functions_qualified_and_lines():
    syms = extract_vbnet(BILLING_VB, "Billing.vb")
    by_name = {f.qualified_name: f for f in syms.functions}
    charge = by_name["Acme.Billing.BillingService.ChargeOrder"]
    validate = by_name["Acme.Billing.BillingService.ValidateAmount"]
    assert charge.exported and not validate.exported
    assert charge.language == "vbnet" and charge.file == "Billing.vb"
    assert charge.start_line < validate.start_line
    assert charge.end_line > charge.start_line


def test_call_sites_with_keyword_blacklist():
    syms = extract_vbnet(BILLING_VB, "Billing.vb")
    plain = {(c.caller.rsplit(".", 1)[1], c.callee_text)
             for c in syms.calls if c.kind == "call"}
    assert ("ChargeOrder", "ValidateAmount") in plain
    assert ("ChargeOrder", "Repo.Save") in plain
    # If / Throw / New must not be call sites
    assert not any(c.callee_text in ("If", "Throw", "New", "ArgumentException")
                   for c in syms.calls if c.kind == "call"
                   and c.caller.endswith("ValidateAmount"))


def test_commandtext_db_call():
    syms = extract_vbnet(BILLING_VB, "Billing.vb")
    db = [c for c in syms.calls if c.kind == "db_call"]
    assert db and db[0].detail == "pkg_billing.validate_amount"
    assert db[0].caller.endswith(".ValidateAmount")


def test_imports_and_module_without_namespace():
    syms = extract_vbnet(
        "Imports System.Data\nModule Util\n  Sub Ping()\n  End Sub\nEnd Module\n",
        "Util.vb")
    assert "System.Data" in syms.imports
    assert [f.qualified_name for f in syms.functions] == ["Util.Ping"]
