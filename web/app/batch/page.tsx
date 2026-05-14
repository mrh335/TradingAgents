"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Batches, SettingsApi } from "@/lib/api";
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

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
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

  const parsed = parseTickers(tickersRaw);

  function submit() {
    if (parsed.length === 0) return;
    create.mutate({
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
    });
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

        <ModelField label="Deep-think model" value={deepModel} onChange={setDeepModel} isOllama={isOllama} ollamaList={ollamaList} />
        <ModelField label="Quick-think model" value={quickModel} onChange={setQuickModel} isOllama={isOllama} ollamaList={ollamaList} />

        <div>
          <label className="label">Bull/Bear rounds</label>
          <input className="input w-full" type="number" min={1} max={5} value={debateRounds} onChange={(e) => setDebateRounds(parseInt(e.target.value) || 1)} />
        </div>
        <div>
          <label className="label">Risk rounds</label>
          <input className="input w-full" type="number" min={1} max={5} value={riskRounds} onChange={(e) => setRiskRounds(parseInt(e.target.value) || 1)} />
        </div>

        <div className="col-span-3 flex justify-end items-center gap-3">
          {provider === "ollama" && (
            <span className="text-xs text-warning">
              ⚠ Ollama runs locally — a batch of N tickers ≈ N × a single run. Plan for hours, not minutes.
            </span>
          )}
          <button type="submit" className="btn btn-primary" disabled={parsed.length === 0 || create.isPending}>
            {create.isPending ? "Queuing…" : `▶ Run batch (${parsed.length})`}
          </button>
        </div>
        {create.isError && (
          <div className="col-span-3 text-sm text-danger">{(create.error as Error).message}</div>
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
  label, value, onChange, isOllama, ollamaList,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  isOllama: boolean;
  ollamaList: Array<{ name: string; parameter_size?: string }>;
}) {
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
  return (
    <div>
      <label className="label">{label}</label>
      <input className="input w-full" value={value} onChange={(e) => onChange(e.target.value)} required />
    </div>
  );
}
