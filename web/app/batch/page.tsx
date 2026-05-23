"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Batches, RunQueue, SettingsApi } from "@/lib/api";
import { fmtDate, statusColor } from "@/lib/format";
import type { BatchCreateRequest } from "@/lib/types";

const PRESET_LISTS: { name: string; tickers: string[] }[] = [
  { name: "Mag 7", tickers: ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"] },
  { name: "Sector ETFs", tickers: ["XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLP", "XLU", "XLB", "XLRE", "XLC"] },
  { name: "Index ETFs", tickers: ["SPY", "QQQ", "IWM", "DIA", "VTI", "VXUS"] },
  { name: "Dow 5 leaders", tickers: ["GS", "MSFT", "HD", "UNH", "MCD"] },
];

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
];

// Mirror of MODEL_CATALOG on the Run page so the picker UX matches.
// Keep the two in sync when adding new models.
const MODEL_CATALOG: Record<string, { value: string; label: string }[]> = {
  anthropic: [
    { value: "claude-opus-4-7", label: "claude-opus-4-7 — top tier" },
    { value: "claude-sonnet-4-6", label: "claude-sonnet-4-6 — balanced (default deep)" },
    { value: "claude-sonnet-4-5", label: "claude-sonnet-4-5" },
    { value: "claude-haiku-4-5", label: "claude-haiku-4-5 — fast + cheap (default quick)" },
  ],
  openai: [
    { value: "gpt-5", label: "gpt-5 — top tier" },
    { value: "gpt-4o", label: "gpt-4o" },
    { value: "gpt-4-turbo", label: "gpt-4-turbo" },
    { value: "gpt-4", label: "gpt-4" },
    { value: "gpt-4o-mini", label: "gpt-4o-mini — cheap" },
    { value: "o1", label: "o1 — reasoning" },
    { value: "o1-mini", label: "o1-mini" },
  ],
  google: [
    { value: "gemini-2.5-pro", label: "gemini-2.5-pro" },
    { value: "gemini-2-pro", label: "gemini-2-pro" },
    { value: "gemini-2-flash", label: "gemini-2-flash" },
    { value: "gemini-1.5-pro", label: "gemini-1.5-pro" },
    { value: "gemini-1.5-flash", label: "gemini-1.5-flash" },
  ],
  xai: [
    { value: "grok-3", label: "grok-3" },
    { value: "grok-2", label: "grok-2" },
    { value: "grok-2-mini", label: "grok-2-mini" },
  ],
  deepseek: [
    { value: "deepseek-r1", label: "deepseek-r1" },
    { value: "deepseek-v3", label: "deepseek-v3" },
    { value: "deepseek-chat", label: "deepseek-chat" },
  ],
  qwen: [
    { value: "qwen-max", label: "qwen-max" },
    { value: "qwen-plus", label: "qwen-plus" },
    { value: "qwen-turbo", label: "qwen-turbo" },
  ],
  glm: [
    { value: "glm-4-plus", label: "glm-4-plus" },
    { value: "glm-4", label: "glm-4" },
    { value: "glm-4-flash", label: "glm-4-flash" },
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

function todayIso(): string {
  // Local-day YYYY-MM-DD. sv-SE formats as YYYY-MM-DD natively.
  // Avoid toISOString().slice — that's UTC and rolls over to tomorrow
  // ~5pm PT.
  return new Date().toLocaleDateString("sv-SE");
}

function parseTickers(raw: string): string[] {
  return raw
    .split(/[\s,;]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean)
    .filter((v, i, arr) => arr.indexOf(v) === i);
}

export default function BatchListPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => SettingsApi.get() });
  const batches = useQuery({
    queryKey: ["batches"],
    queryFn: () => Batches.list(),
    refetchInterval: 5_000,
  });

  const defaults = settings.data?.defaults ?? {};

  // Live Ollama model list (only when provider is ollama).
  const [tickersRaw, setTickersRaw] = useState("");
  const [name, setName] = useState("");
  const [tradeDate, setTradeDate] = useState(todayIso());
  const [provider, setProvider] = useState<string>("anthropic");
  const [deepModel, setDeepModel] = useState<string>("");
  const [quickModel, setQuickModel] = useState<string>("");
  const [debateRounds, setDebateRounds] = useState(1);
  const [riskRounds, setRiskRounds] = useState(1);
  const [analysisMode, setAnalysisMode] = useState<"incremental" | "fresh">("incremental");

  // Seed form with saved defaults once they load.
  useEffect(() => {
    if (!settings.data) return;
    setProvider(defaults.llm_provider ?? "anthropic");
    setDeepModel((defaults.deep_think_llm as string) ?? "");
    setQuickModel((defaults.quick_think_llm as string) ?? "");
    setDebateRounds((defaults.max_debate_rounds as number) ?? 1);
    setRiskRounds((defaults.max_risk_discuss_rounds as number) ?? 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.data]);

  const ollamaModels = useQuery({
    queryKey: ["ollama-models"],
    queryFn: () => SettingsApi.ollamaModels(),
    enabled: provider === "ollama",
    retry: false,
  });
  const isOllama = provider === "ollama";
  const ollamaList = ollamaModels.data?.models ?? [];

  const create = useMutation({
    mutationFn: (req: BatchCreateRequest) => Batches.create(req),
    onSuccess: (b) => {
      qc.invalidateQueries({ queryKey: ["batches"] });
      router.push(`/batch/${b.id}`);
    },
  });

  // "Queue for Claude Desktop" — serially POSTs one /run-queue item per
  // ticker. Serialized (not Promise.allSettled) for two reasons:
  // 1. SQLite is a single-writer store; firing N concurrent inserts is the
  //    canonical recipe for "database is locked". The backend now uses WAL
  //    + busy_timeout so contention is recoverable, but a tight loop here
  //    is gentler regardless.
  // 2. UX: serial gives us a real-time "queued K of N" counter and per-item
  //    error reporting that lines up with the ticker order in the textarea.
  // All items share a batch_label so the /queue page can group them and a
  // worker can pull them as one logical unit if it wants.
  const [queueProgress, setQueueProgress] = useState<{ done: number; total: number } | null>(null);
  const queueAll = useMutation({
    mutationFn: async (req: BatchCreateRequest) => {
      // Local timestamp for batch labels (sv-SE = local YYYY-MM-DD HH:mm)
      const batchLabel = (req.name ?? "").trim() ||
        `batch-${new Date().toLocaleString("sv-SE").replace(/[-T: ]/g, "")}`;
      const failed: { ticker: string; error: string }[] = [];
      let ok = 0;
      setQueueProgress({ done: 0, total: req.tickers.length });
      for (const ticker of req.tickers) {
        try {
          await RunQueue.create({
            ticker,
            trade_date: req.trade_date,
            mode: "analyze",
            options: {
              provider: req.llm_provider,
              deep_model: req.deep_think_llm,
              quick_model: req.quick_think_llm,
              debate_rounds: req.max_debate_rounds,
              risk_rounds: req.max_risk_discuss_rounds,
              data_vendors: req.data_vendors,
              batch_label: batchLabel,
              analysis_mode: analysisMode,
            },
            requested_by: `web-ui:/batch:${batchLabel}`,
          });
          ok += 1;
        } catch (e) {
          failed.push({ ticker, error: (e as Error).message });
        }
        setQueueProgress((p) => p ? { ...p, done: p.done + 1 } : null);
      }
      return { batchLabel, total: req.tickers.length, ok, failed };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["run-queue"] });
      // Leave the progress counter visible briefly so the user sees the final state.
      setTimeout(() => setQueueProgress(null), 4000);
    },
    onError: () => setQueueProgress(null),
  });

  const parsed = parseTickers(tickersRaw);

  function buildBatchRequest(): BatchCreateRequest {
    return {
      name: name.trim() || undefined,
      tickers: parsed,
      trade_date: tradeDate,
      llm_provider: provider,
      deep_think_llm: deepModel,
      quick_think_llm: quickModel,
      max_debate_rounds: debateRounds,
      max_risk_discuss_rounds: riskRounds,
      data_vendors: {
        core_stock_apis: "yfinance",
        technical_indicators: "yfinance",
        fundamental_data: "yfinance",
        news_data: "yfinance",
      },
      // Flowed into both the synchronous batch runner and the queue
      // (the queue's options.analysis_mode is set below in queueAll).
      analysis_mode: analysisMode,
    } as BatchCreateRequest;
  }

  function submit() {
    if (parsed.length === 0) return;
    create.mutate(buildBatchRequest());
  }

  function submitQueue() {
    if (parsed.length === 0) return;
    queueAll.mutate(buildBatchRequest());
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Batch analysis</h1>
        <p className="text-muted text-sm">
          Run the multi-agent pipeline against a list of tickers. Runs execute
          sequentially — when one finishes, the next starts. Open the detail
          page to watch progress; each ticker becomes a regular run you can
          drill into from History.
        </p>
      </header>

      <form
        className="card grid grid-cols-3 gap-4"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
      >
        <div className="col-span-3">
          <label className="label">Tickers (whitespace or comma separated)</label>
          <textarea
            className="input w-full h-24"
            value={tickersRaw}
            onChange={(e) => setTickersRaw(e.target.value)}
            placeholder="NVDA AAPL MSFT GOOGL META AMZN TSLA"
            required
          />
          <div className="mt-2 flex flex-wrap gap-2">
            {PRESET_LISTS.map((p) => (
              <button
                key={p.name}
                type="button"
                className="btn text-xs"
                onClick={() => setTickersRaw(p.tickers.join(" "))}
              >
                + {p.name} ({p.tickers.length})
              </button>
            ))}
            <div className="ml-auto text-xs text-muted self-center">
              {parsed.length === 0 ? "no tickers parsed yet" : `${parsed.length} tickers parsed`}
            </div>
          </div>
        </div>

        <div>
          <label className="label">Batch name (optional)</label>
          <input className="input w-full" value={name} onChange={(e) => setName(e.target.value)} placeholder="Mag 7 — 2026-05-14" />
        </div>
        <div>
          <label className="label">Trade date</label>
          <input className="input w-full" type="date" value={tradeDate} onChange={(e) => setTradeDate(e.target.value)} required />
        </div>
        <div>
          <label className="label">Provider</label>
          <select className="input w-full" value={provider} onChange={(e) => setProvider(e.target.value)}>
            {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </div>

        <ModelField label="Deep-think model" value={deepModel} onChange={setDeepModel} provider={provider} ollamaList={ollamaList} />
        <ModelField label="Quick-think model" value={quickModel} onChange={setQuickModel} provider={provider} ollamaList={ollamaList} />

        <div>
          <label className="label">Bull/Bear rounds</label>
          <input className="input w-full" type="number" min={1} max={5} value={debateRounds} onChange={(e) => setDebateRounds(parseInt(e.target.value) || 1)} />
        </div>
        <div>
          <label className="label">Risk rounds</label>
          <input className="input w-full" type="number" min={1} max={5} value={riskRounds} onChange={(e) => setRiskRounds(parseInt(e.target.value) || 1)} />
        </div>

        <div className="col-span-3">
          <label className="label">Memory mode</label>
          <div className="flex gap-4">
            <label className="flex items-start gap-2 text-sm cursor-pointer">
              <input
                type="radio"
                checked={analysisMode === "incremental"}
                onChange={() => setAnalysisMode("incremental")}
                className="mt-1"
              />
              <span>
                <span className="font-semibold">Incremental</span>
                <span className="block text-xs text-muted">PM sees prior decisions for each ticker.</span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm cursor-pointer">
              <input
                type="radio"
                checked={analysisMode === "fresh"}
                onChange={() => setAnalysisMode("fresh")}
                className="mt-1"
              />
              <span>
                <span className="font-semibold">Fresh</span>
                <span className="block text-xs text-muted">No memory injection — break anchoring drift.</span>
              </span>
            </label>
          </div>
        </div>

        <div className="col-span-3 flex flex-wrap justify-end items-center gap-3">
          {provider === "ollama" && (
            <span className="text-xs text-warning">
              ⚠ Ollama runs locally — a batch of N tickers ≈ N × a single run. Plan for hours, not minutes.
            </span>
          )}
          <button
            type="button"
            className="btn"
            onClick={submitQueue}
            disabled={parsed.length === 0 || queueAll.isPending || create.isPending}
            title={
              "Don't run now — drop one queue item per ticker on the run queue. " +
              "A worker (typically the tradingagents-analyze skill in Claude Desktop " +
              "or Claude Code) drains them later using its own LLM budget."
            }
          >
            {queueAll.isPending && queueProgress
              ? `Queueing ${queueProgress.done}/${queueProgress.total}…`
              : queueAll.isPending
                ? `Queueing ${parsed.length}…`
                : `🤖 Queue ${parsed.length} for Claude Desktop`}
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={parsed.length === 0 || create.isPending || queueAll.isPending}
          >
            {create.isPending ? "Starting…" : `▶ Run batch now (${parsed.length})`}
          </button>
        </div>
        {create.isError && (
          <div className="col-span-3 text-sm text-danger">{(create.error as Error).message}</div>
        )}
        {queueAll.isError && (
          <div className="col-span-3 text-sm text-danger">
            Queue failed: {(queueAll.error as Error).message}
          </div>
        )}
        {queueAll.isSuccess && queueAll.data && (
          <div className="col-span-3 text-sm">
            <span className="text-success">
              ✓ Queued {queueAll.data.ok}/{queueAll.data.total} tickers as{" "}
              <code>{queueAll.data.batchLabel}</code>
            </span>
            {queueAll.data.failed.length > 0 && (
              <div className="text-danger mt-1">
                {queueAll.data.failed.length} failed:{" "}
                {queueAll.data.failed
                  .map((f) => `${f.ticker} (${f.error.slice(0, 60)})`)
                  .join(", ")}
              </div>
            )}
            <div className="mt-1">
              <Link href="/queue" className="text-accent hover:underline">
                View queue →
              </Link>
            </div>
          </div>
        )}
      </form>

      <section>
        <h2 className="text-lg font-semibold mb-3">Recent batches</h2>
        {batches.isLoading && <div className="text-muted text-sm">Loading…</div>}
        {!batches.isLoading && (batches.data?.length ?? 0) === 0 && (
          <div className="card text-sm text-muted">No batches yet.</div>
        )}
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs uppercase text-muted">
                <th className="text-left py-2 px-3 font-medium">Name</th>
                <th className="text-right py-2 px-3 font-medium">Tickers</th>
                <th className="text-left py-2 px-3 font-medium">Trade date</th>
                <th className="text-left py-2 px-3 font-medium">Provider/Model</th>
                <th className="text-left py-2 px-3 font-medium">Status</th>
                <th className="text-left py-2 px-3 font-medium">Started</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(batches.data ?? []).map((b) => (
                <tr key={b.id} className="border-t border-border hover:bg-bg/40">
                  <td className="py-2 px-3 font-semibold">{b.name ?? b.id.slice(0, 8)}</td>
                  <td className="py-2 px-3 text-right">{b.total}</td>
                  <td className="py-2 px-3">{b.trade_date}</td>
                  <td className="py-2 px-3 text-muted">{b.provider ?? "—"} / {b.deep_model ?? "—"}</td>
                  <td className="py-2 px-3">
                    <span className={`pill ${statusColor(b.status)}`}>{b.status}</span>
                  </td>
                  <td className="py-2 px-3 text-muted">{fmtDate(b.started_at)}</td>
                  <td className="py-2 px-3 text-right">
                    <Link className="text-accent hover:underline" href={`/batch/${b.id}`}>Open →</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function ModelField({
  label, value, onChange, provider, ollamaList,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  provider: string;
  ollamaList: Array<{ name: string; parameter_size?: string }>;
}) {
  const isOllama = provider === "ollama";
  if (isOllama) {
    return (
      <div>
        <label className="label">{label}</label>
        <select className="input w-full" value={value} onChange={(e) => onChange(e.target.value)}>
          {value && !ollamaList.find((m) => m.name === value) && (
            <option value={value}>{value} (not installed)</option>
          )}
          {ollamaList.length === 0 && <option value="">No models — set URL in Settings</option>}
          {ollamaList.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}{m.parameter_size ? ` (${m.parameter_size})` : ""}
            </option>
          ))}
        </select>
      </div>
    );
  }
  const catalog = MODEL_CATALOG[provider] ?? [];
  const valueIsInCatalog = !!catalog.find((m) => m.value === value);
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
            onChange(value || "");
          } else {
            onChange(v);
          }
        }}
      >
        {catalog.map((m) => (
          <option key={m.value} value={m.value}>{m.label}</option>
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
