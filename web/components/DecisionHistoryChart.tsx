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
  ReferenceLine,
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

  // Merge decisions + dividends onto the price series by date so Recharts can
  // render everything on one composed chart. Each row carries:
  //   { date, close, decisionPrice, dividendY (anchor on the price line),
  //     decision, runId, provider, dividendAmount }
  // Splits stay as a separate event list rendered as ReferenceLines below.
  const data = useMemo(() => {
    if (!q.data) return [];
    const priceByDate = new Map(q.data.price_series.map((p) => [p.date, p.close]));
    const dates = q.data.price_series.map((p) => p.date).sort();
    function nearestPrice(date: string): number | null {
      if (priceByDate.has(date)) return priceByDate.get(date)!;
      const after = dates.find((d) => d >= date);
      if (after) return priceByDate.get(after) ?? null;
      const before = [...dates].reverse().find((d) => d <= date);
      return before ? priceByDate.get(before) ?? null : null;
    }
    const decisionByDate = new Map<string, typeof q.data.decisions[0]>();
    for (const d of q.data.decisions) decisionByDate.set(d.trade_date, d);
    const divByDate = new Map(q.data.dividends.map((d) => [d.date, d.amount]));
    // Union of every date that has *something* worth a row on the chart.
    const allDates = Array.from(
      new Set([
        ...q.data.price_series.map((p) => p.date),
        ...q.data.decisions.map((d) => d.trade_date),
        ...q.data.dividends.map((d) => d.date),
      ]),
    ).sort();
    // Anchor for dividend triangles: a fixed fraction below the lowest
    // observed close so the triangles sit cleanly at the bottom edge of
    // the plot area, not floating on the line.
    const minClose = q.data.price_series.length
      ? Math.min(...q.data.price_series.map((p) => p.close))
      : 0;
    const dividendAnchor = minClose > 0 ? minClose * 0.985 : 0;
    return allDates.map((date) => {
      const close = priceByDate.get(date) ?? null;
      const decision = decisionByDate.get(date);
      const dividendAmount = divByDate.get(date) ?? null;
      return {
        date,
        close,
        decisionPrice: decision ? nearestPrice(date) : null,
        decision: decision?.decision ?? null,
        runId: decision?.run_id ?? null,
        provider: decision?.provider ?? null,
        dividendAmount,
        dividendY: dividendAmount != null ? dividendAnchor : null,
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
              tickFormatter={(v) => (typeof v === "number" ? `$${v.toFixed(0)}` : v)}
              label={{
                value: "Price ($, split-adj)",
                angle: -90,
                position: "insideLeft",
                style: { fill: "rgb(var(--muted))", fontSize: 11 },
              }}
            />
            <Tooltip content={<DecisionTooltip />} />
            <Legend wrapperStyle={{ fontSize: 12 }} content={<ChartLegend />} />
            {/* Vertical lines for stock splits with ratio annotation */}
            {(q.data.splits ?? []).map((s) => (
              <ReferenceLine
                key={`split-${s.date}`}
                x={s.date}
                stroke="#7c3aed"
                strokeWidth={1.5}
                strokeDasharray="0"
                label={{
                  value: `${s.ratio}× split`,
                  position: "top",
                  fill: "#7c3aed",
                  fontSize: 10,
                  fontWeight: 600,
                }}
              />
            ))}
            <Line
              type="monotone"
              dataKey="close"
              name={`${ticker} close (split-adj)`}
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
            <Scatter
              dataKey="dividendY"
              shape={<DividendMarker />}
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

      {/* Corporate-action summary line under the chart */}
      {(q.data.splits.length > 0 || q.data.dividends.length > 0) && (
        <CorporateActionsSummary
          splits={q.data.splits}
          dividends={q.data.dividends}
        />
      )}

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

/** Small upward triangle along the bottom of the plot area marking a
 *  dividend payout. Tooltip on hover shows the amount per share. */
function DividendMarker(props: any) {
  const { cx, cy, payload } = props;
  if (cx == null || cy == null || payload?.dividendAmount == null) return null;
  const size = 5;
  // Equilateral triangle pointing up, anchored on (cx, cy).
  const points = [
    [cx, cy - size],
    [cx - size, cy + size * 0.7],
    [cx + size, cy + size * 0.7],
  ]
    .map(([x, y]) => `${x},${y}`)
    .join(" ");
  return (
    <polygon
      points={points}
      fill="#059669"
      stroke="black"
      strokeWidth={0.5}
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
      {row.dividendAmount != null && (
        <div className="mt-1" style={{ color: "#059669" }}>
          ▲ Dividend: <span className="tabular-nums">${row.dividendAmount.toFixed(4)}</span>
          /share
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

/** Chart legend covering decisions + corporate-action overlays. */
function ChartLegend() {
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
      <span className="flex items-center gap-1">
        <span
          className="inline-block"
          style={{
            width: 0,
            height: 0,
            borderLeft: "5px solid transparent",
            borderRight: "5px solid transparent",
            borderBottom: "8px solid #059669",
          }}
        />
        Dividend
      </span>
      <span className="flex items-center gap-1">
        <span
          className="inline-block"
          style={{
            width: 2,
            height: 12,
            background: "#7c3aed",
          }}
        />
        Stock split
      </span>
    </div>
  );
}

/** Compact tabular summary of every split + dividend in the window. */
function CorporateActionsSummary({
  splits,
  dividends,
}: {
  splits: Array<{ date: string; ratio: number }>;
  dividends: Array<{ date: string; amount: number }>;
}) {
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted hover:text-fg">
        Corporate actions in window: {splits.length} split{splits.length === 1 ? "" : "s"},{" "}
        {dividends.length} dividend{dividends.length === 1 ? "" : "s"}
      </summary>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-2">
        {splits.length > 0 && (
          <div>
            <div className="text-muted uppercase tracking-wider text-[10px] mb-1">
              Stock splits
            </div>
            <table className="w-full">
              <tbody>
                {splits.map((s) => (
                  <tr key={s.date} className="border-t border-border">
                    <td className="py-1 pr-2">{s.date}</td>
                    <td
                      className="py-1 text-right tabular-nums"
                      style={{ color: "#7c3aed", fontWeight: 600 }}
                    >
                      {s.ratio}-for-1
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {dividends.length > 0 && (
          <div>
            <div className="text-muted uppercase tracking-wider text-[10px] mb-1">
              Dividends (per share)
            </div>
            <table className="w-full">
              <tbody>
                {dividends
                  .slice()
                  .sort((a, b) => b.date.localeCompare(a.date))
                  .map((d) => (
                    <tr key={d.date} className="border-t border-border">
                      <td className="py-1 pr-2">{d.date}</td>
                      <td
                        className="py-1 text-right tabular-nums"
                        style={{ color: "#059669", fontWeight: 600 }}
                      >
                        ${d.amount.toFixed(4)}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
            <div className="text-muted text-[10px] mt-1">
              Total in window:{" "}
              <span className="tabular-nums">
                ${dividends.reduce((acc, d) => acc + d.amount, 0).toFixed(4)}
              </span>
              /share
            </div>
          </div>
        )}
      </div>
    </details>
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
