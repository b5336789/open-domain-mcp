# Sidebar Navigation Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the flat 14-item sidebar in the web SPA into grouped sections (Knowledge / Quality / Publish) with small uppercase headers, moving Settings to the sidebar footer.

**Architecture:** Pure presentational change in `web/src/App.tsx`: the flat `links` array becomes a `NAV_GROUPS` array of `{ title, items }`, the `Sidebar` maps groups → optional header + NavLinks, and Settings renders as a NavLink in the footer next to the theme toggle. No routes, pages, or backend change. Spec: `docs/superpowers/specs/2026-07-19-sidebar-nav-groups-design.md`.

**Tech Stack:** React 18 + react-router-dom NavLink + Tailwind classes already used in the file; Playwright for e2e.

## Global Constraints

- Do not change any route paths or page components — `web/src/main.tsx` stays untouched.
- NavLink markup/styling (active, hover, icon classes) must remain byte-identical to the current implementation.
- Group header style copies the existing "Knowledge base" label: `text-[11px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500`.
- All 14 labels keep their exact current text (the smoke e2e asserts them with `exact: true`).

---

### Task 1: Grouped sidebar in App.tsx (test-first)

**Files:**
- Modify: `web/tests/smoke.spec.ts:26-35` (extend the sidebar test)
- Modify: `web/src/App.tsx:38-53` (`links` → `NAV_GROUPS`) and `web/src/App.tsx:258-304` (`Sidebar`)

**Interfaces:**
- Consumes: existing icon components from `web/src/components/icons.tsx` (unchanged imports).
- Produces: `NAV_GROUPS: { title: string | null; items: { to: string; label: string; end?: boolean; icon: ... }[] }[]` — module-private to `App.tsx`; nothing else depends on it.

- [ ] **Step 1: Extend the smoke e2e to assert group headers and footer Settings**

In `web/tests/smoke.spec.ts`, replace the `renders every sidebar nav link` test body with:

```ts
  test("renders every sidebar nav link", async ({ page }) => {
    await page.goto("/");

    const sidebar = page.locator("aside");
    for (const label of NAV_LABELS) {
      await expect(
        sidebar.getByRole("link", { name: label, exact: true }),
      ).toBeVisible();
    }
    for (const heading of ["Knowledge", "Quality", "Publish"]) {
      await expect(
        sidebar.getByText(heading, { exact: true }),
      ).toBeVisible();
    }
  });
```

(`NAV_LABELS` stays as-is — all 14 labels, including Settings, must still be links inside `aside`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npx playwright test tests/smoke.spec.ts -g "renders every sidebar nav link"`
Expected: FAIL — `getByText("Knowledge", { exact: true })` not visible (headers don't exist yet). Note: config may auto-start the dev server; if it needs a build first, run `npm run build` per `playwright.config.ts`.

- [ ] **Step 3: Restructure `links` into `NAV_GROUPS` in App.tsx**

Replace the `links` array (`web/src/App.tsx:38-53`) with:

```tsx
const NAV_GROUPS: {
  title: string | null;
  items: { to: string; label: string; end?: boolean; icon: (p: { className?: string }) => JSX.Element }[];
}[] = [
  {
    title: null,
    items: [{ to: "/", label: "Command Center", end: true, icon: IconDashboard }],
  },
  {
    title: "Knowledge",
    items: [
      { to: "/intake", label: "Source Intake", icon: IconIngest },
      { to: "/explore", label: "Explore", icon: IconExplore },
      { to: "/ask", label: "Ask", icon: IconAsk },
      { to: "/browse", label: "Browse / Edit", icon: IconBrowse },
      { to: "/articles", label: "Articles", icon: IconArticles },
    ],
  },
  {
    title: "Quality",
    items: [
      { to: "/review", label: "Review", icon: IconReview },
      { to: "/quality", label: "Quality Lab", icon: IconMetrics },
      { to: "/graph", label: "Graph", icon: IconGraph },
      { to: "/metrics", label: "Metrics", icon: IconMetrics },
    ],
  },
  {
    title: "Publish",
    items: [
      { to: "/advisor", label: "Advisor", icon: IconAdvisor },
      { to: "/mcp", label: "MCP Publish", icon: IconBuilder },
      { to: "/simulator", label: "Simulator", icon: IconSimulator },
    ],
  },
];
```

If the icon type annotation fights with the actual icon component types, drop the explicit annotation and let TypeScript infer — the shape above is the contract.

- [ ] **Step 4: Render groups in `Sidebar` and move Settings to the footer**

Extract the current NavLink JSX into a small `NavItem` component (same file) so the main list and the footer Settings link share it, then rewrite `Sidebar`:

```tsx
function NavItem({
  item,
  onNavigate,
}: {
  item: { to: string; label: string; end?: boolean; icon: any };
  onNavigate?: () => void;
}) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      end={item.end}
      onClick={onNavigate}
      className={({ isActive }) =>
        `group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
          isActive
            ? "bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-200"
            : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white"
        }`
      }
    >
      {({ isActive }) => (
        <>
          <Icon
            className={`h-[18px] w-[18px] transition-colors ${
              isActive
                ? "text-brand-600 dark:text-brand-300"
                : "text-slate-400 group-hover:text-slate-600 dark:group-hover:text-slate-200"
            }`}
          />
          {item.label}
        </>
      )}
    </NavLink>
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col gap-5 overflow-y-auto p-4">
      <Brand />
      <CollectionSwitcher />
      <nav>
        {NAV_GROUPS.map((group, i) => (
          <div key={group.title ?? "top"} className={i > 0 ? "mt-4" : ""}>
            {group.title && (
              <div className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                {group.title}
              </div>
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <NavItem key={item.to} item={item} onNavigate={onNavigate} />
              ))}
            </div>
          </div>
        ))}
      </nav>
      <div className="mt-auto space-y-2 border-t border-slate-200 pt-3 dark:border-slate-800">
        <NavItem
          item={{ to: "/settings", label: "Settings", icon: IconSettings }}
          onNavigate={onNavigate}
        />
        <ThemeToggle />
        <p className="px-3 text-[11px] leading-relaxed text-slate-400 dark:text-slate-600">
          Domain knowledge workflow platform
        </p>
      </div>
    </div>
  );
}
```

The `overflow-y-auto` on the wrapper keeps the taller grouped list scrollable on short viewports (`mt-auto` still pins the footer when there's room).

- [ ] **Step 5: Build the SPA and run the smoke spec to verify it passes**

Run: `cd web && npm run build && npx playwright test tests/smoke.spec.ts`
Expected: all smoke tests PASS.

- [ ] **Step 6: Run the full e2e suite to catch any other nav-dependent test**

Run: `cd web && npx playwright test`
Expected: all PASS. (Only `smoke.spec.ts` targets the sidebar today; `quality_lab.spec.ts:131` queries links inside `main`, not the sidebar.)

- [ ] **Step 7: Commit**

```bash
git add web/src/App.tsx web/tests/smoke.spec.ts src/opendomainmcp/api/static
git commit -m "feat(web): group sidebar nav into Knowledge/Quality/Publish sections"
```

(Include `src/opendomainmcp/api/static` only if the build output is tracked and changed — check `git status` first.)
