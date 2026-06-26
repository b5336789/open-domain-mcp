// web/src/components/TaskCenter.tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, TaskChild, TaskItem } from "../api";
import { Button, IconButton } from "./ui";
import { IconClose } from "./icons";

const ACTIVE = new Set(["queued", "running"]);

const STATUS_TONE: Record<string, string> = {
  queued: "text-slate-400",
  running: "text-emerald-500",
  done: "text-brand-500",
  error: "text-red-500",
  cancelled: "text-amber-500",
};

export default function TaskCenter() {
  const [open, setOpen] = useState(false);
  const [tasks, setTasks] = useState<TaskItem[]>([]);

  const refresh = useCallback(async () => {
    try {
      setTasks((await api.listTasks()).tasks);
    } catch {
      /* transient */
    }
  }, []);

  const activeCount = tasks.filter((t) => ACTIVE.has(t.status)).length;

  // Mirror "should poll" into a ref so the interval callback can read it
  // without being recreated on every activeCount change.
  const pollRef = useRef(false);
  pollRef.current = open || activeCount > 0;

  // Single stable interval; only torn down/restarted when `open` changes.
  useEffect(() => {
    refresh();
    const id = setInterval(() => {
      if (pollRef.current) refresh();
    }, 1500);
    return () => clearInterval(id);
  }, [open, refresh]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="relative inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:border-slate-300 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        title="Task Center"
      >
        Tasks
        {activeCount > 0 && (
          <span className="inline-flex h-5 min-w-[20px] items-center justify-center rounded-full bg-brand-600 px-1.5 text-xs font-semibold text-white">
            {activeCount}
          </span>
        )}
      </button>

      {/* Portal to <body>: the overlay uses position:fixed, but an ancestor with
          backdrop-blur (the sticky top bar) would otherwise become its containing
          block and clamp it to that bar's height. The portal escapes that. */}
      {open &&
        createPortal(
          <div className="fixed inset-0 z-50">
            <div
              className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm animate-fade-in"
              onClick={() => setOpen(false)}
            />
            <div className="absolute inset-y-0 right-0 flex w-[26rem] max-w-[90vw] flex-col border-l border-slate-200 bg-white shadow-xl animate-fade-in dark:border-slate-800 dark:bg-slate-900">
              <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
                <span className="font-semibold text-slate-900 dark:text-white">
                  Task Center
                </span>
                <div className="flex items-center gap-2">
                  <Button
                    variant="secondary"
                    onClick={() => api.clearTasks().then(refresh)}
                  >
                    Clear finished
                  </Button>
                  <IconButton onClick={() => setOpen(false)} aria-label="Close">
                    <IconClose />
                  </IconButton>
                </div>
              </div>
              <div className="scroll-thin flex-1 space-y-3 overflow-auto p-4">
                {tasks.length === 0 && (
                  <p className="text-sm text-slate-400">No tasks yet.</p>
                )}
                {tasks.map((t) => (
                  <TaskCard key={t.id} task={t} onChanged={refresh} />
                ))}
              </div>
            </div>
          </div>,
          document.body
        )}
    </>
  );
}

function TaskCard({ task, onChanged }: { task: TaskItem; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<TaskChild[] | null>(null);
  const pct = task.total > 0 ? Math.round((task.done / task.total) * 100) : 0;
  const active = task.status === "queued" || task.status === "running";

  async function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && children === null) {
      try {
        setChildren((await api.taskChildren(task.id, 0, 100)).children);
      } catch {
        setChildren([]);
      }
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
            {task.title}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-xs">
            <span className={STATUS_TONE[task.status] ?? "text-slate-400"}>
              {task.status}
            </span>
            <span className="text-slate-400">
              {task.done}/{task.total}
            </span>
            <span className="rounded bg-slate-100 px-1.5 text-[10px] text-slate-500 dark:bg-slate-800">
              {task.collection}
            </span>
          </div>
        </div>
        {active && (
          <Button variant="secondary" onClick={() => api.cancelTask(task.id).then(onChanged)}>
            Cancel
          </Button>
        )}
      </div>

      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div
          className="h-full bg-brand-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>

      {task.error && (
        <p className="mt-2 break-words text-xs text-red-500">{task.error}</p>
      )}

      {task.total > 0 && (
        <button
          onClick={toggle}
          className="mt-2 text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
        >
          {expanded ? "Hide" : "Show"} items
        </button>
      )}
      {expanded && children && (
        <ul className="mt-1.5 max-h-40 space-y-0.5 overflow-auto font-mono text-[11px]">
          {children.map((c, i) => (
            <li key={`${i}-${c.name}`} className="flex justify-between gap-2">
              <span className="truncate text-slate-500">{c.name}</span>
              <span className={STATUS_TONE[c.status] ?? "text-slate-400"}>{c.status}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
