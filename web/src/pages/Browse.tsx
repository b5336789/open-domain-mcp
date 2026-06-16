import { useEffect, useState } from "react";
import { api, Item } from "../api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  IconButton,
  Input,
  Label,
  Modal,
  PageHeader,
  Select,
  Skeleton,
  useToast,
} from "../components/ui";
import { IconBrowse, IconEdit, IconTrash } from "../components/icons";

const PAGE = 25;

export default function Browse() {
  const [items, setItems] = useState<Item[] | null>(null);
  const [offset, setOffset] = useState(0);
  const [kind, setKind] = useState("");
  const [editing, setEditing] = useState<Item | null>(null);
  const [confirm, setConfirm] = useState<Item | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  function load() {
    setItems(null);
    api
      .items(PAGE, offset, kind || null)
      .then(setItems)
      .catch((e) => {
        toast.show(String(e), "red");
        setItems([]);
      });
  }

  useEffect(load, [offset, kind]);

  async function save() {
    if (!editing) return;
    setBusy(true);
    try {
      await api.updateItem(editing.id, editing.metadata);
      toast.show("Metadata saved", "green");
      setEditing(null);
      load();
    } catch (e) {
      toast.show(String(e), "red");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!confirm) return;
    setBusy(true);
    try {
      await api.deleteItem(confirm.id);
      toast.show("Item deleted", "neutral");
      setConfirm(null);
      load();
    } catch (e) {
      toast.show(String(e), "red");
    } finally {
      setBusy(false);
    }
  }

  const page = Math.floor(offset / PAGE) + 1;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Browse / Edit"
        subtitle="Inspect, edit metadata, or remove stored chunks."
        icon={<IconBrowse />}
        actions={
          <Select
            className="w-36"
            value={kind}
            onChange={(e) => {
              setOffset(0);
              setKind(e.target.value);
            }}
          >
            <option value="">all kinds</option>
            <option value="code">code</option>
            <option value="text">text</option>
          </Select>
        }
      />

      {!items && (
        <Card className="divide-y divide-slate-100 dark:divide-slate-800">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="space-y-2 p-3.5">
              <Skeleton className="h-3 w-1/3" />
              <Skeleton className="h-3 w-2/3" />
            </div>
          ))}
        </Card>
      )}

      {items && items.length === 0 && (
        <EmptyState
          icon={<IconBrowse className="h-6 w-6" />}
          title="Nothing here yet"
          hint="Ingest some files to populate this knowledge base."
        />
      )}

      {items && items.length > 0 && (
        <Card className="divide-y divide-slate-100 dark:divide-slate-800">
          {items.map((it) => (
            <div
              key={it.id}
              className="group flex items-start justify-between gap-4 p-3.5 transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/40"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  {it.metadata.kind && (
                    <Badge
                      tone={it.metadata.kind === "code" ? "brand" : "neutral"}
                    >
                      {it.metadata.kind}
                    </Badge>
                  )}
                  <span className="truncate font-mono text-xs text-slate-500 dark:text-slate-400">
                    {it.metadata.source}
                    {it.metadata.symbol ? `::${it.metadata.symbol}` : ""}
                  </span>
                </div>
                <div className="mt-1 truncate text-sm text-slate-600 dark:text-slate-300">
                  {it.text.slice(0, 140)}
                </div>
              </div>
              <div className="flex shrink-0 gap-1 opacity-60 transition-opacity group-hover:opacity-100">
                <IconButton
                  aria-label="Edit"
                  title="Edit metadata"
                  onClick={() => setEditing(it)}
                >
                  <IconEdit className="h-4 w-4" />
                </IconButton>
                <IconButton
                  aria-label="Delete"
                  title="Delete"
                  className="hover:text-red-600 dark:hover:text-red-400"
                  onClick={() => setConfirm(it)}
                >
                  <IconTrash className="h-4 w-4" />
                </IconButton>
              </div>
            </div>
          ))}
        </Card>
      )}

      <div className="flex items-center justify-between">
        <span className="text-sm text-slate-400">Page {page}</span>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            Prev
          </Button>
          <Button
            variant="secondary"
            size="sm"
            disabled={!items || items.length < PAGE}
            onClick={() => setOffset(offset + PAGE)}
          >
            Next
          </Button>
        </div>
      </div>

      {editing && (
        <Modal
          title="Edit metadata"
          onClose={() => !busy && setEditing(null)}
          footer={
            <>
              <Button
                variant="secondary"
                onClick={() => setEditing(null)}
                disabled={busy}
              >
                Cancel
              </Button>
              <Button onClick={save} loading={busy}>
                Save
              </Button>
            </>
          }
        >
          <pre className="scroll-thin mb-4 max-h-32 overflow-auto rounded-lg bg-slate-50 p-3 font-mono text-xs text-slate-600 dark:bg-slate-950/60 dark:text-slate-300">
            {editing.text.slice(0, 240)}
          </pre>
          <div className="space-y-2.5">
            {Object.entries(editing.metadata).map(([k, v]) => (
              <div key={k} className="flex items-center gap-3">
                <Label>
                  <span className="block w-24 truncate text-slate-500">{k}</span>
                </Label>
                <Input
                  className="h-9 flex-1"
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
          </div>
        </Modal>
      )}

      {confirm && (
        <Modal
          title="Delete item?"
          onClose={() => !busy && setConfirm(null)}
          footer={
            <>
              <Button
                variant="secondary"
                onClick={() => setConfirm(null)}
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
            This permanently removes the chunk
            <span className="mx-1 font-mono text-xs">
              {confirm.metadata.source}
              {confirm.metadata.symbol ? `::${confirm.metadata.symbol}` : ""}
            </span>
            from the knowledge base.
          </p>
        </Modal>
      )}
    </div>
  );
}
