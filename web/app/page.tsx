"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Portfolio, Runs, SettingsApi } from "@/lib/api";
import type { PortfolioSummary, PositionWithLive } from "@/lib/types";
import { decisionColor, fmtDate, statusColor } from "@/lib/format";

// Family-first home screen. The first thing anyone in the household sees should
// answer their real questions — what are we worth, how are we doing, are we
// over-concentrated, and what did the latest analysis recommend — NOT developer
// internals (API-key counts, token usage, filesystem paths). Those are demoted
// to a small "system status" strip at the bottom.

const fmtUsd = (n: number | null | undefined, compact = false) => {
  if (n == null || Number.isNaN(n)) return "—";
  if (compact && Math.abs(n) >= 1000)
    return "$" + (n / 1000).toFixed(n >= 100000 ? 0 : 1) + "k";
  return "$" + Math.round(n).toLocaleString();
};
const fmtPct = (n: number | null | undefined, d = 1) =>
  n == null || Number.isNaN(n) ? "—" : (n >= 0 ? "+" : "") + n.toFixed(d) + "%";

export default function Home() {
  const summary = useQuery({ queryKey: ["portfolio-summary"], queryFn: () => Portfolio.summary() });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => Runs.list() });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => SettingsApi.get() });

  const s = summary.data;
  const runsList = runs.data ?? [];
  const errored = runsList.filter((r) => r.status === "error");
  const keysSet = (settings.data?.api_keys ?? []).filter(
    (k) => k.set_in_env || k.set_in_config,
  ).length;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold">Household portfolio</h1>
        <p className="text-muted text-sm mt-1">
          Your family’s investments at a glance. Analysis here is research — recommendations, not orders.
        </p>
      </header>

      {/* Net worth hero */}
      <NetWorthHero summary={s} loading={summary.isLoading} error={summary.isError} />

      {/* Concentration risk — the family's single biggest financial fact */}
      <ConcentrationCard summary={s} />

      <div className="grid lg:grid-cols-[3fr_2fr] gap-6">
        {/* Holdings */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">Your holdings</h2>
            <Link href="/portfolio" className="text-sm text-accent hover:underline">View all →</Link>
          </div>
          <HoldingsList summary={s} loading={summary.isLoading} />
        </section>

        {/* Latest recommendation + actions */}
        <section className="space-y-4">
          <LatestRecommendation />

          <div className="card">
            <h3 className="font-semibold mb-2">Common tasks</h3>
            <ul className="space-y-1.5 text-sm">
              <li><Link className="text-accent hover:underline" href="/run">▶ Run a new analysis</Link></li>
              <li><Link className="text-accent hover:underline" href="/tax">💰 Plan a tax-smart trim</Link></li>
              <li><Link className="text-accent hover:underline" href="/simulation">📊 Test a what-if scenario</Link></li>
              <li><Link className="text-accent hover:underline" href="/history">📂 Browse past analyses</Link></li>
            </ul>
          </div>

          {keysSet === 0 && (
            <div className="card border-warning/40 bg-warning/5">
              <div className="font-semibold text-warning mb-1">Setup needed</div>
              <div className="text-sm text-muted">
                Add at least one provider key in{" "}
                <Link href="/settings" className="text-accent underline">Settings</Link>{" "}
                before running a new analysis.
              </div>
            </div>
          )}
        </section>
      </div>

      {/* Recent analyses */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Recent analyses</h2>
          <Link href="/history" className="text-sm text-accent hover:underline">All analyses →</Link>
        </div>
        {runs.isLoading && <div className="text-muted text-sm">Loading…</div>}
        {!runs.isLoading && runsList.length === 0 && (
          <div className="card text-sm text-muted">
            No analyses yet. <Link className="text-accent" href="/run">Start one →</Link>
          </div>
        )}
        {runsList.length > 0 && (
          <div className="card divide-y divide-border !p-0">
            {runsList.slice(0, 6).map((r) => (
              <Link
                key={r.run_id}
                href={`/history/${r.run_id}`}
                className="flex items-center justify-between gap-2 py-2.5 px-3 hover:bg-surface first:rounded-t-lg last:rounded-b-lg"
              >
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold">{r.ticker}</div>
                  <div className="text-xs text-muted mt-0.5">{r.trade_date} · {fmtDate(r.started_at)}</div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className={`text-sm font-semibold ${decisionColor(r.decision)}`}>{r.decision ?? "—"}</span>
                  <span className={`pill ${statusColor(r.status)}`}>{r.status}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      {/* System status — demoted dev internals */}
      <details className="text-xs text-muted">
        <summary className="cursor-pointer hover:text-fg select-none">System status</summary>
        <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1">
          <span>API keys configured: <b className="text-fg">{keysSet}</b></span>
          <span>Analyses in database: <b className="text-fg">{runsList.length}</b></span>
          <span className={errored.length ? "text-danger" : ""}>Errored runs: <b>{errored.length}</b></span>
          <Link href="/settings" className="text-accent hover:underline">Settings</Link>
          <Link href="/docs" className="text-accent hover:underline">Help / Docs</Link>
        </div>
      </details>
    </div>
  );
}

function NetWorthHero({ summary, loading, error }: { summary?: PortfolioSummary; loading: boolean; error: boolean }) {
  if (loading) return <div className="card h-28 animate-pulse" />;
  if (error || !summary)
    return (
      <div className="card text-sm text-muted">
        Couldn’t load your portfolio. It syncs from the planner — check{" "}
        <Link href="/settings" className="text-accent underline">Settings</Link>, or{" "}
        <Link href="/portfolio" className="text-accent underline">open Portfolio</Link>.
      </div>
    );
  const gain = summary.unrealized_pnl;
  const gainPct = summary.unrealized_pnl_pct;
  const up = (gain ?? 0) >= 0;
  return (
    <div className="card bg-gradient-to-br from-surface to-bg">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <div className="text-xs text-muted">Total value</div>
          <div className="text-3xl font-bold mt-1">{fmtUsd(summary.total_value)}</div>
        </div>
        <div>
          <div className="text-xs text-muted">Total gain</div>
          <div className={`text-3xl font-bold mt-1 ${up ? "text-success" : "text-danger"}`}>
            {gain == null ? "—" : (up ? "+" : "") + fmtUsd(gain)}
          </div>
          <div className={`text-xs mt-0.5 ${up ? "text-success" : "text-danger"}`}>{fmtPct(gainPct)} on cost</div>
        </div>
        <div>
          <div className="text-xs text-muted">Invested (cost)</div>
          <div className="text-2xl font-semibold mt-1">{fmtUsd(summary.total_cost)}</div>
        </div>
        <div>
          <div className="text-xs text-muted">Positions</div>
          <div className="text-2xl font-semibold mt-1">{summary.open_count}</div>
          {summary.realized_pnl !== 0 && (
            <div className="text-xs text-muted mt-0.5">
              Realized: {summary.realized_pnl >= 0 ? "+" : ""}{fmtUsd(summary.realized_pnl)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function topPosition(summary?: PortfolioSummary): { pos: PositionWithLive; pct: number } | null {
  if (!summary || !summary.open_positions.length || !summary.total_value) return null;
  let top = summary.open_positions[0];
  for (const p of summary.open_positions) if ((p.value ?? 0) > (top.value ?? 0)) top = p;
  return { pos: top, pct: ((top.value ?? 0) / summary.total_value) * 100 };
}

function ConcentrationCard({ summary }: { summary?: PortfolioSummary }) {
  const t = topPosition(summary);
  if (!t || t.pct < 25) return null; // only surface when it's genuinely a concentration
  const high = t.pct >= 50;
  return (
    <div className={`card ${high ? "border-danger/40 bg-danger/5" : "border-warning/40 bg-warning/5"}`}>
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className={`font-semibold ${high ? "text-danger" : "text-warning"}`}>
            {high ? "⚠️ High concentration" : "Heads up: concentrated"}
          </div>
          <p className="text-sm text-muted mt-1 max-w-2xl">
            <b className="text-fg">{t.pos.ticker}</b> is{" "}
            <b className="text-fg">{t.pct.toFixed(0)}%</b> of your portfolio
            ({fmtUsd(t.pos.value)}). If one stock is a big share of everything, a bad
            stretch for it hits your whole net worth. The Tax page can model trimming it
            without a surprise tax bill.
          </p>
        </div>
        <Link href="/tax" className="btn btn-primary text-sm whitespace-nowrap">Plan a tax-smart trim →</Link>
      </div>
    </div>
  );
}

function HoldingsList({ summary, loading }: { summary?: PortfolioSummary; loading: boolean }) {
  if (loading) return <div className="card h-40 animate-pulse" />;
  if (!summary || summary.open_positions.length === 0)
    return <div className="card text-sm text-muted">No holdings synced yet.</div>;
  const sorted = [...summary.open_positions].sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const tv = summary.total_value || 1;
  return (
    <div className="card !p-0 divide-y divide-border">
      {sorted.map((p) => {
        const pct = ((p.value ?? 0) / tv) * 100;
        const up = (p.unrealized ?? 0) >= 0;
        return (
          <Link
            key={p.id}
            href={`/ticker/${p.ticker}`}
            className="flex items-center gap-3 py-2.5 px-3 hover:bg-surface first:rounded-t-lg last:rounded-b-lg"
          >
            <div className="w-16 shrink-0">
              <div className="font-mono font-semibold text-sm">{p.ticker}</div>
              <div className="text-xs text-muted">{pct.toFixed(0)}%</div>
            </div>
            {/* allocation bar */}
            <div className="flex-1 h-2 rounded-full bg-border/60 overflow-hidden">
              <div className="h-full bg-accent/70" style={{ width: `${Math.min(100, pct)}%` }} />
            </div>
            <div className="text-right shrink-0 w-28">
              <div className="text-sm font-semibold tabular-nums">{fmtUsd(p.value)}</div>
              <div className={`text-xs tabular-nums ${up ? "text-success" : "text-danger"}`}>
                {p.unrealized == null ? "—" : (up ? "+" : "") + fmtUsd(p.unrealized)} ({fmtPct(p.unrealized_pct)})
              </div>
            </div>
          </Link>
        );
      })}
    </div>
  );
}

function LatestRecommendation() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => Runs.list() });
  const latest = (runs.data ?? []).find((r) => r.decision && r.status !== "error");
  if (!latest)
    return (
      <div className="card">
        <h3 className="font-semibold mb-1">Latest recommendation</h3>
        <p className="text-sm text-muted">
          No completed analysis yet. <Link href="/run" className="text-accent underline">Run one →</Link>
        </p>
      </div>
    );
  return (
    <Link href={`/history/${latest.run_id}`} className="card block hover:bg-surface">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">Latest recommendation</h3>
        <span className="text-xs text-muted">{fmtDate(latest.started_at)}</span>
      </div>
      <div className="flex items-baseline gap-2 mt-2">
        <span className="font-mono font-bold text-lg">{latest.ticker}</span>
        <span className={`text-lg font-bold ${decisionColor(latest.decision)}`}>{latest.decision}</span>
      </div>
      <p className="text-xs text-muted mt-1">Tap to read the full plain-English brief →</p>
    </Link>
  );
}
