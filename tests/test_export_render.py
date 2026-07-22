from pathlib import Path

from opendomainmcp.export.models import (ExportArticle, ExportBundle,
                                         ExportReport, ExportRule,
                                         ExportWorkflow, Outline,
                                         OutlineDomain, OutlineFlow)
from opendomainmcp.export.render import render_export, slugify


def _bundle():
    return ExportBundle(
        articles=[ExportArticle(id="a1", title="訂單審批", topic="orders",
                                body="內文", sources=["a.py"],
                                source_chunk_ids=["c1"])],
        rules=[
            ExportRule(id="rh", statement="高信心規則", trust="high",
                       corroborations=3, sources=["a.py:1-5"],
                       evidence=[{"claim": "cl", "quote": "qt"}]),
            ExportRule(id="rn", statement="一般規則", trust="normal"),
            ExportRule(id="rc", statement="衝突規則", trust="conflicted",
                       sources=["a.py:1-5", "b.vb:2-9"]),
        ],
        workflows=[ExportWorkflow(
            name="Fulfillment", display_name="出貨流程",
            prerequisites=["庫存同步"],
            steps=[{"order": 1, "text": "揀貨", "precondition": "",
                    "chunk_id": "c1"}])],
        stats={"count": 10, "collection": "test"}, graph_enabled=True)


def _outline():
    return Outline(domains=[OutlineDomain(
        name="訂單管理",
        flows=[OutlineFlow(workflow="Fulfillment", articles=["orders"],
                           rules=["rh"])],
        rules=["rn"])],
        unassigned_articles=[], unassigned_workflows=[], unassigned_rules=[])


def test_domain_tree_layout(tmp_path):
    report = ExportReport()
    render_export(_bundle(), _outline(), tmp_path, report)
    domain_dir = tmp_path / "domains" / slugify("訂單管理", set())
    flow = (domain_dir / "fulfillment.md").read_text(encoding="utf-8")
    assert "出貨流程" in flow                 # display name in heading
    assert "揀貨" in flow                     # step table
    assert "訂單審批" in flow                 # article attached to flow
    assert "高信心規則" in flow and "🟢" in flow
    assert "技術對照" in flow and "a.py:1-5" in flow and "qt" in flow
    readme = (domain_dir / "README.md").read_text(encoding="utf-8")
    assert "一般規則" in readme and "🟡" in readme
    assert (tmp_path / "index.md").exists()
    assert report.out_dir == str(tmp_path)


def test_conflicted_only_in_dedicated_chapter(tmp_path):
    render_export(_bundle(), _outline(), tmp_path, ExportReport())
    conflicted = (tmp_path / "rules-conflicted.md").read_text(encoding="utf-8")
    assert "衝突規則" in conflicted and "b.vb:2-9" in conflicted
    for md in tmp_path.rglob("*.md"):
        if md.name in ("rules-conflicted.md", "handbook.md"):
            continue
        assert "衝突規則" not in md.read_text(encoding="utf-8")


def test_handbook_contains_every_section(tmp_path):
    render_export(_bundle(), _outline(), tmp_path, ExportReport())
    hb = (tmp_path / "handbook.md").read_text(encoding="utf-8")
    for needle in ("訂單管理", "出貨流程", "訂單審批", "高信心規則", "衝突規則"):
        assert needle in hb


def test_flat_fallback_without_outline(tmp_path):
    report = ExportReport()
    render_export(_bundle(), None, tmp_path, report)
    assert (tmp_path / "articles" / "orders.md").exists()
    assert (tmp_path / "workflows" / "fulfillment.md").exists()
    rules = (tmp_path / "rules.md").read_text(encoding="utf-8")
    assert "高信心規則" in rules and "一般規則" in rules
    assert "衝突規則" not in rules
    assert (tmp_path / "rules-conflicted.md").exists()


def test_unassigned_render_to_misc_and_report(tmp_path):
    outline = _outline()
    outline.unassigned_articles = ["orders"]
    outline.domains[0].flows[0].articles = []
    report = ExportReport()
    render_export(_bundle(), outline, tmp_path, report)
    misc = (tmp_path / "misc" / "README.md").read_text(encoding="utf-8")
    assert "訂單審批" in misc
    assert report.unassigned == {"articles": 1, "workflows": 0, "rules": 0}


def test_untranslated_marker(tmp_path):
    b = _bundle()
    b.articles[0].untranslated = True
    render_export(b, _outline(), tmp_path, ExportReport())
    text = "".join(p.read_text(encoding="utf-8")
                   for p in tmp_path.rglob("*.md"))
    assert "〔未翻譯〕" in text


def test_slugify_collisions():
    used = set()
    assert slugify("Order Flow", used) == "order-flow"
    assert slugify("Order Flow", used) == "order-flow-2"
    assert slugify("訂單管理", used)  # non-ascii yields a non-empty slug


def test_graph_disabled_notes_in_index(tmp_path):
    b = _bundle()
    b.workflows, b.graph_enabled = [], False
    report = ExportReport()
    render_export(b, None, tmp_path, report)
    assert "圖庫未啟用" in (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "workflows (graph store not enabled)" in " ".join(report.skipped)
