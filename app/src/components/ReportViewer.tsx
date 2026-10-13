"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { formatDate, type ReportDetail } from "@/lib/api";
import { resolveCitation, slug, toBlocks, tokenize, type ResolvedSource } from "@/lib/citations";
import ExportButtons from "./ExportButtons";
import FactCheckPanel from "./FactCheckPanel";
import { Card, ConfidenceBadge } from "./ui";

interface OpenCitation {
  domain: string;
  date: string;
  credibility: number;
}

function Rich({
  text,
  onCite,
}: {
  text: string;
  onCite: (c: OpenCitation) => void;
}) {
  return (
    <>
      {tokenize(text).map((t, i) => {
        if (t.kind === "text") return <Fragment key={i}>{t.text}</Fragment>;
        if (t.kind === "unverified")
          return (
            <mark
              key={i}
              className="mx-0.5 rounded bg-warn-bg px-1 text-xs font-semibold text-warn"
              title="This claim could not be verified against its source"
            >
              UNVERIFIED
            </mark>
          );
        return (
          <button
            key={i}
            type="button"
            onClick={() => onCite(t)}
            className="mx-0.5 rounded bg-background px-1.5 py-0.5 align-baseline text-xs text-accent hover:underline"
            title={`Credibility ${t.credibility.toFixed(2)}. Click for source details`}
          >
            {t.domain}
          </button>
        );
      })}
    </>
  );
}

function Body({ text, onCite }: { text: string; onCite: (c: OpenCitation) => void }) {
  return (
    <div className="space-y-3 leading-7">
      {toBlocks(text).map((b, i) =>
        b.kind === "bullets" ? (
          <ul key={i} className="list-disc space-y-1 pl-5">
            {b.items.map((item, j) => (
              <li key={j}>
                <Rich text={item} onCite={onCite} />
              </li>
            ))}
          </ul>
        ) : (
          <p key={i}>
            <Rich text={b.text} onCite={onCite} />
          </p>
        ),
      )}
    </div>
  );
}

function CitationDrawer({
  citation,
  detail,
  onClose,
}: {
  citation: OpenCitation;
  detail: ReportDetail;
  onClose: () => void;
}) {
  const sources: ResolvedSource[] = resolveCitation(citation, detail.sources);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/30" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-label="Source details"
        className="fixed inset-y-0 right-0 z-40 w-full max-w-md overflow-y-auto border-l border-line bg-surface p-6 shadow-xl"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Source</h2>
          <button type="button" onClick={onClose} className="text-sm text-muted hover:text-foreground">
            Close
          </button>
        </div>
        {sources.length === 0 ? (
          <div className="space-y-2 text-sm">
            <p className="font-medium">{citation.domain}</p>
            <p className="text-muted">
              Published {citation.date || "n.d."} · credibility {citation.credibility.toFixed(2)}
            </p>
            <p className="text-muted">Detailed source data is not stored for this report.</p>
          </div>
        ) : (
          <div className="space-y-6">
            {sources.map(({ id, meta }) => {
              const claims = detail.evidence.filter((e) => e.source_ids.includes(id));
              return (
                <section key={id} className="space-y-2 text-sm">
                  <h3 className="text-base font-semibold">{meta.title || meta.domain}</h3>
                  <p className="text-muted">
                    {meta.domain} · {(meta.published_date ?? "").slice(0, 10) || "n.d."} · credibility{" "}
                    {meta.credibility_score.toFixed(2)}
                  </p>
                  {meta.url && (
                    <a href={meta.url} target="_blank" rel="noopener noreferrer" className="block break-all text-accent hover:underline">
                      {meta.url}
                    </a>
                  )}
                  {claims.length > 0 && (
                    <div className="pt-2">
                      <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
                        What this source supports
                      </h4>
                      <ul className="space-y-2">
                        {claims.map((c) => (
                          <li key={c.id} className="rounded border-l-4 border-accent bg-background px-3 py-2">
                            {c.claim}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        )}
      </aside>
    </>
  );
}

export default function ReportViewer({ detail, shared = false }: { detail: ReportDetail; shared?: boolean }) {
  const report = detail.report;
  const [open, setOpen] = useState<OpenCitation | null>(null);
  const [active, setActive] = useState("summary");

  const toc = useMemo(() => {
    if (!report) return [];
    return [
      { id: "summary", label: "Executive summary" },
      ...report.sections.map((s) => ({ id: slug(s.heading), label: s.heading })),
      { id: "gaps", label: "Gaps & limitations" },
      { id: "fact-check", label: "Fact-check" },
    ];
  }, [report]);

  useEffect(() => {
    const els = toc.map((t) => document.getElementById(t.id)).filter((e): e is HTMLElement => !!e);
    const io = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "-10% 0px -70% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [toc]);

  if (!report) {
    return <p className="text-sm text-muted">This report has no structured content.</p>;
  }

  return (
    <div className="lg:grid lg:grid-cols-[220px_1fr] lg:gap-10">
      <nav aria-label="Table of contents" className="mb-6 lg:sticky lg:top-6 lg:mb-0 lg:self-start">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Contents</h2>
        <ul className="space-y-1 border-l border-line text-sm">
          {toc.map((t) => (
            <li key={t.id}>
              <a
                href={`#${t.id}`}
                className={`-ml-px block border-l-2 py-1 pl-3 ${active === t.id ? "border-accent font-medium text-foreground" : "border-transparent text-muted hover:text-foreground"}`}
              >
                {t.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      <article className="min-w-0 space-y-8">
        <header className="space-y-3">
          <h1 className="text-3xl font-semibold tracking-tight">{report.title}</h1>
          <p className="text-sm text-muted">Generated {formatDate(detail.created_at)}</p>
          <ExportButtons reportId={detail.id} title={report.title} markdown={detail.markdown} shared={shared} />
        </header>

        <section id="summary" className="space-y-3">
          <h2 className="text-xl font-semibold">Executive summary</h2>
          <Card>
            <Body text={report.executive_summary} onCite={setOpen} />
          </Card>
        </section>

        {report.sections.map((s) => (
          <section key={s.heading} id={slug(s.heading)} className="space-y-3">
            <div className="flex items-center gap-3">
              <h2 className="text-xl font-semibold">{s.heading}</h2>
              <ConfidenceBadge level={s.confidence} />
            </div>
            <Body text={s.body} onCite={setOpen} />
          </section>
        ))}

        <section id="gaps" className="space-y-3">
          <h2 className="text-xl font-semibold">Gaps &amp; limitations</h2>
          <Body text={report.gaps_and_limitations || "None identified."} onCite={setOpen} />
        </section>

        <section id="fact-check">
          <FactCheckPanel factCheck={detail.fact_check} />
        </section>
      </article>

      {open && <CitationDrawer citation={open} detail={detail} onClose={() => setOpen(null)} />}
    </div>
  );
}
