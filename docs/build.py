#!/usr/bin/env python3
"""Docs site generator.

Converts the Markdown sources under ``docs/`` (which already carry mermaid
diagrams and progress tables) into styled, self-contained HTML pages that match
the hand-built ``index.html`` / ``guide.html`` design language.

Usage:
    python docs/build.py            # render all pages
    python docs/build.py --hook     # PostToolUse hook mode: read the tool-call
                                     # JSON on stdin and only rebuild when a
                                     # docs/*.md file was edited.

Pages are written next to their sources in ``docs/``. The generator never
touches the hand-authored pages (index/guide/reference/screenshots).
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import markdown
from pymdownx.superfences import fence_div_format

DOCS_DIR = Path(__file__).resolve().parent

# Heading shown above the generated body, plus the nav label / emoji.
@dataclass(frozen=True)
class Page:
    source: str       # Markdown filename under docs/
    output: str       # HTML filename written under docs/
    title: str        # <title> + hero heading
    emoji: str        # hero glyph
    subtitle: str     # hero lead line


# Single source of truth: which Markdown files become which pages.
PAGES: tuple[Page, ...] = (
    Page("ARCHITECTURE.md", "architecture.html", "技術架構", "🏗️",
         "系統實作架構與資料流程，含即時渲染的 mermaid 架構圖。"),
    Page("TASKS.md", "tasks.html", "開發任務與進度", "✅",
         "自專案起始至今的所有任務，含進度總覽與各 Phase 拆解。"),
    Page("PRD.md", "prd.html", "產品需求文件", "📝",
         "產品目標、使用情境與功能需求。"),
)

# Hand-authored hub pages we link to but never generate.
HUB_LINKS: tuple[tuple[str, str], ...] = (
    ("index.html", "文件首頁"),
    ("guide.html", "使用說明"),
    ("reference.html", "技術文件"),
)

# Map source .md links to their generated .html targets (for cross-doc links).
LINK_REWRITES = {p.source: p.output for p in PAGES}

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs"

STYLE = """
  :root{
    --mint:#34d9b5; --teal:#0d9488; --sky:#0ea5c4; --ink:#13322e;
    --muted:#587a74; --paper:#f5fbf9; --card:#ffffff; --line:#dcefe9;
    --shadow:0 18px 40px -24px rgba(13,80,72,.42);
    --serif:"Noto Serif TC","Lora",Georgia,"Songti TC",serif;
    --sans:"Noto Sans TC","Inter",system-ui,-apple-system,"Segoe UI",sans-serif;
    --mono:"JetBrains Mono","SFMono-Regular",ui-monospace,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth}
  body{font-family:var(--sans);color:var(--ink);background:var(--paper);
    line-height:1.85;letter-spacing:.1px;-webkit-font-smoothing:antialiased}
  code,kbd,pre{font-family:var(--mono)}
  a{color:var(--teal);text-decoration:none}
  a:hover{text-decoration:underline}
  h1,h2,h3,h4,.brand{font-family:var(--serif);letter-spacing:.2px}
  .wrap{max-width:1040px;margin:0 auto;padding:0 22px}

  nav.bar{position:sticky;top:0;z-index:30;backdrop-filter:blur(10px);
    background:rgba(245,251,249,.84);border-bottom:1px solid var(--line)}
  nav.bar .wrap{display:flex;align-items:center;gap:18px;height:64px;flex-wrap:wrap}
  nav.bar .brand{font-weight:700;font-size:1.08rem;display:flex;align-items:center;gap:9px}
  nav.bar .dot{width:13px;height:13px;border-radius:50%;
    background:conic-gradient(from 90deg,var(--mint),var(--sky),var(--mint))}
  nav.bar .links{margin-left:auto;display:flex;gap:18px;font-size:.92rem;font-weight:500}
  nav.bar .links a{color:var(--muted)}
  nav.bar .links a:hover{color:var(--teal);text-decoration:none}
  nav.bar .links a.active{color:var(--teal);font-weight:700}

  header.hero{position:relative;overflow:hidden;padding:62px 0 40px;
    background:
      radial-gradient(900px 380px at 12% -10%,rgba(14,165,196,.16),transparent 60%),
      radial-gradient(760px 420px at 96% 0%,rgba(52,217,181,.26),transparent 55%);}
  .hero .badge{display:inline-flex;align-items:center;gap:8px;background:var(--card);
    border:1px solid var(--line);border-radius:999px;padding:7px 16px;font-size:.82rem;
    font-weight:600;color:var(--teal);box-shadow:var(--shadow)}
  .hero h1{font-size:clamp(2rem,4.6vw,3rem);line-height:1.3;font-weight:700;margin:20px 0 14px;
    display:flex;align-items:center;gap:14px}
  .hero p.lead{font-size:1.1rem;color:#3f5e59;max-width:680px}
  .hero .stamp{margin-top:16px;font-size:.82rem;color:var(--muted)}

  main.wrap{padding-top:34px;padding-bottom:60px}
  main h1{font-size:clamp(1.7rem,3.6vw,2.3rem);margin:38px 0 14px}
  main h2{font-size:clamp(1.45rem,3vw,1.9rem);margin:34px 0 12px;padding-top:8px;
    border-top:1px solid var(--line)}
  main h3{font-size:1.25rem;margin:24px 0 10px}
  main h4{font-size:1.08rem;margin:18px 0 8px}
  main p{margin:12px 0}
  main ul,main ol{margin:12px 0 12px 26px}
  main li{margin:6px 0}
  main blockquote{border-left:4px solid var(--mint);background:linear-gradient(120deg,
    rgba(52,217,181,.1),rgba(14,165,196,.08));border-radius:0 12px 12px 0;
    padding:12px 18px;margin:16px 0;color:#1c5d54}
  main blockquote p{margin:4px 0}
  main hr{border:none;border-top:1px solid var(--line);margin:30px 0}

  main table{border-collapse:collapse;width:100%;margin:18px 0;font-size:.95rem;
    background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
  main th,main td{padding:10px 14px;border-bottom:1px solid var(--line);text-align:left}
  main th{background:linear-gradient(120deg,rgba(52,217,181,.16),rgba(14,165,196,.12));
    font-family:var(--serif);font-weight:700}
  main tr:last-child td{border-bottom:none}

  main pre{background:#08231f;color:#cdeee6;border-radius:13px;padding:16px 18px;overflow:auto;
    font-size:.85rem;line-height:1.7;margin:16px 0;border:1px solid #0c3a33}
  main pre code{background:none;color:inherit;padding:0;font-size:1em}
  main p code,main li code,main td code{background:#e4f5f0;color:#0b6257;padding:2px 7px;
    border-radius:6px;font-size:.85em}

  main .mermaid{background:var(--card);border:1px solid var(--line);border-radius:16px;
    padding:22px;margin:18px 0;box-shadow:var(--shadow);text-align:center;overflow:auto}

  footer{padding:40px 0;text-align:center;color:var(--muted);
    border-top:1px solid var(--line);font-size:.9rem}
"""

# mermaid theme tuned to the site palette.
MERMAID_INIT = """
import mermaid from "%s";
mermaid.initialize({
  startOnLoad:true,
  theme:"base",
  fontFamily:'"Noto Sans TC",system-ui,sans-serif',
  themeVariables:{
    primaryColor:"#e4f5f0", primaryBorderColor:"#0d9488", primaryTextColor:"#13322e",
    lineColor:"#0ea5c4", secondaryColor:"#f5fbf9", tertiaryColor:"#ffffff"
  }
});
""" % MERMAID_CDN


def build_markdown() -> markdown.Markdown:
    """A Markdown converter with tables + mermaid-aware fenced code blocks."""
    return markdown.Markdown(
        extensions=["tables", "sane_lists", "attr_list", "toc", "pymdownx.superfences"],
        extension_configs={
            "pymdownx.superfences": {
                "custom_fences": [
                    {"name": "mermaid", "class": "mermaid", "format": fence_div_format}
                ]
            }
        },
    )


def rewrite_md_links(html: str) -> str:
    """Point cross-document ``*.md`` links at their generated ``*.html`` pages."""
    def repl(match: re.Match[str]) -> str:
        prefix, name, anchor = match.group(1), match.group(2), match.group(3) or ""
        target = LINK_REWRITES.get(f"{name}.md")
        if target is None:
            return match.group(0)  # leave non-generated links untouched
        return f'href="{prefix}{target}{anchor}"'

    return re.sub(r'href="(\./)?([A-Za-z0-9_-]+)\.md(#[^"]*)?"', repl, html)


def render_nav(active_output: str) -> str:
    items = []
    for page in PAGES:
        cls = " class=\"active\"" if page.output == active_output else ""
        items.append(f'<a href="{page.output}"{cls}>{page.title}</a>')
    for href, label in HUB_LINKS:
        items.append(f'<a href="{href}">{label}</a>')
    return "\n      ".join(items)


def render_page(page: Page, body_html: str, stamp: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>openDomainMcp · {page.title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&family=Noto+Serif+TC:wght@600;700;900&family=Lora:wght@600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>{STYLE}</style>
</head>
<body>

<nav class="bar"><div class="wrap">
  <span class="brand"><span class="dot"></span>openDomainMcp</span>
  <span class="links">
      {render_nav(page.output)}
  </span>
</div></nav>

<header class="hero"><div class="wrap">
  <span class="badge">自動生成 · 由 docs/{page.source}</span>
  <h1><span>{page.emoji}</span>{page.title}</h1>
  <p class="lead">{page.subtitle}</p>
  <p class="stamp">最後更新：{stamp}　·　此頁由 <code>docs/build.py</code> 從 Markdown 生成，請勿直接手改 HTML。</p>
</div></header>

<main class="wrap">
{body_html}
</main>

<footer><div class="wrap">
  openDomainMcp · 文件由 Markdown 自動生成　|　<a href="index.html">文件首頁</a>
</div></footer>

<script type="module">{MERMAID_INIT}</script>
</body>
</html>
"""


def build_all() -> list[str]:
    """Render every configured page; returns the list of written filenames."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    md = build_markdown()
    written: list[str] = []
    for page in PAGES:
        src = DOCS_DIR / page.source
        if not src.exists():
            print(f"[build] skip {page.source}: source not found", file=sys.stderr)
            continue
        md.reset()
        body = rewrite_md_links(md.convert(src.read_text(encoding="utf-8")))
        (DOCS_DIR / page.output).write_text(render_page(page, body, stamp), encoding="utf-8")
        written.append(page.output)
        print(f"[build] {page.source} -> docs/{page.output}")
    return written


def edited_docs_md_path(payload: dict) -> str | None:
    """Extract an edited docs/*.md path from a PostToolUse hook payload, if any."""
    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path.endswith(".md"):
        return None
    try:
        rel = Path(file_path).resolve().relative_to(DOCS_DIR)
    except ValueError:
        return None  # not under docs/
    return str(rel)


def run_hook() -> int:
    """PostToolUse entry point: rebuild only when a docs/*.md file was edited."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # not our event; fail quiet, never block the tool
    edited = edited_docs_md_path(payload)
    if edited is None:
        return 0
    print(f"[build] docs/{edited} changed — regenerating site")
    build_all()
    return 0


def main() -> int:
    if "--hook" in sys.argv[1:]:
        return run_hook()
    build_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
