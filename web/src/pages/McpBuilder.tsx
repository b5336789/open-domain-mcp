import { useEffect, useState } from "react";
import {
  api,
  getActiveCollection,
  McpEndpoint,
  QualityEvidenceResponse,
  ReadinessStatus,
  ViewsMap,
} from "../api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  IconButton,
  Label,
  Modal,
  PageHeader,
  Skeleton,
  Textarea,
  useToast,
} from "../components/ui";
import { IconBuilder, IconCheck, IconCopy } from "../components/icons";

// Retrieval-policy settings the builder exposes (a subset of editable settings).
const POLICY_KEYS = ["search_mode", "rerank_enabled", "retrieve_approved_only"] as const;

export default function McpBuilder() {
  const [views, setViews] = useState<ViewsMap | null>(null);
  const [policy, setPolicy] = useState<Record<string, string | number | boolean>>({});
  const [saving, setSaving] = useState(false);
  const [published, setPublished] = useState<string | null>(null);
  const [endpoints, setEndpoints] = useState<McpEndpoint[] | null>(null);
  const [quality, setQuality] = useState<QualityEvidenceResponse | null>(null);
  const toast = useToast();

  useEffect(() => {
    api.views().then(setViews).catch((e) => toast.show(String(e), "red"));
    api
      .mcpEndpoints()
      .then(setEndpoints)
      .catch((e) => toast.show(String(e), "red"));
    api
      .qualityEvidence()
      .then(setQuality)
      .catch((e) => toast.show(String(e), "red"));
    api.getSettings().then((s) => {
      const p: Record<string, string | number | boolean> = {};
      for (const k of POLICY_KEYS) if (k in s.editable) p[k] = s.editable[k];
      setPolicy(p);
    });
  }, []);

  // Replace a single endpoint row immutably after a publish/unpublish toggle.
  function updateEndpoint(next: McpEndpoint) {
    setEndpoints((prev) =>
      prev ? prev.map((e) => (e.view === next.view ? next : e)) : prev,
    );
  }

  async function savePolicy() {
    setSaving(true);
    try {
      await api.patchSettings(policy);
      setQuality(await api.qualityEvidence());
      toast.show("Retrieval policy saved", "green");
    } catch (e) {
      toast.show(String(e), "red");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="MCP Publish"
        subtitle="Publish role-specific MCP views with quality evidence and auditable decisions."
        icon={<IconBuilder />}
      />

      <QualityGatePanel quality={quality} />

      <Card className="space-y-4 p-5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          Retrieval policy
        </h3>
        <Toggle
          label="Approved knowledge only"
          hint="Views and search return only reviewed-and-approved knowledge."
          value={!!policy.retrieve_approved_only}
          onChange={(v) => setPolicy({ ...policy, retrieve_approved_only: v })}
        />
        <Toggle
          label="Cross-encoder re-ranking"
          hint="Re-rank fused candidates for higher precision (slower)."
          value={!!policy.rerank_enabled}
          onChange={(v) => setPolicy({ ...policy, rerank_enabled: v })}
        />
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-sm font-medium text-slate-700 dark:text-slate-300">
              Search mode
            </div>
            <div className="text-xs text-slate-400">hybrid fuses dense + BM25</div>
          </div>
          <select
            className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
            value={String(policy.search_mode ?? "hybrid")}
            onChange={(e) => setPolicy({ ...policy, search_mode: e.target.value })}
          >
            <option value="hybrid">hybrid</option>
            <option value="vector">vector</option>
          </select>
        </div>
        <div className="flex justify-end">
          <Button onClick={savePolicy} loading={saving}>
            Save policy
          </Button>
        </div>
      </Card>

      <Card className="space-y-4 p-5">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            HTTP endpoints
          </h3>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Publish a view to serve it live over HTTP (SSE) at its endpoint URL.
          </p>
        </div>

        {!endpoints && (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        )}

        {endpoints && endpoints.length === 0 && (
          <EmptyState
            title="No MCP views available"
            hint="Define a view to publish it as an HTTP endpoint."
          />
        )}

        {endpoints && endpoints.length > 0 && (
          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            {endpoints.map((endpoint) => (
              <EndpointRow
                key={endpoint.view}
                endpoint={endpoint}
                quality={quality}
                onChange={updateEndpoint}
              />
            ))}
          </div>
        )}
      </Card>

      {!views && (
        <div className="grid gap-4 sm:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i} className="space-y-3 p-5">
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-3 w-2/3" />
            </Card>
          ))}
        </div>
      )}

      {views && (
        <div className="grid gap-4 sm:grid-cols-2">
          {Object.entries(views).map(([name, spec]) => (
            <Card key={name} className="flex flex-col gap-3 p-5">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-semibold text-slate-900 dark:text-white">
                    {spec.title}
                  </h3>
                  <Badge tone="brand">{name}</Badge>
                </div>
                <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                  {spec.purpose}
                </p>
              </div>
              <div className="space-y-1.5">
                {spec.tools.map((t) => (
                  <div key={t.name} className="flex items-center gap-2 text-sm">
                    <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-brand-700 dark:bg-slate-800 dark:text-brand-300">
                      {t.name}
                    </code>
                    {Object.entries(t.filters).map(([k, v]) => (
                      <Badge key={k} tone="neutral">
                        {k}={String(v)}
                      </Badge>
                    ))}
                  </div>
                ))}
              </div>
              <div className="mt-auto">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => setPublished(published === name ? null : name)}
                >
                  {published === name ? "Hide" : "Publish"}
                </Button>
                {published === name && <PublishSnippet view={name} />}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function Toggle({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <div className="text-sm font-medium text-slate-700 dark:text-slate-300">
          {label}
        </div>
        <div className="text-xs text-slate-400">{hint}</div>
      </div>
      <button
        role="switch"
        aria-checked={value}
        onClick={() => onChange(!value)}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
          value ? "bg-brand-600" : "bg-slate-200 dark:bg-slate-700"
        }`}
      >
        <span
          className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
            value ? "translate-x-5" : "translate-x-0.5"
          }`}
        />
      </button>
    </div>
  );
}

function QualityGatePanel({
  quality,
}: {
  quality: QualityEvidenceResponse | null;
}) {
  if (!quality) {
    return (
      <Card className="space-y-3 p-5">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-3 w-2/3" />
      </Card>
    );
  }

  return (
    <Card className="space-y-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Publish readiness
          </h3>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            {quality.next_action}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={statusTone(quality.status)}>{quality.status}</Badge>
          <span className="text-sm font-semibold text-slate-900 dark:text-white">
            {quality.score}
          </span>
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        {quality.evidence.map((gate) => (
          <div
            key={gate.id}
            className="rounded-lg border border-slate-100 p-3 dark:border-slate-800"
          >
            <div className="flex items-center justify-between gap-2">
              <h4 className="text-sm font-medium text-slate-900 dark:text-white">
                {gate.gate}
              </h4>
              <Badge tone={statusTone(gate.status)}>{gate.status}</Badge>
            </div>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              {gate.summary}
            </p>
          </div>
        ))}
      </div>
    </Card>
  );
}

interface EndpointRowProps {
  endpoint: McpEndpoint;
  quality: QualityEvidenceResponse | null;
  onChange: (next: McpEndpoint) => void;
}

function EndpointRow({ endpoint, quality, onChange }: EndpointRowProps) {
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const toast = useToast();

  function copy() {
    navigator.clipboard.writeText(endpoint.url).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => toast.show("Copy failed", "red"),
    );
  }

  async function toggle() {
    if (!endpoint.published && needsOverride(quality?.status)) {
      setOverrideReason("");
      setOverrideOpen(true);
      return;
    }
    await publishOrUnpublish("");
  }

  async function publishOrUnpublish(reason: string) {
    setBusy(true);
    try {
      if (endpoint.published) {
        const next = await api.unpublishMcp(endpoint.view);
        onChange(next);
        toast.show(`Unpublished ${endpoint.title}`, "neutral");
      } else {
        const next = await api.publishMcp(endpoint.view, reason);
        onChange(next);
        toast.show(`Published ${next.title}`, "green");
        setOverrideOpen(false);
      }
    } catch (e) {
      toast.show(String(e), "red");
    } finally {
      setBusy(false);
    }
  }

  async function submitOverride() {
    const reason = overrideReason.trim();
    if (!reason) return;
    await publishOrUnpublish(reason);
  }

  return (
    <div className="flex flex-wrap items-start gap-3 py-3">
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex items-center gap-2">
          <span className="font-medium text-slate-900 dark:text-white">
            {endpoint.title}
          </span>
          <Badge tone={endpoint.published ? "green" : "neutral"}>
            {endpoint.published ? "published" : "unpublished"}
          </Badge>
        </div>
        <div className="mt-1 flex items-center gap-1.5">
          <code className="scroll-thin overflow-auto rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {endpoint.url}
          </code>
          <IconButton
            onClick={copy}
            aria-label="Copy endpoint URL"
            className="h-7 w-7 shrink-0"
          >
            {copied ? (
              <IconCheck className="h-3.5 w-3.5" />
            ) : (
              <IconCopy className="h-3.5 w-3.5" />
            )}
          </IconButton>
        </div>
        {endpoint.latest_decision ? (
          <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-500 dark:bg-slate-950/50 dark:text-slate-400">
            <div className="font-medium text-slate-700 dark:text-slate-200">
              Latest decision: {endpoint.latest_decision.action}
            </div>
            <div>
              Readiness {endpoint.latest_decision.readiness_status} · score{" "}
              {endpoint.latest_decision.readiness_score}
            </div>
            {endpoint.latest_decision.override_reason && (
              <div className="mt-1">{endpoint.latest_decision.override_reason}</div>
            )}
          </div>
        ) : (
          <div className="text-xs text-slate-400">No publish decisions yet.</div>
        )}
        {endpoint.validation && (
          <div className="rounded-lg border border-slate-100 p-3 text-xs dark:border-slate-800">
            <div className="font-medium text-slate-700 dark:text-slate-200">
              Validation {endpoint.validation.status}
            </div>
            <div className="mt-1 text-slate-500 dark:text-slate-400">
              {endpoint.validation.passed} passed · {endpoint.validation.failed} failed ·{" "}
              {endpoint.validation.scenario_count} scenarios
            </div>
            {endpoint.validation.latest_run && (
              <div className="mt-1 text-slate-500 dark:text-slate-400">
                Latest run {formatTimestamp(endpoint.validation.latest_run.created_at)}
              </div>
            )}
          </div>
        )}
      </div>
      <Button
        size="sm"
        variant={endpoint.published ? "danger" : "primary"}
        loading={busy}
        disabled={busy}
        onClick={toggle}
      >
        {endpoint.published ? "Unpublish" : "Publish"}
      </Button>
      {overrideOpen && (
        <Modal
          title="Publish override"
          onClose={() => !busy && setOverrideOpen(false)}
          footer={
            <>
              <Button
                variant="secondary"
                onClick={() => setOverrideOpen(false)}
                disabled={busy}
              >
                Cancel
              </Button>
              <Button
                onClick={submitOverride}
                loading={busy}
                disabled={!overrideReason.trim()}
              >
                Publish with override
              </Button>
            </>
          }
        >
          <div className="space-y-3">
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Current quality evidence is {quality?.status ?? "blocked"}.
            </p>
            <Label>
              Override reason
              <Textarea
                className="mt-1.5 min-h-24"
                value={overrideReason}
                onChange={(e) => setOverrideReason(e.target.value)}
                placeholder="Explain why this view can be published before all gates are ready."
              />
            </Label>
          </div>
        </Modal>
      )}
    </div>
  );
}

function needsOverride(status: ReadinessStatus | undefined): boolean {
  return status !== "ready" && status !== "published";
}

function statusTone(status: ReadinessStatus) {
  if (status === "ready" || status === "published") return "green";
  if (status === "blocked") return "red";
  if (status === "validating") return "amber";
  return "amber";
}

function formatTimestamp(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

function PublishSnippet({ view }: { view: string }) {
  const collection = getActiveCollection();
  const colArg = collection ? ` --collection ${collection}` : "";
  const command = `opendomainmcp-view --view ${view}${colArg}`;
  const config = JSON.stringify(
    {
      mcpServers: {
        [`opendomainmcp-${view}`]: {
          command: "opendomainmcp-view",
          args: ["--view", view, ...(collection ? ["--collection", collection] : [])],
        },
      },
    },
    null,
    2
  );
  const [copied, setCopied] = useState(false);
  const toast = useToast();

  function copy() {
    navigator.clipboard.writeText(config).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => toast.show("Copy failed", "red")
    );
  }

  return (
    <div className="mt-3 space-y-2">
      <div className="text-xs font-medium text-slate-500">Local (stdio)</div>
      <pre className="scroll-thin overflow-auto rounded-lg bg-slate-50 p-3 font-mono text-xs text-slate-700 dark:bg-slate-950/60 dark:text-slate-300">
        {command}
      </pre>
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium text-slate-500">MCP client config</div>
        <button
          onClick={copy}
          className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
        >
          {copied ? <IconCheck className="h-3.5 w-3.5" /> : <IconCopy className="h-3.5 w-3.5" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="scroll-thin max-h-40 overflow-auto rounded-lg bg-slate-50 p-3 font-mono text-xs text-slate-700 dark:bg-slate-950/60 dark:text-slate-300">
        {config}
      </pre>
    </div>
  );
}
