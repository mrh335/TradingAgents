// Types for the portfolio analytics suite (/sim/backtest, /sim/montecarlo,
// /sim/portfolio-actual). Kept in their own module so they don't depend on
// the older Sim* types in types.ts.

export interface ScenarioSpec {
  name: string;
  weights: Record<string, number>;
}

export interface BacktestRequest {
  scenarios: ScenarioSpec[];
  benchmark?: string;
  period?: string;
  initial?: number;
  rebalance?: "none" | "daily";
  windows?: number[];
}

// All stat fields are nullable — the engine returns null for undefined
// (e.g. Sharpe when volatility is 0, or beta without a benchmark).
export interface StatPack {
  total_return: number | null;
  cagr: number | null;
  volatility: number | null;
  sharpe: number | null;
  sortino: number | null;
  calmar: number | null;
  max_drawdown: number | null;
  best_day: number | null;
  worst_day: number | null;
  pct_positive_days: number | null;
  final_value: number | null;
  n_days: number;
  beta?: number | null;
  alpha?: number | null;
  correlation_to_benchmark?: number | null;
}

export interface CurvePoint {
  date: string;
  value: number | null;
}

export type WindowReturns = Record<string, number | null>;

export interface ScenarioResult {
  name: string;
  weights?: Record<string, number>;
  stats?: StatPack;
  windows?: WindowReturns;
  curve?: CurvePoint[];
  error?: string;
}

export interface BacktestResponse {
  as_of: string;
  start: string;
  benchmark: string;
  initial: number;
  rebalance: string;
  scenarios: ScenarioResult[];
  benchmark_curve: CurvePoint[];
  benchmark_stats: StatPack;
  correlation: Record<string, Record<string, number | null>>;
}

export interface MonteCarloRequest {
  weights: Record<string, number>;
  benchmark?: string;
  period?: string;
  horizon_days?: number;
  n_paths?: number;
  method?: "bootstrap" | "normal";
  initial?: number;
  rebalance?: "none" | "daily";
}

export interface FanPoint {
  day: number;
  p5: number | null;
  p25: number | null;
  p50: number | null;
  p75: number | null;
  p95: number | null;
  mean: number | null;
}

export interface HistogramBin {
  low: number | null;
  high: number | null;
  count: number;
}

export interface MonteCarloResponse {
  method: string;
  n_paths: number;
  horizon_days: number;
  initial: number;
  fan: FanPoint[];
  histogram: HistogramBin[];
  ending: {
    mean: number | null;
    median: number | null;
    p5: number | null;
    p95: number | null;
    min: number | null;
    max: number | null;
  };
  prob_loss: number | null;
  prob_double: number | null;
  prob_beat_benchmark?: number | null;
  var_95_pct: number | null;
  cvar_95_pct: number | null;
  expected_return_pct: number | null;
  median_return_pct: number | null;
  weights?: Record<string, number>;
  history_start?: string;
  history_end?: string;
}

export interface PortfolioActual {
  weights: Record<string, number>;
  positions: { ticker: string; shares: number; value: number }[];
  total_value: number;
}
