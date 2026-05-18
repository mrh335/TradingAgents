"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Dashboard, RunQueue, type PositionAction } from "@/lib/api";
import { decisionColor } from "@/lib/format";

const PRIORITY_COLOR: Record<string, string> = {
  high: "text-danger",
  medium: "text-warning",
  low: "text-muted",
  info: "text-accent",
};

const ACTION_LABEL: Record<PositionAction["action"], string> = {
  maintain: "Maintain",
  trim: "Trim",
  add: "Add",
  exit: "Exit",
  refresh: "Refresh",
  blocked: "Blocked",
};

const ACTION_COLOR: Record<PositionAction["action"], string> = {
  maintain: "text-muted",
  trim: "text-warning",
  add: "text-success",
  exit: "text-danger",
  refresh: "text-accent",
  blocked: "text-danger",
};

function fmtUsd(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

export default function RecommendationsPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["recommendations"],
    queryFn: () => Dashboard.recommendations(),
    refetchInterval: 60_000,
  });

  const queue = useMutation({
    mutationFn: (ticker: string) =>
      RunQueue.create({
        ticker,
        trade_date: new Date().toISOString().slice(0, 10),
        mode: "analyze",
        options: {
          provider: "anthropic",
          deep_model: "claude-sonnet-4-6",
          quick_model: "claude-haiku-4-5",
          debate_rounds: 1,
          risk_rounds: 1,
          // Re-queue from /recommendations = explicit re-analysis intent
          // → fresh by default to break decision-anchoring.
          analysis_mode: "fresh",
        },
        requested_by: "web-ui:/recommendations",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recommendations"] }),
  });

  if (q.isLoading) return <div className="text-muted">Loading…</div>;
  if (!q.data) return <div className="text-danger">No data.</div>;
  const { portfolio_summary, positions, sector_mix, observations, action_priority } = q.data;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Portfolio recommendations</h1>
        <p className="text-muted text-sm">
          Cross-position action sheet synthesized from current holdings, the
          latest brief for each ticker, sector concentration, and any active
          trading restrictions. Refreshes every 60s. Rules-based — no LLM call
          per page load.
        </p>
      </header>

      {/* ─── Summary ─── */}
      <div className="card grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
        <Summary label="Positions" value={String(portfolio_summary.position_count)} />
        <Summary
          label="Book value (at cost)"
          value={fmtUsd(portfolio_summary.total_value_at_basis)}
        />
        <Summary
          label="High-priority actions"
          value={String(portfolio_summary.high_priority_actions)}
          tone={portfolio_summary.high_priority_actions > 0 ? "text-danger" : "text-success"}
        />
        <Summary
          label="Blocked tickers"
          value={String(portfolio_summary.blocked_tickers)}
          sub={portfolio_summary.blocked_tickers > 0 ? "blackouts active" : "all clear"}
          tone={portfolio_summary.blocked_tickers > 0 ? "text-warning" : "text-muted"}
        />
      </div>

      {/* ─── Action priority list ─── */}
      {action_priority.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">Do these first</h2>
          <div className="card space-y-2">
            {action_priority.map((a, i) => (
              <div
                key={i}
                className="flex items-start gap-3 border-b border-border pb-2 last:border-0 last:pb-0"
              >
                <span
                  className={`text-xs uppercase tracking-wider font-semibold ${PRIORITY_COLOR[a.priority] || ""}`}
                  style={{ minWidth: 60 }}
                >
                  {a.priority}
                </span>
                <span className="font-bold">{a.verb} {a.ticker}</span>
                <span className="text-sm text-muted flex-1">{a.summary}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ─── Observations ─── */}
      {observations.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">Cross-portfolio observations</h2>
          <div className="space-y-2">
            {observations.map((o, i) => (
              <div key={i} className="card">
                <div className="flex items-start gap-3">
                  <span
                    className={`text-xs uppercase tracking-wider font-semibold ${PRIORITY_COLOR[o.priority] || ""}`}
                    style={{ minWidth: 60 }}
                  >
                    {o.priority}
                  </span>
                  <div className="flex-1">
                    <div className="font-semibold">{o.summary}</div>
                    {o.detail && <div className="text-sm text-muted mt-1">{o.detail}</div>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ─── Per-position breakdown ─── */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Per-position breakdown</h2>
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wider text-muted">
              <tr>
                <th className="py-2">Ticker</th>
                <th>Sector</th>
                <th className="text-right">Weight</th>
                <th>Latest decision</th>
                <th>Action</th>
                <th>Rationale</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.ticker} className="border-t border-border align-top">
                  <td className="py-2 font-semibold">
                    {p.ticker}
                    <div className="text-xs text-muted">{p.shares.toLocaleString()} sh</div>
                  </td>
                  <td className="text-xs text-muted">{p.sector}</td>
                  <td className="text-right tabular-nums">
                    {p.weight_pct.toFixed(1)}%
                    <div className="text-xs text-muted">{fmtUsd(p.cost_basis_total)}</div>
                  </td>
                  <td>
                    {p.latest_decision ? (
                      <>
                        <span className={`text-sm font-semibold ${decisionColor(p.latest_decision)}`}>
                          {p.latest_decision}
                        </span>
                        {p.days_since !== null && (
                          <div className="text-xs text-muted">{p.days_since}d ago</div>
                        )}
                      </>
                    ) : (
                      <span className="text-muted text-xs">never analyzed</span>
                    )}
                  </td>
                  <td>
                    <span className={`text-sm font-semibold ${ACTION_COLOR[p.action]}`}>
                      {ACTION_LABEL[p.action]}
                    </span>
                    <div className={`text-xs ${PRIORITY_COLOR[p.priority] || "text-muted"}`}>
                      {p.priority}
                    </div>
                  </td>
                  <td className="text-xs text-muted max-w-md whitespace-normal">
                    {p.rationale}
                  </td>
                  <td className="text-right whitespace-nowrap">
                    {p.latest_run_id && (
                      <Link
                        href={`/history/${p.latest_run_id}`}
                        className="text-accent hover:underline text-xs"
                      >
                        Open run →
                      </Link>
                    )}{" "}
                    <button
                      className="btn text-xs"
                      onClick={() => queue.mutate(p.ticker)}
                      disabled={queue.isPending}
                      title="Queue a fresh analysis"
                    >
                      🤖 Re-queue
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ─── Sector mix ─── */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Sector mix</h2>
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wider text-muted">
              <tr>
                <th className="py-2">Sector</th>
                <th className="text-right">Weight</th>
                <th>Visual</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(sector_mix).map(([sector, pct]) => (
                <tr key={sector} className="border-t border-border">
                  <td className="py-2">{sector}</td>
                  <td className="text-right tabular-nums">{pct.toFixed(1)}%</td>
                  <td className="w-3/5">
                    <div
                      className="h-3 bg-accent rounded"
                      style={{ width: `${Math.min(pct, 100)}%`, opacity: 0.6 }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-muted mt-2">
          For benchmark comparison vs SPY, see the{" "}
          <Link href="/discover" className="text-accent hover:underline">
            Discover
          </Link>{" "}
          page → Sector gaps.
        </p>
      </section>
    </div>
  );
}

function Summary({
  label, value, sub, tone,
}: {
  label: string;
  value: string;
  sub?: string;
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
