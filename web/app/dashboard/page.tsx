"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Dashboard, RunQueue, Tokens, type FreshnessRow } from "@/lib/api";
import { decisionColor } from "@/lib/format";

// Days-since-last-run colour thresholds. Tunable; reflects how stale
// a discretionary recommendation feels for a mechanical-engineer trader
// who reviews positions on weekly cadence.
function freshnessTone(days: number | null): string {
  if (days === null) return "text-danger";       // never analyzed
  if (days <= 3) return "text-success";
  if (days <= 7) return "text-fg";
  if (days <= 14) return "text-warning";
  return "text-danger";                           // > 14d
}

function freshnessLabel(days: number | null): string {
  if (days === null) return "never";
  if (days === 0) return "today";
  if (days === 1) return "1 day";
  return `${days} days`;
}

export default function DashboardPage() {
  const qc = useQueryClient();
  const freshness = useQuery({
    queryKey: ["dashboard-freshness"],
    queryFn: () => Dashboard.freshness(),
    refetchInterval: 30_000,
  });

  const backfill = useMutation({
    mutationFn: () => Tokens.backfill(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tokens-summary"] });
      qc.invalidateQueries({ queryKey: ["tokens-events"] });
    },
  });

  // Quick "re-analyze this ticker" via the queue. Defaults to FRESH mode
  // because hitting Re-queue is explicit re-analysis intent — the user
  // wants a new look, not the PM picking up where yesterday's decision
  // left off.
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
          analysis_mode: "fresh",
          data_vendors: {
            core_stock_apis: "yfinance",
            technical_indicators: "yfinance",
            fundamental_data: "yfinance",
            news_data: "yfinance",
          },
        },
        requested_by: "web-ui:/dashboard",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dashboard-freshness"] }),
  });

  const rows = freshness.data ?? [];
  const positions = rows.filter((r) => r.shares > 0);
  const watchlistOnly = rows.filter((r) => r.shares === 0);
  const stale = rows.filter((r) => r.days_since === null || r.days_since > 7);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="text-muted text-sm">
          Cross-cutting view of your book — which tickers have stale analysis,
          which have never been analyzed, latest decision, and a quick
          re-queue button per row. Auto-refreshes every 30s.
        </p>
      </header>

      {/* ─── Summary strip ─── */}
      <div className="card grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
        <Summary label="Positions tracked" value={String(positions.length)} sub="Owned shares > 0" />
        <Summary label="Watchlist (no shares)" value={String(watchlistOnly.length)} sub="Just tracking" />
        <Summary
          label="Stale (>7d or never)"
          value={String(stale.length)}
          sub="Re-run candidates"
          tone={stale.length > 0 ? "text-warning" : "text-success"}
        />
        <Summary
          label="Estimated tokens"
          value="see /tokens"
          sub={(
            <button
              className="btn text-xs mt-1"
              onClick={() => backfill.mutate()}
              disabled={backfill.isPending}
              title="Estimate token counts for runs imported before the server-side fallback was added (lights up the /tokens chart)."
            >
              {backfill.isPending
                ? "Backfilling…"
                : backfill.isSuccess
                  ? `Backfilled ${backfill.data!.updated} rows`
                  : "Backfill zero-token rows"}
            </button>
          )}
        />
      </div>

      {/* ─── Positions section ─── */}
      <section>
        <h2 className="text-lg font-semibold mb-3">
          Positions ({positions.length})
        </h2>
        {freshness.isLoading ? (
          <div className="text-muted text-sm">Loading…</div>
        ) : positions.length === 0 ? (
          <div className="card text-sm text-muted">
            No open positions tracked. Sync from your planner via{" "}
            <Link href="/portfolio" className="text-accent hover:underline">
              /portfolio
            </Link>{" "}
            or add positions manually.
          </div>
        ) : (
          <FreshnessTable rows={positions} onQueue={(t) => queue.mutate(t)} queuing={queue.isPending} />
        )}
      </section>

      {/* ─── Watchlist section ─── */}
      {watchlistOnly.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">
            Watchlist only ({watchlistOnly.length})
          </h2>
          <FreshnessTable rows={watchlistOnly} onQueue={(t) => queue.mutate(t)} queuing={queue.isPending} />
        </section>
      )}

      {queue.isSuccess && queue.data && (
        <div className="card text-sm text-success">
          ✓ Queued {queue.data.ticker} for analysis.{" "}
          <Link href="/queue" className="text-accent hover:underline">
            View queue →
          </Link>
        </div>
      )}
      {queue.isError && (
        <div className="card text-sm text-danger">
          Queue failed: {(queue.error as Error).message}
        </div>
      )}
    </div>
  );
}

function Summary({
  label, value, sub, tone,
}: {
  label: string;
  value: string;
  sub?: React.ReactNode;
  tone?: string;
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-muted">{label}</div>
      <div className={`text-2xl font-bold tabular-nums ${tone ?? ""}`}>{value}</div>
      {sub && <div className="text-xs text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function FreshnessTable({
  rows, onQueue, queuing,
}: {
  rows: FreshnessRow[];
  onQueue: (ticker: string) => void;
  queuing: boolean;
}) {
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wider text-muted">
          <tr>
            <th className="py-2">Ticker</th>
            <th className="text-right">Shares</th>
            <th>Last analyzed</th>
            <th>Days since</th>
            <th>Decision</th>
            <th>Provider</th>
            <th className="text-right">Total runs</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.ticker} className="border-t border-border">
              <td className="py-2 font-semibold">{r.ticker}</td>
              <td className="text-right tabular-nums">
                {r.shares > 0 ? r.shares.toLocaleString() : "—"}
              </td>
              <td className="text-xs text-muted">
                {r.last_run_id ? (
                  <Link
                    href={`/history/${r.last_run_id}`}
                    className="text-accent hover:underline"
                  >
                    {(r.last_run_completed_at ?? "").slice(0, 10) || r.last_run_date || "—"}
                  </Link>
                ) : (
                  "never"
                )}
              </td>
              <td className={`tabular-nums text-xs ${freshnessTone(r.days_since)}`}>
                {freshnessLabel(r.days_since)}
              </td>
              <td>
                {r.last_decision ? (
                  <span className={`text-sm font-semibold ${decisionColor(r.last_decision)}`}>
                    {r.last_decision}
                  </span>
                ) : (
                  <span className="text-muted text-xs">—</span>
                )}
              </td>
              <td className="text-xs text-muted">{r.last_provider ?? "—"}</td>
              <td className="text-right tabular-nums text-xs">{r.runs_total}</td>
              <td className="text-right">
                <button
                  className="btn text-xs"
                  onClick={() => onQueue(r.ticker)}
                  disabled={queuing}
                  title="Queue a fresh analysis for this ticker. A worker will drain it."
                >
                  🤖 Re-queue
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
