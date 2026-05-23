"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { RunQueue, Runs, SettingsApi } from "@/lib/api";
import { runStreamUrl } from "@/lib/ws";
import type { RunCreateRequest, RunEvent } from "@/lib/types";
import { Markdown } from "@/components/Markdown";
import { BriefPanel } from "@/components/BriefPanel";
import { ChartComparison } from "@/components/ChartComparison";
import { ChatPanel } from "@/components/ChatPanel";
import { ExportPanel } from "@/components/ExportPanel";

const PROVIDERS = [
  { id: "openai", label: "OpenAI (GPT)" },
  { id: "anthropic", label: "Anthropic (Claude)" },
  { id: "google", label: "Google (Gemini)" },
  { id: "xai", label: "xAI (Grok)" },
  { id: "deepseek", label: "DeepSeek" },
  { id: "qwen", label: "Qwen" },
  { id: "glm", label: "GLM" },
  { id: "openrouter", label: "OpenRouter" },
  { id: "ollama", label: "Ollama (local)" },
] as const;

type SectionKey =
  | "market_report"
  | "sentiment_report"
  | "news_report"
  | "fundamentals_report"
  | "research_judge"
  | "trader_investment_plan"
  | "final_trade_decision";

const SECTION_TABS: { key: SectionKey; label: string }[] = [
  { key: "market_report", label: "Market" },
  { key: "sentiment_report", label: "Sentiment" },
  { key: "news_report", label: "News" },
  { key: "fundamentals_report", label: "Fundamentals" },
  { key: "research_judge", label: "Research Mgr" },
  { key: "trader_investment_plan", label: "Trader Plan" },
  { key: "final_trade_decision", label: "Final Decision" },
];

type RunUiState = {
  sections: Partial<Record<SectionKey, string>>;
  bull: string;
  bear: string;
  aggressive: string;
  conservative: string;
  neutral: string;
  log: string[];
  stats: { llm_calls: number; tool_calls: number; tokens_in: number; tokens_out: number };
  decision: string | null;
  error: string | null;
  warning: string | null;
  done: boolean;
};

const EMPTY_UI: RunUiState = {
  sections: {},
  bull: "",
  bear: "",
  aggressive: "",
  conservative: "",
  neutral: "",
  log: [],
  stats: { llm_calls: 0, tool_calls: 0, tokens_in: 0, tokens_out: 0 },
  decision: null,
  error: null,
  warning: null,
  done: false,
};

function todayIso(): string {
  // Local-day YYYY-MM-DD. Avoid toISOString().slice — that's UTC and
  // rolls over to tomorrow ~5pm PT.
  return new Date().toLocaleDateString("sv-SE");
}

export default function RunPage() {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => SettingsApi.get() });
  const defaults = settings.data?.defaults ?? {};

  const [form, setForm] = useState<RunCreateRequest>({
    ticker: "NVDA",
    trade_date: todayIso(),
    llm_provider: "anthropic",
    deep_think_llm: "claude-sonnet-4-6",
    quick_think_llm: "claude-haiku-4-5",
    max_debate_rounds: 1,
    max_risk_discuss_rounds: 1,
    data_vendors: {
      core_stock_apis: "yfinance",
      technical_indicators: "yfinance",
      fundamental_data: "yfinance",
      news_data: "yfinance",
    },
    analysis_mode: "incremental",
  });

  // Pull saved defaults once they load.
  useEffect(() => {
    if (!settings.data) return;
    setForm((f) => ({
      ...f,
      llm_provider: defaults.llm_provider ?? f.llm_provider,
      deep_think_llm: defaults.deep_think_llm ?? f.deep_think_llm,
      quick_think_llm: defaults.quick_think_llm ?? f.quick_think_llm,
      max_debate_rounds: defaults.max_debate_rounds ?? f.max_debate_rounds,
      max_risk_discuss_rounds: defaults.max_risk_discuss_rounds ?? f.max_risk_discuss_rounds,
      data_vendors: { ...f.data_vendors, ...(defaults.data_vendors ?? {}) },
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.data]);

  const [runId, setRunId] = useState<string | null>(null);
  const [ui, setUi] = useState<RunUiState>(EMPTY_UI);
  const [activeTab, setActiveTab] = useState<SectionKey | "debate" | "risk" | "log">(
    "market_report",
  );
  const wsRef = useRef<WebSocket | null>(null);

  const create = useMutation({
    mutationFn: (req: RunCreateRequest) => Runs.create(req),
    onSuccess: (r) => {
      setUi(EMPTY_UI);
      setRunId(r.run_id);
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  const queueMutation = useMutation({
    mutationFn: (req: RunCreateRequest) =>
      RunQueue.create({
        ticker: req.ticker,
        trade_date: req.trade_date,
        mode: "analyze",
        options: {
          provider: req.llm_provider,
          deep_model: req.deep_think_llm,
          quick_model: req.quick_think_llm,
          debate_rounds: req.max_debate_rounds,
          risk_rounds: req.max_risk_discuss_rounds,
          data_vendors: req.data_vendors,
          analysis_mode: req.analysis_mode ?? "incremental",
        },
        requested_by: "web-ui:/run",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["run-queue"] });
    },
  });

  // Manage the WebSocket lifecycle.
  useEffect(() => {
    if (!runId || ui.done || ui.error) return;
    const ws = new WebSocket(runStreamUrl(runId));
    wsRef.current = ws;

    ws.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as RunEvent;
        applyEvent(ev);
      } catch {
        // skip malformed
      }
    };
    ws.onerror = () => {
      setUi((u) => ({ ...u, error: "WebSocket error — check the API server" }));
    };
    return () => {
      ws.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  function applyEvent(ev: RunEvent) {
    setUi((u) => {
      const next = { ...u };
      const data = ev.data ?? {};
      switch (ev.type) {
        case "section":
          next.sections = { ...u.sections, [data.key as SectionKey]: data.content };
          break;
        case "debate":
          if (data.side === "bull") next.bull = data.content;
          if (data.side === "bear") next.bear = data.content;
          break;
        case "risk":
          if (data.side === "aggressive") next.aggressive = data.content;
          if (data.side === "conservative") next.conservative = data.content;
          if (data.side === "neutral") next.neutral = data.content;
          break;
        case "stats":
          next.stats = {
            llm_calls: data.llm_calls ?? u.stats.llm_calls,
            tool_calls: data.tool_calls ?? u.stats.tool_calls,
            tokens_in: data.tokens_in ?? u.stats.tokens_in,
            tokens_out: data.tokens_out ?? u.stats.tokens_out,
          };
          break;
        case "chunk":
          next.log = [...u.log, `[${data.role ?? "?"}] ${data.content ?? ""}`].slice(-200);
          break;
        case "tool_start":
          next.log = [...u.log, `[tool→${data.tool}] ${data.input ?? ""}`].slice(-200);
          break;
        case "tool_end":
          next.log = [...u.log, `[tool←] ${data.preview ?? ""}`].slice(-200);
          break;
        case "warning":
          next.warning = data.message ?? "";
          break;
        case "error":
          next.error = data.message ?? "unknown error";
          break;
        case "done":
          next.decision = data.decision ?? null;
          next.done = true;
          // Fresh runs may now have a brief, exports etc. — invalidate all caches for this run.
          qc.invalidateQueries({ queryKey: ["runs"] });
          break;
      }
      return next;
    });
  }

  const isStreaming = !!runId && !ui.done && !ui.error;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Run analysis</h1>
        <p className="text-muted text-sm">
          Pick ticker, date, provider, model, and depth. Streams agent output live.
        </p>
      </header>

      {/* ---- Form ---- */}
      <form
        className="card grid grid-cols-2 md:grid-cols-3 gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(form);
        }}
      >
        <div>
          <label className="label">Ticker</label>
          <input
            className="input w-full"
            value={form.ticker}
            onChange={(e) => setForm({ ...form, ticker: e.target.value.toUpperCase() })}
            required
          />
        </div>
        <div>
          <label className="label">Trade date</label>
          <input
            type="date"
            className="input w-full"
            value={form.trade_date}
            onChange={(e) => setForm({ ...form, trade_date: e.target.value })}
            required
          />
        </div>
        <div>
          <label className="label">Provider</label>
          <select
            className="input w-full"
            value={form.llm_provider}
            onChange={(e) => setForm({ ...form, llm_provider: e.target.value })}
          >
            {PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        <ModelField
          label="Deep-think model"
          value={form.deep_think_llm}
          onChange={(v) => setForm({ ...form, deep_think_llm: v })}
          provider={form.llm_provider}
        />
        <ModelField
          label="Quick-think model"
          value={form.quick_think_llm}
          onChange={(v) => setForm({ ...form, quick_think_llm: v })}
          provider={form.llm_provider}
        />
        <div>
          <label className="label">Bull/Bear rounds</label>
          <input
            type="number"
            min={1}
            max={5}
            className="input w-full"
            value={form.max_debate_rounds}
            onChange={(e) =>
              setForm({ ...form, max_debate_rounds: Number(e.target.value) })
            }
          />
        </div>
        <div>
          <label className="label">Risk rounds</label>
          <input
            type="number"
            min={1}
            max={5}
            className="input w-full"
            value={form.max_risk_discuss_rounds}
            onChange={(e) =>
              setForm({ ...form, max_risk_discuss_rounds: Number(e.target.value) })
            }
          />
        </div>
        <div className="col-span-full">
          <label className="label">Memory mode</label>
          <div className="flex gap-4 items-start">
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="radio"
                name="analysis_mode"
                value="incremental"
                checked={(form.analysis_mode ?? "incremental") === "incremental"}
                onChange={() => setForm({ ...form, analysis_mode: "incremental" })}
                className="mt-1"
              />
              <span>
                <span className="font-semibold text-sm">Incremental</span>
                <span className="block text-xs text-muted max-w-xl">
                  PM sees prior decisions for this ticker as context.
                  Faster convergence; can anchor on the prior decision.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="radio"
                name="analysis_mode"
                value="fresh"
                checked={form.analysis_mode === "fresh"}
                onChange={() => setForm({ ...form, analysis_mode: "fresh" })}
                className="mt-1"
              />
              <span>
                <span className="font-semibold text-sm">Fresh</span>
                <span className="block text-xs text-muted max-w-xl">
                  Bypass memory entirely. PM re-evaluates from scratch.
                  Use periodically to break decision-anchoring drift.
                </span>
              </span>
            </label>
          </div>
        </div>
        <div className="col-span-full flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            className="btn"
            onClick={() => queueMutation.mutate(form)}
            disabled={queueMutation.isPending || isStreaming}
            title={
              "Don't run now — drop the request on the queue. A poller " +
              "(typically the tradingagents-analyze skill in Claude Desktop " +
              "or Claude Code) will pick it up and post the result back."
            }
          >
            {queueMutation.isPending ? "Queueing…" : "🤖 Queue for Claude Desktop"}
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={isStreaming || create.isPending}
          >
            {isStreaming ? "Streaming…" : "▶ Analyze now"}
          </button>
        </div>
        {create.isError && (
          <div className="col-span-full text-sm text-danger">
            {(create.error as Error).message}
          </div>
        )}
        {queueMutation.isError && (
          <div className="col-span-full text-sm text-danger">
            Queue failed: {(queueMutation.error as Error).message}
          </div>
        )}
        {queueMutation.isSuccess && queueMutation.data && (
          <div className="col-span-full text-sm text-success">
            ✓ Queued {queueMutation.data.ticker} for {queueMutation.data.trade_date}.{" "}
            <Link href="/queue" className="text-accent hover:underline">
              View queue →
            </Link>
          </div>
        )}
      </form>

      {/* ---- Status ---- */}
      {runId && (
        <div className="card flex items-center justify-between text-sm">
          <div>
            {ui.error ? (
              <span className="text-danger">⚠ {ui.error}</span>
            ) : ui.done ? (
              <span className="text-success">
                ✓ Decision: <strong>{ui.decision ?? "—"}</strong>
              </span>
            ) : (
              <span className="text-accent">● Streaming {form.ticker} …</span>
            )}
            {ui.warning && (
              <span className="ml-3 text-warning">{ui.warning}</span>
            )}
          </div>
          <div className="text-muted">
            LLM {ui.stats.llm_calls} · Tool {ui.stats.tool_calls} · Tokens{" "}
            {ui.stats.tokens_in.toLocaleString()}↑ / {ui.stats.tokens_out.toLocaleString()}↓
          </div>
        </div>
      )}

      {/* ---- Tabs (during + after run) ---- */}
      {runId && (
        <div>
          <div className="flex flex-wrap gap-1 border-b border-border mb-3">
            {SECTION_TABS.map((t) => (
              <TabBtn
                key={t.key}
                active={activeTab === t.key}
                done={!!ui.sections[t.key]}
                onClick={() => setActiveTab(t.key)}
              >
                {t.label}
              </TabBtn>
            ))}
            <TabBtn
              active={activeTab === "debate"}
              done={!!ui.bull || !!ui.bear}
              onClick={() => setActiveTab("debate")}
            >
              Bull vs Bear
            </TabBtn>
            <TabBtn
              active={activeTab === "risk"}
              done={!!(ui.aggressive || ui.conservative || ui.neutral)}
              onClick={() => setActiveTab("risk")}
            >
              Risk Debate
            </TabBtn>
            <TabBtn active={activeTab === "log"} done={ui.log.length > 0} onClick={() => setActiveTab("log")}>
              Live Log
            </TabBtn>
          </div>

          <div className="card">
            {activeTab === "debate" ? (
              <div className="grid sm:grid-cols-2 gap-6">
                <div>
                  <h3 className="font-semibold mb-2">Bull</h3>
                  <Markdown>{ui.bull}</Markdown>
                </div>
                <div>
                  <h3 className="font-semibold mb-2">Bear</h3>
                  <Markdown>{ui.bear}</Markdown>
                </div>
              </div>
            ) : activeTab === "risk" ? (
              <div className="space-y-4">
                <div>
                  <h3 className="font-semibold mb-2">Aggressive</h3>
                  <Markdown>{ui.aggressive}</Markdown>
                </div>
                <div>
                  <h3 className="font-semibold mb-2">Conservative</h3>
                  <Markdown>{ui.conservative}</Markdown>
                </div>
                <div>
                  <h3 className="font-semibold mb-2">Neutral</h3>
                  <Markdown>{ui.neutral}</Markdown>
                </div>
              </div>
            ) : activeTab === "log" ? (
              <pre className="text-xs whitespace-pre-wrap max-h-96 overflow-y-auto font-mono">
                {ui.log.slice(-100).join("\n") || "(no events yet)"}
              </pre>
            ) : (
              <Markdown>{ui.sections[activeTab as SectionKey]}</Markdown>
            )}
          </div>
        </div>
      )}

      {/* ---- After-run panels ---- */}
      {runId && ui.done && (
        <>
          <section>
            <h2 className="text-lg font-semibold mb-3">Plain-English brief</h2>
            <BriefPanel runId={runId} />
          </section>

          <section>
            <h2 className="text-lg font-semibold mb-3">vs S&amp;P 500 / Nasdaq-100</h2>
            <ChartComparison ticker={form.ticker} tradeDate={form.trade_date} />
          </section>

          <section>
            <h2 className="text-lg font-semibold mb-3">Files</h2>
            <ExportPanel runId={runId} />
          </section>

          <section>
            <h2 className="text-lg font-semibold mb-3">Chat about this run</h2>
            <ChatPanel runId={runId} />
          </section>
        </>
      )}
    </div>
  );
}

/**
 * Catalogue of well-known models per provider. Used to populate the model
 * dropdown for non-Ollama providers. Picking ``__other__`` reveals a free
 * text input so any model name still works (e.g. a brand-new release the
 * dropdown hasn't been updated for, or a custom fine-tune).
 *
 * Keep entries roughly ordered "best/biggest" → "fastest/cheapest".
 */
const MODEL_CATALOG: Record<string, { value: string; label: string }[]> = {
  anthropic: [
    { value: "claude-opus-4-7", label: "claude-opus-4-7 — top tier, slowest, most expensive" },
    { value: "claude-sonnet-4-6", label: "claude-sonnet-4-6 — balanced (default deep)" },
    { value: "claude-sonnet-4-5", label: "claude-sonnet-4-5 — prior generation" },
    { value: "claude-haiku-4-5", label: "claude-haiku-4-5 — fast + cheap (default quick)" },
  ],
  openai: [
    { value: "gpt-5", label: "gpt-5 — top tier (when available on your key)" },
    { value: "gpt-4o", label: "gpt-4o — multimodal, balanced" },
    { value: "gpt-4-turbo", label: "gpt-4-turbo" },
    { value: "gpt-4", label: "gpt-4 — slower, more deliberate" },
    { value: "gpt-4o-mini", label: "gpt-4o-mini — fast + cheap" },
    { value: "o1", label: "o1 — reasoning-tuned" },
    { value: "o1-mini", label: "o1-mini — reasoning-tuned, cheaper" },
  ],
  google: [
    { value: "gemini-2.5-pro", label: "gemini-2.5-pro — top tier" },
    { value: "gemini-2-pro", label: "gemini-2-pro" },
    { value: "gemini-2-flash", label: "gemini-2-flash — fast" },
    { value: "gemini-1.5-pro", label: "gemini-1.5-pro" },
    { value: "gemini-1.5-flash", label: "gemini-1.5-flash — cheap" },
  ],
  xai: [
    { value: "grok-3", label: "grok-3" },
    { value: "grok-2", label: "grok-2" },
    { value: "grok-2-mini", label: "grok-2-mini — cheap" },
  ],
  deepseek: [
    { value: "deepseek-r1", label: "deepseek-r1 — reasoning" },
    { value: "deepseek-v3", label: "deepseek-v3" },
    { value: "deepseek-chat", label: "deepseek-chat" },
  ],
  qwen: [
    { value: "qwen-max", label: "qwen-max" },
    { value: "qwen-plus", label: "qwen-plus" },
    { value: "qwen-turbo", label: "qwen-turbo — cheap" },
  ],
  glm: [
    { value: "glm-4-plus", label: "glm-4-plus" },
    { value: "glm-4", label: "glm-4" },
    { value: "glm-4-flash", label: "glm-4-flash — cheap" },
  ],
  openrouter: [
    { value: "anthropic/claude-sonnet-4-6", label: "anthropic/claude-sonnet-4-6" },
    { value: "anthropic/claude-haiku-4-5", label: "anthropic/claude-haiku-4-5" },
    { value: "openai/gpt-4o", label: "openai/gpt-4o" },
    { value: "google/gemini-2-pro", label: "google/gemini-2-pro" },
    { value: "meta-llama/llama-3.3-70b-instruct", label: "meta-llama/llama-3.3-70b-instruct" },
  ],
};

const OTHER_SENTINEL = "__other__";

/**
 * Model field. For Ollama we fetch the live model list from the server so
 * the user picks from what's actually installed. For known providers we
 * show a curated dropdown plus an "Other (custom)" option that reveals a
 * free text input — gives a discoverable picker for common models without
 * locking out brand-new releases or custom fine-tunes.
 */
function ModelField({
  label,
  value,
  onChange,
  provider,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  provider: string;
}) {
  const isOllama = provider === "ollama";
  const ollamaModels = useQuery({
    queryKey: ["ollama-models"],
    queryFn: () => SettingsApi.ollamaModels(),
    enabled: isOllama,
    retry: false,
  });

  if (isOllama) {
    const list = ollamaModels.data?.models ?? [];
    return (
      <div>
        <label className="label">{label}</label>
        {ollamaModels.isError && (
          <div className="text-xs text-danger mb-1">
            Couldn't reach Ollama. Configure URL on the Settings page.
          </div>
        )}
        <select
          className="input w-full"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          {value && !list.find((m) => m.name === value) && (
            <option value={value}>{value} (not installed)</option>
          )}
          {list.length === 0 && !ollamaModels.isLoading && (
            <option value="">{ollamaModels.isError ? "Set URL in Settings" : "No models found"}</option>
          )}
          {list.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
              {m.parameter_size ? ` (${m.parameter_size})` : ""}
            </option>
          ))}
        </select>
      </div>
    );
  }

  const catalog = MODEL_CATALOG[provider] ?? [];
  const valueIsInCatalog = !!catalog.find((m) => m.value === value);
  // Show free-text input either when "Other" is selected from the picker,
  // or when the current value isn't in the catalog (e.g. user typed a
  // model the dropdown doesn't know about, or there's no catalog yet).
  const showCustom = !valueIsInCatalog;

  return (
    <div>
      <label className="label">{label}</label>
      <select
        className="input w-full"
        value={showCustom ? OTHER_SENTINEL : value}
        onChange={(e) => {
          const v = e.target.value;
          if (v === OTHER_SENTINEL) {
            // Reveal the text input. Don't clear `value` so the user can
            // edit whatever they had.
            onChange(value || "");
          } else {
            onChange(v);
          }
        }}
      >
        {catalog.map((m) => (
          <option key={m.value} value={m.value}>
            {m.label}
          </option>
        ))}
        <option value={OTHER_SENTINEL}>Other (custom)…</option>
      </select>
      {showCustom && (
        <input
          className="input w-full mt-1 text-sm"
          placeholder="Type any model name your provider supports"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          required
        />
      )}
    </div>
  );
}


function TabBtn({
  active,
  done,
  onClick,
  children,
}: {
  active: boolean;
  done: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 text-sm border-b-2 transition-colors ${
        active
          ? "border-accent text-accent"
          : "border-transparent text-muted hover:text-fg"
      }`}
    >
      {done && <span className="mr-1 text-success">✓</span>}
      {children}
    </button>
  );
}
