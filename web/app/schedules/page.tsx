"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Schedules, type Schedule } from "@/lib/api";

// ─── Cron building blocks ─────────────────────────────────────────────
// Cron day-of-week: Sun=0, Mon=1, ..., Sat=6 (standard 5-field).
type DowKey = "Sun" | "Mon" | "Tue" | "Wed" | "Thu" | "Fri" | "Sat";
const DOW_ORDER: DowKey[] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DOW_TO_CRON: Record<DowKey, number> = {
  Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6,
};

// Curated model catalogue per provider — same source-of-truth as /run.
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
    { value: "gpt-4o-mini", label: "gpt-4o-mini — cheap" },
    { value: "o1", label: "o1 — reasoning" },
    { value: "o1-mini", label: "o1-mini" },
  ],
  google: [
    { value: "gemini-2.5-pro", label: "gemini-2.5-pro" },
    { value: "gemini-2-pro", label: "gemini-2-pro" },
    { value: "gemini-2-flash", label: "gemini-2-flash" },
  ],
  ollama: [],
};
const OTHER = "__other__";

// Quick-start presets — populate the builder so the user can see the
// shape, then customize.
const PRESETS: { label: string; days: DowKey[]; hour: number; minute: number; ampm: "AM" | "PM" }[] = [
  { label: "Weekdays 6 AM", days: ["Mon","Tue","Wed","Thu","Fri"], hour: 6, minute: 0, ampm: "AM" },
  { label: "Weekdays 5 PM (after close)", days: ["Mon","Tue","Wed","Thu","Fri"], hour: 5, minute: 0, ampm: "PM" },
  { label: "Mon/Wed/Fri 7 AM", days: ["Mon","Wed","Fri"], hour: 7, minute: 0, ampm: "AM" },
  { label: "Every day 6 AM", days: ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"], hour: 6, minute: 0, ampm: "AM" },
  { label: "Sunday 7 PM", days: ["Sun"], hour: 7, minute: 0, ampm: "PM" },
];

function to24h(hour: number, ampm: "AM" | "PM"): number {
  // hour is in [1..12]
  if (ampm === "AM") return hour === 12 ? 0 : hour;
  return hour === 12 ? 12 : hour + 12;
}

function buildCron(days: DowKey[], hour: number, minute: number, ampm: "AM" | "PM"): string {
  if (days.length === 0) return "";
  const h24 = to24h(hour, ampm);
  const dow = days
    .map((d) => DOW_TO_CRON[d])
    .sort((a, b) => a - b)
    .join(",");
  return `${minute} ${h24} * * ${dow}`;
}

function describeBuild(days: DowKey[], hour: number, minute: number, ampm: "AM" | "PM"): string {
  if (days.length === 0) return "Pick at least one day";
  // Compact day display
  let dayDesc: string;
  if (days.length === 7) dayDesc = "every day";
  else if (days.length === 5 && days.every((d) => d !== "Sat" && d !== "Sun")) dayDesc = "weekdays";
  else if (days.length === 2 && days.includes("Sat") && days.includes("Sun")) dayDesc = "weekends";
  else dayDesc = days.join("/");
  const mm = minute.toString().padStart(2, "0");
  return `${dayDesc} at ${hour}:${mm} ${ampm} Pacific`;
}

function fmtTs(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function SchedulesPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["schedules"],
    queryFn: () => Schedules.list(),
    refetchInterval: 30_000,
  });

  // ─── Form state ─────────────────────────────────────────────────────
  const [form, setForm] = useState<{
    ticker: string;
    days: DowKey[];
    hour: number;       // 1..12
    minute: number;     // 0,15,30,45
    ampm: "AM" | "PM";
    notes: string;
    analysis_mode: "incremental" | "fresh";
    overrideDays: DowKey[];     // days on which to flip to the other mode
    overrideModel: boolean;
    provider: string;
    deep_model: string;
    quick_model: string;
    debate_rounds: number;
    risk_rounds: number;
  }>({
    ticker: "",
    days: ["Mon", "Tue", "Wed", "Thu", "Fri"],
    hour: 6,
    minute: 0,
    ampm: "AM",
    notes: "",
    analysis_mode: "fresh",
    overrideDays: [],
    overrideModel: false,
    provider: "anthropic",
    deep_model: "claude-sonnet-4-6",
    quick_model: "claude-haiku-4-5",
    debate_rounds: 1,
    risk_rounds: 1,
  });

  const effectiveCron = useMemo(
    () => buildCron(form.days, form.hour, form.minute, form.ampm),
    [form.days, form.hour, form.minute, form.ampm],
  );

  function toggleDay(d: DowKey) {
    setForm((f) => ({
      ...f,
      days: f.days.includes(d) ? f.days.filter((x) => x !== d) : [...f.days, d],
    }));
  }
  function toggleOverrideDay(d: DowKey) {
    setForm((f) => ({
      ...f,
      overrideDays: f.overrideDays.includes(d)
        ? f.overrideDays.filter((x) => x !== d)
        : [...f.overrideDays, d],
    }));
  }
  function applyPreset(p: typeof PRESETS[number]) {
    setForm((f) => ({ ...f, days: p.days, hour: p.hour, minute: p.minute, ampm: p.ampm }));
  }

  const create = useMutation({
    mutationFn: () => {
      const options: Record<string, any> = {
        analysis_mode: form.analysis_mode,
      };
      // Per-weekday memory override — backend scheduler reads this and
      // flips analysis_mode for the named days when the cron fires.
      if (form.overrideDays.length > 0) {
        const otherMode = form.analysis_mode === "fresh" ? "incremental" : "fresh";
        options.analysis_mode_overrides = Object.fromEntries(
          form.overrideDays.map((d) => [d, otherMode]),
        );
      }
      if (form.overrideModel) {
        options.provider = form.provider;
        options.deep_model = form.deep_model;
        options.quick_model = form.quick_model;
        options.debate_rounds = form.debate_rounds;
        options.risk_rounds = form.risk_rounds;
        options.data_vendors = {
          core_stock_apis: "yfinance",
          technical_indicators: "yfinance",
          fundamental_data: "yfinance",
          news_data: "yfinance",
        };
      }
      return Schedules.create({
        ticker: form.ticker.toUpperCase(),
        cron_expression: effectiveCron,
        mode: "analyze",
        options,
        enabled: true,
        notes: form.notes || undefined,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schedules"] });
      setForm({ ...form, ticker: "", notes: "" });
    },
  });

  const toggle = useMutation({
    mutationFn: (s: Schedule) => Schedules.update(s.id, { enabled: !s.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }),
  });
  const fire = useMutation({
    mutationFn: (id: number) => Schedules.fire(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }),
  });
  const remove = useMutation({
    mutationFn: (id: number) => Schedules.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }),
  });

  const items = q.data ?? [];
  const enabled = items.filter((s) => s.enabled);
  const disabled = items.filter((s) => !s.enabled);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Auto-run schedules</h1>
        <p className="text-muted text-sm">
          Per-ticker analysis schedules. The background scheduler ticks every
          minute, evaluates each enabled row, and posts a queue item when due.
          Your Claude Desktop drain cron picks it up — so the loop closes itself.
        </p>
        <p className="text-muted text-xs mt-1">
          Times shown in Pacific time (TZ=America/Los_Angeles on the api container).
        </p>
      </header>

      {/* ─── Builder form ─── */}
      <form
        className="card space-y-5"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
          <div>
            <label className="label">Ticker</label>
            <input
              className="input w-full"
              value={form.ticker}
              onChange={(e) => setForm({ ...form, ticker: e.target.value.toUpperCase() })}
              placeholder="NVDA"
              required
            />
          </div>
          <div className="md:col-span-2">
            <label className="label">Notes <span className="text-muted">(optional)</span></label>
            <input
              className="input w-full"
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              placeholder="e.g. weekday morning refresh"
              maxLength={200}
            />
          </div>
        </div>

        {/* ─── Cadence builder ─── */}
        <div className="border border-border rounded-md p-3 space-y-3">
          <div className="text-sm font-semibold">When should this run?</div>

          {/* Day chips */}
          <div>
            <div className="text-xs text-muted mb-1">Days</div>
            <div className="flex flex-wrap gap-1">
              {DOW_ORDER.map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => toggleDay(d)}
                  className={`px-3 py-1.5 text-sm rounded border transition-colors ${
                    form.days.includes(d)
                      ? "bg-accent text-white border-accent"
                      : "border-border text-muted hover:text-fg"
                  }`}
                >
                  {d}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setForm({ ...form, days: ["Mon","Tue","Wed","Thu","Fri"] })}
                className="px-3 py-1.5 text-xs rounded text-accent hover:underline ml-2"
              >
                weekdays
              </button>
              <button
                type="button"
                onClick={() => setForm({ ...form, days: ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"] })}
                className="px-3 py-1.5 text-xs rounded text-accent hover:underline"
              >
                every day
              </button>
            </div>
          </div>

          {/* Time picker */}
          <div className="flex items-center gap-2">
            <div className="text-xs text-muted">Time</div>
            <select
              className="input"
              value={form.hour}
              onChange={(e) => setForm({ ...form, hour: Number(e.target.value) })}
            >
              {Array.from({ length: 12 }, (_, i) => i + 1).map((h) => (
                <option key={h} value={h}>{h}</option>
              ))}
            </select>
            <span>:</span>
            <select
              className="input"
              value={form.minute}
              onChange={(e) => setForm({ ...form, minute: Number(e.target.value) })}
            >
              {[0, 15, 30, 45].map((m) => (
                <option key={m} value={m}>{m.toString().padStart(2, "0")}</option>
              ))}
            </select>
            <select
              className="input"
              value={form.ampm}
              onChange={(e) => setForm({ ...form, ampm: e.target.value as "AM" | "PM" })}
            >
              <option value="AM">AM</option>
              <option value="PM">PM</option>
            </select>
            <span className="text-xs text-muted ml-2">Pacific</span>
          </div>

          {/* Preview */}
          <div className="text-xs text-muted">
            Cadence: <span className="text-fg font-semibold">{describeBuild(form.days, form.hour, form.minute, form.ampm)}</span>
            <br />
            Cron: <code className="text-fg">{effectiveCron || "—"}</code>
          </div>

          {/* Quick presets */}
          <div className="flex flex-wrap gap-2">
            <span className="text-xs text-muted self-center">Quick presets:</span>
            {PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                onClick={() => applyPreset(p)}
                className="px-2 py-1 text-xs rounded border border-border text-muted hover:text-fg"
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* ─── Memory mode ─── */}
        <div className="border border-border rounded-md p-3 space-y-3">
          <div className="text-sm font-semibold">Memory mode</div>
          <div className="flex gap-4">
            <label className="flex items-start gap-2 text-sm cursor-pointer">
              <input
                type="radio"
                checked={form.analysis_mode === "fresh"}
                onChange={() => setForm({ ...form, analysis_mode: "fresh" })}
                className="mt-1"
              />
              <span>
                <span className="font-semibold">Fresh</span>{" "}
                <span className="text-xs text-muted">(recommended)</span>
                <span className="block text-xs text-muted max-w-xl">
                  No memory injection. PM evaluates each run from scratch — breaks
                  decision-anchoring drift on recurring runs.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm cursor-pointer">
              <input
                type="radio"
                checked={form.analysis_mode === "incremental"}
                onChange={() => setForm({ ...form, analysis_mode: "incremental" })}
                className="mt-1"
              />
              <span>
                <span className="font-semibold">Incremental</span>
                <span className="block text-xs text-muted max-w-xl">
                  PM sees prior decisions for this ticker. Faster convergence
                  but anchors on yesterday.
                </span>
              </span>
            </label>
          </div>

          {/* Per-weekday override — for "Mon-Thu incremental, Fri fresh" */}
          <div className="pt-2 border-t border-border">
            <div className="text-xs text-muted mb-1">
              Override on specific days (run those days in the opposite mode):
            </div>
            <div className="flex flex-wrap gap-1">
              {DOW_ORDER.filter((d) => form.days.includes(d)).map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => toggleOverrideDay(d)}
                  className={`px-3 py-1 text-xs rounded border transition-colors ${
                    form.overrideDays.includes(d)
                      ? "bg-warning text-white border-warning"
                      : "border-border text-muted hover:text-fg"
                  }`}
                  title={
                    form.overrideDays.includes(d)
                      ? `${d} runs as ${form.analysis_mode === "fresh" ? "incremental" : "fresh"} (opposite of default)`
                      : `${d} runs as ${form.analysis_mode} (default)`
                  }
                >
                  {d}
                </button>
              ))}
              {form.days.length === 0 && (
                <span className="text-xs text-muted">Pick days above first</span>
              )}
            </div>
            {form.overrideDays.length > 0 && (
              <div className="text-xs text-muted mt-2">
                Override active: <span className="text-warning font-semibold">
                  {form.overrideDays.join(", ")}
                </span>{" "}
                will run as <span className="font-semibold">
                  {form.analysis_mode === "fresh" ? "incremental" : "fresh"}
                </span>; other days as <span className="font-semibold">{form.analysis_mode}</span>.
              </div>
            )}
          </div>
        </div>

        {/* ─── Advanced overrides ─── */}
        <details className="border border-border rounded-md">
          <summary className="cursor-pointer px-3 py-2 text-sm text-muted hover:text-fg">
            Advanced — override model / depth (leave closed for skill defaults)
          </summary>
          <div className="px-3 pb-3 pt-1 space-y-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.overrideModel}
                onChange={(e) => setForm({ ...form, overrideModel: e.target.checked })}
              />
              Apply these overrides
            </label>
            <div
              className={`grid grid-cols-2 md:grid-cols-3 gap-3 ${
                form.overrideModel ? "" : "opacity-50 pointer-events-none"
              }`}
            >
              <div>
                <label className="label">Provider</label>
                <select
                  className="input w-full"
                  value={form.provider}
                  onChange={(e) => setForm({ ...form, provider: e.target.value })}
                >
                  <option value="anthropic">Anthropic (Claude)</option>
                  <option value="openai">OpenAI (GPT)</option>
                  <option value="google">Google (Gemini)</option>
                  <option value="ollama">Ollama (local)</option>
                </select>
              </div>
              <ModelDropdown
                label="Deep-think model"
                provider={form.provider}
                value={form.deep_model}
                onChange={(v) => setForm({ ...form, deep_model: v })}
              />
              <ModelDropdown
                label="Quick-think model"
                provider={form.provider}
                value={form.quick_model}
                onChange={(v) => setForm({ ...form, quick_model: v })}
              />
              <div>
                <label className="label">Bull/Bear rounds</label>
                <input
                  type="number"
                  min={1}
                  max={5}
                  className="input w-full"
                  value={form.debate_rounds}
                  onChange={(e) => setForm({ ...form, debate_rounds: Number(e.target.value) })}
                />
              </div>
              <div>
                <label className="label">Risk rounds</label>
                <input
                  type="number"
                  min={1}
                  max={5}
                  className="input w-full"
                  value={form.risk_rounds}
                  onChange={(e) => setForm({ ...form, risk_rounds: Number(e.target.value) })}
                />
              </div>
            </div>
          </div>
        </details>

        <div className="flex justify-end items-center gap-3">
          {!form.ticker && (
            <span className="text-muted text-xs">Type a ticker to enable</span>
          )}
          {form.days.length === 0 && (
            <span className="text-warning text-xs">Pick at least one day</span>
          )}
          {create.isError && (
            <span className="text-danger text-sm">
              {(create.error as Error).message}
            </span>
          )}
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!form.ticker || !effectiveCron || create.isPending}
          >
            {create.isPending ? "Saving…" : "+ Add schedule"}
          </button>
        </div>
      </form>

      {/* ─── Active schedules ─── */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Active ({enabled.length})</h2>
        {q.isLoading ? (
          <div className="text-muted text-sm">Loading…</div>
        ) : enabled.length === 0 ? (
          <div className="card text-sm text-muted">
            No active schedules. Add one above.
          </div>
        ) : (
          <ScheduleTable
            rows={enabled}
            onToggle={(s) => toggle.mutate(s)}
            onFire={(id) => fire.mutate(id)}
            onDelete={(id) => {
              if (confirm("Delete this schedule? (Existing queue items aren't affected.)"))
                remove.mutate(id);
            }}
            busy={toggle.isPending || fire.isPending || remove.isPending}
          />
        )}
      </section>

      {disabled.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 text-muted">
            Paused ({disabled.length})
          </h2>
          <ScheduleTable
            rows={disabled}
            onToggle={(s) => toggle.mutate(s)}
            onFire={(id) => fire.mutate(id)}
            onDelete={(id) => {
              if (confirm("Delete this schedule?")) remove.mutate(id);
            }}
            busy={toggle.isPending || fire.isPending || remove.isPending}
          />
        </section>
      )}

      <div className="card text-xs text-muted">
        <strong>How it works.</strong> When a schedule fires, the backend POSTs
        to <code>/run-queue</code> with the ticker + options from this row.
        Memory mode (and per-day overrides) carry through; the worker checks
        <code> options.analysis_mode</code> and bypasses the memory log when
        fresh. See <Link href="/queue" className="text-accent hover:underline">/queue</Link>{" "}
        for live items.
      </div>
    </div>
  );
}

function ModelDropdown({
  label, provider, value, onChange,
}: {
  label: string;
  provider: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const catalog = MODEL_CATALOG[provider] ?? [];
  const inCatalog = catalog.find((m) => m.value === value);
  const showCustom = !inCatalog && provider !== "ollama";
  return (
    <div>
      <label className="label">{label}</label>
      {catalog.length === 0 ? (
        <input
          className="input w-full font-mono text-xs"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={provider === "ollama" ? "ollama-model-name" : "model-name"}
        />
      ) : (
        <select
          className="input w-full text-xs"
          value={showCustom ? OTHER : value}
          onChange={(e) => {
            const v = e.target.value;
            if (v === OTHER) onChange(value || "");
            else onChange(v);
          }}
        >
          {catalog.map((m) => (
            <option key={m.value} value={m.value}>{m.label}</option>
          ))}
          <option value={OTHER}>Other (custom)…</option>
        </select>
      )}
      {showCustom && (
        <input
          className="input w-full mt-1 text-xs font-mono"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Type any model name your provider supports"
        />
      )}
    </div>
  );
}

function ScheduleTable({
  rows, onToggle, onFire, onDelete, busy,
}: {
  rows: Schedule[];
  onToggle: (s: Schedule) => void;
  onFire: (id: number) => void;
  onDelete: (id: number) => void;
  busy: boolean;
}) {
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wider text-muted">
          <tr>
            <th className="py-2">Ticker</th>
            <th>Cadence</th>
            <th>Memory mode</th>
            <th>Last fired</th>
            <th>Next fire</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => {
            const overrides = (s.options?.analysis_mode_overrides ?? {}) as Record<string, string>;
            const overrideDays = Object.keys(overrides);
            return (
              <tr key={s.id} className="border-t border-border align-top">
                <td className="py-2 font-semibold">
                  {s.ticker}
                  {s.notes && <div className="text-muted text-xs">{s.notes}</div>}
                </td>
                <td className="text-xs">
                  {s.cadence_human || s.cron_expression}
                  <div className="text-muted text-[10px] font-mono">{s.cron_expression}</div>
                </td>
                <td className="text-xs">
                  <span className="font-semibold">{s.options?.analysis_mode ?? "incremental"}</span>
                  {overrideDays.length > 0 && (
                    <div className="text-warning text-[10px]">
                      override: {overrideDays.join(",")} → {Object.values(overrides)[0]}
                    </div>
                  )}
                </td>
                <td className="text-xs text-muted">
                  {fmtTs(s.last_fired_at)}
                  {s.last_error && (
                    <div className="text-danger mt-0.5 max-w-xs whitespace-normal">
                      ⚠ {s.last_error.slice(0, 120)}
                    </div>
                  )}
                  {s.last_queue_id && !s.last_error && (
                    <Link
                      href="/queue"
                      className="text-accent hover:underline text-[10px]"
                    >
                      queue: {s.last_queue_id.slice(0, 8)}…
                    </Link>
                  )}
                </td>
                <td className="text-xs text-muted">{fmtTs(s.next_fire_at)}</td>
                <td className="text-right whitespace-nowrap">
                  <button className="btn text-xs" onClick={() => onFire(s.id)} disabled={busy}>
                    ▶ Fire
                  </button>{" "}
                  <button className="btn text-xs" onClick={() => onToggle(s)} disabled={busy}>
                    {s.enabled ? "Pause" : "Resume"}
                  </button>{" "}
                  <button className="btn text-xs" onClick={() => onDelete(s.id)} disabled={busy}>
                    Delete
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
