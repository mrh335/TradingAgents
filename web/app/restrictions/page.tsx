"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Restrictions, type Restriction } from "@/lib/api";

const KIND_LABELS: Record<Restriction["kind"], string> = {
  blackout: "Pre-earnings / 10b5-1 blackout",
  restricted_list: "Employer restricted list",
  regulatory: "Regulatory hold",
  other: "Other",
};

const KIND_COLORS: Record<Restriction["kind"], string> = {
  blackout: "text-warning",
  restricted_list: "text-danger",
  regulatory: "text-accent",
  other: "text-muted",
};

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function fmtActive(r: Restriction): string {
  const today = todayIso();
  const past_start = r.start_date <= today;
  const past_end = r.end_date ? r.end_date < today : false;
  if (!past_start) return "🕒 upcoming";
  if (past_end) return "⏎ expired";
  return r.end_date ? "🚫 active" : "♾ open-ended";
}

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

  // ─── New restriction form ─────────────────────────────────────────
  const [form, setForm] = useState({
    ticker: "",
    start_date: todayIso(),
    end_date: "",
    kind: "blackout" as Restriction["kind"],
    reason: "",
  });

  const create = useMutation({
    mutationFn: () =>
      Restrictions.create({
        ticker: form.ticker.toUpperCase(),
        start_date: form.start_date,
        end_date: form.end_date || undefined,
        kind: form.kind,
        reason: form.reason || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["restrictions"] });
      setForm({
        ticker: "",
        start_date: todayIso(),
        end_date: "",
        kind: "blackout",
        reason: "",
      });
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
          Per-ticker blackout windows the framework injects into the trader +
          portfolio-manager agent prompts as a <strong>hard constraint</strong>.
          When a restriction is active for a ticker, the agents default to{" "}
          <code>Hold</code> regardless of bullish or bearish signal from the
          analysts. Use for employer restricted lists, 10b5-1 earnings
          blackouts, regulatory holds, or anything else where you can&apos;t
          legally trade for a defined window.
        </p>
      </header>

      {/* ─── New restriction ─── */}
      <form
        className="card grid grid-cols-2 md:grid-cols-5 gap-3 items-end"
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
            onChange={(e) =>
              setForm({ ...form, ticker: e.target.value.toUpperCase() })
            }
            placeholder="AAPL"
            required
          />
        </div>
        <div>
          <label className="label">Start date</label>
          <input
            type="date"
            className="input w-full"
            value={form.start_date}
            onChange={(e) => setForm({ ...form, start_date: e.target.value })}
            required
          />
        </div>
        <div>
          <label className="label">End date <span className="text-muted">(optional)</span></label>
          <input
            type="date"
            className="input w-full"
            value={form.end_date}
            onChange={(e) => setForm({ ...form, end_date: e.target.value })}
            placeholder="(open-ended)"
          />
        </div>
        <div>
          <label className="label">Kind</label>
          <select
            className="input w-full"
            value={form.kind}
            onChange={(e) =>
              setForm({ ...form, kind: e.target.value as Restriction["kind"] })
            }
          >
            {Object.entries(KIND_LABELS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="col-span-2 md:col-span-5">
          <label className="label">Reason <span className="text-muted">(shown to the agent)</span></label>
          <input
            className="input w-full"
            value={form.reason}
            onChange={(e) => setForm({ ...form, reason: e.target.value })}
            placeholder="e.g. employee restricted list — AAPL holdings via former employer, indefinite"
            maxLength={500}
          />
        </div>
        <div className="col-span-2 md:col-span-5 flex justify-end items-center gap-3">
          {create.isError && (
            <span className="text-danger text-sm">
              {(create.error as Error).message}
            </span>
          )}
          <button
            type="submit"
            className="btn btn-primary"
            disabled={create.isPending || !form.ticker || !form.start_date}
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
          Active today only
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
          No restrictions match the current filter. Add one above to register
          a blackout window — common cases:{" "}
          <code>AAPL</code> indefinite (former-employer holdings),{" "}
          <code>LCID</code> indefinite (any other employer restriction),
          or a date range around your company&apos;s earnings.
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
                  <td>
                    <div>{r.start_date}</div>
                    <div className="text-muted text-xs">
                      → {r.end_date ?? "open-ended"}
                    </div>
                  </td>
                  <td className={`text-xs ${KIND_COLORS[r.kind]}`}>
                    {KIND_LABELS[r.kind]}
                  </td>
                  <td className="text-xs max-w-md whitespace-normal">
                    {r.reason || <span className="text-muted">—</span>}
                  </td>
                  <td className="text-xs">{fmtActive(r)}</td>
                  <td className="text-right">
                    <button
                      className="btn text-xs"
                      onClick={() => {
                        if (
                          confirm(
                            `Delete the ${r.ticker} restriction (${r.start_date}${r.end_date ? ` → ${r.end_date}` : ""})?`,
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
        <strong>How this affects the agents:</strong> The trader and portfolio
        manager nodes call <code>get_trading_restrictions(ticker, trade_date)</code>{" "}
        on every run. If any restriction is active on that date, the prompt
        prepends a hard constraint that forces the recommendation to{" "}
        <code>Hold</code> regardless of the analysts&apos; signal. The brief
        will explicitly state that the recommendation is muted by the
        restriction and reference the end date.
      </div>
    </div>
  );
}
