"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Discover, RunQueue, Watchlist } from "@/lib/api";

type Tab = "sector-gaps" | "peers" | "screener";

export default function DiscoverPage() {
  const [tab, setTab] = useState<Tab>("sector-gaps");
  const qc = useQueryClient();

  const queue = useMutation({
    mutationFn: (ticker: string) =>
      RunQueue.create({
        ticker,
        trade_date: new Date().toLocaleDateString("sv-SE"), // local YYYY-MM-DD
        mode: "analyze",
        options: {
          provider: "anthropic",
          deep_model: "claude-sonnet-4-6",
          quick_model: "claude-haiku-4-5",
          debate_rounds: 1,
          risk_rounds: 1,
          // Discovery queue items are first-look analyses for tickers the
          // user doesn't own yet, so memory is irrelevant — use fresh.
          analysis_mode: "fresh",
        },
        requested_by: "web-ui:/discover",
      }),
  });

  const addToWatchlist = useMutation({
    mutationFn: (ticker: string) => Watchlist.add({ ticker }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Discover</h1>
        <p className="text-muted text-sm">
          Find new tickers by sector gap, by similarity to what you already own,
          or via a screener. Click any suggested ticker to queue a fresh
          analysis or add it to your watchlist.
        </p>
      </header>

      {/* Tab strip */}
      <div className="flex flex-wrap gap-1 border-b border-border">
        {([
          { key: "sector-gaps", label: "Sector gaps" },
          { key: "peers", label: "Similar to what you own" },
          { key: "screener", label: "Screener (preview)" },
        ] as { key: Tab; label: string }[]).map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-3 py-1.5 text-sm border-b-2 transition-colors ${
              tab === t.key
                ? "border-accent text-accent"
                : "border-transparent text-muted hover:text-fg"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "sector-gaps" && (
        <SectorGapsTab
          onQueue={(t) => queue.mutate(t)}
          onWatch={(t) => addToWatchlist.mutate(t)}
          busy={queue.isPending || addToWatchlist.isPending}
        />
      )}
      {tab === "peers" && (
        <PeersTab
          onQueue={(t) => queue.mutate(t)}
          onWatch={(t) => addToWatchlist.mutate(t)}
          busy={queue.isPending || addToWatchlist.isPending}
        />
      )}
      {tab === "screener" && <ScreenerTab />}

      {queue.isSuccess && queue.data && (
        <div className="card text-sm text-success">
          ✓ Queued {queue.data.ticker} for analysis.{" "}
          <Link href="/queue" className="text-accent hover:underline">
            View queue →
          </Link>
        </div>
      )}
      {addToWatchlist.isSuccess && addToWatchlist.data && (
        <div className="card text-sm text-success">
          ✓ Added {addToWatchlist.data.ticker} to watchlist.{" "}
          <Link href="/watchlist" className="text-accent hover:underline">
            View watchlist →
          </Link>
        </div>
      )}
    </div>
  );
}

function SectorGapsTab({
  onQueue, onWatch, busy,
}: {
  onQueue: (ticker: string) => void;
  onWatch: (ticker: string) => void;
  busy: boolean;
}) {
  const q = useQuery({
    queryKey: ["discover-sector-gaps"],
    queryFn: () => Discover.sectorGaps(),
  });

  if (q.isLoading) return <div className="text-muted">Loading…</div>;
  if (!q.data) return <div className="text-danger">No data.</div>;
  const { sector_rows, biggest_underweights } = q.data;

  return (
    <div className="space-y-4">
      <div className="card text-sm">
        <strong>Biggest gaps to consider:</strong>{" "}
        {biggest_underweights.length === 0 ? (
          <span className="text-muted">
            Your sector mix is roughly aligned with SPY — no major underweights.
          </span>
        ) : (
          biggest_underweights.map((t, i) => (
            <span key={t}>
              {i > 0 && ", "}
              <code className="text-accent font-semibold">{t}</code>
            </span>
          ))
        )}
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wider text-muted">
            <tr>
              <th className="py-2">Sector</th>
              <th className="text-right">Your %</th>
              <th className="text-right">SPY %</th>
              <th className="text-right">Gap</th>
              <th>Suggested tickers</th>
            </tr>
          </thead>
          <tbody>
            {sector_rows.map((r) => (
              <tr
                key={r.sector}
                className={`border-t border-border align-top ${r.underweight ? "" : "opacity-70"}`}
              >
                <td className="py-2 font-semibold">{r.sector}</td>
                <td className="text-right tabular-nums">{r.portfolio_pct}%</td>
                <td className="text-right tabular-nums text-muted">{r.benchmark_pct}%</td>
                <td
                  className={`text-right tabular-nums ${
                    r.gap_pct < -3 ? "text-warning" : r.gap_pct > 3 ? "text-success" : "text-muted"
                  }`}
                >
                  {r.gap_pct > 0 ? "+" : ""}
                  {r.gap_pct}%
                </td>
                <td>
                  {r.suggested_tickers.length === 0 ? (
                    <span className="text-muted text-xs">—</span>
                  ) : (
                    <div className="space-y-1">
                      {r.suggested_tickers.map((s) => (
                        <SuggestionRow
                          key={s.ticker}
                          ticker={s.ticker}
                          rationale={s.rationale}
                          onQueue={onQueue}
                          onWatch={onWatch}
                          busy={busy}
                        />
                      ))}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PeersTab({
  onQueue, onWatch, busy,
}: {
  onQueue: (ticker: string) => void;
  onWatch: (ticker: string) => void;
  busy: boolean;
}) {
  const q = useQuery({
    queryKey: ["discover-peers"],
    queryFn: () => Discover.peers(),
  });

  if (q.isLoading) return <div className="text-muted">Loading…</div>;
  if (!q.data) return <div className="text-danger">No data.</div>;
  const { suggestions } = q.data;

  if (suggestions.length === 0) {
    return (
      <div className="card text-sm text-muted">
        No peer suggestions for your current positions. The peer map covers
        major tickers (NVDA, AAPL, MSFT, TSLA, etc.). Add a more common ticker
        to your portfolio or watchlist to see peer suggestions.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {suggestions.map((s) => (
        <div key={s.base_ticker} className="card">
          <div className="flex items-baseline gap-3 mb-2">
            <h3 className="font-semibold">
              <span className="text-accent">{s.base_ticker}</span> peers
            </h3>
            <span className="text-xs text-muted">{s.base_sector}</span>
          </div>
          <div className="space-y-1">
            {s.peers.map((p) => (
              <SuggestionRow
                key={p.ticker}
                ticker={p.ticker}
                rationale={p.rationale}
                onQueue={onQueue}
                onWatch={onWatch}
                busy={busy}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ScreenerTab() {
  const q = useQuery({
    queryKey: ["discover-screener"],
    queryFn: () => Discover.screener(),
  });

  if (q.isLoading) return <div className="text-muted">Loading…</div>;
  if (!q.data) return null;
  return (
    <div className="card text-sm space-y-3">
      <div className="text-muted">{q.data.message}</div>
      <div>
        <div className="text-xs uppercase tracking-wider text-muted mb-2">
          Planned filters
        </div>
        <div className="flex flex-wrap gap-2">
          {q.data.available_filters.map((f) => (
            <span
              key={f}
              className="text-xs px-2 py-1 rounded border border-border text-muted"
            >
              {f}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function SuggestionRow({
  ticker, rationale, onQueue, onWatch, busy,
}: {
  ticker: string;
  rationale: string;
  onQueue: (t: string) => void;
  onWatch: (t: string) => void;
  busy: boolean;
}) {
  return (
    <div className="flex items-start gap-3 text-xs">
      <code className="text-accent font-semibold" style={{ minWidth: 60 }}>
        {ticker}
      </code>
      <span className="text-muted flex-1">{rationale}</span>
      <button
        className="btn text-xs"
        onClick={() => onWatch(ticker)}
        disabled={busy}
        title="Add to watchlist"
      >
        + Watch
      </button>
      <button
        className="btn text-xs"
        onClick={() => onQueue(ticker)}
        disabled={busy}
        title="Queue a fresh analysis"
      >
        🤖 Queue
      </button>
    </div>
  );
}
