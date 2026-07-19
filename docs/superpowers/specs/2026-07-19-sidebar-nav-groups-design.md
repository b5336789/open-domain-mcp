# Sidebar Navigation Groups — Design

**Date:** 2026-07-19
**Status:** Approved

## Problem

The web SPA sidebar (`web/src/App.tsx`) lists 14 links in a single flat list.
It is hard to scan and gives no sense of which pages belong together.

## Goal

Reorganize the sidebar into a single-level list with small uppercase section
headers, so the menu can be scanned at a glance. No routes, pages, or backend
behavior change.

## Design

### Scope

Only `web/src/App.tsx`: the `links` data structure and the `Sidebar` render.
Optionally the e2e spec that asserts sidebar contents.

### Data structure

Replace the flat `links` array with grouped data:

```ts
const NAV_GROUPS = [
  { title: null, items: [ { to: "/", label: "Command Center", end: true, icon: IconDashboard } ] },
  { title: "Knowledge", items: [ Source Intake (/intake), Explore (/explore), Ask (/ask), Browse / Edit (/browse), Articles (/articles) ] },
  { title: "Quality", items: [ Review (/review), Quality Lab (/quality), Graph (/graph), Metrics (/metrics) ] },
  { title: "Publish", items: [ Advisor (/advisor), MCP Publish (/mcp), Simulator (/simulator) ] },
];
```

- **Command Center** sits alone at the top with no header.
- **Settings** moves out of the main list into the bottom section of the
  sidebar (above the divider content, alongside the Dark mode toggle), keeping
  the same NavLink styling.

### Rendering

- `Sidebar` maps over `NAV_GROUPS`; each group renders its optional header then
  its NavLinks.
- Header style matches the existing "Knowledge base" label:
  `text-[11px] font-semibold uppercase tracking-wide text-slate-400
  dark:text-slate-500`, with `mt-4`-ish spacing between groups.
- Individual NavLink markup, icons, and active/hover styling are unchanged.
- The mobile drawer reuses the same `Sidebar` component, so it inherits the
  grouping automatically.

## Error handling

None — pure presentational change; no new failure modes.

## Testing

- Run the existing Playwright e2e suite; fix any test that depends on the old
  flat menu.
- Add or adjust an e2e assertion that the group headers (Knowledge / Quality /
  Publish) render and that Settings is still navigable from the sidebar.
