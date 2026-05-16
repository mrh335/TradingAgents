"use client";

/**
 * Decision-history chart — interactive Recharts visualisation of every
 * past run for a ticker overlaid on the price line. Companion to the
 * static PNG endpoint at /charts/decisions/{ticker}.png (which is for
 * email/Slack embeds; this is the primary in-app view).
 *
 * Features:
 *   - Hover tooltip on price points and decision dots
 *   - Click a decision dot → opens that run's detail page in a new tab
 *   - Brush selector for date-range zoom
 *   - Lookback-window picker (30 / 90 / 180 / 365 / 730 days)
 *   - 5-tier colour palette matching the rating vocabulary
 */

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Brush,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Charts } from "@/lib/api";

const DECISION_COLOR: Record<string, string> = {
  Buy: "#16a34a",          // green
  Overweight: "#84cc16",   // lime
  Hold: "#a3a3a3",         // grey
  Underweight: "#f59e0b",  // amber
  Sell: "#dc2626",         // red
};

const DECISION_LABEL_PLAIN: Record<string, string> = {
  Buy: "buy a starter position",
  Overweight: "add more than usual",
  Hold: "keep what you have",
  Underweight: "trim about half",
  Sell: "sell out completely",
};

const WINDOWS = [
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
  { label: "6mo", days: 180 },
  { label: "1y", days: 365 },
  { label: "2y", days: 730 },
];

export function DecisionHistoryChart({ ticker }: { ticker: string }) {
  const [lookback, setLookback] = useState(180);

  const q = useQuery({
    queryKey: ["decisions-chart", ticker, lookback],
    queryFn: () => Charts.decisions(ticker, lookback),
    enabled: !!ticker,
  });

  // Merge decisions onto the price series by date so Recharts can render
  // both on one composed chart. Each row carries:
  //   { date, close, decisionPrice (= close if a decision landed here), ...decisionMeta }
  const data = useMemo(() => {
    if (!q.data) return [];
    const priceByDate = new Map(q.data.price_series.map((p) => [p.date, p.close]));
    // Some decisions may not have a matching trading-day close (weekend,
    // holiday, or pre-IPO). Snap them to the nearest available close.
    const dates = q.data.price_series.map((p) => p.date).sort();
    function nearestPrice(date: string): number | null {
      if (priceByDate.has(date)) return priceByDate.get(date)!;
      const after = dates.find((d) => d >= date);
      if (after) return priceByDate.get(after) ?? null;
      const before = [...dates].reverse().find((d) => d <= date);
      return before ? priceByDate.get(before) ?? null : null;
    }
    const decisionByDate = new Map<string, typeof q.data.decisions[0]>();
    for (const d of q.data.decisions) {
      decisionByDate.set(d.trade_date, d);
    }
    // Build a union of dates so decisions on non-trading days still get
    // a row.
    const allDates = Array.from(
      new Set([
        ...q.data.price_series.map((p) => p.date),
        ...q.data.decisions.map((d) => d.trade_date),
      ]),
    ).sort();
    return allDates.map((date) => {
      const close = priceByDate.get(date) ?? null;
      const decision = decisionByDate.get(date);
      return {
        date,
        close,
        decisionPrice: decision ? nearestPrice(date) : null,
        decision: decision?.decision ?? null,
        runId: decision?.run_id ?? null,
        provider: decision?.provider ?? null,
      };
    });
  }, [q.data]);

  if (!ticker) {
    return <div className="text-sm text-muted">Pick a ticker.</div>;
  }
  if (q.isLoading) {
    return <div className="text-sm text-muted">Loading decision history…</div>;
  }
  if (q.error) {
    return (
      <div className="text-sm text-danger">
        Failed to load: {(q.error as Error).message}
      </div>
    );
  }
  if (!q.data || (q.data.decisions.length === 0 && q.data.price_series.length === 0)) {
    return (
      <div className="text-sm text-muted">
        No decisions or price data for {ticker} in the last {lookback} days.
      </div>
    );
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h3 className="font-semibold">{ticker} — decisions over time</h3>
          <p className="text-xs text-muted">
            Every recorded recommendation overlaid on the close price. Click a
            dot to open that run.
          </p>
        </div>
        <div className="flex gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w.days}
              onClick={() => setLookback(w.days)}
              className={`btn text-xs ${
                lookback === w.days ? "btn-primary" : ""
              }`}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      <div className="h-80">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="rgb(var(--border))" strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              stroke="rgb(var(--muted))"
              tick={{ fontSize: 11 }}
              minTickGap={32}
            />
            <YAxis
              stroke="rgb(var(--muted))"
              tick={{ fontSize: 11 }}
              domain={["auto", "auto"]}
            />
            <Tooltip content={<DecisionTooltip />} />
            <Legend wrapperStyle={{ fontSize: 12 }} content={<DecisionLegend />} />
            <Line
              type="monotone"
              dataKey="close"
              name={`${ticker} close`}
              stroke="rgb(var(--accent))"
              strokeWidth={1.5}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
            <Scatter
              dataKey="decisionPrice"
              shape={<DecisionDot />}
              isAnimationActive={false}
            />
            <Brush
              dataKey="date"
              height={20}
              stroke="rgb(var(--muted))"
              fill="rgb(var(--surface))"
              travellerWidth={6}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <DecisionTable decisions={q.data.decisions} />
    </div>
  );
}

/** Coloured circle for each decision, sized so it's clickable on touch. */
function DecisionDot(props: any) {
  const { cx, cy, payload } = props;
  if (cx == null || cy == null || payload?.decisionPrice == null) return null;
  const color = DECISION_COLOR[payload.decision] ?? "#6b7280";
  const handleClick = () => {
    if (payload.runId && typeof window !== "undefined") {
      // Open in a new tab so the chart doesn't lose state.
      window.open(`/history/${payload.runId}`, "_blank", "noopener,noreferrer");
    }
  };
  return (
    <circle
      cx={cx}
      cy={cy}
      r={6}
      fill={color}
      stroke="black"
      strokeWidth={0.8}
      style={{ cursor: payload.runId ? "pointer" : "default" }}
      onClick={handleClick}
    />
  );
}

function DecisionTooltip({ active, payload }: any) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload ?? {};
  return (
    <div
      className="text-xs"
      style={{
        background: "rgb(var(--surface))",
        border: "1px solid rgb(var(--border))",
        borderRadius: 6,
        padding: "6px 10px",
        color: "rgb(var(--fg))",
      }}
    >
      <div className="text-muted">{row.date}</div>
      {row.close != null && (
        <div>
          Close: <span className="tabular-nums">${row.close.toFixed(2)}</span>
        </div>
      )}
      {row.decision && (
        <div className="mt-1">
          <span
            className="font-semibold"
            style={{ color: DECISION_COLOR[row.decision] ?? "inherit" }}
          >
            {row.decision}
          </span>{" "}
          <span className="text-muted">
            ({DECISION_LABEL_PLAIN[row.decision] ?? "—"})
          </span>
          {row.provider && (
            <div className="text-muted">via {row.provider}</div>
          )}
          <div className="text-muted text-[10px]">click to open run</div>
        </div>
      )}
    </div>
  );
}

/** Decision legend in 5-tier colour order. */
function DecisionLegend() {
  return (
    <div className="flex flex-wrap gap-3 justify-center text-xs">
      {Object.entries(DECISION_COLOR).map(([decision, color]) => (
        <span key={decision} className="flex items-center gap-1">
          <span
            className="inline-block w-3 h-3 rounded-full"
            style={{ background: color, border: "1px solid black" }}
          />
          {decision}
        </span>
      ))}
    </div>
  );
}

/** Tabular list of decisions below the chart, click-through to each run. */
function DecisionTable({
  decisions,
}: {
  decisions: Array<{
    trade_date: string;
    run_id: string;
    decision: string | null;
    provider: string | null;
  }>;
}) {
  if (decisions.length === 0) return null;
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted hover:text-fg">
        {decisions.length} decisions in this window
      </summary>
      <table className="w-full mt-2">
        <thead>
          <tr className="text-muted">
            <th className="text-left py-1 px-2">Date</th>
            <th className="text-left py-1 px-2">Decision</th>
            <th className="text-left py-1 px-2">Provider</th>
            <th className="text-left py-1 px-2">Run</th>
          </tr>
        </thead>
        <tbody>
          {decisions
            .slice()
            .sort((a, b) => b.trade_date.localeCompare(a.trade_date))
            .map((d) => (
              <tr key={d.run_id} className="border-t border-border">
                <td className="py-1 px-2">{d.trade_date}</td>
                <td
                  className="py-1 px-2 font-semibold"
                  style={{ color: DECISION_COLOR[d.decision ?? "Hold"] }}
                >
                  {d.decision ?? "—"}
                </td>
                <td className="py-1 px-2 text-muted">{d.provider ?? "—"}</td>
                <td className="py-1 px-2">
                  <Link
                    href={`/history/${d.run_id}`}
                    className="text-accent hover:underline"
                  >
                    {d.run_id.slice(0, 8)}…
                  </Link>
                </td>
              </tr>
            ))}
        </tbody>
      </table>
    </details>
  );
}
