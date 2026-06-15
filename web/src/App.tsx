import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api, Collection, getActiveCollection, setActiveCollection } from "./api";

const links = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/ingest", label: "Ingest" },
  { to: "/explore", label: "Explore" },
  { to: "/ask", label: "Ask" },
  { to: "/browse", label: "Browse / Edit" },
  { to: "/settings", label: "Settings" },
];

function CollectionSwitcher() {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [active, setActive] = useState<string>("");

  useEffect(() => {
    api.collections().then((d) => {
      setCollections(d.collections);
      setActive(getActiveCollection() ?? d.active);
    });
  }, []);

  function choose(name: string) {
    setActiveCollection(name);
    window.location.reload();
  }

  async function create() {
    const name = window.prompt("New knowledge base name:");
    if (!name) return;
    await api.createCollection(name);
    choose(name);
  }

  return (
    <div className="mb-4">
      <label className="block text-xs uppercase tracking-wide text-slate-400 mb-1">
        Knowledge base
      </label>
      <div className="flex gap-1">
        <select
          className="flex-1 min-w-0 rounded bg-slate-800 text-slate-100 text-sm px-2 py-1.5 border border-slate-700"
          value={active}
          onChange={(e) => choose(e.target.value)}
        >
          {collections.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name} ({c.count})
            </option>
          ))}
        </select>
        <button
          onClick={create}
          title="New knowledge base"
          className="rounded bg-slate-800 border border-slate-700 px-2 text-slate-200 hover:bg-slate-700"
        >
          +
        </button>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <div className="min-h-screen flex">
      <aside className="w-56 bg-slate-900 text-slate-100 p-4 space-y-1">
        <h1 className="text-lg font-semibold mb-4">openDomainMcp</h1>
        <CollectionSwitcher />
        {links.map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            end={l.end}
            className={({ isActive }) =>
              `block rounded px-3 py-2 text-sm ${
                isActive ? "bg-slate-700 font-medium" : "hover:bg-slate-800"
              }`
            }
          >
            {l.label}
          </NavLink>
        ))}
        <p className="pt-6 text-xs text-slate-400">
          Domain knowledge workflow platform
        </p>
      </aside>
      <main className="flex-1 p-8 max-w-5xl">
        <Outlet />
      </main>
    </div>
  );
}
