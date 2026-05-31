"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Simulation } from "@/lib/api";
import type {
  BacktestResponse, MonteCarloResponse, StatPack,
} from "@/lib/simTypes";

// --------------------------------------------------------------------------
// Small formatting helpers (all null-safe — the engine returns null freely)
// --------------------------------------------------------------------------
const pct = (x: number | null | undefined, d = 1) =>
  x == null || !Number.isFinite(x) ? "—" : `${(x * 100).toFixed(d)}%`;
const usd = (x: number | null | undefined) =>
  x == null || !Number.isFinite(x) ? "—" : `$${Math.round(x).toLocaleString()}`;
const num = (x: number | null | undefined, d = 2) =>
  x == null || !Number.isFinite(x) ? "—" : x.toFixed(d);

const COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#a855f7", "#ec4899", "#14b8a6", "#ef4444"];
const BENCH_COLOR = "#94a3b8";

type Scenario = { name: string; weights: Record<string, number> };

// Parse a "AAPL:0.5, MSFT:0.5" or "AAPL 60, MSFT 40" weight string.
function parseWeights(s: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const part of s.split(/[,\n]/)) {
    const m = part.trim().match(/^([A-Za-z.\-^]+)\s*[:=\s]\s*([0-9.]+)$/);
    if (m) out[m[1].toUpperCase()] = parseFloat(m[2]);
  }
  return out;
}
function weightsToStr(w: Record<string, number>): string {
  return Object.entries(w)
    .map(([t, v]) => `${t}:${v}`)
    .join(", ");
}

const STAT_ROWS: { key: keyof StatPack; label: string; fmt: (v: any) => string; hint: string }[] = [
  { key: "total_return", label: "Total return", fmt: (v) => pct(v), hint: "Full-period gain" },
  { key: "cagr", label: "CAGR (per year)", fmt: (v) => pct(v), hint: "Compound annual growth rate" },
  { key: "volatility", label: "Volatility", fmt: (v) => pct(v), hint: "Annualized std-dev — the bumpiness of the ride" },
  { key: "sharpe", label: "Sharpe", fmt: (v) => num(v), hint: "Return per unit of total bumpiness (higher better)" },
  { key: "sortino", label: "Sortino", fmt: (v) => num(v), hint: "Like Sharpe but only counts downside bumpiness" },
  { key: "calmar", label: "Calmar", fmt: (v) => num(v), hint: "CAGR ÷ worst drawdown (reward vs worst pain)" },
  { key: "max_drawdown", label: "Max drawdown", fmt: (v) => pct(v), hint: "Worst peak-to-trough drop along the way" },
  { key: "beta", label: "Beta vs SPY", fmt: (v) => num(v), hint: "Market sensitivity (1 = moves with SPY)" },
  { key: "alpha", label: "Alpha (per year)", fmt: (v) => pct(v), hint: "Annual return beyond what beta explains" },
  { key: "final_value", label: "Final value", fmt: (v) => usd(v), hint: "Ending value of the starting capital" },
];

export default function SimulationPage() {
  const [benchmark] = useState("SPY");
  const [period, setPeriod] = useState("6y");
  const [initial, setInitial] = useState(100_000);
  const [rebalance, setRebalance] = useState<"none" | "daily">("none");
  const [scenarios, setScenarios] = useState<Scenario[]>([]);

  // Seed the four presets once the live portfolio mix loads.
  const actual = useQuery({
    queryKey: ["sim-portfolio-actual"],
    queryFn: () => Simulation.portfolioActual(),
    retry: false,
  });

  useEffect(() => {
    if (scenarios.length) return;
    const yourMix = actual.data?.weights;
    const seeded: Scenario[] = [];
    if (yourMix && Object.keys(yourMix).length) {
      seeded.push({ name: "Your actual mix", weights: yourMix });
      seeded.push({
        name: "Equal-weight your names",
        weights: Object.fromEntries(Object.keys(yourMix).map((t) => [t, 1])),
      });
    }
    seeded.push({ name: "Nasdaq-100 (QQQ)", weights: { QQQ: 1 } });
    seeded.push({ name: "Total market (VTI)", weights: { VTI: 1 } });
    seeded.push({
      name: "Diversified big-6 tech",
      weights: { AAPL: 1, MSFT: 1, NVDA: 1, GOOGL: 1, AMZN: 1, META: 1 },
    });
    setScenarios(seeded);
  }, [actual.data, scenarios.length]);

  const backtest = useMutation({
    mutationFn: () =>
      Simulation.backtest({
        scenarios: scenarios.filter((s) => Object.keys(s.weights).length),
        benchmark,
        period,
        initial,
        rebalance,
        windows: [1, 2, 3, 5],
      }),
  });

  // Monte Carlo runs on one selected scenario.
  const [mcIdx, setMcIdx] = useState(0);
  const [mcHorizon, setMcHorizon] = useState(252);
  const [mcPaths, setMcPaths] = useState(5000);
  const [mcMethod, setMcMethod] = useState<"bootstrap" | "normal">("bootstrap");
  const montecarlo = useMutation({
    mutationFn: () => {
      const s = scenarios[mcIdx];
      return Simulation.montecarlo({
        weights: s.weights, benchmark, period: "5y",
        horizon_days: mcHorizon, n_paths: mcPaths, method: mcMethod, initial,
      });
    },
  });

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold">Portfolio lab — backtest, risk & Monte Carlo</h1>
        <p className="text-muted text-sm max-w-3xl">
          Compare allocation strategies on <strong>real total-return history</strong> (dividends
          reinvested), with the statistics a professional would use — CAGR, volatility,
          Sharpe/Sortino, max drawdown, beta &amp; alpha vs SPY — then project any mix forward with
          Monte Carlo. Every strategy is measured over the <em>same</em> window, so differences are
          the strategy, not timing luck.
        </p>
      </header>

      <ScenarioBuilder
        scenarios={scenarios}
        setScenarios={setScenarios}
        period={period} setPeriod={setPeriod}
        initial={initial} setInitial={setInitial}
        rebalance={rebalance} setRebalance={setRebalance}
        onRun={() => backtest.mutate()}
        running={backtest.isPending}
        actualLoading={actual.isLoading}
      />

      {backtest.isError && (
        <div className="card text-danger text-sm">
          Backtest failed: {(backtest.error as Error)?.message ?? "unknown error"}
        </div>
      )}

      {backtest.data && <BacktestResults data={backtest.data} initial={initial} />}

      {/* ---- Monte Carlo ---- */}
      {scenarios.length > 0 && (
        <div className="card space-y-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <h2 className="text-lg font-semibold">Monte Carlo — forward projection</h2>
            <div className="flex items-end gap-2 flex-wrap">
              <Field label="Scenario">
                <select className="input" value={mcIdx} onChange={(e) => setMcIdx(Number(e.target.value))}>
                  {scenarios.map((s, i) => <option key={i} value={i}>{s.name}</option>)}
                </select>
              </Field>
              <Field label="Horizon (days)">
                <input className="input w-24" type="number" value={mcHorizon}
                  onChange={(e) => setMcHorizon(Number(e.target.value))} />
              </Field>
              <Field label="Paths">
                <input className="input w-24" type="number" value={mcPaths}
                  onChange={(e) => setMcPaths(Number(e.target.value))} />
              </Field>
              <Field label="Method">
                <select className="input" value={mcMethod}
                  onChange={(e) => setMcMethod(e.target.value as "bootstrap" | "normal")}>
                  <option value="bootstrap">Bootstrap (real returns)</option>
                  <option value="normal">Normal (parametric)</option>
                </select>
              </Field>
              <button className="btn btn-primary" onClick={() => montecarlo.mutate()}
                disabled={montecarlo.isPending}>
                {montecarlo.isPending ? "Simulating…" : "Run Monte Carlo"}
              </button>
            </div>
          </div>
          <p className="text-xs text-muted">
            Draws thousands of possible futures from the last 5 years of daily moves.
            <strong> Bootstrap</strong> resamples real days (keeps fat tails);
            <strong> Normal</strong> assumes a bell curve (smoother, understates crash risk).
          </p>
          {montecarlo.isError && (
            <div className="text-danger text-sm">
              {(montecarlo.error as Error)?.message ?? "Monte Carlo failed"}
            </div>
          )}
          {montecarlo.data && <MonteCarloResults data={montecarlo.data} initial={initial} />}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col">
      <label className="text-xs text-muted mb-0.5">{label}</label>
      {children}
    </div>
  );
}

// --------------------------------------------------------------------------
function ScenarioBuilder({
  scenarios, setScenarios, period, setPeriod, initial, setInitial,
  rebalance, setRebalance, onRun, running, actualLoading,
}: {
  scenarios: Scenario[]; setScenarios: (s: Scenario[]) => void;
  period: string; setPeriod: (s: string) => void;
  initial: number; setInitial: (n: number) => void;
  rebalance: "none" | "daily"; setRebalance: (r: "none" | "daily") => void;
  onRun: () => void; running: boolean; actualLoading: boolean;
}) {
  function update(i: number, field: "name" | "weights", value: string) {
    const next = [...scenarios];
    if (field === "name") next[i] = { ...next[i], name: value };
    else next[i] = { ...next[i], weights: parseWeights(value) };
    setScenarios(next);
  }
  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Scenarios {actualLoading && <span className="text-xs text-muted">(loading your mix…)</span>}</h2>
        <div className="flex items-end gap-2 flex-wrap">
          <Field label="History">
            <select className="input" value={period} onChange={(e) => setPeriod(e.target.value)}>
              <option value="3y">3 years</option>
              <option value="5y">5 years</option>
              <option value="6y">6 years</option>
              <option value="10y">10 years</option>
              <option value="max">Max</option>
            </select>
          </Field>
          <Field label="Capital">
            <input className="input w-28" type="number" value={initial}
              onChange={(e) => setInitial(Number(e.target.value))} />
          </Field>
          <Field label="Rebalance">
            <select className="input" value={rebalance}
              onChange={(e) => setRebalance(e.target.value as "none" | "daily")}>
              <option value="none">Buy &amp; hold</option>
              <option value="daily">Constant weight</option>
            </select>
          </Field>
        </div>
      </div>

      <div className="space-y-2">
        {scenarios.map((s, i) => (
          <div key={i} className="flex gap-2 items-center">
            <span className="w-3 h-3 rounded-full shrink-0" style={{ background: COLORS[i % COLORS.length] }} />
            <input className="input w-48" value={s.name} onChange={(e) => update(i, "name", e.target.value)} />
            <input className="input flex-1 font-mono text-xs" defaultValue={weightsToStr(s.weights)}
              onBlur={(e) => update(i, "weights", e.target.value)}
              placeholder="AAPL:0.6, MSFT:0.4" />
            <button className="btn text-xs" onClick={() => setScenarios(scenarios.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
      </div>

      <div className="flex gap-2">
        <button className="btn text-sm"
          onClick={() => setScenarios([...scenarios, { name: `Scenario ${scenarios.length + 1}`, weights: {} }])}>
          + Add scenario
        </button>
        <button className="btn btn-primary ml-auto" onClick={onRun} disabled={running || !scenarios.length}>
          {running ? "Running backtest…" : "Run backtest"}
        </button>
      </div>
      <p className="text-xs text-muted">
        Weights need not sum to 1 — they’re normalized. Benchmark is SPY (shown as the grey line).
        “Buy &amp; hold” lets winners grow their share; “constant weight” rebalances daily.
      </p>
    </div>
  );
}

// --------------------------------------------------------------------------
function BacktestResults({ data, initial }: { data: BacktestResponse; initial: number }) {
  const valid = data.scenarios.filter((s) => !s.error && s.curve);

  // Merge per-scenario curves + benchmark into one date-keyed array for recharts.
  const merged = useMemo(() => {
    const byDate: Record<string, any> = {};
    for (const s of valid) {
      for (const pt of s.curve ?? []) {
        byDate[pt.date] = byDate[pt.date] ?? { date: pt.date };
        byDate[pt.date][s.name] = pt.value;
      }
    }
    for (const pt of data.benchmark_curve ?? []) {
      byDate[pt.date] = byDate[pt.date] ?? { date: pt.date };
      byDate[pt.date]["SPY (benchmark)"] = pt.value;
    }
    return Object.values(byDate).sort((a: any, b: any) => a.date.localeCompare(b.date));
  }, [data]);

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex items-baseline justify-between mb-2">
          <h2 className="text-lg font-semibold">Growth of {usd(initial)}</h2>
          <span className="text-xs text-muted">{data.start} → {data.as_of}</span>
        </div>
        <ResponsiveContainer width="100%" height={340}>
          <LineChart data={merged} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.15} />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={40} />
            <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
              width={55} domain={["auto", "auto"]} />
            <Tooltip formatter={(v: any) => usd(v)} contentStyle={{ fontSize: 12 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {valid.map((s, i) => (
              <Line key={s.name} type="monotone" dataKey={s.name} dot={false}
                stroke={COLORS[i % COLORS.length]} strokeWidth={2} />
            ))}
            <Line type="monotone" dataKey="SPY (benchmark)" dot={false}
              stroke={BENCH_COLOR} strokeWidth={1.5} strokeDasharray="5 4" />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <StatsTable data={data} />
      <WindowsTable data={data} />
      {Object.keys(data.correlation ?? {}).length >= 2 && <CorrelationHeatmap corr={data.correlation} />}
    </div>
  );
}

// --------------------------------------------------------------------------
function StatsTable({ data }: { data: BacktestResponse }) {
  const cols: { name: string; stats?: StatPack }[] = [
    ...data.scenarios.filter((s) => s.stats).map((s) => ({ name: s.name, stats: s.stats })),
    { name: "SPY (benchmark)", stats: data.benchmark_stats },
  ];
  return (
    <div className="card overflow-x-auto">
      <h3 className="font-semibold mb-2">Risk &amp; return statistics</h3>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-muted text-left border-b border-border">
            <th className="py-1 pr-3 font-medium">Metric</th>
            {cols.map((c) => <th key={c.name} className="py-1 px-2 text-right font-medium">{c.name}</th>)}
          </tr>
        </thead>
        <tbody>
          {STAT_ROWS.map((row) => (
            <tr key={row.key} className="border-b border-border/40">
              <td className="py-1 pr-3" title={row.hint}>{row.label}</td>
              {cols.map((c) => {
                const v = c.stats ? (c.stats as any)[row.key] : null;
                const danger = (row.key === "max_drawdown" || row.key === "worst_day") && v != null && v < 0;
                const good = (row.key === "alpha" || row.key === "total_return" || row.key === "cagr") && v != null && v > 0;
                return (
                  <td key={c.name}
                    className={`py-1 px-2 text-right tabular-nums ${danger ? "text-danger" : good ? "text-success" : ""}`}>
                    {row.fmt(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --------------------------------------------------------------------------
function WindowsTable({ data }: { data: BacktestResponse }) {
  const years = [1, 2, 3, 5];
  const rows = data.scenarios.filter((s) => s.windows);
  if (!rows.length) return null;
  return (
    <div className="card overflow-x-auto">
      <h3 className="font-semibold mb-2">Trailing total return by window</h3>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-muted text-left border-b border-border">
            <th className="py-1 pr-3 font-medium">Scenario</th>
            {years.map((y) => <th key={y} className="py-1 px-2 text-right font-medium">{y}-yr</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.name} className="border-b border-border/40">
              <td className="py-1 pr-3">{s.name}</td>
              {years.map((y) => {
                const v = s.windows?.[`${y}y`];
                return (
                  <td key={y} className={`py-1 px-2 text-right tabular-nums ${v != null && v > 0 ? "text-success" : v != null && v < 0 ? "text-danger" : ""}`}>
                    {pct(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --------------------------------------------------------------------------
function CorrelationHeatmap({ corr }: { corr: Record<string, Record<string, number | null>> }) {
  const tickers = Object.keys(corr);
  const cell = (v: number | null) => {
    if (v == null) return { background: "transparent", color: "inherit" };
    // -1 (red) → 0 (grey) → +1 (blue)
    const r = v < 0 ? 239 : Math.round(148 - v * 80);
    const g = v < 0 ? Math.round(120 + v * 60) : Math.round(163 - v * 50);
    const b = v < 0 ? Math.round(120 + v * 60) : Math.round(184 + v * 60);
    return { background: `rgb(${r},${g},${b})`, color: Math.abs(v) > 0.6 ? "#fff" : "#0f172a" };
  };
  return (
    <div className="card overflow-x-auto">
      <h3 className="font-semibold mb-1">Correlation matrix</h3>
      <p className="text-xs text-muted mb-2">
        How tightly each pair moves together (1 = lockstep, 0 = unrelated, −1 = opposite).
        Lower correlations = more genuine diversification.
      </p>
      <table className="text-xs border-collapse">
        <thead>
          <tr>
            <th className="p-1.5"></th>
            {tickers.map((t) => <th key={t} className="p-1.5 font-mono">{t}</th>)}
          </tr>
        </thead>
        <tbody>
          {tickers.map((rt) => (
            <tr key={rt}>
              <th className="p-1.5 text-right font-mono">{rt}</th>
              {tickers.map((ct) => {
                const v = corr[rt]?.[ct] ?? null;
                return (
                  <td key={ct} className="p-1.5 text-center tabular-nums" style={{ ...cell(v), minWidth: 46 }}>
                    {v == null ? "—" : v.toFixed(2)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --------------------------------------------------------------------------
function MonteCarloResults({ data, initial }: { data: MonteCarloResponse; initial: number }) {
  const histData = (data.histogram ?? []).map((b) => ({
    mid: b.low != null && b.high != null ? ((b.low + b.high) / 2) * 100 : 0,
    count: b.count,
  }));
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard label="Median outcome" value={usd(data.ending?.median)}
          sub={pct(data.median_return_pct)} />
        <MetricCard label="Chance of a loss" value={pct(data.prob_loss)}
          tone={data.prob_loss != null && data.prob_loss > 0.3 ? "danger" : "ok"} />
        <MetricCard label="Chance of beating SPY" value={pct(data.prob_beat_benchmark)}
          tone={data.prob_beat_benchmark != null && data.prob_beat_benchmark > 0.5 ? "ok" : "warn"} />
        <MetricCard label="Worst-5% loss (CVaR)" value={pct(data.cvar_95_pct)} tone="danger"
          sub={`VaR ${pct(data.var_95_pct)}`} />
      </div>

      <div className="card">
        <h3 className="font-semibold mb-2">Range of outcomes over {data.horizon_days} trading days</h3>
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={data.fan} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.15} />
            <XAxis dataKey="day" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} width={55} />
            <Tooltip formatter={(v: any) => usd(v)} contentStyle={{ fontSize: 12 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line type="monotone" dataKey="p95" name="95th pct (lucky)" dot={false} stroke="#22c55e" strokeWidth={1} />
            <Line type="monotone" dataKey="p75" name="75th" dot={false} stroke="#86efac" strokeWidth={1} />
            <Line type="monotone" dataKey="p50" name="Median" dot={false} stroke="#3b82f6" strokeWidth={2.5} />
            <Line type="monotone" dataKey="p25" name="25th" dot={false} stroke="#fca5a5" strokeWidth={1} />
            <Line type="monotone" dataKey="p5" name="5th pct (unlucky)" dot={false} stroke="#ef4444" strokeWidth={1} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="card">
        <h3 className="font-semibold mb-2">Distribution of ending returns</h3>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={histData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.15} />
            <XAxis dataKey="mid" tick={{ fontSize: 11 }} tickFormatter={(v) => `${v.toFixed(0)}%`} />
            <YAxis tick={{ fontSize: 11 }} width={40} />
            <Tooltip formatter={(v: any) => `${v} paths`}
              labelFormatter={(v: any) => `~${Number(v).toFixed(0)}% return`} contentStyle={{ fontSize: 12 }} />
            <Bar dataKey="count" fill="#3b82f6" />
          </BarChart>
        </ResponsiveContainer>
        <p className="text-xs text-muted mt-1">
          {data.n_paths.toLocaleString()} simulated paths ({data.method}). Each bar = how many futures
          landed in that return bucket.
        </p>
      </div>
    </div>
  );
}

function MetricCard({ label, value, sub, tone }: {
  label: string; value: string; sub?: string; tone?: "ok" | "warn" | "danger";
}) {
  const c = tone === "danger" ? "text-danger" : tone === "ok" ? "text-success" : tone === "warn" ? "text-amber-500" : "";
  return (
    <div className="card">
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-xl font-bold ${c}`}>{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}
