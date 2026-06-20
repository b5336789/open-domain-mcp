import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api, Collection, getActiveCollection, setActiveCollection } from "./api";
import { useTheme } from "./lib/theme";
import {
  Button,
  IconButton,
  Input,
  Label,
  Modal,
  Spinner,
  useToast,
} from "./components/ui";
import {
  IconAdvisor,
  IconArticles,
  IconAsk,
  IconBrowse,
  IconBuilder,
  IconClose,
  IconDashboard,
  IconDatabase,
  IconExplore,
  IconGraph,
  IconIngest,
  IconMenu,
  IconMetrics,
  IconMoon,
  IconPlus,
  IconTrash,
  IconReview,
  IconSettings,
  IconSimulator,
  IconSun,
} from "./components/icons";

const links = [
  { to: "/", label: "Dashboard", end: true, icon: IconDashboard },
  { to: "/ingest", label: "Ingest", icon: IconIngest },
  { to: "/explore", label: "Explore", icon: IconExplore },
  { to: "/ask", label: "Ask", icon: IconAsk },
  { to: "/browse", label: "Browse / Edit", icon: IconBrowse },
  { to: "/articles", label: "Articles", icon: IconArticles },
  { to: "/review", label: "Review", icon: IconReview },
  { to: "/graph", label: "Graph", icon: IconGraph },
  { to: "/advisor", label: "Advisor", icon: IconAdvisor },
  { to: "/mcp", label: "MCP Builder", icon: IconBuilder },
  { to: "/simulator", label: "Simulator", icon: IconSimulator },
  { to: "/metrics", label: "Metrics", icon: IconMetrics },
  { to: "/settings", label: "Settings", icon: IconSettings },
];

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-brand-700 text-white shadow-glow">
        <IconDatabase className="h-5 w-5" />
      </div>
      <div className="leading-tight">
        <div className="text-[15px] font-semibold text-slate-900 dark:text-white">
          openDomainMcp
        </div>
        <div className="text-[11px] text-slate-400 dark:text-slate-500">
          knowledge console
        </div>
      </div>
    </div>
  );
}

function CollectionSwitcher() {
  const [collections, setCollections] = useState<Collection[] | null>(null);
  const [active, setActive] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api
      .collections()
      .then((d) => {
        setCollections(d.collections);
        setActive(getActiveCollection() ?? d.active);
      })
      .catch(() => setCollections([]));
  }, []);

  function choose(name: string) {
    setActiveCollection(name);
    window.location.reload();
  }

  async function create() {
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await api.createCollection(trimmed);
      toast.show(`Created knowledge base “${trimmed}”`, "green");
      choose(trimmed);
    } catch (e) {
      toast.show(String(e), "red");
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.deleteCollection(active);
      toast.show(`Deleted knowledge base “${active}”`, "neutral");
      // Switch to a remaining collection (reloads the console).
      const next = collections?.find((c) => c.name !== active);
      setActiveCollection(next ? next.name : null);
      window.location.reload();
    } catch (e) {
      toast.show(String(e), "red");
      setBusy(false);
    }
  }

  return (
    <div>
      <Label>
        <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
          Knowledge base
        </span>
      </Label>
      <div className="mt-1.5 flex gap-1.5">
        <div className="relative flex-1">
          <select
            className="h-9 w-full appearance-none rounded-lg border border-slate-200 bg-white pl-3 pr-8 text-sm font-medium text-slate-800 transition-colors hover:border-slate-300 focus:border-brand-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:hover:border-slate-600"
            value={active}
            disabled={!collections}
            onChange={(e) => choose(e.target.value)}
          >
            {!collections && <option>Loading…</option>}
            {collections?.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} ({c.count})
              </option>
            ))}
          </select>
          <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400">
            {collections ? "▾" : <Spinner className="h-3.5 w-3.5" />}
          </span>
        </div>
        <IconButton
          onClick={() => {
            setName("");
            setCreating(true);
          }}
          title="New knowledge base"
          aria-label="New knowledge base"
          className="h-9 w-9 border border-slate-200 dark:border-slate-700"
        >
          <IconPlus className="h-4 w-4" />
        </IconButton>
        <IconButton
          onClick={() => setDeleting(true)}
          disabled={!collections || collections.length <= 1 || !active}
          title="Delete knowledge base"
          aria-label="Delete knowledge base"
          className="h-9 w-9 border border-slate-200 dark:border-slate-700"
        >
          <IconTrash className="h-4 w-4" />
        </IconButton>
      </div>

      {creating && (
        <Modal
          title="New knowledge base"
          onClose={() => !busy && setCreating(false)}
          footer={
            <>
              <Button
                variant="secondary"
                onClick={() => setCreating(false)}
                disabled={busy}
              >
                Cancel
              </Button>
              <Button onClick={create} loading={busy} disabled={!name.trim()}>
                Create
              </Button>
            </>
          }
        >
          <Label>Name</Label>
          <Input
            autoFocus
            className="mt-1.5"
            placeholder="e.g. my_project"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create()}
          />
          <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">
            A separate vector collection. Switching reloads the console.
          </p>
        </Modal>
      )}

      {deleting && (
        <Modal
          title="Delete knowledge base?"
          onClose={() => !busy && setDeleting(false)}
          footer={
            <>
              <Button
                variant="secondary"
                onClick={() => setDeleting(false)}
                disabled={busy}
              >
                Cancel
              </Button>
              <Button variant="danger" onClick={remove} loading={busy}>
                Delete
              </Button>
            </>
          }
        >
          <p className="text-sm text-slate-600 dark:text-slate-300">
            This permanently removes the collection{" "}
            <span className="font-semibold text-slate-900 dark:text-white">
              “{active}”
            </span>{" "}
            and all of its indexed chunks. This cannot be undone.
          </p>
        </Modal>
      )}
    </div>
  );
}

function ThemeToggle() {
  const [theme, toggle] = useTheme();
  return (
    <button
      onClick={toggle}
      className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white"
      title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      {theme === "dark" ? (
        <IconSun className="h-4 w-4" />
      ) : (
        <IconMoon className="h-4 w-4" />
      )}
      <span>{theme === "dark" ? "Light" : "Dark"} mode</span>
    </button>
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col gap-5 p-4">
      <Brand />
      <CollectionSwitcher />
      <nav className="space-y-0.5">
        {links.map((l) => {
          const Icon = l.icon;
          return (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
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
                  {l.label}
                </>
              )}
            </NavLink>
          );
        })}
      </nav>
      <div className="mt-auto space-y-2 border-t border-slate-200 pt-3 dark:border-slate-800">
        <ThemeToggle />
        <p className="px-3 text-[11px] leading-relaxed text-slate-400 dark:text-slate-600">
          Domain knowledge workflow platform
        </p>
      </div>
    </div>
  );
}

export default function App() {
  const [open, setOpen] = useState(false);
  const location = useLocation();

  // Close the mobile drawer whenever the route changes.
  useEffect(() => setOpen(false), [location.pathname]);

  return (
    <div className="min-h-screen lg:flex">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 lg:block">
        <Sidebar />
      </aside>

      {/* Mobile top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-slate-200 bg-white/80 px-4 py-3 backdrop-blur dark:border-slate-800 dark:bg-slate-900/80 lg:hidden">
        <Brand />
        <IconButton onClick={() => setOpen(true)} aria-label="Open menu">
          <IconMenu />
        </IconButton>
      </header>

      {/* Mobile drawer */}
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm animate-fade-in"
            onClick={() => setOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 w-72 max-w-[80vw] animate-fade-in border-r border-slate-200 bg-white shadow-xl dark:border-slate-800 dark:bg-slate-900">
            <div className="flex justify-end p-2">
              <IconButton onClick={() => setOpen(false)} aria-label="Close menu">
                <IconClose />
              </IconButton>
            </div>
            <Sidebar onNavigate={() => setOpen(false)} />
          </div>
        </div>
      )}

      <main className="flex-1">
        <div className="mx-auto w-full max-w-5xl p-5 sm:p-8">
          <div key={location.pathname} className="animate-fade-in-up">
            <Outlet />
          </div>
        </div>
      </main>
    </div>
  );
}
