"use client";

import { useState } from "react";
import { API_URL, api, errorMessage, formatDate } from "@/lib/api";
import { ErrorNote, ghostButtonClass } from "./ui";

function downloadText(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function ExportButtons({
  reportId,
  title,
  markdown,
  shared = false,
}: {
  reportId: string;
  title: string;
  markdown: string;
  /** Public share view: only the report itself is available, so offer a local .md download. */
  shared?: boolean;
}) {
  const [link, setLink] = useState<{ url: string; expires_at: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function createLink() {
    setBusy(true);
    setError(null);
    try {
      setLink(await api(`/reports/${reportId}/share`, { method: "POST" }));
      setCopied(false);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(true);
    } catch {
      setError("Could not copy automatically. Select the link and copy it.");
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {shared ? (
          <button
            type="button"
            className={ghostButtonClass}
            onClick={() => downloadText(`${title.replace(/[^\w]+/g, "-").toLowerCase() || "report"}.md`, markdown)}
          >
            Download .md
          </button>
        ) : (
          <>
            <a className={ghostButtonClass} href={`${API_URL}/reports/${reportId}/export.pdf`}>
              Download PDF
            </a>
            <a className={ghostButtonClass} href={`${API_URL}/reports/${reportId}/export.md`}>
              Download .md
            </a>
            <button type="button" className={ghostButtonClass} onClick={createLink} disabled={busy}>
              {busy ? "Creating…" : link ? "New share link" : "Copy share link"}
            </button>
          </>
        )}
      </div>

      {link && (
        <div className="space-y-1 rounded-md border border-line bg-background p-3 text-sm">
          <div className="flex gap-2">
            <input
              readOnly
              value={link.url}
              onFocus={(e) => e.currentTarget.select()}
              aria-label="Share link"
              className="min-w-0 flex-1 rounded border border-line bg-surface px-2 py-1 font-mono text-xs"
            />
            <button type="button" className={ghostButtonClass} onClick={copy}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <p className="text-xs text-muted">Read-only link. Expires {formatDate(link.expires_at)}.</p>
        </div>
      )}
      <ErrorNote message={error} />
    </div>
  );
}
