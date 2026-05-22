"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Compare, type ModelCombo, type CompareListRow } from "@/lib/api";

// ──────────────────────────────────────────────────────────────────────
// /compare — model A/B testing
//
// Submit a ticker + trade_date and check 2-6 model combos. Each combo
// queues an analyze run with the same inputs but a different LLM.
// Drainer (CD, Windows Task, or server-side) processes them; the
// /compare/[id] page shows the side-by-side result.
// ──────────────────────────────────────────────────────────────────────

type ProviderModels = Record<string, { value: string; label: string }[]>;

// Same catalog as /schedules + /run — keep in sync if you add models there.
const MODEL_CATALOG: ProviderModels = {
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
const PRESETS: { label: string; combos: ModelCombo[] }[] = [
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

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
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
  const [notes, setNotes] = useState("");
  const [selected, setSelected] = useState<ModelCombo[]>([]);

  const listQ = useQuery({
    queryKey: ["compare-list"],
    queryFn: () => Compare.list(50),
    refetchInterval: 30_000,
  });

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
                <div className="text-xs uppercase text-muted mb-1">{provider}</div>
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
