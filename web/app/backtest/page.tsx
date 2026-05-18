"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Backtest, type HitRateCell } from "@/lib/api";
import { decisionColor } from "@/lib/format";

const WINDOWS = [
  { label: "+5d", days: 5 },
  { label: "+30d", days: 30 },
  { label: "+60d", days: 60 },
  { label: "+180d", days: 180 },
];

function fmtPct(n: number | null, withSign = true): string {
  if (n === null || n === undefined) return "—";
  const sign = withSign && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function hitToneClass(hitPct: number | null): string {
  if (hitPct === null) return "text-muted";
  if (hitPct >= 70) return "text-success";
  if (hitPct >= 55) return "text-success";
  if (hitPct >= 45) return "text-muted";
  if (hitPct >= 30) return "text-warning";
  return "text-danger";
}

export default function BacktestPage() {
  const qc = useQueryClient();
  const [windowDays, setWindowDays] = useState(30);

  const q = useQuery({
    queryKey: ["backtest-summary", windowDays],
    queryFn: () => Backtest.summary(windowDays),
    refetchInterval: 5 * 60_000,
    refetchOnWindowFocus: false,
  });

  const recompute = useMutation({
    mutationFn: () => Backtest.recomputeAll(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backtest-summary"] }),
  });

  if (q.isLoading) return <div className="text-muted">Computing backtest data… (first load fetches yfinance per ticker; ~5s per run)</div>;
  if (!q.data) return <div className="text-danger">No data.</div>;
  const { overall, by_decision, by_provider, by_model, sample_rows } = q.data;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Backtest — hit rate + alpha</h1>
        <p className="text-muted text-sm">
          For every completed run with a decision, the realized total return is
          computed at +5d / +30d / +60d / +180d post-trade-date. SPY is the
          benchmark; alpha = realized − benchmark. <strong>Win</strong>: Buy/Overweight
          gained &gt; 0, or Sell/Underweight fell &lt; 0. Hold decisions are not counted.
        </p>
        <p className="text-muted text-xs mt-1">
          Cached as <code>.backtest.json</code> sidecars next to each run archive. First load
          per run fetches yfinance for the ticker + SPY price history.
        </p>
      </header>

      {/* Window selector */}
      <div className="card flex items-center gap-3 flex-wrap">
        <span className="text-sm text-muted">Window:</span>
        {WINDOWS.map((w) => (
          <button
            key={w.days}
            onClick={() => setWindowDays(w.days)}
            className={`btn text-xs ${windowDays === w.days ? "btn-primary" : ""}`}
          >
            {w.label}
          </button>
        ))}
        <button
          className="btn text-xs ml-auto"
          onClick={() => recompute.mutate()}
          disabled={recompute.isPending}
          title="Force recompute of every run's backtest sidecar (clears cache)"
        >
          {recompute.isPending
            ? "Recomputing…"
            : recompute.isSuccess
              ? `Recomputed ${recompute.data!.computed} runs`
              : "↻ Recompute all"}
        </button>
      </div>

      {/* Overall scoreboard */}
      <div className="card grid grid-cols-2 md:grid-cols-5 gap-4">
        <ScoreboardCell label="Runs counted" value={String(overall.runs)} sub={`${overall.skipped} skipped`} />
        <ScoreboardCell
          label="Hit rate"
          value={overall.hit_rate_pct !== null ? `${overall.hit_rate_pct}%` : "—"}
          tone={hitToneClass(overall.hit_rate_pct)}
          sub={`${overall.wins}W / ${overall.losses}L`}
        />
        <ScoreboardCell
          label="Mean alpha"
          value={fmtPct(overall.mean_alpha_pct)}
          tone={
            (overall.mean_alpha_pct ?? 0) > 0 ? "text-success" :
            (overall.mean_alpha_pct ?? 0) < 0 ? "text-danger" : "text-muted"
          }
          sub={`vs SPY at +${windowDays}d`}
        />
        <ScoreboardCell
          label="Wins"
          value={String(overall.wins)}
          sub={overall.runs > 0 ? `${((overall.wins / overall.runs) * 100).toFixed(0)}% of counted` : "—"}
        />
        <ScoreboardCell
          label="Losses"
          value={String(overall.losses)}
          sub={overall.runs > 0 ? `${((overall.losses / overall.runs) * 100).toFixed(0)}% of counted` : "—"}
        />
      </div>

      {/* By decision class */}
      <section>
        <h2 className="text-lg font-semibold mb-3">By decision class</h2>
        <HitRateTable rows={by_decision} windowDays={windowDays} />
      </section>

      {/* By provider */}
      {by_provider.length > 1 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">By LLM provider</h2>
          <HitRateTable rows={by_provider} windowDays={windowDays} />
          <p className="text-xs text-muted mt-2">
            Use this to spot which provider actually picks winners on your
            book — if anthropic + sonnet has 70% hit rate but ollama has 40%,
            the answer for your portfolio is clear.
          </p>
        </section>
      )}

      {/* By deep_model */}
      {by_model.length > 1 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">By deep-think model</h2>
          <HitRateTable rows={by_model} windowDays={windowDays} />
        </section>
      )}

      {/* Per-run sample */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Recent runs scored</h2>
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wider text-muted">
              <tr>
                <th className="py-2">Run</th>
                <th>Ticker</th>
                <th>Trade date</th>
                <th>Decision</th>
                <th>Provider</th>
                <th className="text-right">Ticker</th>
                <th className="text-right">SPY</th>
                <th className="text-right">Alpha</th>
                <th>Win</th>
              </tr>
            </thead>
            <tbody>
              {sample_rows.map((r) => (
                <tr key={r.run_id} className={`border-t border-border ${!r.horizon_reached ? "opacity-60" : ""}`}>
                  <td className="py-2">
                    <Link href={`/history/${r.run_id}`} className="text-accent hover:underline">
                      {r.run_id.slice(0, 8)}…
                    </Link>
                  </td>
                  <td className="font-semibold">{r.ticker}</td>
                  <td className="text-xs text-muted">{r.trade_date}</td>
                  <td>
                    {r.decision ? (
                      <span className={`text-sm font-semibold ${decisionColor(r.decision)}`}>
                        {r.decision}
                      </span>
                    ) : <span className="text-muted">—</span>}
                  </td>
                  <td className="text-xs text-muted">{r.provider ?? "—"}</td>
                  <td className={`text-right tabular-nums ${(r.ticker_return_pct ?? 0) >= 0 ? "text-success" : "text-danger"}`}>
                    {fmtPct(r.ticker_return_pct)}
                  </td>
                  <td className="text-right tabular-nums text-muted">{fmtPct(r.benchmark_return_pct)}</td>
                  <td className={`text-right tabular-nums ${(r.alpha_pct ?? 0) > 0 ? "text-success" : (r.alpha_pct ?? 0) < 0 ? "text-danger" : "text-muted"}`}>
                    {fmtPct(r.alpha_pct)}
                  </td>
                  <td>
                    {r.win === true && <span className="text-success text-sm font-semibold">✓ win</span>}
                    {r.win === false && <span className="text-danger text-sm font-semibold">✗ loss</span>}
                    {r.win === null && (
                      <span className="text-muted text-xs">
                        {r.horizon_reached ? "n/c" : "pending"}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="card text-xs text-muted">
        <strong>How the hit rate is computed.</strong> A run is counted only
        if (a) the horizon has reached (today &gt;= trade_date + window) AND
        (b) the decision is Buy / Overweight / Underweight / Sell. Hold
        decisions are never counted (no directional bet to score). Wins are
        binary: Buy / Overweight win if ticker return &gt; 0, Underweight / Sell
        win if ticker return &lt; 0. Alpha is the (signed) excess return over
        SPY — reported separately as a sharper measure of skill than the
        binary win/lose.
      </div>
    </div>
  );
}

function ScoreboardCell({
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

function HitRateTable({ rows, windowDays }: { rows: HitRateCell[]; windowDays: number }) {
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wider text-muted">
          <tr>
            <th className="py-2">Group</th>
            <th className="text-right">Runs</th>
            <th className="text-right">Wins</th>
            <th className="text-right">Losses</th>
            <th className="text-right">Hit rate</th>
            <th className="text-right">Mean alpha (vs SPY @+{windowDays}d)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className="border-t border-border">
              <td className="py-2 font-semibold">{r.label}</td>
              <td className="text-right tabular-nums">{r.runs}</td>
              <td className="text-right tabular-nums text-success">{r.wins}</td>
              <td className="text-right tabular-nums text-danger">{r.losses}</td>
              <td className={`text-right tabular-nums font-semibold ${hitToneClass(r.hit_rate_pct)}`}>
                {r.hit_rate_pct !== null ? `${r.hit_rate_pct}%` : "—"}
              </td>
              <td className={`text-right tabular-nums ${(r.mean_alpha_pct ?? 0) > 0 ? "text-success" : (r.mean_alpha_pct ?? 0) < 0 ? "text-danger" : "text-muted"}`}>
                {fmtPct(r.mean_alpha_pct)}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={6} className="text-center text-muted py-3">
                No counted runs yet. Wait for the horizon to elapse on your historical runs.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
