"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, errorMessage, formatDate, type ReportListItem } from "@/lib/api";
import { Card, ErrorNote } from "./ui";

export default function ReportsList({ limit }: { limit?: number }) {
  const [items, setItems] = useState<ReportListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ items: ReportListItem[] }>(`/reports?limit=${limit ?? 50}`)
      .then((r) => setItems(r.items))
      .catch((e) => setError(errorMessage(e)));
  }, [limit]);

  if (error) return <ErrorNote message={error} />;
  if (!items) return <p className="text-sm text-muted">Loading reports…</p>;
  if (items.length === 0) return <p className="text-sm text-muted">No reports yet. Start a research run to create one.</p>;

  return (
    <ul className="space-y-2">
      {items.map((r) => (
        <li key={r.id}>
          <Link href={`/reports/${r.id}`} className="block">
            <Card className="flex items-center justify-between gap-4 !p-4 hover:border-accent">
              <div className="min-w-0">
                <p className="truncate font-medium">{r.title}</p>
                <p className="text-xs text-muted">{formatDate(r.created_at)}</p>
              </div>
              {r.fact_check_pass_rate !== null && (
                <span className="shrink-0 text-xs text-muted">{Math.round(r.fact_check_pass_rate * 100)}% verified</span>
              )}
            </Card>
          </Link>
        </li>
      ))}
    </ul>
  );
}
