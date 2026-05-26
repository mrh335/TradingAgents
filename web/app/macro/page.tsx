"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Macro,
  Regime,
  type MacroPoint,
  type SectorRow,
  type RegimePerformanceRow,
} from "@/lib/api";

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

      {/* Markov + HMM regime section */}
      <RegimeSection />

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

// ───────────────────────────────────────────────────────────────────────
// Regime section — combines all 3 tiers in a single panel:
//   tier 1: current regime card (VIX + 200d-SMA classification)
//   tier 2: transition matrix + stationary distribution + 30d forecast
//   tier 3: HMM-fitted comparison
// + framework hit rate per regime (joins to historical runs)
// ───────────────────────────────────────────────────────────────────────

const REGIME_ROW_LABEL: Record<string, string> = {
  CALM_BULL: "Calm bull",
  VOLATILE_BULL: "Volatile bull",
  VOLATILE_BEAR: "Volatile bear",
  CALM_BEAR: "Calm bear",
};

const REGIME_ROW_TONE: Record<string, string> = {
  CALM_BULL: "text-success",
  VOLATILE_BULL: "text-warning",
  VOLATILE_BEAR: "text-danger",
  CALM_BEAR: "text-muted",
};

function regimeCellColor(p: number): string {
  // Probability heatmap: 0 = invisible bg, 1 = strong accent
  const v = Math.max(0, Math.min(1, p));
  if (v > 0.5) return "bg-accent/40";
  if (v > 0.25) return "bg-accent/20";
  if (v > 0.1) return "bg-accent/10";
  return "";
}

function RegimeSection() {
  const snap = useQuery({
    queryKey: ["regime-snapshot"],
    queryFn: () => Regime.snapshot(),
    refetchInterval: 60 * 60_000,
  });
  const hmm = useQuery({
    queryKey: ["regime-hmm"],
    queryFn: () => Regime.hmm(4),
    refetchInterval: 24 * 60 * 60_000,
  });
  const perf = useQuery({
    queryKey: ["regime-perf", 30, 365],
    queryFn: () => Regime.byRunPerformance(30, 365),
    refetchInterval: 60 * 60_000,
  });

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Market regime (Markov + HMM)</h2>

      {/* ── Tier 1: current regime card ── */}
      {snap.data?.available && snap.data.current_regime && (
        <div
          className={`card border-l-4 ${
            REGIME_ROW_TONE[snap.data.current_regime] === "text-success"
              ? "border-l-success"
              : REGIME_ROW_TONE[snap.data.current_regime] === "text-warning"
                ? "border-l-warning"
                : REGIME_ROW_TONE[snap.data.current_regime] === "text-danger"
                  ? "border-l-danger"
                  : "border-l-muted"
          }`}
        >
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="text-xs uppercase tracking-wider text-muted">
              Current regime (rule-based)
            </span>
            <span
              className={`text-2xl font-bold ${REGIME_ROW_TONE[snap.data.current_regime]}`}
            >
              {snap.data.current_label}
            </span>
            <span className="text-xs text-muted">
              SPY {snap.data.current_spy?.toFixed(2)} ·{" "}
              {snap.data.current_sma_200 && snap.data.current_spy && (
                <>
                  {snap.data.current_spy > snap.data.current_sma_200 ? "above" : "below"}{" "}
                  200d SMA ({snap.data.current_sma_200.toFixed(2)})
                </>
              )}{" "}
              · VIX {snap.data.current_vix?.toFixed(2)}
            </span>
          </div>
          {snap.data.current_blurb && (
            <p className="text-sm mt-2">{snap.data.current_blurb}</p>
          )}
        </div>
      )}

      {/* ── Tier 2: transition matrix + stationary + forecast ── */}
      {snap.data?.available && snap.data.transition_matrix.length > 0 && (
        <>
          <div className="card overflow-x-auto">
            <div className="font-semibold text-sm mb-2">
              Transition probabilities (day → day)
            </div>
            <p className="text-xs text-muted mb-3">
              Rows = today&apos;s regime. Columns = tomorrow&apos;s regime.
              Cells show the historical probability of moving from row →
              column in one trading day. Diagonal probability = stickiness
              (how often the regime persists).
            </p>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted">
                  <th className="py-1 pr-2">from \ to</th>
                  {snap.data.regime_order.map((r) => (
                    <th key={r} className="text-center px-2 py-1">
                      {REGIME_ROW_LABEL[r] ?? r}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {snap.data.transition_matrix.map((row, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className={`py-1 pr-2 font-semibold ${REGIME_ROW_TONE[snap.data!.regime_order[i]]}`}>
                      {REGIME_ROW_LABEL[snap.data!.regime_order[i]] ?? snap.data!.regime_order[i]}
                    </td>
                    {row.map((p, j) => (
                      <td
                        key={j}
                        className={`text-center px-2 py-1 tabular-nums ${regimeCellColor(p)} ${i === j ? "font-semibold" : ""}`}
                        title={`${snap.data!.regime_order[i]} → ${snap.data!.regime_order[j]}: ${(p * 100).toFixed(1)}%`}
                      >
                        {(p * 100).toFixed(0)}%
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {/* Stationary distribution */}
            <div className="card">
              <div className="font-semibold text-sm mb-2">
                Long-run time in each regime (stationary)
              </div>
              <p className="text-xs text-muted mb-2">
                What % of trading days the market spends in each regime over a
                very long horizon. From the eigenvector of the transition
                matrix at eigenvalue 1.
              </p>
              <table className="w-full text-sm">
                <tbody>
                  {snap.data.regime_order.map((r) => {
                    const p = snap.data!.stationary[r] ?? 0;
                    return (
                      <tr key={r} className="border-t border-border first:border-t-0">
                        <td className={`py-1.5 ${REGIME_ROW_TONE[r]}`}>
                          {REGIME_ROW_LABEL[r] ?? r}
                        </td>
                        <td className="py-1.5 text-right tabular-nums">
                          {(p * 100).toFixed(1)}%
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* 30-day forecast */}
            <div className="card">
              <div className="font-semibold text-sm mb-2">
                30-day forecast (from current state)
              </div>
              <p className="text-xs text-muted mb-2">
                Probability of being in each regime 30 trading days from
                now, given today&apos;s state. Matrix^30 from the current
                state vector.
              </p>
              <table className="w-full text-sm">
                <tbody>
                  {snap.data.regime_order.map((r) => {
                    const p = snap.data!.forecast_30d[r] ?? 0;
                    return (
                      <tr key={r} className="border-t border-border first:border-t-0">
                        <td className={`py-1.5 ${REGIME_ROW_TONE[r]}`}>
                          {REGIME_ROW_LABEL[r] ?? r}
                        </td>
                        <td className="py-1.5 text-right tabular-nums">
                          {(p * 100).toFixed(1)}%
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* ── Tier 3: HMM ── */}
      {hmm.data?.available ? (
        <div className="card">
          <div className="font-semibold text-sm mb-1">
            HMM (Hidden Markov Model) — data-driven regimes
          </div>
          <div className="text-xs text-muted mb-3">
            A {hmm.data.n_states}-state GaussianHMM fit (Baum-Welch / EM)
            on (SPY daily log return, VIX level) over {hmm.data.n_days_observed}{" "}
            trading days. States are auto-labeled by mean return + variance,
            then mapped to the same 4 canonical regimes as tier 1 for
            comparison.{" "}
            <strong>
              HMM and rule-based agreement:{" "}
              {hmm.data.tier1_agreement_pct !== null
                ? `${hmm.data.tier1_agreement_pct}%`
                : "—"}
            </strong>
            {" "}of days. Disagreements are usually the HMM detecting a regime
            shift slightly before the rule-based threshold triggers (or vice
            versa).
          </div>
          <div className="text-xs">
            <strong>Current HMM regime:</strong>{" "}
            <span className={REGIME_ROW_TONE[hmm.data.current_regime ?? ""]}>
              {REGIME_ROW_LABEL[hmm.data.current_regime ?? ""] ?? hmm.data.current_regime}
            </span>
          </div>
        </div>
      ) : hmm.isLoading ? (
        <div className="card text-sm text-muted">
          Fitting HMM (~5-10s on cold cache, then cached 24h)…
        </div>
      ) : (
        <div className="card text-sm text-warning">
          HMM unavailable: {hmm.data?.error ?? "unknown"}
        </div>
      )}

      {/* ── Framework hit rate stratified by regime ── */}
      {perf.data && perf.data.rows.length > 0 && (
        <div className="card">
          <div className="font-semibold text-sm mb-1">
            Framework hit rate by regime (last {perf.data.lookback_days} days)
          </div>
          <p className="text-xs text-muted mb-3">
            For every completed run with a +{perf.data.window_days}d horizon
            reached, joined to the regime that was active on its trade_date.
            <strong> Baseline (un-stratified):</strong>{" "}
            {perf.data.baseline_hit_rate_pct ?? "—"}% hit rate ·{" "}
            {perf.data.baseline_mean_alpha_pct?.toFixed(2) ?? "—"}% mean alpha.
            Regimes where the framework outperforms the baseline are where it
            actually adds value — down-weight conviction in the others.
          </p>
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-muted">
              <tr>
                <th className="py-2">Regime</th>
                <th className="text-right">Runs</th>
                <th className="text-right">Hit rate</th>
                <th className="text-right">vs baseline</th>
                <th className="text-right">Mean alpha</th>
                <th>Decision mix</th>
              </tr>
            </thead>
            <tbody>
              {perf.data.rows.map((r: RegimePerformanceRow) => {
                const delta =
                  r.hit_rate_pct !== null && perf.data!.baseline_hit_rate_pct !== null
                    ? r.hit_rate_pct - perf.data!.baseline_hit_rate_pct
                    : null;
                const deltaTone =
                  delta === null
                    ? "text-muted"
                    : delta > 5
                      ? "text-success font-semibold"
                      : delta < -5
                        ? "text-danger font-semibold"
                        : "text-muted";
                return (
                  <tr key={r.regime} className="border-t border-border">
                    <td className={`py-1.5 font-semibold ${REGIME_ROW_TONE[r.regime]}`}>
                      {REGIME_ROW_LABEL[r.regime] ?? r.regime}
                    </td>
                    <td className="text-right tabular-nums">{r.n_runs}</td>
                    <td className="text-right tabular-nums">
                      {r.hit_rate_pct !== null ? `${r.hit_rate_pct}%` : "—"}
                    </td>
                    <td className={`text-right tabular-nums ${deltaTone}`}>
                      {delta !== null
                        ? `${delta > 0 ? "+" : ""}${delta.toFixed(1)}pp`
                        : "—"}
                    </td>
                    <td className={`text-right tabular-nums ${(r.mean_alpha_pct ?? 0) > 0 ? "text-success" : (r.mean_alpha_pct ?? 0) < 0 ? "text-danger" : "text-muted"}`}>
                      {r.mean_alpha_pct !== null
                        ? `${r.mean_alpha_pct > 0 ? "+" : ""}${r.mean_alpha_pct.toFixed(2)}%`
                        : "—"}
                    </td>
                    <td className="text-xs text-muted">
                      {Object.entries(r.decisions)
                        .map(([d, n]) => `${d}: ${n}`)
                        .join(" · ") || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
