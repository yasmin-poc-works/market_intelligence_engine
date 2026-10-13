"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, errorMessage, formatDate, type Alert, type DiffSummary, type WatchlistItem } from "@/lib/api";
import { buttonClass, Card, ErrorNote, ghostButtonClass } from "./ui";

const POLL_MS = 30_000;

function DiffView({ diff, title, onClose }: { diff: DiffSummary; title: string; onClose: () => void }) {
  const groups: { label: string; items: string[]; style: string }[] = [
    { label: "Added", items: diff.added, style: "border-good bg-good-bg" },
    { label: "Removed", items: diff.removed, style: "border-bad bg-bad-bg" },
    { label: "Changed", items: diff.changed, style: "border-warn bg-warn-bg" },
  ];
  const empty = groups.every((g) => g.items.length === 0);
  return (
    <Card className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">What changed</h2>
          <p className="text-sm text-muted">{title}</p>
        </div>
        <button type="button" onClick={onClose} className={ghostButtonClass}>
          Close
        </button>
      </div>
      {empty && <p className="text-sm text-muted">No differences recorded.</p>}
      {groups
        .filter((g) => g.items.length > 0)
        .map((g) => (
          <div key={g.label}>
            <h3 className="mb-2 text-sm font-medium">
              {g.label} ({g.items.length})
            </h3>
            <ul className="space-y-2 text-sm">
              {g.items.map((item) => (
                <li key={item} className={`rounded border-l-4 px-3 py-2 ${g.style}`}>
                  {g.label === "Changed" && item.includes(" -> ") ? (
                    <>
                      <span className="line-through opacity-70">{item.split(" -> ")[0]}</span>
                      <br />
                      <span>{item.split(" -> ").slice(1).join(" -> ")}</span>
                    </>
                  ) : (
                    item
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
    </Card>
  );
}

export default function WatchlistDashboard() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [diff, setDiff] = useState<{ diff: DiffSummary; title: string } | null>(null);

  const [topic, setTopic] = useState("");
  const [frequency, setFrequency] = useState<"daily" | "weekly">("weekly");
  const [webhook, setWebhook] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      const [list, feed] = await Promise.all([
        api<WatchlistItem[]>("/watchlist"),
        api<{ items: Alert[] }>("/alerts?limit=50"),
      ]);
      setItems(list);
      setAlerts(feed.items);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  async function act(fn: () => Promise<unknown>, success?: string) {
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (success) setNotice(success);
      await load();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setAdding(true);
    await act(async () => {
      await api("/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic: topic.trim(),
          frequency,
          webhook_url: webhook.trim() || null,
        }),
      });
      setTopic("");
      setWebhook("");
    });
    setAdding(false);
  }

  const patch = (item: WatchlistItem, body: Partial<WatchlistItem>) =>
    act(() =>
      api(`/watchlist/${item.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );

  const remove = (item: WatchlistItem) => {
    if (!window.confirm(`Stop watching "${item.topic}"?`)) return;
    act(() => api(`/watchlist/${item.id}`, { method: "DELETE" }));
  };

  const runNow = (item: WatchlistItem) =>
    act(() => api(`/watchlist/${item.id}/run`, { method: "POST" }), "Run started. A diff alert appears here if something material changed.");

  const markRead = (a: Alert) => act(() => api(`/alerts/${a.id}/read`, { method: "POST" }));
  const markAllRead = () => act(() => api("/alerts/read-all", { method: "POST" }));

  const topicOf = (id: string) => items.find((i) => i.id === id)?.topic ?? "Deleted topic";
  const latestAlert = (id: string) => alerts.find((a) => a.watchlist_item_id === id);
  const unread = alerts.filter((a) => !a.read);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Watchlist</h1>
        <p className="mt-1 text-sm text-muted">
          Topics are re-researched on a schedule. You are alerted when the new report materially differs from the last one.
        </p>
      </div>

      <ErrorNote message={error} />
      {notice && <p className="rounded-md bg-good-bg px-3 py-2 text-sm text-good">{notice}</p>}

      <Card>
        <form onSubmit={add} className="grid gap-4 sm:grid-cols-[1fr_auto]">
          <div className="space-y-3">
            <label className="block text-sm font-medium" htmlFor="topic">
              Add a topic
            </label>
            <input
              id="topic"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g. European EV charging market: funding and competitors"
              maxLength={500}
              className="w-full rounded-md border border-line bg-background px-3 py-2 text-sm outline-none focus:border-accent"
            />
            <div className="flex flex-wrap gap-3">
              <select
                value={frequency}
                onChange={(e) => setFrequency(e.target.value as "daily" | "weekly")}
                aria-label="Frequency"
                className="rounded-md border border-line bg-background px-3 py-2 text-sm"
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
              </select>
              <input
                value={webhook}
                onChange={(e) => setWebhook(e.target.value)}
                type="url"
                placeholder="Slack webhook URL (optional)"
                aria-label="Webhook URL"
                className="min-w-0 flex-1 rounded-md border border-line bg-background px-3 py-2 text-sm outline-none focus:border-accent"
              />
            </div>
          </div>
          <button type="submit" disabled={!topic.trim() || adding} className={`${buttonClass} self-end`}>
            {adding ? "Adding…" : "Add to watchlist"}
          </button>
        </form>
      </Card>

      {diff && <DiffView diff={diff.diff} title={diff.title} onClose={() => setDiff(null)} />}

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Watched topics</h2>
        <div className="overflow-x-auto rounded-lg border border-line bg-surface">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="border-b border-line text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">Topic</th>
                <th className="px-4 py-2 font-medium">Frequency</th>
                <th className="px-4 py-2 font-medium">Last run</th>
                <th className="px-4 py-2 font-medium">Next run</th>
                <th className="px-4 py-2 font-medium">Active</th>
                <th className="px-4 py-2 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-muted">
                    Loading…
                  </td>
                </tr>
              )}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-muted">
                    Nothing is being watched yet.
                  </td>
                </tr>
              )}
              {items.map((item) => {
                const latest = latestAlert(item.id);
                return (
                  <tr key={item.id} className="border-b border-line last:border-0">
                    <td className="max-w-xs px-4 py-3">
                      <div className="truncate" title={item.topic}>
                        {item.topic}
                      </div>
                      {item.webhook_url && <div className="text-xs text-muted">Webhook on</div>}
                    </td>
                    <td className="px-4 py-3">
                      <select
                        value={item.frequency}
                        onChange={(e) => patch(item, { frequency: e.target.value as "daily" | "weekly" })}
                        aria-label={`Frequency for ${item.topic}`}
                        className="rounded border border-line bg-background px-2 py-1 text-sm"
                      >
                        <option value="daily">Daily</option>
                        <option value="weekly">Weekly</option>
                      </select>
                    </td>
                    <td className="px-4 py-3 text-muted">{formatDate(item.last_run)}</td>
                    <td className="px-4 py-3 text-muted">{item.active ? formatDate(item.next_run) : "Paused"}</td>
                    <td className="px-4 py-3">
                      <input
                        type="checkbox"
                        checked={item.active}
                        onChange={(e) => patch(item, { active: e.target.checked })}
                        aria-label={`Active: ${item.topic}`}
                      />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          className={ghostButtonClass}
                          disabled={!latest}
                          title={latest ? "Show the latest detected change" : "No changes detected yet"}
                          onClick={() => latest && setDiff({ diff: latest.diff, title: `${item.topic} · ${formatDate(latest.created_at)}` })}
                        >
                          View diff
                        </button>
                        <button type="button" className={ghostButtonClass} onClick={() => runNow(item)}>
                          Run now
                        </button>
                        <button type="button" className={`${ghostButtonClass} hover:text-bad`} onClick={() => remove(item)}>
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">
            Alerts {unread.length > 0 && <span className="ml-1 rounded-full bg-accent px-2 py-0.5 text-xs text-white">{unread.length}</span>}
          </h2>
          {unread.length > 0 && (
            <button type="button" className={ghostButtonClass} onClick={markAllRead}>
              Mark all read
            </button>
          )}
        </div>
        {alerts.length === 0 ? (
          <Card>
            <p className="text-sm text-muted">No alerts yet. They appear when a re-run finds a material change.</p>
          </Card>
        ) : (
          <ul className="space-y-2">
            {alerts.map((a) => (
              <li
                key={a.id}
                className={`flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3 text-sm ${a.read ? "border-line bg-surface text-muted" : "border-accent bg-surface"}`}
              >
                <div className="min-w-0">
                  <p className={a.read ? "" : "font-medium text-foreground"}>{a.summary}</p>
                  <p className="text-xs text-muted">
                    {topicOf(a.watchlist_item_id)} · {formatDate(a.created_at)}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    className={ghostButtonClass}
                    onClick={() => setDiff({ diff: a.diff, title: `${topicOf(a.watchlist_item_id)} · ${formatDate(a.created_at)}` })}
                  >
                    View diff
                  </button>
                  {a.run_id && (
                    <Link href={`/runs/${a.run_id}`} className={ghostButtonClass}>
                      Run
                    </Link>
                  )}
                  {!a.read && (
                    <button type="button" className={ghostButtonClass} onClick={() => markRead(a)}>
                      Mark read
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
