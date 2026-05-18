"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { PortfolioAnalytics, type AccountRollup, type CorrelationCell } from "@/lib/api";

function fmtUsd(n: number | null): string {
  if (n === null || n === undefined) return "—";
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (Math.abs(n) >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

function fmtPct(n: number | null): string {
  if (n === null || n === undefined) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

// Correlation heatmap colour. Linear interpolation:
// -1.0 (deep red) → 0.0 (gray) → 1.0 (deep green). Diagonal cells are
// always 1.0 / dark green.
function corrColor(c: number | null): string {
  if (c === null) return "rgb(var(--surface))";
  // Clamp + map to two-tone heatmap
  const v = Math.max(-1, Math.min(1, c));
  if (v >= 0) {
    // gray → green
    const a = v;
    const r = Math.round(60 + (40 * (1 - a)));   // 60..100
    const g = Math.round(60 + (140 * a));        // 60..200
    const b = Math.round(60 + (40 * (1 - a)));   // 60..100
    return `rgb(${r}, ${g}, ${b})`;
  }
  const a = -v;
  const r = Math.round(60 + (160 * a));
  const g = Math.round(60 + (30 * (1 - a)));
  const b = Math.round(60 + (30 * (1 - a)));
  return `rgb(${r}, ${g}, ${b})`;
}

function corrTextColor(c: number | null): string {
  if (c === null) return "rgb(var(--muted))";
  return Math.abs(c) > 0.5 ? "#fff" : "rgb(var(--fg))";
}

export default function PortfolioAnalyticsPage() {
  const byAccount = useQuery({
    queryKey: ["portfolio-by-account"],
    queryFn: () => PortfolioAnalytics.byAccount(),
    refetchInterval: 60_000,
  });
  const [lookbackDays, setLookbackDays] = useState(90);
  const correlation = useQuery({
    queryKey: ["portfolio-correlation", lookbackDays],
    queryFn: () => PortfolioAnalytics.correlation(lookbackDays, true),
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Portfolio analytics</h1>
        <p className="text-muted text-sm">
          Account-level rollup + pairwise correlation across held tickers.
          Spots hidden concentration (3 semis at 0.9 ρ = one bet 3x) and
          breaks the book down by which account holds what.
        </p>
      </header>

      {/* ─── Multi-account rollup ─── */}
      <section>
        <h2 className="text-lg font-semibold mb-3">By account</h2>
        {byAccount.isLoading ? (
          <div className="text-muted text-sm">Loading…</div>
        ) : !byAccount.data || byAccount.data.accounts.length === 0 ? (
          <div className="card text-sm text-muted">
            No open positions. Sync from your planner at{" "}
            <Link href="/portfolio" className="text-accent hover:underline">
              /portfolio
            </Link>{" "}
            or add manually.
          </div>
        ) : (
          <>
            <div className="card grid grid-cols-2 md:grid-cols-4 gap-4 text-center mb-3">
              <SummaryCell label="Accounts" value={String(byAccount.data.totals.account_count)} />
              <SummaryCell label="Total cost basis" value={fmtUsd(byAccount.data.totals.total_cost)} />
              <SummaryCell label="Total current value" value={fmtUsd(byAccount.data.totals.total_value)} />
              <SummaryCell
                label="Unrealized P&L"
                value={
                  byAccount.data.totals.total_value !== null
                    ? fmtUsd(byAccount.data.totals.total_value - byAccount.data.totals.total_cost)
                    : "—"
                }
                tone={
                  byAccount.data.totals.total_value !== null
                    ? (byAccount.data.totals.total_value - byAccount.data.totals.total_cost) > 0
                      ? "text-success"
                      : "text-danger"
                    : "text-muted"
                }
              />
            </div>
            <div className="space-y-2">
              {byAccount.data.accounts.map((a) => (
                <AccountCard key={a.account} acct={a} />
              ))}
            </div>
          </>
        )}
      </section>

      {/* ─── Correlation matrix ─── */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Correlation matrix</h2>
        <div className="card flex flex-wrap gap-2 items-center mb-3">
          <span className="text-sm text-muted">Lookback:</span>
          {[30, 60, 90, 180].map((d) => (
            <button
              key={d}
              onClick={() => setLookbackDays(d)}
              className={`btn text-xs ${lookbackDays === d ? "btn-primary" : ""}`}
            >
              {d}d
            </button>
          ))}
        </div>
        {correlation.isLoading ? (
          <div className="text-muted text-sm">Loading…</div>
        ) : !correlation.data ? (
          <div className="text-danger text-sm">No data</div>
        ) : correlation.data.note ? (
          <div className="card text-sm text-muted">{correlation.data.note}</div>
        ) : (
          <>
            <CorrelationGrid data={correlation.data} />
            {correlation.data.pairs_high_correlation.length > 0 && (
              <div className="card text-sm mt-3">
                <strong className="text-warning">High-correlation pairs (ρ &gt; 0.7):</strong>
                <ul className="mt-2 space-y-1 text-xs">
                  {correlation.data.pairs_high_correlation.map((p, i) => (
                    <HighCorrPair key={i} p={p} />
                  ))}
                </ul>
                <p className="text-xs text-muted mt-2">
                  These tickers move together — holding them all is more
                  concentrated than the number of names suggests. Consider
                  trimming the smaller of each pair if you weren&apos;t
                  trying to double-up the exposure intentionally.
                </p>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

function SummaryCell({
  label, value, sub, tone,
}: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-muted">{label}</div>
      <div className={`text-2xl font-bold tabular-nums ${tone ?? ""}`}>{value}</div>
      {sub && <div className="text-xs text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function AccountCard({ acct }: { acct: AccountRollup }) {
  const pnlTone =
    acct.unrealized_pnl === null
      ? "text-muted"
      : acct.unrealized_pnl > 0
        ? "text-success"
        : "text-danger";
  return (
    <details className="card">
      <summary className="cursor-pointer">
        <div className="flex flex-wrap gap-3 items-baseline">
          <span className="font-semibold">{acct.account}</span>
          <span className="text-xs text-muted">{acct.positions} position{acct.positions === 1 ? "" : "s"}</span>
          <span className="ml-auto tabular-nums">
            cost: <span className="font-semibold">{fmtUsd(acct.total_cost)}</span>
          </span>
          {acct.total_value !== null && (
            <span className="tabular-nums">
              value: <span className="font-semibold">{fmtUsd(acct.total_value)}</span>
            </span>
          )}
          {acct.unrealized_pnl !== null && (
            <span className={`tabular-nums font-semibold ${pnlTone}`}>
              {fmtUsd(acct.unrealized_pnl)} ({fmtPct(acct.unrealized_pnl_pct)})
            </span>
          )}
        </div>
      </summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1">Ticker</th>
              <th className="text-right">Shares</th>
              <th className="text-right">Cost basis/sh</th>
              <th className="text-right">Cost</th>
              <th className="text-right">Live</th>
              <th className="text-right">Value</th>
            </tr>
          </thead>
          <tbody>
            {acct.tickers.map((t, i) => (
              <tr key={i} className="border-t border-border">
                <td className="py-1 font-semibold">{t.ticker}</td>
                <td className="text-right tabular-nums">{t.shares.toLocaleString()}</td>
                <td className="text-right tabular-nums">${t.cost_basis_per_share.toFixed(2)}</td>
                <td className="text-right tabular-nums">{fmtUsd(t.cost)}</td>
                <td className="text-right tabular-nums">{t.live_price ? `$${t.live_price.toFixed(2)}` : "—"}</td>
                <td className="text-right tabular-nums">{fmtUsd(t.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function CorrelationGrid({
  data,
}: { data: { tickers: string[]; matrix: (number | null)[][]; lookback_days: number } }) {
  return (
    <div className="card overflow-x-auto">
      <table className="text-xs border-separate" style={{ borderSpacing: 0 }}>
        <thead>
          <tr>
            <th className="p-1.5 text-left text-muted text-[10px]"></th>
            {data.tickers.map((t) => (
              <th key={t} className="p-1.5 text-center font-mono">{t}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.tickers.map((rowTicker, i) => (
            <tr key={rowTicker}>
              <th className="p-1.5 text-right font-mono text-[11px]">{rowTicker}</th>
              {data.matrix[i].map((c, j) => (
                <td
                  key={j}
                  className="p-1.5 text-center tabular-nums font-mono"
                  style={{
                    background: corrColor(c),
                    color: corrTextColor(c),
                    minWidth: 50,
                  }}
                  title={`${rowTicker} vs ${data.tickers[j]}: ρ=${c ?? "n/a"}`}
                >
                  {c === null ? "—" : c.toFixed(2)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-muted mt-3">
        Pearson correlation of daily returns over the last {data.lookback_days} days.
        Diagonal is always 1.0 (self-correlation). SPY row included as a
        market-beta reference if present.
      </p>
    </div>
  );
}

function HighCorrPair({ p }: { p: CorrelationCell }) {
  return (
    <li>
      <code className="text-warning font-semibold">{p.a}</code> ↔{" "}
      <code className="text-warning font-semibold">{p.b}</code>: ρ = {p.correlation}
    </li>
  );
}
