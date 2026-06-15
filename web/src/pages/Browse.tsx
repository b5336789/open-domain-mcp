import { useEffect, useState } from "react";
import { api, Item } from "../api";

const PAGE = 25;

export default function Browse() {
  const [items, setItems] = useState<Item[]>([]);
  const [offset, setOffset] = useState(0);
  const [kind, setKind] = useState("");
  const [editing, setEditing] = useState<Item | null>(null);

  function load() {
    api.items(PAGE, offset, kind || null).then(setItems);
  }

  useEffect(load, [offset, kind]);

  async function save() {
    if (!editing) return;
    await api.updateItem(editing.id, editing.metadata);
    setEditing(null);
    load();
  }

  async function remove(id: string) {
    await api.deleteItem(id);
    load();
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold">Browse / Edit</h2>
        <select
          className="rounded border px-2 py-1"
          value={kind}
          onChange={(e) => {
            setOffset(0);
            setKind(e.target.value);
          }}
        >
          <option value="">all kinds</option>
          <option value="code">code</option>
          <option value="text">text</option>
        </select>
      </div>

      <div className="rounded-lg border bg-white divide-y">
        {items.map((it) => (
          <div key={it.id} className="p-3 flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="font-mono text-xs text-slate-500 truncate">
                {it.metadata.source}
                {it.metadata.symbol ? `::${it.metadata.symbol}` : ""}
              </div>
              <div className="text-sm truncate">{it.text.slice(0, 120)}</div>
            </div>
            <div className="flex gap-2 shrink-0">
              <button className="text-blue-600 text-sm" onClick={() => setEditing(it)}>
                edit
              </button>
              <button className="text-red-600 text-sm" onClick={() => remove(it.id)}>
                delete
              </button>
            </div>
          </div>
        ))}
        {items.length === 0 && <div className="p-4 text-slate-500">No items.</div>}
      </div>

      <div className="flex gap-2">
        <button
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE))}
          className="rounded border px-3 py-1 disabled:opacity-40"
        >
          Prev
        </button>
        <button
          disabled={items.length < PAGE}
          onClick={() => setOffset(offset + PAGE)}
          className="rounded border px-3 py-1 disabled:opacity-40"
        >
          Next
        </button>
      </div>

      {editing && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4">
          <div className="bg-white rounded-lg p-5 w-full max-w-lg space-y-3">
            <h3 className="font-semibold">Edit metadata</h3>
            <pre className="text-xs bg-slate-50 p-2 rounded max-h-32 overflow-auto">
              {editing.text.slice(0, 240)}
            </pre>
            {Object.entries(editing.metadata).map(([k, v]) => (
              <div key={k} className="flex gap-2 items-center">
                <label className="w-24 text-sm text-slate-500">{k}</label>
                <input
                  className="flex-1 rounded border px-2 py-1 text-sm"
                  value={String(v)}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      metadata: { ...editing.metadata, [k]: e.target.value },
                    })
                  }
                />
              </div>
            ))}
            <div className="flex justify-end gap-2 pt-2">
              <button className="rounded border px-3 py-1" onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button className="rounded bg-slate-900 text-white px-3 py-1" onClick={save}>
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
