import { useEffect, useState } from "react";
import { api, Article, AUDIENCES, EvidenceEntry, Item, KNOWLEDGE_TYPES } from "../api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Label,
  Modal,
  PageHeader,
  Select,
  Skeleton,
  Textarea,
  useToast,
} from "../components/ui";
import { IconCheck, IconClose, IconPlus, IconReview } from "../components/icons";

const PAGE = 25;
const STATUSES = ["pending", "approved", "rejected"] as const;
type Status = (typeof STATUSES)[number];

const STATUS_TONE: Record<Status, "amber" | "green" | "red"> = {
  pending: "amber",
  approved: "green",
  rejected: "red",
};

const EVIDENCE_TONE: Record<string, "green" | "amber" | "red"> = {
  verified: "green",
  partial: "amber",
  unverified: "red",
};

export default function Review() {
  const [status, setStatus] = useState<Status>("pending");
  const [items, setItems] = useState<Item[] | null>(null);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const toast = useToast();

  function load() {
    setItems(null);
    api
      .items(PAGE, offset, null, { review_status: status })
      .then(setItems)
      .catch((e) => {
        toast.show(String(e), "red");
        setItems([]);
      });
  }

  useEffect(load, [status, offset]);

  async function act(it: Item, action: "approve" | "reject") {
    setBusy(it.id);
    try {
      if (action === "approve") await api.approveItem(it.id);
      else await api.rejectItem(it.id);
      toast.show(`Marked ${action}d`, action === "approve" ? "green" : "neutral");
      setItems((cur) => cur?.filter((x) => x.id !== it.id) ?? cur);
    } catch (e) {
      toast.show(String(e), "red");
    } finally {
      setBusy(null);
    }
  }

  const page = Math.floor(offset / PAGE) + 1;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Knowledge Review"
        subtitle="Approve, reject, or hand-author extracted domain knowledge."
        icon={<IconReview />}
        actions={
          <Button size="sm" onClick={() => setAdding(true)}>
            <IconPlus className="h-4 w-4" /> Add knowledge
          </Button>
        }
      />

      <div className="flex gap-1.5">
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => {
              setOffset(0);
              setStatus(s);
            }}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium capitalize transition-colors ${
              status === s
                ? "bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-200"
                : "text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-4">
          {!items && (
            <Card className="divide-y divide-slate-100 dark:divide-slate-800">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="space-y-2 p-3.5">
                  <Skeleton className="h-3 w-1/3" />
                  <Skeleton className="h-3 w-2/3" />
                </div>
              ))}
            </Card>
          )}

          {items && items.length === 0 && (
            <EmptyState
              icon={<IconReview className="h-6 w-6" />}
              title={`No ${status} knowledge`}
              hint={
                status === "pending"
                  ? "Turn on review mode in Settings to queue new extractions here."
                  : undefined
              }
            />
          )}

          {items && items.length > 0 && (
            <Card className="divide-y divide-slate-100 dark:divide-slate-800">
              {items.map((it) => (
                <div key={it.id} className="flex items-start justify-between gap-4 p-3.5">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      {it.metadata.knowledge_type && (
                        <Badge tone="brand">{it.metadata.knowledge_type}</Badge>
                      )}
                      {it.metadata.audience && (
                        <Badge tone="neutral">{it.metadata.audience}</Badge>
                      )}
                      {it.metadata.confidence !== undefined && (
                        <span className="text-xs text-slate-400">
                          conf {Number(it.metadata.confidence).toFixed(2)}
                        </span>
                      )}
                      {it.metadata.evidence_status &&
                        EVIDENCE_TONE[it.metadata.evidence_status] && (
                          <Badge tone={EVIDENCE_TONE[it.metadata.evidence_status]}>
                            {it.metadata.evidence_status}
                          </Badge>
                        )}
                      <span className="truncate font-mono text-xs text-slate-500 dark:text-slate-400">
                        {it.metadata.source}
                      </span>
                    </div>
                    {it.metadata.summary && (
                      <div className="mt-1 text-sm font-medium text-slate-700 dark:text-slate-200">
                        {it.metadata.summary}
                      </div>
                    )}
                    <div className="mt-0.5 truncate text-sm text-slate-500 dark:text-slate-400">
                      {it.text.slice(0, 160)}
                    </div>
                    {it.evidence && it.evidence.length > 0 && (
                      <EvidencePanel entries={it.evidence} />
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1.5">
                    {status !== "approved" && (
                      <Button
                        size="sm"
                        variant="secondary"
                        loading={busy === it.id}
                        onClick={() => act(it, "approve")}
                      >
                        <IconCheck className="h-4 w-4" /> Approve
                      </Button>
                    )}
                    {status !== "rejected" && (
                      <Button
                        size="sm"
                        variant="danger"
                        disabled={busy === it.id}
                        onClick={() => act(it, "reject")}
                      >
                        <IconClose className="h-4 w-4" /> Reject
                      </Button>
                    )}
                    <Badge tone={STATUS_TONE[status]}>{status}</Badge>
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
        </div>

        <ArticleCurationPanel />
      </div>

      {adding && (
        <AddKnowledgeModal
          onClose={() => setAdding(false)}
          onAdded={() => {
            setAdding(false);
            if (status === "approved") load();
            else toast.show("Added to approved knowledge", "green");
          }}
        />
      )}
    </div>
  );
}

function EvidencePanel({ entries }: { entries: EvidenceEntry[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-xs text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
      >
        {open ? "▾" : "▸"} Evidence ({entries.length})
      </button>
      {open && (
        <div className="mt-1 space-y-2">
          {entries.map((entry, i) => (
            <div
              key={i}
              className="rounded-md border border-slate-100 p-2 dark:border-slate-800"
            >
              {!entry.verified && (
                <div className="mb-1">
                  <Badge tone="red">unverified</Badge>
                </div>
              )}
              <code className="block whitespace-pre-wrap break-all font-mono text-xs text-slate-700 dark:text-slate-300">
                {entry.quote}
              </code>
              <div className="mt-0.5 font-mono text-xs text-slate-400">
                {entry.start_line != null
                  ? `${entry.source ?? ""}:${entry.start_line}-${entry.end_line}`
                  : "unverified"}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ArticleCurationPanel() {
  const [articles, setArticles] = useState<Article[] | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  function load() {
    setArticles(null);
    api
      .articles()
      .then((rows) =>
        setArticles(
          [...rows].sort((a, b) => b.business_relevance - a.business_relevance),
        ),
      )
      .catch((e) => {
        toast.show(String(e), "red");
        setArticles([]);
      });
  }

  useEffect(() => {
    load();
  }, []);

  async function synthesize() {
    setBusy(true);
    try {
      await api.createTask("synthesize", {});
      toast.show("Synthesis queued in Task Center", "green");
    } catch (e) {
      toast.show(String(e), "red");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="h-fit p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">
            Article Curation
          </h3>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Business-meaning articles synthesized from approved knowledge.
          </p>
        </div>
        <Button size="sm" variant="secondary" onClick={synthesize} loading={busy}>
          Synthesize articles
        </Button>
      </div>

      {!articles && (
        <div className="mt-4 space-y-2">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-4 w-3/4" />
        </div>
      )}

      {articles && articles.length === 0 && (
        <div className="mt-4 text-sm text-slate-500 dark:text-slate-400">
          No synthesized articles yet.
        </div>
      )}

      {articles && articles.length > 0 && (
        <div className="mt-4 space-y-3">
          {articles.slice(0, 5).map((article) => (
            <article
              key={article.id}
              className="border-t border-slate-100 pt-3 first:border-t-0 first:pt-0 dark:border-slate-800"
            >
              <div className="font-medium text-slate-800 dark:text-slate-100">
                {article.title}
              </div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                <Badge tone="brand">
                  relevance {Math.round(article.business_relevance * 100)}%
                </Badge>
                {article.cross_validated && (
                  <Badge tone="green">cross-validated</Badge>
                )}
                <Badge tone="neutral">
                  {article.sources.length} {article.sources.length === 1 ? "source" : "sources"}
                </Badge>
              </div>
              <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                {article.topic}
              </div>
            </article>
          ))}
        </div>
      )}
    </Card>
  );
}

function AddKnowledgeModal({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: () => void;
}) {
  const [text, setText] = useState("");
  const [knowledgeType, setKnowledgeType] = useState("");
  const [audience, setAudience] = useState("");
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function submit() {
    if (!text.trim()) return;
    setBusy(true);
    try {
      await api.addItem({
        text: text.trim(),
        knowledge_type: knowledgeType || undefined,
        audience: audience ? [audience] : undefined,
        tags: tags
          ? tags.split(",").map((t) => t.trim()).filter(Boolean)
          : undefined,
      });
      toast.show("Knowledge added", "green");
      onAdded();
    } catch (e) {
      toast.show(String(e), "red");
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Add knowledge"
      onClose={() => !busy && onClose()}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!text.trim()}>
            Add
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <Label>Content</Label>
          <Textarea
            autoFocus
            className="mt-1.5 h-28"
            placeholder="A fact, constraint, or procedure agents should know…"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Type</Label>
            <Select
              className="mt-1.5"
              value={knowledgeType}
              onChange={(e) => setKnowledgeType(e.target.value)}
            >
              <option value="">(unset)</option>
              {KNOWLEDGE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Audience</Label>
            <Select
              className="mt-1.5"
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
            >
              <option value="">(any)</option>
              {AUDIENCES.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div>
          <Label>Tags (comma separated)</Label>
          <Input
            className="mt-1.5"
            placeholder="billing, onboarding"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
          />
        </div>
        <p className="text-xs text-slate-400 dark:text-slate-500">
          Manually added knowledge is stored as approved.
        </p>
      </div>
    </Modal>
  );
}
