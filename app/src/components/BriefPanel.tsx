"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { API_URL, ApiError, errorMessage } from "@/lib/api";
import { buttonClass, Card, ErrorNote } from "./ui";

const MAX_CHARS = 500;
const MAX_FILES = 10;
const MAX_FILE_MB = 20;

/** Rough runtime range in minutes from what the user switched on. */
export function estimateMinutes(chars: number, files: number, web: boolean, docs: boolean) {
  let lo = 1;
  let hi = 2;
  if (web) {
    lo += 1;
    hi += 3;
  }
  if (docs && files > 0) {
    lo += Math.ceil(files / 3);
    hi += files;
  }
  if (chars > 300) hi += 1;
  return { lo, hi };
}

export default function BriefPanel({
  initialBrief = "",
  clarification,
}: {
  initialBrief?: string;
  clarification?: string;
}) {
  const router = useRouter();
  const [brief, setBrief] = useState(initialBrief);
  const [files, setFiles] = useState<File[]>([]);
  const [includeWeb, setIncludeWeb] = useState(true);
  const [includeDocs, setIncludeDocs] = useState(true);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fileNote, setFileNote] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function addFiles(list: FileList | File[]) {
    const incoming = Array.from(list);
    const notes: string[] = [];
    const accepted = incoming.filter((f) => {
      if (!f.name.toLowerCase().endsWith(".pdf")) {
        notes.push(`${f.name}: only PDF files are accepted`);
        return false;
      }
      if (f.size > MAX_FILE_MB * 1024 * 1024) {
        notes.push(`${f.name}: larger than ${MAX_FILE_MB} MB`);
        return false;
      }
      return true;
    });
    const merged = [...files];
    for (const f of accepted) {
      if (merged.some((m) => m.name === f.name && m.size === f.size)) continue;
      if (merged.length >= MAX_FILES) {
        notes.push(`At most ${MAX_FILES} files; ${f.name} was skipped`);
        continue;
      }
      merged.push(f);
    }
    setFiles(merged);
    setFileNote(notes.length ? notes.join(". ") : null);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const form = new FormData();
      form.append("brief", brief.trim());
      form.append("include_web", String(includeWeb));
      form.append("include_documents", String(includeDocs));
      if (clarification) form.append("clarification", clarification);
      if (includeDocs) files.forEach((f) => form.append("files", f));
      let res: Response;
      try {
        res = await fetch(`${API_URL}/research/run`, { method: "POST", body: form });
      } catch {
        throw new ApiError(`Cannot reach the API at ${API_URL}. Is it running?`, 0);
      }
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new ApiError(body?.error?.message ?? `Request failed (${res.status})`, res.status);
      router.push(`/runs/${body.run_id}`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  const count = brief.length;
  const { lo, hi } = estimateMinutes(count, files.length, includeWeb, includeDocs);
  const canSubmit = brief.trim().length > 0 && count <= MAX_CHARS && (includeWeb || (includeDocs && files.length > 0)) && !busy;

  return (
    <Card>
      <form onSubmit={submit} className="space-y-5">
        <div>
          <label htmlFor="brief" className="mb-1 block text-sm font-medium">
            Research brief
          </label>
          <textarea
            id="brief"
            value={brief}
            onChange={(e) => setBrief(e.target.value.slice(0, MAX_CHARS))}
            rows={5}
            maxLength={MAX_CHARS}
            placeholder="e.g. How is Acme positioned against its competitors in the European EV charging market, and what funding has it raised?"
            className="w-full resize-y rounded-md border border-line bg-background px-3 py-2 text-sm outline-none focus:border-accent"
          />
          <div
            className={`mt-1 text-right text-xs ${count >= MAX_CHARS ? "text-bad" : count > MAX_CHARS * 0.9 ? "text-warn" : "text-muted"}`}
            aria-live="polite"
          >
            {count} / {MAX_CHARS}
          </div>
        </div>

        <fieldset className="flex flex-wrap gap-6 text-sm">
          <legend className="mb-1 text-sm font-medium">Sources</legend>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={includeWeb} onChange={(e) => setIncludeWeb(e.target.checked)} />
            Web search
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={includeDocs} onChange={(e) => setIncludeDocs(e.target.checked)} />
            Uploaded documents
          </label>
        </fieldset>

        {includeDocs && (
          <div>
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                addFiles(e.dataTransfer.files);
              }}
              onClick={() => inputRef.current?.click()}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
              role="button"
              tabIndex={0}
              className={`cursor-pointer rounded-md border-2 border-dashed px-4 py-6 text-center text-sm ${dragging ? "border-accent bg-background" : "border-line text-muted"}`}
            >
              Drag PDFs here or click to browse
              <div className="mt-1 text-xs">
                Up to {MAX_FILES} files, {MAX_FILE_MB} MB each
              </div>
              <input
                ref={inputRef}
                type="file"
                accept="application/pdf,.pdf"
                multiple
                hidden
                onChange={(e) => {
                  if (e.target.files) addFiles(e.target.files);
                  e.target.value = "";
                }}
              />
            </div>
            {fileNote && <p className="mt-2 text-xs text-warn">{fileNote}</p>}
            {files.length > 0 && (
              <ul className="mt-3 space-y-1 text-sm">
                {files.map((f) => (
                  <li key={`${f.name}-${f.size}`} className="flex items-center justify-between rounded border border-line px-3 py-1.5">
                    <span className="truncate">
                      {f.name} <span className="text-xs text-muted">({(f.size / 1024 / 1024).toFixed(1)} MB)</span>
                    </span>
                    <button
                      type="button"
                      onClick={() => setFiles(files.filter((x) => x !== f))}
                      className="ml-3 text-xs text-muted hover:text-bad"
                      aria-label={`Remove ${f.name}`}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {!includeWeb && !(includeDocs && files.length > 0) && (
          <p className="text-xs text-warn">Turn on web search or add at least one PDF.</p>
        )}

        <ErrorNote message={error} />

        <div className="flex items-center justify-between gap-4">
          <p className="text-xs text-muted">
            Estimated runtime: about {lo}–{hi} minutes
          </p>
          <button type="submit" disabled={!canSubmit} className={buttonClass}>
            {busy ? "Starting…" : clarification ? "Continue research" : "Start research"}
          </button>
        </div>
      </form>
    </Card>
  );
}
