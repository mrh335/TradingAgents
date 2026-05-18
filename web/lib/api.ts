// Thin fetch wrapper around the FastAPI service.
//
// Browser → Next.js rewrite → FastAPI:
//   /api/runs  →  http://localhost:8000/runs   (dev or behind reverse proxy)
//
// All paths in this file start with `/api/...` so they hit the rewrite.

import type {
  BatchCreateRequest,
  BatchDetail,
  BatchSummary,
  Brief,
  CalendarEvent,
  ChatMessage,
  ChartComparisonResponse,
  ExportFile,
  MemoryResponse,
  NewsItem,
  Note,
  PortfolioSummary,
  Position,
  RunCreateRequest,
  RunDetail,
  RunSummary,
  Settings,
  SimDetail,
  SimRow,
  SimRunRequest,
  WatchlistEntry,
} from "./types";

// Two ways the analysis can run. "incremental" injects the memory-log
// past_context into the Portfolio Manager prompt (default, efficient).
// "fresh" bypasses memory entirely so the PM evaluates the analyst
// reports without anchoring on prior decisions. See gui/runner_worker.py
// for the server-side branch.
export type AnalysisMode = "incremental" | "fresh";

const API_BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  // 204 / empty response
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T;
  }
  return (await res.json()) as T;
}

// ---- Runs ---------------------------------------------------------------

export const Runs = {
  list: (ticker?: string) =>
    request<RunSummary[]>(
      `/runs${ticker ? `?ticker=${encodeURIComponent(ticker)}` : ""}`,
    ),
  get: (runId: string) => request<RunDetail>(`/runs/${runId}`),
  create: (req: RunCreateRequest) =>
    request<RunSummary>("/runs", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  cancel: (runId: string) =>
    request<{ cancelled: boolean }>(`/runs/${runId}/cancel`, { method: "POST" }),
  delete: (runId: string, deleteFiles = true) =>
    request<{ deleted_run: string; files_deleted: string[] }>(
      `/runs/${runId}?delete_files=${deleteFiles}`,
      { method: "DELETE" },
    ),
  diskIndex: () => request<any[]>(`/runs/disk/index`),
};

export const Sidecars = {
  pending: () =>
    request<Array<{
      run_id: string;
      ticker: string;
      trade_date: string;
      archive_path: string;
      request_path: string;
      request_body: string;
      has_brief_already: boolean;
    }>>("/sidecars/pending"),
  requestAllMissing: (includeExisting = false) =>
    request<{
      requested: string[];
      skipped: string[];
      no_archive: string[];
    }>(`/sidecars/request-all-missing?include_existing=${includeExisting}`, {
      method: "POST",
    }),
};

// ---- Briefs -------------------------------------------------------------

type BriefResp = {
  run_id: string;
  brief: Brief | null;
  cached: boolean;
  source?: "sidecar" | "markdown_sidecar" | "llm" | null;
  markdown?: string | null;
  request_pending: boolean;
};

export const Briefs = {
  get: (runId: string) => request<BriefResp>(`/runs/${runId}/brief`),
  generate: (runId: string, force = false) =>
    request<BriefResp>(
      `/runs/${runId}/brief${force ? "?force=true" : ""}`,
      { method: "POST" },
    ),
  requestClaudeCode: (runId: string) =>
    request<BriefResp>(`/runs/${runId}/request-claude-code-analysis`, {
      method: "POST",
    }),
  cancelClaudeCodeRequest: (runId: string) =>
    request<{ cleared: boolean }>(`/runs/${runId}/brief/request`, {
      method: "DELETE",
    }),
  files: (runId: string) =>
    request<{
      archive: string | null;
      sidecars: Array<{
        name: string;
        kind: string;
        path: string;
        size_bytes: number;
        modified_at: string;
      }>;
    }>(`/runs/${runId}/files`),
};

// ---- Chat ---------------------------------------------------------------

export const Chat = {
  list: (runId: string) =>
    request<ChatMessage[]>(`/runs/${runId}/chat`),
  clear: (runId: string) =>
    request<{ cleared: boolean }>(`/runs/${runId}/chat`, { method: "DELETE" }),
};

// ---- Notes --------------------------------------------------------------

export const Notes = {
  list: (params?: { ticker?: string; run_id?: string; q?: string }) => {
    const qp = new URLSearchParams();
    if (params?.ticker) qp.set("ticker", params.ticker);
    if (params?.run_id) qp.set("run_id", params.run_id);
    if (params?.q) qp.set("q", params.q);
    const qs = qp.toString();
    return request<Note[]>(`/notes${qs ? `?${qs}` : ""}`);
  },
  create: (req: { title: string; body: string; ticker?: string; run_id?: string; tags?: string }) =>
    request<Note>("/notes", { method: "POST", body: JSON.stringify(req) }),
  update: (id: number, req: { title: string; body: string; tags?: string }) =>
    request<Note>(`/notes/${id}`, { method: "PUT", body: JSON.stringify(req) }),
  delete: (id: number) =>
    request<{ deleted: boolean }>(`/notes/${id}`, { method: "DELETE" }),
};

// ---- Settings -----------------------------------------------------------

export const SettingsApi = {
  get: () => request<Settings>("/settings"),
  update: (req: { api_keys?: Record<string, string>; defaults?: Record<string, any> }) =>
    request<Settings>("/settings", { method: "PUT", body: JSON.stringify(req) }),
  ollamaModels: (url?: string) => {
    const qs = url ? `?url=${encodeURIComponent(url)}` : "";
    return request<{
      url: string;
      models: Array<{
        name: string;
        size?: number;
        modified_at?: string;
        parameter_size?: string;
        family?: string;
      }>;
      count: number;
    }>(`/settings/ollama/models${qs}`);
  },
  // ── Financial planner integration ──────────────────────────────────
  // Stored URL + key for the sibling planner. Env vars (PLANNER_API_URL,
  // PLANNER_API_KEY) take precedence — the *_set_in_env flags in the
  // response tell the UI to disable the corresponding input.
  getPlannerIntegration: () => request<{
    name: string;
    url: string;
    masked_key: string;
    url_set_in_env: boolean;
    key_set_in_env: boolean;
  }>("/settings/integrations/planner"),
  updatePlannerIntegration: (req: { url?: string; key?: string }) =>
    request<{
      name: string;
      url: string;
      masked_key: string;
      url_set_in_env: boolean;
      key_set_in_env: boolean;
    }>("/settings/integrations/planner", { method: "PUT", body: JSON.stringify(req) }),
  testPlannerIntegration: () =>
    request<{ ok: boolean; status_code?: number; error?: string }>(
      "/settings/integrations/planner/test",
      { method: "POST" },
    ),
};

// ---- Memory -------------------------------------------------------------

export const Memory = {
  get: () => request<MemoryResponse>("/memory"),
};

// ---- Charts -------------------------------------------------------------

export const Charts = {
  comparison: (params: {
    ticker: string;
    trade_date: string;
    days_back?: number;
    days_forward?: number;
    benchmarks?: string[];
  }) => {
    const qp = new URLSearchParams();
    qp.set("ticker", params.ticker);
    qp.set("trade_date", params.trade_date);
    if (params.days_back) qp.set("days_back", String(params.days_back));
    if (params.days_forward) qp.set("days_forward", String(params.days_forward));
    (params.benchmarks ?? ["SPY", "QQQ"]).forEach((b) => qp.append("benchmarks", b));
    return request<ChartComparisonResponse>(`/charts/comparison?${qp}`);
  },
  decisions: (ticker: string, lookbackDays: number = 180) =>
    request<{
      ticker: string;
      lookback_days: number;
      fetched_at: string;
      decisions: Array<{
        trade_date: string;
        run_id: string;
        decision: string | null;
        provider: string | null;
      }>;
      price_series: Array<{ date: string; close: number }>;
      splits: Array<{ date: string; ratio: number }>;
      dividends: Array<{ date: string; amount: number }>;
    }>(`/charts/decisions/${encodeURIComponent(ticker)}?lookback_days=${lookbackDays}`),
};

// ---- Exports ------------------------------------------------------------

export const Exports = {
  list: (runId: string) =>
    request<ExportFile[]>(`/runs/${runId}/exports`),
  downloadUrl: (runId: string, ext: ExportFile["ext"]) =>
    `${API_BASE}/runs/${runId}/exports/${ext}`,
  regenerate: (runId: string) =>
    request<Array<{ ext: string; path: string }>>(
      `/runs/${runId}/exports/regenerate`,
      { method: "POST" },
    ),
};

// ---- Watchlist ---------------------------------------------------------

export const Watchlist = {
  list: () => request<WatchlistEntry[]>("/watchlist"),
  add: (req: { ticker: string; notes?: string }) =>
    request<WatchlistEntry>("/watchlist", { method: "POST", body: JSON.stringify(req) }),
  remove: (ticker: string) =>
    request<{ removed: string }>(`/watchlist/${ticker}`, { method: "DELETE" }),
  quotes: () =>
    request<Record<string, { price: number; change: number; change_pct: number; polled_at: number } | null>>(
      "/watchlist/quotes",
    ),
};

// ---- Portfolio ---------------------------------------------------------

export const Portfolio = {
  positions: (includeClosed = false) =>
    request<Position[]>(`/portfolio/positions${includeClosed ? "?include_closed=true" : ""}`),
  addPosition: (req: {
    ticker: string;
    shares: number;
    cost_basis_per_share: number;
    opened_at?: string;
    account?: string;
    notes?: string;
  }) =>
    request<Position>("/portfolio/positions", { method: "POST", body: JSON.stringify(req) }),
  updatePosition: (id: number, req: Partial<{ shares: number; cost_basis_per_share: number; account: string; notes: string }>) =>
    request<Position>(`/portfolio/positions/${id}`, { method: "PUT", body: JSON.stringify(req) }),
  closePosition: (id: number, req: { closing_price: number; closed_at?: string }) =>
    request<Position>(`/portfolio/positions/${id}/close`, { method: "POST", body: JSON.stringify(req) }),
  deletePosition: (id: number) =>
    request<{ deleted: number }>(`/portfolio/positions/${id}`, { method: "DELETE" }),
  summary: () => request<PortfolioSummary>("/portfolio/summary"),
};

// ---- Planner integration ----------------------------------------------

export type PlannerStatus = {
  configured: boolean;
  url: string | null;
  reachable: boolean;
  error?: string | null;
};

export type SyncDiffEntry = {
  ticker: string;
  account: string;
  action: "create" | "update" | "unchanged";
  planner_shares: number;
  planner_cost_basis: number | null;
  existing_shares: number | null;
  existing_cost_basis: number | null;
};

export type SyncResult = {
  dry_run: boolean;
  fetched_holdings: number;
  accounts: number;
  diff: SyncDiffEntry[];
  applied: number;
  skipped: number;
  errors: string[];
};

export const Planner = {
  status: () => request<PlannerStatus>("/planner/status"),
  sync: (dryRun: boolean) =>
    request<SyncResult>(`/planner/sync?dry_run=${dryRun}`, { method: "POST" }),
};

// ---- Calendar ----------------------------------------------------------

export const Calendar = {
  events: (params: { from: string; to: string; tickers?: string[] }) => {
    const qp = new URLSearchParams();
    qp.set("from", params.from);
    qp.set("to", params.to);
    if (params.tickers?.length) qp.set("tickers", params.tickers.join(","));
    return request<CalendarEvent[]>(`/calendar?${qp}`);
  },
};

// ---- News --------------------------------------------------------------

export const News = {
  feed: (params?: { tickers?: string[]; limit?: number }) => {
    const qp = new URLSearchParams();
    if (params?.tickers?.length) qp.set("tickers", params.tickers.join(","));
    if (params?.limit) qp.set("limit", String(params.limit));
    const qs = qp.toString();
    return request<NewsItem[]>(`/news/feed${qs ? `?${qs}` : ""}`);
  },
};

// ---- Simulation --------------------------------------------------------

export const Batches = {
  create: (req: BatchCreateRequest) =>
    request<BatchDetail>("/runs/batch", { method: "POST", body: JSON.stringify(req) }),
  list: () => request<BatchSummary[]>("/runs/batch"),
  get: (id: string) => request<BatchDetail>(`/runs/batch/${id}`),
  cancel: (id: string) =>
    request<BatchDetail>(`/runs/batch/${id}/cancel`, { method: "POST" }),
};

export const Simulation = {
  run: (req: SimRunRequest) =>
    request<SimDetail>("/sim/run", { method: "POST", body: JSON.stringify(req) }),
  list: () => request<SimRow[]>("/sim"),
  get: (id: number) => request<SimDetail>(`/sim/${id}`),
  delete: (id: number) =>
    request<{ deleted: number }>(`/sim/${id}`, { method: "DELETE" }),
};

// ---- Run queue (Claude Desktop / Claude Code worker handoff) ----------

export type QueueItem = {
  id: string;
  ticker: string;
  trade_date: string;
  mode: "analyze" | "brief" | "refresh";
  options: Record<string, any>;
  requested_by: string | null;
  priority: number;
  status: "pending" | "claimed" | "done" | "error" | "cancelled";
  claimed_by: string | null;
  claimed_at: string | null;
  completed_at: string | null;
  result_run_id: string | null;
  error_message: string | null;
  created_at: string;
};

// ---- Trading restrictions (per-ticker blackout windows) ----------------

export type Restriction = {
  id: number;
  ticker: string;
  start_date: string;
  end_date: string | null;
  kind: "blackout" | "restricted_list" | "regulatory" | "other";
  reason: string | null;
  created_at: string;
  updated_at: string;
};

export const Restrictions = {
  list: (params?: { ticker?: string; active_on?: string }) => {
    const qp = new URLSearchParams();
    if (params?.ticker) qp.set("ticker", params.ticker);
    if (params?.active_on) qp.set("active_on", params.active_on);
    const qs = qp.toString();
    return request<Restriction[]>(`/restrictions${qs ? `?${qs}` : ""}`);
  },
  create: (req: {
    ticker: string;
    start_date: string;
    end_date?: string | null;
    kind?: Restriction["kind"];
    reason?: string | null;
  }) =>
    request<Restriction>("/restrictions", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  update: (
    id: number,
    req: Partial<{
      start_date: string;
      end_date: string | null;
      kind: Restriction["kind"];
      reason: string | null;
    }>,
  ) =>
    request<Restriction>(`/restrictions/${id}`, {
      method: "PUT",
      body: JSON.stringify(req),
    }),
  delete: (id: number) =>
    request<{ deleted: number }>(`/restrictions/${id}`, { method: "DELETE" }),
};

// ---- Token usage --------------------------------------------------------

export type TokenEvent = {
  run_id: string;
  ticker: string;
  trade_date: string;
  provider: string | null;
  deep_model: string | null;
  completed_at: string | null;
  tokens_in: number;
  tokens_out: number;
  llm_calls: number;
  tool_calls: number;
  estimated_cost_usd: number;
};

export type TokenBucket = {
  date: string;
  provider: string | null;
  tokens_in: number;
  tokens_out: number;
  runs: number;
  estimated_cost_usd: number;
};

export type TokenSummary = {
  buckets: TokenBucket[];
  totals: {
    tokens_in: number;
    tokens_out: number;
    runs: number;
    estimated_cost_usd: number;
    days: number;
  };
  providers: string[];
};

export const Tokens = {
  events: (params?: { since_iso?: string; ticker?: string; limit?: number }) => {
    const qp = new URLSearchParams();
    if (params?.since_iso) qp.set("since_iso", params.since_iso);
    if (params?.ticker) qp.set("ticker", params.ticker);
    if (params?.limit) qp.set("limit", String(params.limit));
    const qs = qp.toString();
    return request<TokenEvent[]>(`/tokens/events${qs ? `?${qs}` : ""}`);
  },
  summary: (days: number = 30, groupByProvider: boolean = true) =>
    request<TokenSummary>(
      `/tokens/summary?days=${days}&group_by_provider=${groupByProvider}`,
    ),
  backfill: () =>
    request<{ updated: number; skipped: number; errors: string[]; note: string }>(
      "/tokens/backfill",
      { method: "POST" },
    ),
};

// ---- Dashboard (cross-cutting portfolio views) -------------------------

export type FreshnessRow = {
  ticker: string;
  shares: number;
  last_run_id: string | null;
  last_run_date: string | null;
  last_run_completed_at: string | null;
  days_since: number | null;
  last_decision: string | null;
  last_provider: string | null;
  runs_total: number;
};

export const Dashboard = {
  freshness: (params?: { include_watchlist?: boolean; include_positions?: boolean }) => {
    const qp = new URLSearchParams();
    if (params?.include_watchlist === false) qp.set("include_watchlist", "false");
    if (params?.include_positions === false) qp.set("include_positions", "false");
    const qs = qp.toString();
    return request<FreshnessRow[]>(`/dashboard/freshness${qs ? `?${qs}` : ""}`);
  },
  recommendations: () => request<RecommendationsResponse>("/dashboard/recommendations"),
};

// ---- Recommendations (portfolio-level action sheet) --------------------

export type PositionAction = {
  ticker: string;
  shares: number;
  cost_basis: number;
  cost_basis_total: number;
  sector: string;
  weight_pct: number;
  latest_decision: string | null;
  latest_action_plain: string | null;
  latest_tldr: string | null;
  latest_run_id: string | null;
  days_since: number | null;
  restriction_active: boolean;
  restriction_reason: string | null;
  action: "maintain" | "trim" | "add" | "exit" | "refresh" | "blocked";
  priority: "high" | "medium" | "low" | "info";
  rationale: string;
};

export type PortfolioObservation = {
  kind: string;
  priority: string;
  summary: string;
  detail: string | null;
};

export type RecommendationsResponse = {
  generated_at: string;
  portfolio_summary: {
    position_count: number;
    total_value_at_basis: number;
    high_priority_actions: number;
    blocked_tickers: number;
  };
  positions: PositionAction[];
  sector_mix: Record<string, number>;
  observations: PortfolioObservation[];
  action_priority: Array<{ priority: string; ticker: string; verb: string; summary: string }>;
};

// ---- Discovery (sector gaps + peers + screener) ------------------------

export type SectorGapRow = {
  sector: string;
  portfolio_pct: number;
  benchmark_pct: number;
  gap_pct: number;
  underweight: boolean;
  suggested_tickers: Array<{ ticker: string; rationale: string }>;
};

export type SectorGapsResponse = {
  portfolio_total_basis: number;
  sector_rows: SectorGapRow[];
  biggest_underweights: string[];
};

export type PeerSuggestion = {
  base_ticker: string;
  base_sector: string;
  peers: Array<{ ticker: string; rationale: string }>;
};

export const Discover = {
  sectorGaps: () => request<SectorGapsResponse>("/discover/sector-gaps"),
  peers: (ticker?: string) => {
    const qs = ticker ? `?ticker=${encodeURIComponent(ticker)}` : "";
    return request<{ suggestions: PeerSuggestion[] }>(`/discover/peers${qs}`);
  },
  screener: () =>
    request<{ status: string; message: string; available_filters: string[] }>(
      "/discover/screener",
    ),
};

// ---- Backtest (realized return + hit rate) -----------------------------

export type BacktestWindow = {
  days: number;
  end_date: string | null;
  ticker_return_pct: number | null;
  benchmark_return_pct: number | null;
  alpha_pct: number | null;
  horizon_reached: boolean;
  win: boolean | null;
};

export type BacktestResult = {
  run_id: string;
  ticker: string;
  trade_date: string;
  decision: string | null;
  provider: string | null;
  deep_model: string | null;
  benchmark: string;
  windows: BacktestWindow[];
  computed_at: string;
  note: string | null;
};

export type HitRateCell = {
  label: string;
  runs: number;
  wins: number;
  losses: number;
  skipped: number;
  hit_rate_pct: number | null;
  mean_alpha_pct: number | null;
};

export type BacktestSummaryResponse = {
  window_days: number;
  overall: HitRateCell;
  by_decision: HitRateCell[];
  by_provider: HitRateCell[];
  by_model: HitRateCell[];
  sample_rows: Array<{
    run_id: string;
    ticker: string;
    trade_date: string;
    decision: string | null;
    provider: string | null;
    deep_model: string | null;
    ticker_return_pct: number | null;
    benchmark_return_pct: number | null;
    alpha_pct: number | null;
    horizon_reached: boolean;
    win: boolean | null;
  }>;
};

export type TickerAttributionRow = {
  ticker: string;
  runs: number;
  counted: number;
  wins: number;
  losses: number;
  hit_rate_pct: number | null;
  mean_alpha_pct: number | null;
  best_alpha_pct: number | null;
  best_run_id: string | null;
  worst_alpha_pct: number | null;
  worst_run_id: string | null;
};

export type AttributionResponse = {
  window_days: number;
  rows: TickerAttributionRow[];
};

export const Backtest = {
  summary: (windowDays: number = 30) =>
    request<BacktestSummaryResponse>(`/backtest/?window_days=${windowDays}`),
  get: (runId: string, force = false) =>
    request<BacktestResult>(`/backtest/${runId}${force ? "?force=true" : ""}`),
  attribution: (windowDays: number = 30) =>
    request<AttributionResponse>(`/backtest/attribution?window_days=${windowDays}`),
  recomputeAll: () =>
    request<{ computed: number; errors: number; error_details: string[] }>(
      "/backtest/recompute-all",
      { method: "POST" },
    ),
};

// ---- Portfolio aggregations (multi-account + correlation) ---------------

export type AccountRollup = {
  account: string;
  positions: number;
  total_cost: number;
  total_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  tickers: Array<{
    ticker: string;
    shares: number;
    cost: number;
    value: number | null;
    cost_basis_per_share: number;
    live_price: number | null;
  }>;
};

export type ByAccountResponse = {
  accounts: AccountRollup[];
  totals: {
    account_count: number;
    total_cost: number;
    total_value: number | null;
  };
};

export type CorrelationCell = { a: string; b: string; correlation: number };
export type CorrelationResponse = {
  tickers: string[];
  lookback_days: number;
  matrix: (number | null)[][];
  pairs_high_correlation: CorrelationCell[];
  note: string | null;
};

// Extend the existing Portfolio object below — we already declared it
// earlier; add the new methods by re-exporting through a separate
// PortfolioAnalytics object so we don't conflict.
export const PortfolioAnalytics = {
  byAccount: () => request<ByAccountResponse>("/portfolio/by-account"),
  correlation: (lookbackDays: number = 90, includeBenchmark = true) =>
    request<CorrelationResponse>(
      `/portfolio/correlation?lookback_days=${lookbackDays}&include_benchmark=${includeBenchmark}`,
    ),
};

// ---- Risk metrics (VaR / max drawdown / Sharpe) ------------------------

export type PositionRisk = {
  ticker: string;
  weight_pct: number;
  annualized_volatility_pct: number | null;
  annualized_return_pct: number | null;
  sharpe: number | null;
  max_drawdown_pct: number | null;
  var_5pct_daily: number | null;
  var_5pct_dollar: number | null;
};

export type PortfolioRiskResponse = {
  lookback_days: number;
  benchmark: string;
  portfolio: PositionRisk;
  benchmark_risk: PositionRisk;
  positions: PositionRisk[];
  correlation_avg: number | null;
  note: string | null;
};

export const Risk = {
  portfolio: (lookbackDays: number = 365, benchmark: string = "SPY") =>
    request<PortfolioRiskResponse>(
      `/risk/portfolio?lookback_days=${lookbackDays}&benchmark=${benchmark}`,
    ),
};

// ---- Live ticker prices ------------------------------------------------

export type LivePrice = {
  ticker: string;
  price: number;
  change: number | null;
  change_pct: number | null;
  volume: number | null;
  polled_at: number | null;
};

export const Streaming = {
  state: () => request<{ prices: Record<string, LivePrice> }>("/streaming/state"),
};

// ---- Schedules (per-ticker auto-run scheduler) -------------------------

export type Schedule = {
  id: number;
  ticker: string;
  cron_expression: string;
  mode: "analyze" | "brief" | "refresh";
  options: Record<string, any>;
  enabled: boolean;
  notes: string | null;
  last_fired_at: string | null;
  last_queue_id: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  next_fire_at: string | null;
  cadence_human: string | null;
};

export const Schedules = {
  list: () => request<Schedule[]>("/schedules"),
  create: (req: {
    ticker: string;
    cron_expression: string;
    mode?: "analyze" | "brief" | "refresh";
    options?: Record<string, any>;
    enabled?: boolean;
    notes?: string;
  }) =>
    request<Schedule>("/schedules", { method: "POST", body: JSON.stringify(req) }),
  update: (
    id: number,
    req: Partial<{
      cron_expression: string;
      mode: Schedule["mode"];
      options: Record<string, any>;
      enabled: boolean;
      notes: string;
    }>,
  ) =>
    request<Schedule>(`/schedules/${id}`, { method: "PUT", body: JSON.stringify(req) }),
  delete: (id: number) =>
    request<{ deleted: number }>(`/schedules/${id}`, { method: "DELETE" }),
  fire: (id: number) =>
    request<Schedule>(`/schedules/${id}/fire`, { method: "POST" }),
};

export const RunQueue = {
  list: (status?: QueueItem["status"]) => {
    const qs = status ? `?status=${status}` : "";
    return request<QueueItem[]>(`/run-queue${qs}`);
  },
  pending: () => request<QueueItem[]>("/run-queue/pending"),
  create: (req: {
    ticker: string;
    trade_date: string;
    mode?: "analyze" | "brief" | "refresh";
    options?: Record<string, any>;
    requested_by?: string;
    priority?: number;
  }) =>
    request<QueueItem>("/run-queue", {
      method: "POST",
      body: JSON.stringify({ mode: "analyze", ...req }),
    }),
  cancel: (id: string) =>
    request<QueueItem>(`/run-queue/${id}/cancel`, { method: "POST" }),
  delete: (id: string) =>
    request<{ deleted: string }>(`/run-queue/${id}`, { method: "DELETE" }),
};
