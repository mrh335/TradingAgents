"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Schedules, type Schedule } from "@/lib/api";

// Preset cadences. Map a friendly label → standard 5-field cron expression
// evaluated in the API container's local timezone (typically UTC unless
// you've set TZ in docker-compose).
const CADENCE_PRESETS: { label: string; cron: string; description: string }[] = [
  { label: "Every weekday morning",       cron: "0 6 * * 1-5",   description: "Mon-Fri 6:00 AM" },
  { label: "Every weekday after market",  cron: "0 17 * * 1-5",  description: "Mon-Fri 5:00 PM (after US close)" },
  { label: "Every weekday at noon",       cron: "0 12 * * 1-5",  description: "Mon-Fri 12:00 PM" },
  { label: "Mon/Wed/Fri morning",         cron: "0 7 * * 1,3,5", description: "Three times a week at 7 AM" },
  { label: "Every other day at 7 AM",     cron: "0 7 */2 * *",   description: "Every 2 days" },
  { label: "Every Sunday evening",        cron: "0 19 * * 0",    description: "Weekly Sun 7 PM" },
  { label: "Every day at 6 AM",           cron: "0 6 * * *",     description: "Daily 6:00 AM (incl. weekends)" },
  { label: "Custom",                      cron: "",              description: "Type your own 5-field cron expression" },
];

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

  // ─── New schedule form state ──────────────────────────────────────
  const [form, setForm] = useState({
    ticker: "",
    preset: CADENCE_PRESETS[0].label,
    customCron: "",
    provider: "anthropic",
    deep_model: "claude-sonnet-4-6",
    quick_model: "claude-haiku-4-5",
    debate_rounds: 1,
    risk_rounds: 1,
    notes: "",
  });

  const presetCron =
    CADENCE_PRESETS.find((p) => p.label === form.preset)?.cron ?? "";
  const effectiveCron = form.preset === "Custom" ? form.customCron : presetCron;

  const create = useMutation({
    mutationFn: () =>
      Schedules.create({
        ticker: form.ticker.toUpperCase(),
        cron_expression: effectiveCron,
        mode: "analyze",
        options: {
          provider: form.provider,
          deep_model: form.deep_model,
          quick_model: form.quick_model,
          debate_rounds: form.debate_rounds,
          risk_rounds: form.risk_rounds,
          data_vendors: {
            core_stock_apis: "yfinance",
            technical_indicators: "yfinance",
            fundamental_data: "yfinance",
            news_data: "yfinance",
          },
        },
        enabled: true,
        notes: form.notes || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schedules"] });
      setForm({ ...form, ticker: "", notes: "", customCron: "" });
    },
  });

  const toggle = useMutation({
    mutationFn: (s: Schedule) =>
      Schedules.update(s.id, { enabled: !s.enabled }),
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
          Per-ticker analysis schedules. The background scheduler in the API
          container ticks every minute, evaluates each enabled row's cron
          expression, and posts a queue item when due. Your existing
          queue-drain cron then picks it up — so the loop closes itself.
        </p>
        <p className="text-muted text-xs mt-1">
          Cron expressions evaluated in the API container's local timezone
          (typically UTC unless you've set <code>TZ</code> in docker-compose).
        </p>
      </header>

      {/* ─── New schedule ─── */}
      <form
        className="card grid grid-cols-1 md:grid-cols-3 gap-3 items-end"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
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
        <div>
          <label className="label">Cadence</label>
          <select
            className="input w-full"
            value={form.preset}
            onChange={(e) => setForm({ ...form, preset: e.target.value })}
          >
            {CADENCE_PRESETS.map((p) => (
              <option key={p.label} value={p.label}>
                {p.label}{p.cron && p.label !== "Custom" ? ` — ${p.description}` : ""}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Effective cron</label>
          {form.preset === "Custom" ? (
            <input
              className="input w-full font-mono text-xs"
              value={form.customCron}
              onChange={(e) => setForm({ ...form, customCron: e.target.value })}
              placeholder="0 6 * * 1-5"
              required
            />
          ) : (
            <code className="block input bg-bg text-xs">{presetCron}</code>
          )}
        </div>

        {/* Model + depth */}
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
        <div>
          <label className="label">Deep-think model</label>
          <input
            className="input w-full font-mono text-xs"
            value={form.deep_model}
            onChange={(e) => setForm({ ...form, deep_model: e.target.value })}
            required
          />
        </div>
        <div>
          <label className="label">Quick-think model</label>
          <input
            className="input w-full font-mono text-xs"
            value={form.quick_model}
            onChange={(e) => setForm({ ...form, quick_model: e.target.value })}
            required
          />
        </div>

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
        <div>
          <label className="label">Notes <span className="text-muted">(optional)</span></label>
          <input
            className="input w-full"
            value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
            placeholder="e.g. weekday morning refresh"
            maxLength={200}
          />
        </div>

        <div className="md:col-span-3 flex justify-end items-center gap-3">
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
        <h2 className="text-lg font-semibold mb-3">
          Active ({enabled.length})
        </h2>
        {q.isLoading ? (
          <div className="text-muted text-sm">Loading…</div>
        ) : enabled.length === 0 ? (
          <div className="card text-sm text-muted">
            No active schedules. Add one above. Each scheduled run drops a
            queue item that your existing drain cron picks up.
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

      {/* ─── Paused / disabled ─── */}
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
              if (confirm("Delete this schedule?"))
                remove.mutate(id);
            }}
            busy={toggle.isPending || fire.isPending || remove.isPending}
          />
        </section>
      )}

      <div className="card text-xs text-muted">
        <strong>How it works.</strong> When a schedule's cron fires, the
        backend POSTs to <code>/run-queue</code> with the ticker + options
        from this row. The queue item gets <code>requested_by: scheduler:&lt;id&gt;</code>{" "}
        so you can see which schedule created which run on the{" "}
        <Link href="/queue" className="text-accent hover:underline">/queue</Link>{" "}
        page. The worker (typically your Claude Desktop scheduled-tasks
        drain) then claims and processes it.
      </div>
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
            <th>Cron</th>
            <th>Model</th>
            <th>Last fired</th>
            <th>Next fire</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id} className="border-t border-border align-top">
              <td className="py-2 font-semibold">
                {s.ticker}
                {s.notes && (
                  <div className="text-muted text-xs">{s.notes}</div>
                )}
              </td>
              <td className="text-xs">
                {s.cadence_human || s.cron_expression}
              </td>
              <td className="text-xs font-mono text-muted">{s.cron_expression}</td>
              <td className="text-xs text-muted">
                {s.options?.provider ?? "—"}
                <div>{String(s.options?.deep_model ?? "—")}</div>
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
                <button
                  className="btn text-xs"
                  onClick={() => onFire(s.id)}
                  disabled={busy}
                  title="Fire this schedule right now (skips the cron check)"
                >
                  ▶ Fire now
                </button>{" "}
                <button
                  className="btn text-xs"
                  onClick={() => onToggle(s)}
                  disabled={busy}
                >
                  {s.enabled ? "Pause" : "Resume"}
                </button>{" "}
                <button
                  className="btn text-xs"
                  onClick={() => onDelete(s.id)}
                  disabled={busy}
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
