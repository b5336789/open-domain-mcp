import { useEffect, useState } from "react";
import { api, SettingsView } from "../api";
import {
  Button,
  Card,
  Input,
  PageHeader,
  Skeleton,
  useToast,
} from "../components/ui";
import { IconSettings } from "../components/icons";

function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function Settings() {
  const [view, setView] = useState<SettingsView | null>(null);
  const [draft, setDraft] = useState<Record<string, string | number | boolean>>(
    {},
  );
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api.getSettings().then((v) => {
      setView(v);
      setDraft(v.editable);
    });
  }, []);

  async function save() {
    setSaving(true);
    try {
      await api.patchSettings(draft);
      toast.show("Settings saved — new ingests use them", "green");
    } catch (e) {
      toast.show(String(e), "red");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <PageHeader
        title="Settings"
        subtitle="Runtime configuration. Persists to settings.json in the data dir."
        icon={<IconSettings />}
      />

      {!view ? (
        <Card className="space-y-4 p-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3">
              <Skeleton className="h-4 w-44" />
              <Skeleton className="h-9 flex-1" />
            </div>
          ))}
        </Card>
      ) : (
        <>
          <Card className="divide-y divide-slate-100 p-0 dark:divide-slate-800">
            {Object.entries(draft).map(([key, value]) => (
              <div
                key={key}
                className="flex items-center justify-between gap-4 px-5 py-3.5"
              >
                <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
                  {humanize(key)}
                </label>
                {typeof value === "boolean" ? (
                  <button
                    role="switch"
                    aria-checked={value}
                    onClick={() => setDraft({ ...draft, [key]: !value })}
                    className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
                      value
                        ? "bg-brand-600"
                        : "bg-slate-200 dark:bg-slate-700"
                    }`}
                  >
                    <span
                      className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                        value ? "translate-x-5" : "translate-x-0.5"
                      }`}
                    />
                  </button>
                ) : (
                  <Input
                    className="h-9 w-56"
                    value={String(value)}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        [key]:
                          typeof value === "number"
                            ? Number(e.target.value)
                            : e.target.value,
                      })
                    }
                  />
                )}
              </div>
            ))}
          </Card>

          <div className="flex justify-end">
            <Button onClick={save} loading={saving} className="px-6">
              Save settings
            </Button>
          </div>

          <Card className="space-y-2 p-5 text-sm">
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
              Environment
            </h3>
            <Row label="Collection" value={view.collection} />
            <Row label="Embedder backend" value={view.embedder_backend} />
            <Row label="Data dir" value={view.data_dir} mono />
          </Card>
        </>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <span
        className={`text-slate-800 dark:text-slate-200 ${mono ? "font-mono text-xs" : ""}`}
      >
        {value}
      </span>
    </div>
  );
}
