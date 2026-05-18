"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NewsAlerts, type NewsAlert } from "@/lib/api";

const IMPACT_TONE: Record<NewsAlert["impact"], string> = {
  high: "text-danger",
  medium: "text-warning",
  low: "text-muted",
};

function fmtTs(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function NewsAlertsPage() {
  const qc = useQueryClient();
  const [status, setStatus] = useState<"unread" | "read" | "dismissed" | "all">("unread");
  const [impact, setImpact] = useState<"high" | "medium" | "low" | "all">("all");
  const [filterTicker, setFilterTicker] = useState("");

  const q = useQuery({
    queryKey: ["news-alerts", status, impact, filterTicker],
    queryFn: () =>
      NewsAlerts.list({
        status: status === "all" ? undefined : status,
        impact: impact === "all" ? undefined : impact,
        ticker: filterTicker || undefined,
        limit: 200,
      }),
    refetchInterval: 60_000,
  });

  const markRead = useMutation({
    mutationFn: (id: number) => NewsAlerts.markRead(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["news-alerts"] }),
  });
  const dismiss = useMutation({
    mutationFn: (id: number) => NewsAlerts.dismiss(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["news-alerts"] }),
  });
  const markAllRead = useMutation({
    mutationFn: () => NewsAlerts.markAllRead(filterTicker || undefined),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["news-alerts"] }),
  });
  const refresh = useMutation({
    mutationFn: () => NewsAlerts.refresh(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["news-alerts"] }),
  });

  const items = q.data ?? [];
  const counts = {
    high: items.filter((i) => i.impact === "high").length,
    medium: items.filter((i) => i.impact === "medium").length,
    low: items.filter((i) => i.impact === "low").length,
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">News alerts</h1>
        <p className="text-muted text-sm">
          Auto-scored news items for held + watchlist tickers. Background
          poller fetches yfinance news every 15 minutes; impact is rated
          high/medium/low based on keyword scoring + recency + position size.
        </p>
        <p className="text-muted text-xs mt-1">
          High-impact items typically mention earnings, FDA, M&A, lawsuits,
          SEC actions. Medium: analyst rating changes, contracts, dividends.
          Low: everything else.
        </p>
      </header>

      {/* Filter strip */}
      <div className="card flex flex-wrap gap-3 items-center">
        <div className="flex gap-1">
          {(["unread", "read", "dismissed", "all"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`btn text-xs ${status === s ? "btn-primary" : ""}`}
            >
              {s}
            </button>
          ))}
        </div>
        <div className="flex gap-1">
          {(["all", "high", "medium", "low"] as const).map((i) => (
            <button
              key={i}
              onClick={() => setImpact(i)}
              className={`btn text-xs ${impact === i ? "btn-primary" : ""}`}
            >
              {i}
            </button>
          ))}
        </div>
        <input
          className="input text-sm"
          value={filterTicker}
          onChange={(e) => setFilterTicker(e.target.value.toUpperCase())}
          placeholder="Ticker filter"
        />
        <span className="text-xs text-muted ml-auto">
          {items.length} items · 🔴 {counts.high} 🟡 {counts.medium} ⚪ {counts.low}
        </span>
        <button
          className="btn text-xs"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          title="Force a poller tick now"
        >
          {refresh.isPending ? "Polling…" : "↻ Refresh"}
        </button>
        {status === "unread" && items.length > 0 && (
          <button
            className="btn text-xs"
            onClick={() => markAllRead.mutate()}
            disabled={markAllRead.isPending}
          >
            Mark all read
          </button>
        )}
      </div>

      {q.isLoading ? (
        <div className="text-muted text-sm">Loading…</div>
      ) : items.length === 0 ? (
        <div className="card text-sm text-muted">
          No alerts match the current filter. The poller runs every 15 min;
          hit Refresh above to pull immediately. New positions / watchlist
          adds pick up on the next tick.
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((alert) => (
            <AlertCard
              key={alert.id}
              alert={alert}
              onRead={() => markRead.mutate(alert.id)}
              onDismiss={() => dismiss.mutate(alert.id)}
              busy={markRead.isPending || dismiss.isPending}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function AlertCard({
  alert, onRead, onDismiss, busy,
}: {
  alert: NewsAlert;
  onRead: () => void;
  onDismiss: () => void;
  busy: boolean;
}) {
  return (
    <div
      className={`card ${
        alert.status === "unread" ? "border-l-4" : ""
      } ${
        alert.impact === "high" && alert.status === "unread"
          ? "border-l-danger"
          : alert.impact === "medium" && alert.status === "unread"
            ? "border-l-warning"
            : alert.status === "unread"
              ? "border-l-muted"
              : ""
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="font-semibold">{alert.ticker}</span>
            <span className={`text-xs uppercase font-semibold ${IMPACT_TONE[alert.impact]}`}>
              {alert.impact}
            </span>
            <span className="text-xs text-muted">score {alert.impact_score}</span>
            <span className="text-xs text-muted ml-auto">
              {fmtTs(alert.published_at)} · {alert.source ?? "—"}
            </span>
          </div>
          <div className="mt-1">
            {alert.url ? (
              <a
                href={alert.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent hover:underline"
              >
                {alert.headline}
              </a>
            ) : (
              <span>{alert.headline}</span>
            )}
          </div>
          {alert.keywords && (
            <div className="text-xs text-muted mt-1">
              keywords: {alert.keywords}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-1">
          {alert.status === "unread" && (
            <button
              className="btn text-xs"
              onClick={onRead}
              disabled={busy}
            >
              Mark read
            </button>
          )}
          {alert.status !== "dismissed" && (
            <button
              className="btn text-xs"
              onClick={onDismiss}
              disabled={busy}
            >
              Dismiss
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
