import re
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


def test_item_omitted_from_outline_entirely_still_surfaces_in_misc(tmp_path):
    # Finding 1: an outline that neither places an item nor lists it in
    # unassigned_* must not silently drop it — it should still land in
    # misc/README.md and be counted in report.unassigned.
    bundle = _bundle()
    bundle.articles.append(ExportArticle(id="a2", title="退貨政策",
                                         topic="returns", body="退貨內文"))
    outline = _outline()  # domain flow references only "orders"; "returns"
    # is absent from the flow/domain AND absent from unassigned_articles.
    report = ExportReport()
    render_export(bundle, outline, tmp_path, report)
    misc = (tmp_path / "misc" / "README.md").read_text(encoding="utf-8")
    assert "退貨政策" in misc
    assert report.unassigned["articles"] == 1


def test_flow_referencing_unknown_workflow_skips_with_error(tmp_path):
    # Finding 2: an outline flow pointing at a workflow missing from the
    # bundle must not raise KeyError — it's skipped with a report.errors
    # entry, and other flows still render.
    bundle = _bundle()
    outline = _outline()
    outline.domains[0].flows.append(OutlineFlow(workflow="Ghost"))
    report = ExportReport()
    render_export(bundle, outline, tmp_path, report)  # must not raise
    assert any(e.get("kind") == "workflow" and e.get("id") == "Ghost"
              for e in report.errors)
    domain_dir = tmp_path / "domains" / slugify("訂單管理", set())
    assert (domain_dir / "fulfillment.md").exists()


def test_pipe_in_rule_statement_does_not_corrupt_table_row(tmp_path):
    # Finding 3: literal "|" in a cell value must be escaped, not break the
    # table structure.
    bundle = _bundle()
    bundle.rules[1].statement = "A | B rule"  # rn, a domain-level rule
    outline = _outline()
    report = ExportReport()
    render_export(bundle, outline, tmp_path, report)
    domain_dir = tmp_path / "domains" / slugify("訂單管理", set())
    readme = (domain_dir / "README.md").read_text(encoding="utf-8")
    assert "A \\| B rule" in readme
    row = next(l for l in readme.splitlines() if "A \\| B rule" in l)
    # 4 columns => 5 unescaped "|" delimiters, regardless of the escaped
    # pipe embedded in the statement text.
    unescaped_pipes = re.findall(r"(?<!\\)\|", row)
    assert len(unescaped_pipes) == 5


def test_domain_rules_table_gets_tech_appendix(tmp_path):
    # Finding 4: evidence quotes for rules rendered only via _rules_table
    # (domain README) must not be lost.
    bundle = _bundle()
    bundle.rules[1].evidence = [{"claim": "領域佐證", "quote": "網域佐證引文"}]
    bundle.rules[1].sources = ["c.py:9-10"]
    outline = _outline()
    report = ExportReport()
    render_export(bundle, outline, tmp_path, report)
    domain_dir = tmp_path / "domains" / slugify("訂單管理", set())
    readme = (domain_dir / "README.md").read_text(encoding="utf-8")
    assert "網域佐證引文" in readme


def test_misc_rules_table_gets_tech_appendix(tmp_path):
    # Same as above but for the misc/README.md unassigned-rules path.
    bundle = _bundle()
    bundle.rules[1].evidence = [{"claim": "未分類佐證", "quote": "未分類引文"}]
    outline = _outline()
    outline.domains[0].rules = []  # "rn" no longer assigned anywhere
    report = ExportReport()
    render_export(bundle, outline, tmp_path, report)
    misc = (tmp_path / "misc" / "README.md").read_text(encoding="utf-8")
    assert "未分類引文" in misc


def test_flat_rules_page_gets_tech_appendix(tmp_path):
    # Same fix applied to the outline-less flat rules.md page.
    bundle = _bundle()
    bundle.rules[1].evidence = [{"claim": "flat佐證", "quote": "flat引文"}]
    report = ExportReport()
    render_export(bundle, None, tmp_path, report)
    rules_page = (tmp_path / "rules.md").read_text(encoding="utf-8")
    assert "flat引文" in rules_page


def test_index_links_match_deduped_domain_slugs(tmp_path):
    # Finding 5: two domain names that slugify identically must get
    # distinct directories, and index.md's links must match those exact
    # directories rather than being computed from a second fresh slug set.
    bundle = _bundle()
    outline = Outline(domains=[
        OutlineDomain(name="A/B", flows=[], articles=["orders"], rules=["rn"]),
        OutlineDomain(name="A B", flows=[], articles=[], rules=[]),
    ])
    report = ExportReport()
    render_export(bundle, outline, tmp_path, report)
    domain_dirs = sorted(p.name for p in (tmp_path / "domains").iterdir())
    assert domain_dirs == ["a-b", "a-b-2"]
    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "domains/a-b/README.md" in index
    assert "domains/a-b-2/README.md" in index


def test_workflow_embedded_in_misc_nests_under_h2(tmp_path):
    # Finding 6: _render_workflow always emitted an H1; when embedded under
    # misc/README.md's own H1 it must nest as H2 instead.
    bundle = _bundle()
    outline = _outline()
    outline.domains[0].flows = []  # "Fulfillment" is no longer placed
    outline.domains[0].rules = []
    report = ExportReport()
    render_export(bundle, outline, tmp_path, report)
    misc = (tmp_path / "misc" / "README.md").read_text(encoding="utf-8")
    assert "## 出貨流程" in misc
    assert not re.search(r"(?m)^# 出貨流程", misc)
