"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Compare,
  SettingsApi,
  type ModelCombo,
  type CompareListRow,
} from "@/lib/api";

// ──────────────────────────────────────────────────────────────────────
// /compare — model A/B testing
//
// Submit a ticker + trade_date and check 2-6 model combos. Each combo
// queues an analyze run with the same inputs but a different LLM.
// Drainer (CD, Windows Task, or server-side) processes them; the
// /compare/[id] page shows the side-by-side result.
// ──────────────────────────────────────────────────────────────────────

type ProviderModels = Record<string, { value: string; label: string }[]>;

// Static catalog for hosted providers. Same as /schedules + /run.
const STATIC_CATALOG: ProviderModels = {
  anthropic: [
    { value: "claude-opus-4-7", label: "Opus 4.7 — top tier" },
    { value: "claude-sonnet-4-6", label: "Sonnet 4.6 — balanced" },
    { value: "claude-sonnet-4-5", label: "Sonnet 4.5" },
    { value: "claude-haiku-4-5", label: "Haiku 4.5 — fast + cheap" },
  ],
  openai: [
    { value: "gpt-5", label: "GPT-5 — top tier" },
    { value: "gpt-4o", label: "GPT-4o" },
    { value: "gpt-4-turbo", label: "GPT-4-turbo" },
    { value: "gpt-4o-mini", label: "GPT-4o-mini — cheap" },
    { value: "o1", label: "o1 — reasoning" },
  ],
  google: [
    { value: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
    { value: "gemini-2-pro", label: "Gemini 2 Pro" },
    { value: "gemini-2-flash", label: "Gemini 2 Flash" },
  ],
};

// Curated presets — common A/B questions a user might want to ask.
// Note: presets reference STATIC models only; Ollama presets are
// generated from the live catalog (see useMemo in the component).
const STATIC_PRESETS: { label: string; combos: ModelCombo[] }[] = [
  {
    label: "Anthropic flagships (Opus vs Sonnet)",
    combos: [
      { provider: "anthropic", deep_model: "claude-opus-4-7", label: "Opus" },
      { provider: "anthropic", deep_model: "claude-sonnet-4-6", label: "Sonnet" },
    ],
  },
  {
    label: "Anthropic tier ladder (Opus / Sonnet / Haiku)",
    combos: [
      { provider: "anthropic", deep_model: "claude-opus-4-7", label: "Opus" },
      { provider: "anthropic", deep_model: "claude-sonnet-4-6", label: "Sonnet" },
      { provider: "anthropic", deep_model: "claude-haiku-4-5", label: "Haiku" },
    ],
  },
  {
    label: "Cross-vendor flagships (Claude / GPT / Gemini)",
    combos: [
      { provider: "anthropic", deep_model: "claude-opus-4-7", label: "Claude Opus" },
      { provider: "openai", deep_model: "gpt-5", label: "GPT-5" },
      { provider: "google", deep_model: "gemini-2.5-pro", label: "Gemini Pro" },
    ],
  },
  {
    label: "Reasoning vs general (o1 / Sonnet / GPT-4o)",
    combos: [
      { provider: "openai", deep_model: "o1", label: "o1 (reasoning)" },
      { provider: "anthropic", deep_model: "claude-sonnet-4-6", label: "Sonnet" },
      { provider: "openai", deep_model: "gpt-4o", label: "GPT-4o" },
    ],
  },
];

// Vision-only Ollama models — these can describe images but cannot
// reason about text-only stock data. They produced empty briefs in
// the user's 5/25 NVDA comparison (no decision, no tldr, no triggers).
// Filter them out of the picker so they're never selectable.
//
// Match strategy: substring match on the model name (case-insensitive).
// Covers both the bare model name (e.g. "llava") and any quantization
// suffix (e.g. "llava:7b", "llava:13b").
const OLLAMA_VISION_EXCLUDES = [
  "llava",
  "bakllava",
  "moondream",
  "minicpm-v",
  "llava-llama",
  "llava-phi",
  "cogvlm",
  "obsidian",   // tiny vision model
];

function isOllamaTextOnlyModel(name: string): boolean {
  const lower = name.toLowerCase();
  return !OLLAMA_VISION_EXCLUDES.some((v) => lower.includes(v));
}

// Built dynamically once we know which Ollama models are installed.
function ollamaPreset(ollamaModels: string[]): { label: string; combos: ModelCombo[] }[] {
  const textModels = ollamaModels.filter(isOllamaTextOnlyModel);
  if (textModels.length === 0) return [];
  const flagship =
    textModels.find((m) => m.includes("qwen2.5:14b")) ||
    textModels.find((m) => m.includes("qwen3")) ||
    textModels[0];
  return [
    {
      label: `Opus vs Sonnet vs ${flagship} (free local)`,
      combos: [
        { provider: "anthropic", deep_model: "claude-opus-4-7", label: "Opus" },
        { provider: "anthropic", deep_model: "claude-sonnet-4-6", label: "Sonnet" },
        { provider: "ollama", deep_model: flagship, label: `Ollama: ${flagship}` },
      ],
    },
  ];
}

function todayIso(): string {
  // Local-day YYYY-MM-DD (sv-SE locale formats this way natively).
  // Avoid toISOString().slice — that's UTC and rolls over at ~5pm PT.
  return new Date().toLocaleDateString("sv-SE");
}

function fmtTs(s: string | null): string {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}

const STATUS_LABEL: Record<string, string> = {
  pending: "⏳ pending",
  in_progress: "🤖 running",
  partial: "◐ partial",
  complete: "✓ complete",
};

const DECISION_TONE: Record<string, string> = {
  Buy: "text-success font-semibold",
  Overweight: "text-success",
  Hold: "text-muted",
  Underweight: "text-warning",
  Sell: "text-danger font-semibold",
};

export default function ComparePage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [ticker, setTicker] = useState("");
  const [tradeDate, setTradeDate] = useState(todayIso());
  const [analysisMode, setAnalysisMode] = useState<"fresh" | "incremental">("fresh");
  const [executionMode, setExecutionMode] = useState<"server" | "queue">("server");
  const [notes, setNotes] = useState("");
  const [selected, setSelected] = useState<ModelCombo[]>([]);

  const listQ = useQuery({
    queryKey: ["compare-list"],
    queryFn: () => Compare.list(50),
    refetchInterval: 30_000,
  });

  // Discover locally-installed Ollama models so they can be checked here.
  const ollamaQ = useQuery({
    queryKey: ["ollama-models"],
    queryFn: () => SettingsApi.ollamaModels(),
    retry: false, // don't hammer if Ollama isn't configured
    staleTime: 60_000,
  });

  // Build the full catalog: static hosted providers + discovered Ollama.
  // Vision-only Ollama models are filtered out — they can't reason about
  // text-only stock data and produced empty briefs in past comparisons.
  const MODEL_CATALOG = useMemo<ProviderModels>(() => {
    const cat: ProviderModels = { ...STATIC_CATALOG };
    const ollamaModels = (ollamaQ.data?.models ?? []).filter((m: any) =>
      isOllamaTextOnlyModel(m.name),
    );
    if (ollamaModels.length > 0) {
      cat.ollama = ollamaModels.map((m: any) => ({
        value: m.name,
        label: `${m.name}${m.size ? ` — ${(m.size / 1e9).toFixed(1)}GB` : ""}`,
      }));
    }
    return cat;
  }, [ollamaQ.data]);

  const PRESETS = useMemo(() => {
    const ollamaModels = (ollamaQ.data?.models ?? [])
      .filter((m: any) => isOllamaTextOnlyModel(m.name))
      .map((m: any) => m.name);
    return [...STATIC_PRESETS, ...ollamaPreset(ollamaModels)];
  }, [ollamaQ.data]);

  const createM = useMutation({
    mutationFn: (req: Parameters<typeof Compare.create>[0]) => Compare.create(req),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["compare-list"] });
      router.push(`/compare/${res.comparison_id}`);
    },
  });

  function toggleModel(provider: string, deep_model: string) {
    const key = `${provider}/${deep_model}`;
    const existing = selected.find(
      (c) => `${c.provider}/${c.deep_model}` === key,
    );
    if (existing) {
      setSelected(selected.filter((c) => `${c.provider}/${c.deep_model}` !== key));
    } else {
      setSelected([
        ...selected,
        { provider, deep_model, label: MODEL_CATALOG[provider]?.find((m) => m.value === deep_model)?.label?.split(" — ")[0] ?? deep_model },
      ]);
    }
  }

  function applyPreset(preset: (typeof PRESETS)[number]) {
    setSelected(preset.combos);
  }

  function canSubmit(): boolean {
    return !!ticker.trim() && selected.length >= 2 && selected.length <= 6;
  }

  function submit() {
    if (!canSubmit()) return;
    createM.mutate({
      ticker: ticker.trim().toUpperCase(),
      trade_date: tradeDate,
      analysis_mode: analysisMode,
      execution_mode: executionMode,
      combos: selected,
      notes: notes || undefined,
    });
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Compare models</h1>
        <p className="text-muted text-sm">
          Run the same ticker + date through 2-6 model combos in parallel.
          The decisions, brief excerpts, and token costs land side-by-side
          on <code>/compare/[id]</code>. Useful for sanity-checking which
          model to trust for a given ticker, or just exploring how each
          one reasons differently.
        </p>
        <p className="text-muted text-xs mt-2">
          Each combo queues a separate analyze run. The drainer (Claude
          Desktop / Windows Task / server-side) picks them up like any
          other queue item. 3-5 minutes per heavy run, so 3 combos ≈
          10-15 min total. Comparison rows poll the queue + run state
          and update live as each finishes.
        </p>
      </header>

      {/* ─── Submission form ─── */}
      <div className="card space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="label">Ticker</label>
            <input
              className="input w-full"
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              placeholder="NVDA"
              required
            />
          </div>
          <div>
            <label className="label">Trade date</label>
            <input
              type="date"
              className="input w-full"
              value={tradeDate}
              onChange={(e) => setTradeDate(e.target.value)}
            />
          </div>
          <div>
            <label className="label">Memory mode</label>
            <select
              className="input w-full"
              value={analysisMode}
              onChange={(e) => setAnalysisMode(e.target.value as "fresh" | "incremental")}
            >
              <option value="fresh">fresh (recommended — fair comparison)</option>
              <option value="incremental">incremental (biased by prior decision)</option>
            </select>
          </div>
        </div>

        {/* Execution mode toggle */}
        <div className="bg-surface p-3 rounded">
          <label className="label">How to execute</label>
          <div className="space-y-2 text-sm">
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="radio"
                checked={executionMode === "server"}
                onChange={() => setExecutionMode("server")}
                className="mt-1"
              />
              <div>
                <div className="font-semibold">
                  Run server-side now (recommended)
                </div>
                <div className="text-xs text-muted">
                  Each combo kicks off immediately on the NAS using the
                  specific model you picked. Anthropic / OpenAI / Google
                  combos cost API tokens; Ollama combos are{" "}
                  <strong>free</strong> (run on your local Ollama at{" "}
                  {ollamaQ.data?.url ?? "(not configured)"}). This is the
                  only way to get an honest A/B comparison — each model
                  actually used.
                </div>
              </div>
            </label>
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="radio"
                checked={executionMode === "queue"}
                onChange={() => setExecutionMode("queue")}
                className="mt-1"
              />
              <div>
                <div className="font-semibold">
                  Queue for Claude Desktop drainer
                </div>
                <div className="text-xs text-muted">
                  Creates run_queue items at priority 10. The Windows
                  Scheduled Task / CD drainer picks them up.{" "}
                  <strong className="text-warning">
                    Caveat: CD can&apos;t be programmatically forced to
                    use a specific model
                  </strong>{" "}
                  — it always uses whatever your chat is currently set to.
                  So a queue-mode comparison of Opus vs Sonnet would
                  actually run BOTH with whatever model CD is in. Useful
                  only for sanity-checks with one model. For real A/B
                  testing, use server-side.
                </div>
              </div>
            </label>
          </div>
        </div>

        {/* Presets */}
        <div>
          <label className="label">Quick-start presets</label>
          <div className="flex flex-wrap gap-2">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                onClick={() => applyPreset(p)}
                className="btn text-xs"
                type="button"
              >
                {p.label} ({p.combos.length})
              </button>
            ))}
            <button
              onClick={() => setSelected([])}
              className="btn text-xs"
              type="button"
              disabled={selected.length === 0}
            >
              Clear
            </button>
          </div>
        </div>

        {/* Model checkboxes */}
        <div>
          <label className="label">Models to compare ({selected.length}/6)</label>
          <div className="space-y-3">
            {Object.entries(MODEL_CATALOG).map(([provider, models]) => (
              <div key={provider} className="bg-surface p-3 rounded">
                <div className="text-xs uppercase text-muted mb-1">
                  {provider}
                  {provider === "ollama" && (
                    <>
                      <span className="ml-2 text-success normal-case">
                        free + local
                      </span>
                      {(() => {
                        // Note when vision-only models were filtered out so the
                        // user knows why their llava/moondream aren't listed.
                        const all = ollamaQ.data?.models ?? [];
                        const filtered = all.filter((m: any) => isOllamaTextOnlyModel(m.name));
                        const hidden = all.length - filtered.length;
                        if (hidden <= 0) return null;
                        const hiddenNames = all
                          .filter((m: any) => !isOllamaTextOnlyModel(m.name))
                          .map((m: any) => m.name)
                          .join(", ");
                        return (
                          <span
                            className="ml-2 text-muted normal-case lowercase text-[10px]"
                            title={`Vision-only models hidden (can't analyze text): ${hiddenNames}`}
                          >
                            ({hidden} vision-only model{hidden === 1 ? "" : "s"} hidden)
                          </span>
                        );
                      })()}
                    </>
                  )}
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-1">
                  {models.map((m) => {
                    const checked = selected.some(
                      (c) =>
                        c.provider === provider && c.deep_model === m.value,
                    );
                    const disabled = !checked && selected.length >= 6;
                    return (
                      <label
                        key={m.value}
                        className={`flex items-center gap-2 text-sm cursor-pointer ${disabled ? "opacity-40 cursor-not-allowed" : ""}`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={disabled}
                          onChange={() => toggleModel(provider, m.value)}
                        />
                        <span>{m.label}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            ))}
            {!ollamaQ.data && (
              <p className="text-xs text-muted italic">
                (Ollama not detected — to use local models in comparisons,
                configure ollama_base_url in /settings)
              </p>
            )}
          </div>
          {selected.length < 2 && (
            <p className="text-xs text-warning mt-2">
              Pick at least 2 models to enable comparison.
            </p>
          )}
        </div>

        <div>
          <label className="label">
            Notes <span className="text-muted">(optional)</span>
          </label>
          <input
            className="input w-full"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. 'Pre-earnings test — which model called NVDA Q4 right?'"
            maxLength={500}
          />
        </div>

        <div className="flex items-center gap-3 justify-end">
          {createM.isError && (
            <span className="text-danger text-xs">
              {(createM.error as Error).message}
            </span>
          )}
          <button
            className="btn btn-primary"
            onClick={submit}
            disabled={!canSubmit() || createM.isPending}
          >
            {createM.isPending
              ? "Submitting…"
              : `Submit comparison (${selected.length} runs)`}
          </button>
        </div>
      </div>

      {/* ─── Recent comparisons ─── */}
      <section>
        <h2 className="text-lg font-semibold mb-2">Recent comparisons</h2>
        {listQ.isLoading ? (
          <div className="text-muted text-sm">Loading…</div>
        ) : (listQ.data?.length ?? 0) === 0 ? (
          <div className="card text-sm text-muted">
            No comparisons yet. Submit one above.
          </div>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-muted">
                <tr>
                  <th className="py-2">Ticker / Date</th>
                  <th>Models</th>
                  <th>Progress</th>
                  <th>Status</th>
                  <th>Consensus</th>
                  <th>Submitted</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {(listQ.data ?? []).map((c: CompareListRow) => (
                  <tr key={c.comparison_id} className="border-t border-border">
                    <td className="py-2 font-semibold">
                      {c.ticker}
                      <div className="text-xs text-muted">{c.trade_date}</div>
                    </td>
                    <td>{c.combo_count}</td>
                    <td>
                      {c.completed_count}/{c.combo_count}
                    </td>
                    <td className="text-xs">{STATUS_LABEL[c.overall_status]}</td>
                    <td className={DECISION_TONE[c.consensus ?? ""] ?? "text-muted"}>
                      {c.consensus ?? <span className="text-muted">—</span>}
                    </td>
                    <td className="text-xs text-muted">{fmtTs(c.created_at)}</td>
                    <td className="text-right">
                      <Link
                        href={`/compare/${c.comparison_id}`}
                        className="btn text-xs"
                      >
                        View →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
