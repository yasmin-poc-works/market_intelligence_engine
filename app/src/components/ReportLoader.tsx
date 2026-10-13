"use client";

import { useEffect, useState } from "react";
import { api, errorMessage, type ReportDetail } from "@/lib/api";
import ReportViewer from "./ReportViewer";
import { ErrorNote } from "./ui";

/** Loads a report by id, or through a public share token, and renders it. */
export default function ReportLoader({ reportId, shareToken }: { reportId?: string; shareToken?: string }) {
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const path = shareToken ? `/share/${encodeURIComponent(shareToken)}` : `/reports/${reportId}`;
    api<ReportDetail>(path)
      .then(setDetail)
      .catch((e) =>
        setError(shareToken && e?.status === 401 ? "This share link is invalid or has expired." : errorMessage(e)),
      );
  }, [reportId, shareToken]);

  if (error) return <ErrorNote message={error} />;
  if (!detail) return <p className="text-sm text-muted">Loading report…</p>;
  return <ReportViewer detail={detail} shared={!!shareToken} />;
}
