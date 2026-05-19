"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Restrictions, type Restriction, type RestrictionKind } from "@/lib/api";

// Restrictions are a DISPLAY OVERLAY only — they don't bias the agent
// recommendation. The market view (Buy/Hold/Sell etc.) reflects the
// analysts' read; the restriction tells you whether you can act on it.

const KIND_LABELS: Record<RestrictionKind, string> = {
  earnings_window: "Earnings open window (pattern)",
  earnings_blackout: "Earnings blackout (closed around earnings)",
  blackout: "Fixed-date blackout",
  restricted_list: "Employer restricted list",
  regulatory: "Regulatory hold",
  other: "Other",
};

const KIND_COLORS: Record<RestrictionKind, string> = {
  earnings_window: "text-accent",
  earnings_blackout: "text-warning",
  blackout: "text-warning",
  restricted_list: "text-danger",
  regulatory: "text-accent",
  other: "text-muted",
};

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function fmtStatus(r: Restriction): string {
  // For earnings_window, surface the open/closed state explicitly.
  if (r.kind === "earnings_window") {
    if (r.currently_open === true) return "✅ OPEN now";
    if (r.currently_open === false) return "🚫 CLOSED now";
    return "❓ no earnings date";
  }
  const today = todayIso();
  const past_start = r.start_date && r.start_date <= today;
  const past_end = r.end_date ? r.end_date < today : false;
  if (!past_start) return "🕒 upcoming";
  if (past_end) return "⏎ expired";
  return r.end_date ? "🚫 active" : "♾ open-ended";
}

function fmtWindow(r: Restriction): React.ReactNode {
  if (r.kind === "earnings_window") {
    const offset = r.earnings_window_open_offset_days ?? 0;
    const dur = r.earnings_window_duration_days ?? 0;
    return (
      <div>
        <div className="text-xs">
          Open <strong>{offset}d</strong> after earnings · for{" "}
          <strong>{dur}d</strong>
        </div>
        {r.resolved_start && r.resolved_end && (
          <div className="text-xs text-muted">
            Next window: {r.resolved_start} → {r.resolved_end}
          </div>
        )}
        {r.resolved_earnings_date && (
          <div className="text-xs text-muted">
            Anchored to earnings: {r.resolved_earnings_date}
          </div>
        )}
      </div>
    );
  }
  if (r.kind === "earnings_blackout") {
    return (
      <div>
        <div className="text-xs">
          Closed <strong>{r.earnings_days_before ?? 0}d</strong> before to{" "}
          <strong>{r.earnings_days_after ?? 0}d</strong> after earnings
        </div>
        {r.resolved_start && r.resolved_end && (
          <div className="text-xs text-muted">
            Currently: {r.resolved_start} → {r.resolved_end}
          </div>
        )}
      </div>
    );
  }
  return (
    <div>
      <div>{r.start_date || "—"}</div>
      <div className="text-muted text-xs">
        → {r.end_date ?? "open-ended"}
      </div>
    </div>
  );
}

type FormState = {
  ticker: string;
  kind: RestrictionKind;
  // Fixed-window fields
  start_date: string;
  end_date: string;
  // Earnings blackout
  earnings_days_before: number;
  earnings_days_after: number;
  // Earnings window (open pattern)
  earnings_window_open_offset_days: number;
  earnings_window_duration_days: number;
  reason: string;
};

const DEFAULT_FORM: FormState = {
  ticker: "",
  kind: "earnings_window",
  start_date: todayIso(),
  end_date: "",
  earnings_days_before: 14,
  earnings_days_after: 2,
  earnings_window_open_offset_days: 2,
  earnings_window_duration_days: 21,
  reason: "",
};

export default function RestrictionsPage() {
  const qc = useQueryClient();
  const [filterTicker, setFilterTicker] = useState<string>("");
  const [showOnlyActive, setShowOnlyActive] = useState(false);

  const q = useQuery({
    queryKey: ["restrictions", filterTicker, showOnlyActive],
    queryFn: () =>
      Restrictions.list({
        ticker: filterTicker || undefined,
        active_on: showOnlyActive ? todayIso() : undefined,
      }),
  });

  const [form, setForm] = useState<FormState>(DEFAULT_FORM);

  const create = useMutation({
    mutationFn: () => {
      const base: Parameters<typeof Restrictions.create>[0] = {
        ticker: form.ticker.toUpperCase(),
        kind: form.kind,
        reason: form.reason || undefined,
      };
      if (form.kind === "earnings_window") {
        base.earnings_window_open_offset_days = form.earnings_window_open_offset_days;
        base.earnings_window_duration_days = form.earnings_window_duration_days;
      } else if (form.kind === "earnings_blackout") {
        base.earnings_days_before = form.earnings_days_before;
        base.earnings_days_after = form.earnings_days_after;
      } else {
        base.start_date = form.start_date;
        base.end_date = form.end_date || undefined;
      }
      return Restrictions.create(base);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["restrictions"] });
      setForm(DEFAULT_FORM);
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => Restrictions.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["restrictions"] }),
  });

  const items = q.data ?? [];

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Trading restrictions</h1>
        <p className="text-muted text-sm">
          Per-ticker rules describing when <strong>you</strong> can or can&apos;t
          act on a recommendation. These are <strong>display-only</strong> — the
          framework&apos;s market analysis (Buy / Hold / Sell etc.) is computed
          independent of restrictions, so you always see what the market is
          saying. The restriction tells you whether you can act on it right now.
        </p>
        <p className="text-muted text-xs mt-1">
          Most common pattern: an{" "}
          <strong>earnings open window</strong> — trading is allowed starting N
          days after each earnings call and stays open for M days, then closes
          until the next earnings cycle.
        </p>
      </header>

      {/* ─── New restriction ─── */}
      <form
        className="card space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <div>
            <label className="label">Ticker</label>
            <input
              className="input w-full"
              value={form.ticker}
              onChange={(e) =>
                setForm({ ...form, ticker: e.target.value.toUpperCase() })
              }
              placeholder="AAPL"
              required
            />
          </div>
          <div className="col-span-2">
            <label className="label">Kind</label>
            <select
              className="input w-full"
              value={form.kind}
              onChange={(e) =>
                setForm({ ...form, kind: e.target.value as RestrictionKind })
              }
            >
              {Object.entries(KIND_LABELS).map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Kind-specific fields */}
        {form.kind === "earnings_window" && (
          <div className="grid grid-cols-2 gap-3 bg-surface p-3 rounded">
            <div>
              <label className="label">Window opens (days after earnings)</label>
              <input
                type="number"
                className="input w-full"
                value={form.earnings_window_open_offset_days}
                onChange={(e) =>
                  setForm({
                    ...form,
                    earnings_window_open_offset_days: Number(e.target.value),
                  })
                }
                min={0}
                max={30}
              />
              <p className="text-xs text-muted mt-1">
                Typical: 1-3 (cooldown after the print)
              </p>
            </div>
            <div>
              <label className="label">Stays open for (days)</label>
              <input
                type="number"
                className="input w-full"
                value={form.earnings_window_duration_days}
                onChange={(e) =>
                  setForm({
                    ...form,
                    earnings_window_duration_days: Number(e.target.value),
                  })
                }
                min={1}
                max={120}
              />
              <p className="text-xs text-muted mt-1">
                Typical: 14-28 (2-4 weeks)
              </p>
            </div>
            <p className="col-span-2 text-xs text-muted">
              Closed at all other times. Pattern repeats each earnings cycle —
              the system auto-recomputes the open dates from yfinance.
            </p>
          </div>
        )}

        {form.kind === "earnings_blackout" && (
          <div className="grid grid-cols-2 gap-3 bg-surface p-3 rounded">
            <div>
              <label className="label">Closed N days BEFORE earnings</label>
              <input
                type="number"
                className="input w-full"
                value={form.earnings_days_before}
                onChange={(e) =>
                  setForm({
                    ...form,
                    earnings_days_before: Number(e.target.value),
                  })
                }
                min={0}
                max={120}
              />
              <p className="text-xs text-muted mt-1">
                Typical: 14 (two-week pre-earnings quiet period)
              </p>
            </div>
            <div>
              <label className="label">Closed N days AFTER earnings</label>
              <input
                type="number"
                className="input w-full"
                value={form.earnings_days_after}
                onChange={(e) =>
                  setForm({
                    ...form,
                    earnings_days_after: Number(e.target.value),
                  })
                }
                min={0}
                max={30}
              />
              <p className="text-xs text-muted mt-1">
                Typical: 1-2 days for volatility to settle
              </p>
            </div>
            <p className="col-span-2 text-xs text-muted">
              Open at all other times. Inverse of earnings_window — pick this
              if you think in &quot;closed days&quot; instead of &quot;open
              window&quot;.
            </p>
          </div>
        )}

        {(form.kind === "blackout" ||
          form.kind === "restricted_list" ||
          form.kind === "regulatory" ||
          form.kind === "other") && (
          <div className="grid grid-cols-2 gap-3 bg-surface p-3 rounded">
            <div>
              <label className="label">Start date</label>
              <input
                type="date"
                className="input w-full"
                value={form.start_date}
                onChange={(e) =>
                  setForm({ ...form, start_date: e.target.value })
                }
                required
              />
            </div>
            <div>
              <label className="label">
                End date <span className="text-muted">(optional)</span>
              </label>
              <input
                type="date"
                className="input w-full"
                value={form.end_date}
                onChange={(e) => setForm({ ...form, end_date: e.target.value })}
                placeholder="(open-ended)"
              />
            </div>
            <p className="col-span-2 text-xs text-muted">
              Use for employer restricted lists (open-ended) or regulatory
              holds with a known release date.
            </p>
          </div>
        )}

        <div>
          <label className="label">
            Reason / notes <span className="text-muted">(optional, for your records)</span>
          </label>
          <input
            className="input w-full"
            value={form.reason}
            onChange={(e) => setForm({ ...form, reason: e.target.value })}
            placeholder="e.g. employee restricted list, or 'company policy: trade only Q+2 weeks'"
            maxLength={500}
          />
        </div>

        <div className="flex justify-end items-center gap-3">
          {create.isError && (
            <span className="text-danger text-sm">
              {(create.error as Error).message}
            </span>
          )}
          <button
            type="submit"
            className="btn btn-primary"
            disabled={create.isPending || !form.ticker}
          >
            {create.isPending ? "Saving…" : "+ Add restriction"}
          </button>
        </div>
      </form>

      {/* ─── Filter chips ─── */}
      <div className="card flex flex-wrap gap-3 items-center">
        <label className="label mb-0">Filter:</label>
        <input
          className="input"
          placeholder="ticker (e.g. AAPL)"
          value={filterTicker}
          onChange={(e) => setFilterTicker(e.target.value.toUpperCase())}
        />
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={showOnlyActive}
            onChange={(e) => setShowOnlyActive(e.target.checked)}
          />
          Restricted today only
        </label>
        <span className="text-xs text-muted ml-auto">
          {items.length} restriction{items.length === 1 ? "" : "s"}
        </span>
      </div>

      {/* ─── List ─── */}
      {q.isLoading ? (
        <div className="text-muted">Loading…</div>
      ) : items.length === 0 ? (
        <div className="card text-sm text-muted">
          No restrictions match the current filter. Add one above. The most
          common setup for a self-managed portfolio: <strong>earnings open
          window</strong> on each held ticker — &quot;open 2 days after
          earnings for 3 weeks&quot;.
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-muted">
              <tr>
                <th className="py-2">Ticker</th>
                <th>Window</th>
                <th>Kind</th>
                <th>Reason</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id} className="border-t border-border align-top">
                  <td className="py-2 font-semibold">{r.ticker}</td>
                  <td>{fmtWindow(r)}</td>
                  <td className={`text-xs ${KIND_COLORS[r.kind]}`}>
                    {KIND_LABELS[r.kind]}
                  </td>
                  <td className="text-xs max-w-md whitespace-normal">
                    {r.reason || <span className="text-muted">—</span>}
                  </td>
                  <td className="text-xs">{fmtStatus(r)}</td>
                  <td className="text-right">
                    <button
                      className="btn text-xs"
                      onClick={() => {
                        if (
                          confirm(
                            `Delete the ${r.ticker} ${KIND_LABELS[r.kind]} restriction?`,
                          )
                        )
                          remove.mutate(r.id);
                      }}
                      disabled={remove.isPending}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card text-xs text-muted">
        <strong>How this surfaces:</strong> the dashboard, portfolio, and
        recommendation pages all check active restrictions and overlay a
        &quot;trade status: CLOSED until {"{date}"}&quot; badge alongside the
        analyst recommendation. The recommendation itself is{" "}
        <strong>unchanged</strong> by restrictions — restrictions never bias
        Buy → Hold.
      </div>
    </div>
  );
}
