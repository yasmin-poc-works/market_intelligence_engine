export const API_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

export type Confidence = "HIGH" | "MEDIUM" | "LOW";

export interface ReportSection {
  heading: string;
  body: string;
  confidence: Confidence;
}

export interface Report {
  id: string;
  run_id: string;
  title: string;
  executive_summary: string;
  sections: ReportSection[];
  gaps_and_limitations: string;
  markdown: string;
  created_at: string;
}

export interface FactCheck {
  total_checked: number;
  passed: number;
  failed: number;
  pass_rate: number;
  unverified_claims: string[];
}

export interface SourceMeta {
  url: string;
  domain: string;
  title: string;
  published_date: string | null;
  credibility_score: number;
}

export interface EvidenceItem {
  id: string;
  claim: string;
  source_ids: string[];
  confidence: Confidence;
}

export interface ReportDetail {
  id: string;
  run_id: string;
  title: string;
  created_at: string;
  markdown: string;
  report: Report | null;
  fact_check: FactCheck | null;
  sources: Record<string, SourceMeta>;
  evidence: EvidenceItem[];
}

export interface ReportListItem {
  id: string;
  run_id: string;
  title: string;
  created_at: string;
  fact_check_pass_rate: number | null;
}

export type RunStatus =
  | "PLANNING"
  | "RESEARCHING"
  | "SYNTHESIZING"
  | "WRITING"
  | "FACT_CHECKING"
  | "COMPLETE"
  | "FAILED"
  | "NEEDS_CLARIFICATION";

export interface RunInfo {
  run_id: string;
  brief: string;
  status: RunStatus;
  error: string | null;
  clarifying_question: string | null;
  documents?: string[];
  reference_urls?: string[];
  report_id: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface WatchlistItem {
  id: string;
  topic: string;
  frequency: "daily" | "weekly";
  webhook_url: string | null;
  active: boolean;
  last_run: string | null;
  next_run: string | null;
  created_at: string;
}

export interface DiffSummary {
  added: string[];
  removed: string[];
  changed: string[];
  material: boolean;
}

export interface Alert {
  id: string;
  watchlist_item_id: string;
  run_id: string | null;
  summary: string;
  diff: DiffSummary;
  read: boolean;
  created_at: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(`Cannot reach the API at ${API_URL}. Is it running?`, 0);
  }
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
    } catch {}
    throw new ApiError(message, res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : "Something went wrong";
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
