"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Portfolio, Watchlist } from "@/lib/api";

// /earnings index — pick a ticker to see its earnings card, or
// browse a quick list of upcoming earnings across your held +
// watched tickers (sorted by days-until-next).

function fmtDays(d: number | null): string {
  if (d === null || d === undefined) return "—";
  if (d === 0) return "today";
  if (d > 0) return `in ${d}d`;
  return `${-d}d ago`;
}

function urgencyTone(d: number | null): string {
  if (d === null) return "text-muted";
  if (d <= 3 && d >= 0) return "text-danger font-semibold"; // earnings this week
  if (d <= 14 && d > 0) return "text-warning"; // earnings in 2 weeks
  if (d > 14) return "text-muted";
  return "text-muted";
}

export default function EarningsIndexPage() {
  const [search, setSearch] = useState("");

  const positions = useQuery({
    queryKey: ["earnings-index-positions"],
    queryFn: () => Portfolio.positions(),
  });
  const watchlist = useQuery({
    queryKey: ["earnings-index-watchlist"],
    queryFn: () => Watchlist.list(),
  });

  // Build deduplicated ticker list sorted by next earnings.
  type Row = {
    ticker: string;
    source: "position" | "watchlist" | "both";
    next_earnings_date: string | null;
    days_until_earnings: number | null;
  };
  const rowsMap = new Map<string, Row>();
  for (const p of positions.data ?? []) {
    rowsMap.set(p.ticker, {
      ticker: p.ticker,
      source: "position",
      next_earnings_date: p.next_earnings_date ?? null,
      days_until_earnings: p.days_until_earnings ?? null,
    });
  }
  for (const w of watchlist.data ?? []) {
    const existing = rowsMap.get(w.ticker);
    if (existing) {
      rowsMap.set(w.ticker, { ...existing, source: "both" });
    } else {
      rowsMap.set(w.ticker, {
        ticker: w.ticker,
        source: "watchlist",
        next_earnings_date: w.next_earnings_date ?? null,
        days_until_earnings: w.days_until_earnings ?? null,
      });
    }
  }
  const rows = Array.from(rowsMap.values()).sort((a, b) => {
    const ad = a.days_until_earnings ?? 9999;
    const bd = b.days_until_earnings ?? 9999;
    return ad - bd;
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Earnings</h1>
        <p className="text-muted text-sm">
          Per-ticker earnings cards with latest reported quarter, analyst
          estimate revisions, recommendation mix, and AI-generated
          plain-English summary. Held + watched tickers sorted by
          days-until-earnings below; pick one to drill in.
        </p>
      </header>

      {/* Free-text search */}
      <div className="card flex gap-2 items-center">
        <input
          className="input flex-1"
          placeholder="Type any ticker to view its earnings card (e.g. AAPL, NVDA, MSFT)"
          value={search}
          onChange={(e) => setSearch(e.target.value.toUpperCase())}
        />
        {search && (
          <Link
            href={`/earnings/${search.trim()}`}
            className="btn btn-primary text-sm"
          >
            Open {search} →
          </Link>
        )}
      </div>

      {/* Held + watched list */}
      <section>
        <h2 className="text-lg font-semibold mb-2">Your tickers</h2>
        {positions.isLoading || watchlist.isLoading ? (
          <div className="text-muted text-sm">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="card text-sm text-muted">
            No held or watched tickers yet. Add some at /portfolio or /watchlist.
          </div>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-muted">
                <tr>
                  <th className="py-2">Ticker</th>
                  <th>Source</th>
                  <th>Next earnings</th>
                  <th>When</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.ticker} className="border-t border-border">
                    <td className="py-2 font-semibold">{r.ticker}</td>
                    <td className="text-xs text-muted">{r.source}</td>
                    <td>{r.next_earnings_date ?? "—"}</td>
                    <td className={`text-xs ${urgencyTone(r.days_until_earnings)}`}>
                      {fmtDays(r.days_until_earnings)}
                    </td>
                    <td className="text-right">
                      <Link
                        href={`/earnings/${r.ticker}`}
                        className="btn text-xs"
                      >
                        View →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="card text-xs text-muted">
        Next earnings date is pulled from yfinance and cached for 15 min.
        Empty values usually mean yfinance hasn&apos;t confirmed the next
        date yet (typically updates 1-2 months ahead of the call).
      </div>
    </div>
  );
}
