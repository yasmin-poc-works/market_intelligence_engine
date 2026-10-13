"use client";

import Link from "next/link";
import { useCallback, useEffect, useReducer, useState } from "react";
import { API_URL, api, errorMessage, formatDuration, type RunInfo, type RunStatus } from "@/lib/api";
import BriefPanel from "./BriefPanel";
import { buttonClass, Card, ErrorNote } from "./ui";

type TaskState = "queued" | "running" | "done" | "failed";

interface Task {
  id: string;
  title: string;
  state: TaskState;
  detail?: string;
}

interface LogLine {
  eventId: string;
  at: string;
  text: string;
  tone: "info" | "good" | "bad";
}

interface State {
  status: RunStatus | null;
  tasks: Task[];
  log: LogLine[];
  seen: Set<string>;
}

interface StreamEvent {
  eventId: string;
  name: string;
  data: { subtask_id: string | null; payload: Record<string, unknown>; at: string };
}

const STAGES: { status: RunStatus; label: string }[] = [
  { status: "PLANNING", label: "Planning" },
  { status: "RESEARCHING", label: "Researching" },
  { status: "SYNTHESIZING", label: "Synthesizing" },
  { status: "WRITING", label: "Writing" },
  { status: "FACT_CHECKING", label: "Fact-checking" },
];

const EVENT_NAMES = [
  "status",
  "task_graph_created",
  "subtask_started",
  "subtask_done",
  "subtask_failed",
  "clarification_requested",
];

const TERMINAL: RunStatus[] = ["COMPLETE", "FAILED", "NEEDS_CLARIFICATION"];

function reduce(state: State, ev: StreamEvent): State {
  if (state.seen.has(ev.eventId)) return state;
  const seen = new Set(state.seen).add(ev.eventId);
  const { payload, subtask_id: sid, at } = ev.data;
  let { status, tasks } = state;
  let text = ev.name;
  let tone: LogLine["tone"] = "info";

  const taskTitle = (id: string | null) => tasks.find((t) => t.id === id)?.title ?? id ?? "task";
  const setTask = (state_: TaskState, detail?: string) => {
    tasks = tasks.map((t) => (t.id === sid ? { ...t, state: state_, detail } : t));
  };

  switch (ev.name) {
    case "status":
      status = payload.status as RunStatus;
      text = payload.error ? `${status}: ${payload.error}` : `Stage: ${String(status).toLowerCase().replace("_", "-")}`;
      if (status === "FAILED") tone = "bad";
      if (status === "COMPLETE") tone = "good";
      break;
    case "task_graph_created": {
      const subs = (payload.subtasks as { id: string; title: string }[]) ?? [];
      tasks = subs.map((s) => ({ id: s.id, title: s.title, state: "queued" }));
      text = `Plan ready: ${subs.length} research tasks`;
      break;
    }
    case "subtask_started":
      setTask("running");
      text = `Started: ${taskTitle(sid)}`;
      break;
    case "subtask_done":
      setTask("done", `${payload.refs ?? 0} sources`);
      text = `Done: ${taskTitle(sid)} (${payload.refs ?? 0} sources)`;
      tone = "good";
      break;
    case "subtask_failed":
      setTask("failed", String(payload.error ?? ""));
      text = `Failed: ${taskTitle(sid)}: ${payload.error ?? "unknown error"}`;
      tone = "bad";
      break;
    case "clarification_requested":
      text = "The planner needs more detail";
      break;
  }
  return { status, tasks, seen, log: [...state.log, { eventId: ev.eventId, at, text, tone }] };
}

/** Overall progress, 0-100: research tasks fill the middle of the bar. */
export function progressPercent(status: RunStatus | null, tasks: Task[]): number {
  switch (status) {
    case null:
      return 0;
    case "PLANNING":
      return 8;
    case "RESEARCHING": {
      const finished = tasks.filter((t) => t.state === "done" || t.state === "failed").length;
      return 10 + (tasks.length ? (finished / tasks.length) * 45 : 0);
    }
    case "SYNTHESIZING":
      return 62;
    case "WRITING":
      return 78;
    case "FACT_CHECKING":
      return 92;
    default:
      return 100;
  }
}

const TASK_STYLE: Record<TaskState, string> = {
  queued: "bg-background text-muted",
  running: "bg-warn-bg text-warn",
  done: "bg-good-bg text-good",
  failed: "bg-bad-bg text-bad",
};
const TASK_LABEL: Record<TaskState, string> = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  failed: "Failed",
};

export default function RunProgress({ runId }: { runId: string }) {
  const [state, dispatch] = useReducer(reduce, {
    status: null,
    tasks: [],
    log: [],
    seen: new Set<string>(),
  });
  const [run, setRun] = useState<RunInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [connectionLost, setConnectionLost] = useState(false);

  const loadRun = useCallback(async () => {
    try {
      setRun(await api<RunInfo>(`/research/runs/${runId}`));
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [runId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadRun();
  }, [loadRun]);

  useEffect(() => {
    const es = new EventSource(`${API_URL}/research/status/${runId}`);
    const handler = (name: string) => (msg: MessageEvent) => {
      setConnectionLost(false);
      const data = JSON.parse(msg.data);
      dispatch({ eventId: msg.lastEventId, name, data });
      if (name === "status" && TERMINAL.includes(data.payload.status)) {
        es.close();
        loadRun();
      }
    };
    for (const name of EVENT_NAMES) es.addEventListener(name, handler(name));
    es.onerror = () => setConnectionLost(true);
    return () => es.close();
  }, [runId, loadRun]);

  const status = state.status ?? run?.status ?? null;
  const finished = status !== null && TERMINAL.includes(status);

  useEffect(() => {
    if (finished) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [finished]);

  const started = run ? new Date(run.created_at).getTime() : null;
  const ended = run?.completed_at ? new Date(run.completed_at).getTime() : now;
  const elapsed = started ? (ended - started) / 1000 : 0;
  const percent = progressPercent(status, state.tasks);
  const stageIndex = STAGES.findIndex((s) => s.status === status);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Research in progress</h1>
        {run && <p className="mt-1 line-clamp-2 text-sm text-muted">{run.brief}</p>}
      </div>

      <ErrorNote message={error} />

      <Card>
        <div className="mb-2 flex items-center justify-between text-sm">
          <span className="font-medium">
            {status === "COMPLETE"
              ? "Complete"
              : status === "FAILED"
                ? "Failed"
                : status === "NEEDS_CLARIFICATION"
                  ? "Needs more detail"
                  : (STAGES[stageIndex]?.label ?? "Connecting…")}
          </span>
          <span className="font-mono text-muted" title="Elapsed time">
            {formatDuration(elapsed)}
          </span>
        </div>
        <div
          className="h-2 overflow-hidden rounded-full bg-background"
          role="progressbar"
          aria-valuenow={Math.round(percent)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className={`h-full rounded-full transition-all duration-500 ${status === "FAILED" ? "bg-bad" : status === "COMPLETE" ? "bg-good" : "bg-accent"}`}
            style={{ width: `${percent}%` }}
          />
        </div>
        <ol className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
          {STAGES.map((s, i) => (
            <li key={s.status} className={i <= stageIndex || status === "COMPLETE" ? "font-medium text-foreground" : ""}>
              {s.label}
            </li>
          ))}
        </ol>
        {connectionLost && !finished && (
          <p className="mt-3 text-xs text-warn">Connection lost. Reconnecting…</p>
        )}
      </Card>

      {status === "COMPLETE" && run?.report_id && (
        <Card className="flex items-center justify-between">
          <p className="text-sm">Your report is ready.</p>
          <Link href={`/reports/${run.report_id}`} className={buttonClass}>
            Open report
          </Link>
        </Card>
      )}

      {status === "FAILED" && (
        <Card className="space-y-3">
          <ErrorNote message={run?.error ?? "The run failed."} />
          <Link href="/" className="text-sm text-accent hover:underline">
            Start a new run
          </Link>
        </Card>
      )}

      {status === "NEEDS_CLARIFICATION" && run && (
        <ClarificationForm run={run} />
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="mb-3 text-sm font-semibold">Research tasks</h2>
          {state.tasks.length === 0 ? (
            <p className="text-sm text-muted">Waiting for the plan…</p>
          ) : (
            <ul className="space-y-2">
              {state.tasks.map((t) => (
                <li key={t.id} className="flex items-center justify-between gap-3 rounded border border-line px-3 py-2 text-sm">
                  <span className="min-w-0 truncate" title={t.detail && t.state === "failed" ? t.detail : t.title}>
                    {t.title}
                  </span>
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${TASK_STYLE[t.state]}`}>
                    {TASK_LABEL[t.state]}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <h2 className="mb-3 text-sm font-semibold">Agent activity</h2>
          <ul className="max-h-72 space-y-1 overflow-y-auto font-mono text-xs" aria-live="polite">
            {state.log.length === 0 && <li className="text-muted">No activity yet.</li>}
            {[...state.log].reverse().map((l) => (
              <li key={l.eventId} className="flex gap-2">
                <span className="shrink-0 text-muted">
                  {new Date(l.at).toLocaleTimeString(undefined, { hour12: false })}
                </span>
                <span className={l.tone === "bad" ? "text-bad" : l.tone === "good" ? "text-good" : ""}>{l.text}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

function ClarificationForm({ run }: { run: RunInfo }) {
  const [answer, setAnswer] = useState("");
  return (
    <Card className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold">The planner needs a bit more detail</h2>
        <p className="mt-1 text-sm">{run.clarifying_question ?? "Could you narrow down the brief?"}</p>
      </div>
      <textarea
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        rows={3}
        placeholder="Your answer"
        aria-label="Your answer to the clarifying question"
        className="w-full rounded-md border border-line bg-background px-3 py-2 text-sm outline-none focus:border-accent"
      />
      {answer.trim() ? (
        <BriefPanel initialBrief={run.brief.slice(0, 500)} clarification={answer.trim()} />
      ) : (
        <p className="text-xs text-muted">Type an answer to continue with the same brief.</p>
      )}
    </Card>
  );
}
