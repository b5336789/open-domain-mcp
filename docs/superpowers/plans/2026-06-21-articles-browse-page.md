# Articles Browse Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only web page that lists synthesized articles and lets a user read any one in full with its sources, backed by a single `GET /api/articles` endpoint.

**Architecture:** One FastAPI endpoint reads the collection-scoped `<base>__articles` sibling and returns flat records; one React page (master/detail, client-side filter) renders them, wired into the existing nav/routing. Matches existing conventions throughout (no new deps, no markdown library).

**Tech Stack:** FastAPI + pytest (backend); React 18 + TypeScript + Vite + Playwright (frontend).

## Global Constraints

- Read-only: NO generate/delete/manage; no per-article route; no markdown library (render body with `whitespace-pre-wrap`, like `Ask.tsx`).
- Articles come from `ctx.store.sibling(f"{ctx.store.stats()['collection']}__articles")`; collection scoping is automatic via `ctx`.
- Empty / never-synthesized → `[]` and an explicit empty state; never crash.
- List sorted by `business_relevance` descending; cards show title/topic/badges only (no body preview).
- Nav entry "Articles" immediately after "Browse / Edit".
- Match existing patterns: `app.py` route decorators, `api.ts` `name: (args) => fetch(...)` + `headers()`, `components/ui.tsx` primitives, `icons.tsx` `Base` wrapper, `helpers/mockApi.ts` for e2e.

Spec: `docs/superpowers/specs/2026-06-21-articles-browse-page-design.md`

## File Structure

- Modify `src/opendomainmcp/api/app.py` — add `GET /api/articles`.
- Modify `tests/test_api.py` — endpoint tests.
- Modify `web/src/api.ts` — `Article` interface + `api.articles()`.
- Create `web/src/pages/Articles.tsx` — the page.
- Modify `web/src/components/icons.tsx` — `IconArticles`.
- Modify `web/src/App.tsx` — nav entry.
- Modify `web/src/main.tsx` — route.
- Modify `web/tests/helpers/mockApi.ts` — default `GET /api/articles`.
- Modify `web/tests/smoke.spec.ts` — add "Articles" to NAV_LABELS.
- Create `web/tests/articles.spec.ts` — page smoke test.

---

### Task 1: `GET /api/articles` endpoint

**Files:**
- Modify: `src/opendomainmcp/api/app.py` (add a route inside `create_app`, next to `/api/items` near line 229)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `ctx.store.sibling(...)`, `ChromaStore.get_items(limit, offset)` (returns `[{"id","text","metadata"}]`), `Article` (for tests).
- Produces: `GET /api/articles?limit=&offset=` → `list[dict]` with keys `id, title, topic, business_relevance(float), cross_validated(bool), sources(list[str]), body(str)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api.py — add (reuse the existing `client` fixture: `tc, ctx, _ = client`)
def test_list_articles_returns_mapped_records(client):
    from opendomainmcp.models import Article
    tc, ctx, _ = client
    arts = ctx.store.sibling(f"{ctx.store.stats()['collection']}__articles")
    arts.upsert([Article(
        title="Order Approval Rule", topic="order approval",
        body="Orders above $10k require manager sign-off [1].",
        business_relevance=0.8, source_chunk_ids=["a"],
        sources=["rules.md:1", "approve.py:5"], cross_validated=True,
        critic_verdict={"grounded": True, "business_meaningful": True})])
    data = tc.get("/api/articles").json()
    assert len(data) == 1
    a = data[0]
    assert a["title"] == "Order Approval Rule"
    assert a["topic"] == "order approval"
    assert a["business_relevance"] == 0.8
    assert a["cross_validated"] is True
    assert a["sources"] == ["rules.md:1", "approve.py:5"]
    assert "manager sign-off" in a["body"]


def test_list_articles_empty_when_none(client):
    tc, _, _ = client
    assert tc.get("/api/articles").json() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -k articles -v`
Expected: FAIL — 404 Not Found (route missing).

- [ ] **Step 3: Write minimal implementation**

```python
# src/opendomainmcp/api/app.py — add inside create_app, next to the /api/items route
    @app.get("/api/articles")
    def list_articles(limit: int = 200, offset: int = 0,
                      ctx: Context = Depends(get_ctx)):
        arts = ctx.store.sibling(f"{ctx.store.stats()['collection']}__articles")
        out = []
        for row in arts.get_items(limit=limit, offset=offset):
            meta = row.get("metadata") or {}
            sources = [s.strip() for s in str(meta.get("sources", "")).split(" | ")
                       if s.strip()]
            out.append({
                "id": row["id"],
                "title": meta.get("title") or meta.get("topic") or row["id"],
                "topic": meta.get("topic", ""),
                "business_relevance": float(meta.get("business_relevance", 0) or 0),
                "cross_validated": bool(meta.get("cross_validated", False)),
                "sources": sources,
                "body": row.get("text", ""),
            })
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api.py -k articles -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/api/app.py tests/test_api.py
git commit -m "feat(api): GET /api/articles lists synthesized articles"
```

---

### Task 2: Articles React page + wiring

**Files:**
- Modify: `web/src/api.ts`
- Create: `web/src/pages/Articles.tsx`
- Modify: `web/src/components/icons.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/main.tsx`

**Interfaces:**
- Consumes: `GET /api/articles` (Task 1); `components/ui.tsx` (`PageHeader`, `Card`, `Badge`, `EmptyState`, `Input`, `Skeleton`, `useToast`).
- Produces: `api.articles(): Promise<Article[]>`, `Article` interface, default-exported `Articles` page at route `/articles`, nav entry, `IconArticles`.

- [ ] **Step 1: Add the API client method + type**

```ts
// web/src/api.ts — add near the other interfaces
export interface Article {
  id: string;
  title: string;
  topic: string;
  business_relevance: number;
  cross_validated: boolean;
  sources: string[];
  body: string;
}
```

```ts
// web/src/api.ts — add inside the `export const api = { ... }` object
  articles: () =>
    fetch("/api/articles", { headers: headers() }).then(json<Article[]>),
```

- [ ] **Step 2: Add the icon**

```tsx
// web/src/components/icons.tsx — add with the other icon exports
export const IconArticles = (p: IconProps) => (
  <Base {...p}>
    <path d="M6 3h8l4 4v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
    <path d="M14 3v4h4" />
    <path d="M8 12h8M8 16h8" />
  </Base>
);
```

- [ ] **Step 3: Create the page**

```tsx
// web/src/pages/Articles.tsx
import { useEffect, useMemo, useState } from "react";
import { api, Article } from "../api";
import {
  Badge,
  Card,
  EmptyState,
  Input,
  PageHeader,
  Skeleton,
  useToast,
} from "../components/ui";
import { IconArticles } from "../components/icons";

export default function Articles() {
  const [articles, setArticles] = useState<Article[] | null>(null);
  const [selected, setSelected] = useState<Article | null>(null);
  const [q, setQ] = useState("");
  const toast = useToast();

  useEffect(() => {
    api
      .articles()
      .then((rows) => {
        const sorted = [...rows].sort(
          (a, b) => b.business_relevance - a.business_relevance,
        );
        setArticles(sorted);
        setSelected(sorted[0] ?? null);
      })
      .catch((e) => {
        toast.show(String(e), "red");
        setArticles([]);
      });
  }, []);

  const filtered = useMemo(() => {
    if (!articles) return [];
    const needle = q.trim().toLowerCase();
    if (!needle) return articles;
    return articles.filter((a) =>
      `${a.title} ${a.topic} ${a.body}`.toLowerCase().includes(needle),
    );
  }, [articles, q]);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Articles"
        subtitle="Synthesized business-meaning articles from your knowledge base."
        icon={<IconArticles />}
        actions={
          <Input
            className="w-56"
            placeholder="Filter articles…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        }
      />

      {!articles && (
        <Card className="space-y-2 p-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-4 w-2/3" />
          ))}
        </Card>
      )}

      {articles && articles.length === 0 && (
        <EmptyState
          icon={<IconArticles className="h-6 w-6" />}
          title="No articles yet"
          hint="Run `synthesize` to generate business-meaning articles."
        />
      )}

      {articles && articles.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
          <Card className="divide-y divide-slate-100 dark:divide-slate-800">
            {filtered.map((a) => (
              <button
                key={a.id}
                onClick={() => setSelected(a)}
                className={
                  "block w-full px-3.5 py-3 text-left transition hover:bg-slate-50 dark:hover:bg-slate-800/50" +
                  (selected?.id === a.id
                    ? " bg-slate-50 dark:bg-slate-800/50"
                    : "")
                }
              >
                <div className="font-medium text-slate-800 dark:text-slate-100">
                  {a.title}
                </div>
                <div className="mt-0.5 text-xs text-slate-500">{a.topic}</div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  <Badge tone="brand">
                    relevance {a.business_relevance.toFixed(2)}
                  </Badge>
                  {a.cross_validated && <Badge tone="green">cross-validated</Badge>}
                </div>
              </button>
            ))}
            {filtered.length === 0 && (
              <div className="px-3.5 py-6 text-center text-sm text-slate-500">
                No matches.
              </div>
            )}
          </Card>

          {selected && (
            <Card className="space-y-4 p-5">
              <div>
                <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-50">
                  {selected.title}
                </h2>
                <div className="mt-1 text-sm text-slate-500">{selected.topic}</div>
              </div>
              <div className="whitespace-pre-wrap leading-relaxed text-slate-800 dark:text-slate-200">
                {selected.body}
              </div>
              {selected.sources.length > 0 && (
                <div className="border-t border-slate-100 pt-3 dark:border-slate-800">
                  <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                    Sources
                  </div>
                  <ul className="space-y-1 font-mono text-xs text-slate-600 dark:text-slate-400">
                    {selected.sources.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Wire the nav entry**

```tsx
// web/src/App.tsx — add IconArticles to the existing `{ ... } from "./components/icons"` import,
// then add the nav entry immediately after the "/browse" entry:
  { to: "/browse", label: "Browse / Edit", icon: IconBrowse },
  { to: "/articles", label: "Articles", icon: IconArticles },
```

- [ ] **Step 5: Wire the route**

```tsx
// web/src/main.tsx — add the import alongside the other page imports:
import Articles from "./pages/Articles";
// then add the child route immediately after the "browse" route:
      { path: "browse", element: <Browse /> },
      { path: "articles", element: <Articles /> },
```

- [ ] **Step 6: Build to verify it compiles and wires up**

Run: `cd web && npm run build`
Expected: build succeeds (TypeScript compiles, no type errors). This emits to `src/opendomainmcp/api/static/`.

- [ ] **Step 7: Commit**

```bash
git add web/src/api.ts web/src/pages/Articles.tsx web/src/components/icons.tsx web/src/App.tsx web/src/main.tsx
git commit -m "feat(web): read-only Articles browse page"
```

Note: `src/opendomainmcp/api/static/` is gitignored (the build output is not tracked), so do NOT add it — commit only the `web/src` sources. The `npm run build` in step 6 is purely a compile/type-check gate.

---

### Task 3: Playwright smoke test

**Files:**
- Modify: `web/tests/helpers/mockApi.ts` (add a default for `GET /api/articles`)
- Modify: `web/tests/smoke.spec.ts` (add "Articles" to `NAV_LABELS`)
- Create: `web/tests/articles.spec.ts`

**Interfaces:**
- Consumes: `installApiMocks(page, overrides?)` from `helpers/mockApi.ts`; the `/articles` route (Task 2).

- [ ] **Step 1: Add the default mock so all pages still render**

```ts
// web/tests/helpers/mockApi.ts — add to the defaults map (the object holding
// "GET /api/items": [], around line 159):
    "GET /api/articles": [],
```

- [ ] **Step 2: Add "Articles" to the smoke nav list**

```ts
// web/tests/smoke.spec.ts — insert into NAV_LABELS, right after "Browse / Edit":
  "Browse / Edit",
  "Articles",
  "Review",
```

- [ ] **Step 3: Write the articles page spec**

```ts
// web/tests/articles.spec.ts
import { expect, test } from "@playwright/test";
import { installApiMocks } from "./helpers/mockApi";

const ARTICLES = [
  {
    id: "a1",
    title: "Order Approval Rule",
    topic: "order approval",
    business_relevance: 0.9,
    cross_validated: true,
    sources: ["rules.md:1", "approve.py:5"],
    body: "Orders above $10k require manager sign-off.",
  },
  {
    id: "a2",
    title: "PDF Export Limit",
    topic: "pdf export",
    business_relevance: 0.4,
    cross_validated: false,
    sources: ["billing.py:12"],
    body: "Free-tier customers cannot export to PDF.",
  },
];

test.describe("articles page", () => {
  test.beforeEach(async ({ page }) => {
    await installApiMocks(page, { "GET /api/articles": ARTICLES });
  });

  test("lists articles and opens one to read its body", async ({ page }) => {
    await page.goto("/articles");

    await expect(page.getByRole("heading", { name: "Articles" })).toBeVisible();
    // Highest relevance first; first article auto-selected → its body shows.
    await expect(page.getByText("Orders above $10k require manager sign-off.")).toBeVisible();
    await expect(page.getByText("cross-validated")).toBeVisible();

    // Selecting the second article shows its body.
    await page.getByRole("button", { name: /PDF Export Limit/ }).click();
    await expect(page.getByText("Free-tier customers cannot export to PDF.")).toBeVisible();
    await expect(page.getByText("billing.py:12")).toBeVisible();
  });

  test("shows the empty state when there are no articles", async ({ page }) => {
    await installApiMocks(page, { "GET /api/articles": [] });
    await page.goto("/articles");
    await expect(page.getByText("No articles yet")).toBeVisible();
  });
});
```

- [ ] **Step 4: Run the e2e suite**

Run: `cd web && npm run test:e2e -- articles.spec.ts smoke.spec.ts`
Expected: PASS (the new articles spec + the updated smoke nav check). The Playwright `webServer` rebuilds and serves the preview automatically.

- [ ] **Step 5: Run the full e2e suite to confirm no regressions**

Run: `cd web && npm run test:e2e`
Expected: PASS (the new `GET /api/articles` default keeps every other page rendering).

- [ ] **Step 6: Commit**

```bash
git add web/tests/helpers/mockApi.ts web/tests/smoke.spec.ts web/tests/articles.spec.ts
git commit -m "test(web): Playwright smoke for the Articles page"
```

---

## Self-Review Notes

- **Spec coverage:** `GET /api/articles` mapping + empty-safe (Task 1) ✓; `Article` type + `api.articles()` (Task 2 step 1) ✓; page master/detail, `whitespace-pre-wrap` body, sources list, client-side filter, relevance-desc sort, title/topic/badges-only cards, Skeleton/EmptyState (Task 2 step 3) ✓; nav after Browse/Edit + route + icon (Task 2 steps 2/4/5) ✓; backend pytest + frontend build gate + Playwright smoke (Tasks 1/2/3) ✓; collection scoping via `ctx` (Task 1) ✓; read-only / no markdown lib / no delete (nothing builds them) ✓.
- **Resolved decisions honored:** nav after Browse/Edit; cards title/topic/badges only; sort by business_relevance desc.
- **Type consistency:** `Article` fields identical across the endpoint record (Task 1), the TS interface (Task 2), and the e2e fixtures (Task 3): `id, title, topic, business_relevance, cross_validated, sources, body`.
- **Implementer notes:** (a) `src/opendomainmcp/api/static/` is gitignored — do NOT commit build output (Task 2 step 7). (b) `useToast`, `PageHeader`, `Card`, `Badge`, `EmptyState`, `Input`, `Skeleton` are all exported from `web/src/components/ui.tsx` (verified) — import paths as shown. (c) The `client` fixture in `tests/test_api.py` yields `(TestClient, Context, _)`; seed via `ctx.store.sibling(...)`. (d) Badge `tone` values are limited to `neutral|brand|green|amber|red` — use `brand`/`green` as shown.
