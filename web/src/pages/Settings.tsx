import { useEffect, useState } from "react";
import { api, SettingsView } from "../api";

export default function Settings() {
  const [view, setView] = useState<SettingsView | null>(null);
  const [draft, setDraft] = useState<Record<string, string | number | boolean>>({});
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    api.getSettings().then((v) => {
      setView(v);
      setDraft(v.editable);
    });
  }, []);

  async function save() {
    setStatus(null);
    try {
      await api.patchSettings(draft);
      setStatus("Saved. New ingests use the updated settings.");
    } catch (e) {
      setStatus(String(e));
    }
  }

  if (!view) return <p>Loading…</p>;

  return (
    <div className="space-y-6 max-w-xl">
      <h2 className="text-2xl font-semibold">Settings</h2>

      <div className="rounded-lg border bg-white p-4 space-y-3">
        {Object.entries(draft).map(([key, value]) => (
          <div key={key} className="flex items-center gap-3">
            <label className="w-48 text-sm text-slate-600">{key}</label>
            {typeof value === "boolean" ? (
              <input
                type="checkbox"
                checked={value}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.checked })}
              />
            ) : (
              <input
                className="flex-1 rounded border px-2 py-1 text-sm"
                value={String(value)}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    [key]:
                      typeof value === "number" ? Number(e.target.value) : e.target.value,
                  })
                }
              />
            )}
          </div>
        ))}
        <button onClick={save} className="rounded bg-slate-900 px-4 py-2 text-white">
          Save settings
        </button>
        {status && <p className="text-sm text-slate-600">{status}</p>}
      </div>

      <div className="rounded-lg border bg-white p-4 text-sm text-slate-600 space-y-1">
        <div>Collection: {view.collection}</div>
        <div>Embedder backend: {view.embedder_backend}</div>
        <div>Data dir: {view.data_dir}</div>
      </div>
    </div>
  );
}
