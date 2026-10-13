import type { SourceMeta } from "./api";

export type Token =
  | { kind: "text"; text: string }
  | { kind: "citation"; domain: string; date: string; credibility: number; raw: string }
  | { kind: "unverified" };

const TOKEN_RE =
  /\[Source: ([^,\]]+), ([^,\]]+), credibility: ([\d.]+)\]|\[Source: unknown\]|\[UNVERIFIED\]/g;

/** Splits report text into plain text, `[Source: domain, date, credibility: x]` citations and `[UNVERIFIED]` marks. */
export function tokenize(text: string): Token[] {
  const out: Token[] = [];
  let last = 0;
  for (const m of text.matchAll(TOKEN_RE)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ kind: "text", text: text.slice(last, at) });
    if (m[0] === "[UNVERIFIED]") out.push({ kind: "unverified" });
    else if (m[1])
      out.push({
        kind: "citation",
        domain: m[1],
        date: m[2],
        credibility: Number(m[3]),
        raw: m[0],
      });
    else out.push({ kind: "citation", domain: "unknown", date: "", credibility: 0, raw: m[0] });
    last = at + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

export type Block =
  | { kind: "paragraph"; text: string }
  | { kind: "bullets"; items: string[] };

/** Groups lines into paragraphs and "- " bullet lists. */
export function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    const bullet = /^[-*]\s+(.*)$/.exec(t);
    const last = blocks[blocks.length - 1];
    if (bullet) {
      if (last?.kind === "bullets") last.items.push(bullet[1]);
      else blocks.push({ kind: "bullets", items: [bullet[1]] });
    } else blocks.push({ kind: "paragraph", text: t });
  }
  return blocks;
}

export interface ResolvedSource {
  id: string;
  meta: SourceMeta;
}

/** Finds the stored sources a citation refers to (matched by domain and publication date). */
export function resolveCitation(
  cite: { domain: string; date: string },
  sources: Record<string, SourceMeta>,
): ResolvedSource[] {
  const matches = Object.entries(sources).filter(
    ([, m]) =>
      m.domain === cite.domain &&
      ((m.published_date ?? "").slice(0, 10) || "n.d.") === cite.date,
  );
  const pool = matches.length
    ? matches
    : Object.entries(sources).filter(([, m]) => m.domain === cite.domain);
  return pool.map(([id, meta]) => ({ id, meta }));
}

export function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}
