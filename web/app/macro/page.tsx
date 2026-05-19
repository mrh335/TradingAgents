"use client";

import { useQuery } from "@tanstack/react-query";
import { Macro, type MacroPoint, type SectorRow } from "@/lib/api";

// ──────────────────────────────────────────────────────────────────────
// Macro dashboard — cross-asset regime snapshot + sector rotation.
//
// Two glance-checks for "what kind of market is this?":
//
// 1. **Macro series** — VIX, rates, USD, oil, gold, credit. Composite
//    indicators tell you whether the market is risk-on or risk-off,
//    inflationary or deflationary, calm or stressed.
//
// 2. **Sector rotation** — % returns of the 11 S&P sector ETFs over
//    1m / 3m / 6m / YTD. The leaders/laggards rotation tells you
//    which themes are driving the market right now.
//
// Both refresh on a 5-minute interval (macro data doesn't change fast).
// ──────────────────────────────────────────────────────────────────────

const REGIME_TONE: Record<string, string> = {
  stressed: "bg-danger/20 text-danger border-danger",
  cautious: "bg-warning/20 text-warning border-warning",
  calm: "bg-success/20 text-success border-success",
  complacent: "bg-accent/20 text-accent border-accent",
};

const REGIME_BLURB: Record<string, string> = {
  stressed:
    "VIX over 25 — market is pricing fear. Historically, this is when bargains appear, but also when drawdowns happen.",
  cautious:
    "VIX 18-25 — elevated but not panicky. Headwinds present.",
  calm: "VIX 12-18 — normal trading conditions. Trend-following works.",
  complacent:
    "VIX under 12 — extreme calm, often a contrarian warning sign. The market is pricing perfection.",
};

function fmtNum(n: number | null, digits = 2): string {
  if (n === null || n === undefined) return "—";
  return n.toFixed(digits);
}

function fmtPct(n: number | null, withSign = true): string {
  if (n === null || n === undefined) return "—";
  const sign = withSign && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function pctTone(n: number | null): string {
  if (n === null || n === undefined) return "text-muted";
  if (n > 0) return "text-success";
  if (n < 0) return "text-danger";
  return "text-muted";
}

// Heatmap cell background tone — fades from red (-5%+) through neutral
// to green (+5%+). Tailwind classes only.
function heatmapCell(pct: number | null): string {
  if (pct === null || pct === undefined) return "bg-surface";
  const v = Math.max(-10, Math.min(10, pct));
  if (v > 5) return "bg-success/40 text-success";
  if (v > 2) return "bg-success/25 text-success";
  if (v > 0) return "bg-success/10";
  if (v < -5) return "bg-danger/40 text-danger";
  if (v < -2) return "bg-danger/25 text-danger";
  if (v < 0) return "bg-danger/10";
  return "bg-surface";
}

export default function MacroPage() {
  const dashQ = useQuery({
    queryKey: ["macro-dashboard"],
    queryFn: () => Macro.dashboard(),
    refetchInterval: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
  const rotQ = useQuery({
    queryKey: ["sector-rotation"],
    queryFn: () => Macro.sectorRotation(),
    refetchInterval: 5 * 60_000,
    refetchOnWindowFocus: false,
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Macro & sector rotation</h1>
        <p className="text-muted text-sm">
          Cross-asset regime snapshot + which S&P sectors are leading or
          lagging. Use this as your first 30-second check of the day — is the
          backdrop favorable for risk-on, risk-off, or sideways trading?
        </p>
      </header>

      {/* ─── Regime tag ─── */}
      {dashQ.data?.derived?.regime && (
        <div
          className={`card border-l-4 ${REGIME_TONE[dashQ.data.derived.regime] ?? ""}`}
        >
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="text-xs uppercase tracking-wider opacity-70">
              Regime
            </span>
            <span className="text-2xl font-bold capitalize">
              {dashQ.data.derived.regime}
            </span>
            {dashQ.data.derived.vix_level !== undefined && (
              <span className="text-sm">
                VIX {dashQ.data.derived.vix_level}
              </span>
            )}
          </div>
          <p className="text-sm mt-1">
            {REGIME_BLURB[dashQ.data.derived.regime]}
          </p>
        </div>
      )}

      {/* ─── Macro series ─── */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Cross-asset levels</h2>
        {dashQ.isLoading ? (
          <div className="card text-muted text-sm">Loading…</div>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-muted">
                <tr>
                  <th className="py-2">Series</th>
                  <th className="text-right">Last</th>
                  <th className="text-right">1d</th>
                  <th className="text-right">1w</th>
                  <th className="text-right">1m</th>
                  <th className="pl-4">What it tells you</th>
                </tr>
              </thead>
              <tbody>
                {(dashQ.data?.series ?? []).map((p: MacroPoint) => (
                  <tr key={p.ticker} className="border-t border-border">
                    <td className="py-2">
                      <div className="font-semibold">{p.label}</div>
                      <div className="text-xs text-muted">{p.ticker}</div>
                    </td>
                    <td className="text-right tabular-nums">{fmtNum(p.last, 2)}</td>
                    <td className={`text-right tabular-nums ${pctTone(p.pct_1d)}`}>
                      {fmtPct(p.pct_1d)}
                    </td>
                    <td className={`text-right tabular-nums ${pctTone(p.pct_1w)}`}>
                      {fmtPct(p.pct_1w)}
                    </td>
                    <td className={`text-right tabular-nums ${pctTone(p.pct_1m)}`}>
                      {fmtPct(p.pct_1m)}
                    </td>
                    <td className="pl-4 text-xs text-muted">{p.hint}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Composite signals */}
        {dashQ.data?.derived && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
            {dashQ.data.derived["10y_minus_3mo_spread_pct"] !== undefined && (
              <div className="card">
                <div className="text-xs text-muted">10y − 3mo spread</div>
                <div
                  className={`text-xl font-bold ${dashQ.data.derived["10y_minus_3mo_inverted"] ? "text-danger" : "text-success"}`}
                >
                  {dashQ.data.derived["10y_minus_3mo_spread_pct"]}%
                </div>
                <div className="text-xs text-muted mt-0.5">
                  {dashQ.data.derived["10y_minus_3mo_inverted"]
                    ? "INVERTED — recession signal"
                    : "Normal slope"}
                </div>
              </div>
            )}
            {dashQ.data.derived.hyg_ief_ratio !== undefined && (
              <div className="card">
                <div className="text-xs text-muted">HYG / IEF ratio</div>
                <div className="text-xl font-bold">
                  {dashQ.data.derived.hyg_ief_ratio.toFixed(4)}
                </div>
                <div className="text-xs text-muted mt-0.5">
                  Credit risk-on proxy
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      {/* ─── Sector rotation heatmap ─── */}
      <section>
        <h2 className="text-lg font-semibold mb-1">Sector rotation</h2>
        {rotQ.data?.leadership && (
          <p className="text-xs text-muted mb-3">
            <strong>Leading (3m):</strong>{" "}
            {rotQ.data.leadership.top_3_3m.join(" · ")}
            {" — "}
            <strong>lagging (3m):</strong>{" "}
            {rotQ.data.leadership.bottom_3_3m.join(" · ")}
            {rotQ.data.leadership.spread_3m_pct !== null && (
              <> · top-bottom spread {rotQ.data.leadership.spread_3m_pct.toFixed(1)}%</>
            )}
          </p>
        )}
        {rotQ.isLoading ? (
          <div className="card text-muted text-sm">Loading…</div>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-muted">
                <tr>
                  <th className="py-2">Sector</th>
                  <th>ETF</th>
                  <th className="text-right">Last</th>
                  <th className="text-right">1m</th>
                  <th className="text-right">3m</th>
                  <th className="text-right">6m</th>
                  <th className="text-right">YTD</th>
                </tr>
              </thead>
              <tbody>
                {(rotQ.data?.rows ?? []).map((r: SectorRow) => (
                  <tr key={r.ticker} className="border-t border-border">
                    <td className="py-2 font-semibold">{r.sector}</td>
                    <td className="text-xs text-muted">{r.ticker}</td>
                    <td className="text-right tabular-nums">{fmtNum(r.last, 2)}</td>
                    <td className={`text-right tabular-nums px-2 ${heatmapCell(r.pct_1m)}`}>
                      {fmtPct(r.pct_1m)}
                    </td>
                    <td className={`text-right tabular-nums px-2 ${heatmapCell(r.pct_3m)}`}>
                      {fmtPct(r.pct_3m)}
                    </td>
                    <td className={`text-right tabular-nums px-2 ${heatmapCell(r.pct_6m)}`}>
                      {fmtPct(r.pct_6m)}
                    </td>
                    <td className={`text-right tabular-nums px-2 ${heatmapCell(r.pct_ytd)}`}>
                      {fmtPct(r.pct_ytd)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="card text-xs text-muted">
        <strong>Sources:</strong> yfinance daily closes for all series. The
        regime tag is a heuristic on VIX level (under 12 = complacent, 12-18 =
        calm, 18-25 = cautious, over 25 = stressed). Composite signals
        (yield-curve spread, HYG/IEF ratio) are derived from the underlying
        series. Page refreshes every 5 minutes; underlying data is end-of-day
        for most series.
      </div>
    </div>
  );
}
