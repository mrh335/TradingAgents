"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Tokens, type TokenBucket } from "@/lib/api";

const WINDOWS = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
  { label: "180d", days: 180 },
  { label: "1y", days: 365 },
];

// Distinct colour per provider so the stacked bars are legible at a glance.
// Same hue rotation Recharts ships, picked to read well on the dark/light
// canvas.
const PROVIDER_COLOR: Record<string, string> = {
  anthropic: "#a06ef0",
  openai: "#10a37f",
  google: "#4285f4",
  xai: "#888888",
  deepseek: "#5e72e4",
  qwen: "#ff6f00",
  glm: "#0c8473",
  openrouter: "#ec4899",
  ollama: "#22c55e",
  "claude-desktop-skill": "#fb923c",
  unknown: "#6b7280",
};

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function fmtUsd(n: number): string {
  if (Math.abs(n) < 0.01) return `$${n.toFixed(4)}`;
  if (Math.abs(n) < 1) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

export default function TokensPage() {
  const [days, setDays] = useState(30);
  const [groupByProvider, setGroupByProvider] = useState(true);

  const summary = useQuery({
    queryKey: ["tokens-summary", days, groupByProvider],
    queryFn: () => Tokens.summary(days, groupByProvider),
  });

  const events = useQuery({
    queryKey: ["tokens-events", days],
    queryFn: () =>
      Tokens.events({
        since_iso: new Date(
          Date.now() - days * 24 * 3600 * 1000,
        ).toISOString(),
        limit: 1000,
      }),
  });

  // Pivot summary.buckets → one row per date, with one column per provider
  // for the stacked-bar chart.
  const chartData = useMemo(() => {
    if (!summary.data) return [];
    const byDate = new Map<string, any>();
    for (const b of summary.data.buckets) {
      const row =
        byDate.get(b.date) ?? {
          date: b.date,
          total_in: 0,
          total_out: 0,
          total_cost: 0,
        };
      const key = b.provider || "unknown";
      row[`${key}_total`] = (row[`${key}_total`] ?? 0) + b.tokens_in + b.tokens_out;
      row[`${key}_cost`] = (row[`${key}_cost`] ?? 0) + b.estimated_cost_usd;
      row.total_in += b.tokens_in;
      row.total_out += b.tokens_out;
      row.total_cost += b.estimated_cost_usd;
      byDate.set(b.date, row);
    }
    return Array.from(byDate.values()).sort((a, b) =>
      a.date.localeCompare(b.date),
    );
  }, [summary.data]);

  const providers = summary.data?.providers ?? [];

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Token usage</h1>
        <p className="text-muted text-sm">
          Tokens consumed by completed runs — across the synchronous webapp
          path, the queue → Claude Desktop / Claude Code skill path, and any
          externally-imported runs. Cost estimates are{" "}
          <strong>indicative only</strong>: per-provider rates use approximate
          public pricing for the deep-think tier and don&apos;t reconcile
          against actual billing.
        </p>
        <p className="text-muted text-xs mt-1">
          Subscription-bound providers (<code>claude-desktop-skill</code>,{" "}
          <code>ollama</code>) are charged at $0 here even though they consume
          tokens — those tokens flow through your Pro/Team subscription or
          local compute rather than per-call API metering.
        </p>
      </header>

      <div className="card flex flex-wrap gap-2 items-center">
        <span className="text-sm text-muted mr-2">Window:</span>
        {WINDOWS.map((w) => (
          <button
            key={w.days}
            className={`btn text-xs ${days === w.days ? "btn-primary" : ""}`}
            onClick={() => setDays(w.days)}
          >
            {w.label}
          </button>
        ))}
        <label className="flex items-center gap-2 text-sm ml-auto cursor-pointer">
          <input
            type="checkbox"
            checked={groupByProvider}
            onChange={(e) => setGroupByProvider(e.target.checked)}
          />
          Group by provider
        </label>
      </div>

      {/* ─── Totals strip ─── */}
      {summary.data && (
        <div className="card grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
          <Totals
            label="Total tokens"
            value={fmtTokens(
              summary.data.totals.tokens_in + summary.data.totals.tokens_out,
            )}
            sub={`${fmtTokens(summary.data.totals.tokens_in)}↑ / ${fmtTokens(summary.data.totals.tokens_out)}↓`}
          />
          <Totals
            label="Runs"
            value={String(summary.data.totals.runs)}
            sub={`${days} days`}
          />
          <Totals
            label="Estimated cost"
            value={fmtUsd(summary.data.totals.estimated_cost_usd)}
            sub="indicative — see above"
          />
          <Totals
            label="Providers"
            value={String(summary.data.providers.length)}
            sub={summary.data.providers.join(", ") || "—"}
          />
        </div>
      )}

      {/* ─── Chart ─── */}
      <div className="card">
        <h2 className="font-semibold mb-2">Daily token consumption</h2>
        {summary.isLoading ? (
          <div className="text-muted text-sm">Loading…</div>
        ) : (summary.data?.buckets.length ?? 0) === 0 ? (
          <div className="text-muted text-sm">
            No token events in the last {days} days. Run an analysis or queue
            one to populate this chart.
          </div>
        ) : (
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
                <CartesianGrid stroke="rgb(var(--border))" strokeDasharray="3 3" />
                <XAxis
                  dataKey="date"
                  stroke="rgb(var(--muted))"
                  tick={{ fontSize: 11 }}
                  minTickGap={32}
                />
                <YAxis
                  yAxisId="tokens"
                  stroke="rgb(var(--muted))"
                  tick={{ fontSize: 11 }}
                  tickFormatter={fmtTokens}
                  label={{
                    value: "Tokens",
                    angle: -90,
                    position: "insideLeft",
                    style: { fill: "rgb(var(--muted))", fontSize: 11 },
                  }}
                />
                <YAxis
                  yAxisId="cost"
                  orientation="right"
                  stroke="rgb(var(--success))"
                  tick={{ fontSize: 11 }}
                  tickFormatter={(v: number) => `$${v.toFixed(2)}`}
                />
                <Tooltip content={<TokensTooltip />} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {groupByProvider ? (
                  providers.map((p) => (
                    <Bar
                      key={p}
                      yAxisId="tokens"
                      dataKey={`${p}_total`}
                      name={p}
                      stackId="provider"
                      fill={PROVIDER_COLOR[p] ?? PROVIDER_COLOR.unknown}
                      isAnimationActive={false}
                    />
                  ))
                ) : (
                  <Bar
                    yAxisId="tokens"
                    dataKey="total_in"
                    name="Tokens in"
                    stackId="io"
                    fill="rgb(var(--accent))"
                    isAnimationActive={false}
                  />
                )}
                {!groupByProvider && (
                  <Bar
                    yAxisId="tokens"
                    dataKey="total_out"
                    name="Tokens out"
                    stackId="io"
                    fill="#fb923c"
                    isAnimationActive={false}
                  />
                )}
                <Line
                  yAxisId="cost"
                  type="monotone"
                  dataKey="total_cost"
                  name="$ cost"
                  stroke="rgb(var(--success))"
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* ─── Per-run events table ─── */}
      <div className="card">
        <h2 className="font-semibold mb-2">Per-run events</h2>
        {events.isLoading ? (
          <div className="text-muted text-sm">Loading…</div>
        ) : (events.data?.length ?? 0) === 0 ? (
          <div className="text-muted text-sm">No events.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-muted">
                <tr>
                  <th className="py-2">Date</th>
                  <th>Ticker</th>
                  <th>Provider</th>
                  <th className="text-right">In</th>
                  <th className="text-right">Out</th>
                  <th className="text-right">LLM</th>
                  <th className="text-right">Tools</th>
                  <th className="text-right">Cost</th>
                  <th>Run</th>
                </tr>
              </thead>
              <tbody>
                {(events.data ?? []).slice(0, 200).map((e) => (
                  <tr key={e.run_id} className="border-t border-border">
                    <td className="py-1.5">{(e.completed_at ?? "").slice(0, 10)}</td>
                    <td className="font-semibold">{e.ticker}</td>
                    <td className="text-muted">
                      {e.provider ?? "—"}
                      {e.deep_model && (
                        <div className="text-[10px]">{e.deep_model}</div>
                      )}
                    </td>
                    <td className="text-right tabular-nums">{fmtTokens(e.tokens_in)}</td>
                    <td className="text-right tabular-nums">{fmtTokens(e.tokens_out)}</td>
                    <td className="text-right tabular-nums">{e.llm_calls}</td>
                    <td className="text-right tabular-nums">{e.tool_calls}</td>
                    <td className="text-right tabular-nums">
                      {fmtUsd(e.estimated_cost_usd)}
                    </td>
                    <td>
                      <Link
                        href={`/history/${e.run_id}`}
                        className="text-accent hover:underline"
                      >
                        {e.run_id.slice(0, 8)}…
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(events.data?.length ?? 0) > 200 && (
              <div className="text-muted text-xs mt-2">
                Showing first 200 of {events.data!.length}. Use the date-window
                buttons above to narrow.
              </div>
            )}
          </div>
        )}
      </div>

      <div className="card text-xs text-muted">
        <strong>About this data</strong>: tokens come from the{" "}
        <code>runs.tokens_in</code> / <code>runs.tokens_out</code> columns,
        populated by the framework runner for the synchronous path and by{" "}
        <code>POST /runs/import</code> from the Claude Desktop /
        Claude Code skill submissions. The skill estimates token usage via
        tiktoken (<code>cl100k_base</code>, ±10%). Cost rates per provider
        are hard-coded in <code>service/routers/tokens.py</code> — update when
        provider pricing changes.
      </div>
    </div>
  );
}

function Totals({
  label, value, sub,
}: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-muted">{label}</div>
      <div className="text-2xl font-bold tabular-nums">{value}</div>
      {sub && <div className="text-xs text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function TokensTooltip({ active, payload, label }: any) {
  if (!active || !payload || payload.length === 0) return null;
  const total = payload.reduce(
    (acc: number, p: any) =>
      acc + (typeof p.value === "number" && p.name !== "$ cost" ? p.value : 0),
    0,
  );
  return (
    <div
      className="text-xs"
      style={{
        background: "rgb(var(--surface))",
        border: "1px solid rgb(var(--border))",
        borderRadius: 6,
        padding: "6px 10px",
      }}
    >
      <div className="text-muted">{label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} className="flex justify-between gap-3">
          <span style={{ color: p.color }}>● {p.name}</span>
          <span className="tabular-nums">
            {p.name === "$ cost" ? fmtUsd(p.value) : fmtTokens(p.value)}
          </span>
        </div>
      ))}
      <div className="border-t border-border mt-1 pt-1 flex justify-between gap-3">
        <span className="text-muted">Total tokens</span>
        <span className="tabular-nums">{fmtTokens(total)}</span>
      </div>
    </div>
  );
}
