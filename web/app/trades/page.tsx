"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trades, type TradeAction, type TradeEntry } from "@/lib/api";

const ACTIONS: { value: TradeAction; label: string }[] = [
  { value: "buy", label: "Buy" },
  { value: "sell", label: "Sell" },
  { value: "dividend", label: "Dividend received" },
  { value: "split", label: "Stock split" },
  { value: "transfer", label: "Transfer in/out" },
  { value: "short", label: "Short sale" },
  { value: "cover", label: "Short cover" },
];

const ACTION_COLOR: Record<TradeAction, string> = {
  buy: "text-success",
  sell: "text-danger",
  dividend: "text-accent",
  split: "text-muted",
  transfer: "text-muted",
  short: "text-warning",
  cover: "text-warning",
};

function fmtUsd(n: number | null): string {
  if (n === null || n === undefined) return "—";
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (Math.abs(n) >= 1_000) return `$${(n / 1_000).toFixed(2)}K`;
  return `$${n.toFixed(2)}`;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function TradesPage() {
  const qc = useQueryClient();
  const [filterTicker, setFilterTicker] = useState("");
  const trades = useQuery({
    queryKey: ["trades", filterTicker],
    queryFn: () => Trades.list(filterTicker || undefined),
  });
  const summary = useQuery({
    queryKey: ["trades-summary"],
    queryFn: () => Trades.summary(),
  });

  // ─── New trade form ──────────────────────────────────────────────
  const [form, setForm] = useState({
    ticker: "",
    action: "buy" as TradeAction,
    shares: 0,
    price: 0,
    executed_at: todayIso(),
    account: "",
    notes: "",
    fees: 0,
  });

  const create = useMutation({
    mutationFn: () =>
      Trades.create({
        ticker: form.ticker.toUpperCase(),
        action: form.action,
        shares: form.shares,
        price: form.price || undefined,
        executed_at: form.executed_at,
        account: form.account || undefined,
        notes: form.notes || undefined,
        fees: form.fees || 0,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trades"] });
      qc.invalidateQueries({ queryKey: ["trades-summary"] });
      setForm({ ...form, ticker: "", shares: 0, price: 0, notes: "", fees: 0 });
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => Trades.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trades"] });
      qc.invalidateQueries({ queryKey: ["trades-summary"] });
    },
  });

  const rows = trades.data ?? [];
  const summaryRows = summary.data?.by_ticker ?? [];
  const summaryTotals = summary.data?.totals ?? {};

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Trade journal</h1>
        <p className="text-muted text-sm">
          Log actual executed trades (buy / sell / dividend / split / transfer).
          Separate from <Link href="/portfolio" className="text-accent hover:underline">/portfolio</Link>{" "}
          which is a snapshot of current holdings — this is the chronological
          history that produced those holdings.
        </p>
        <p className="text-muted text-xs mt-1">
          Linking a trade to a <code>run_id</code> lets you later answer
          "did the trades I made on the framework's recommendations actually
          work" — feeds future actual-vs-notional backtest comparisons.
        </p>
      </header>

      {/* ─── Summary ─── */}
      {summary.data && (
        <div className="card grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
          <Summary label="Total trades" value={String(summaryTotals.trade_count ?? 0)} />
          <Summary label="Capital in" value={fmtUsd(summaryTotals.total_capital_in)} sub="buy + cover" />
          <Summary label="Capital out" value={fmtUsd(summaryTotals.total_capital_out)} sub="sell + short" />
          <Summary
            label="Realized P&L"
            value={fmtUsd(summaryTotals.total_realized_pnl)}
            tone={
              (summaryTotals.total_realized_pnl ?? 0) > 0
                ? "text-success"
                : (summaryTotals.total_realized_pnl ?? 0) < 0
                  ? "text-danger"
                  : "text-muted"
            }
            sub={`+ ${fmtUsd(summaryTotals.total_dividends)} divs`}
          />
        </div>
      )}

      {/* ─── New trade form ─── */}
      <form
        className="card grid grid-cols-2 md:grid-cols-4 gap-3 items-end"
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
          <label className="label">Action</label>
          <select
            className="input w-full"
            value={form.action}
            onChange={(e) => setForm({ ...form, action: e.target.value as TradeAction })}
          >
            {ACTIONS.map((a) => (
              <option key={a.value} value={a.value}>{a.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Shares</label>
          <input
            type="number"
            step="any"
            min={0}
            className="input w-full"
            value={form.shares || ""}
            onChange={(e) => setForm({ ...form, shares: Number(e.target.value) })}
            required
          />
        </div>
        <div>
          <label className="label">Price/share</label>
          <input
            type="number"
            step="any"
            min={0}
            className="input w-full"
            value={form.price || ""}
            onChange={(e) => setForm({ ...form, price: Number(e.target.value) })}
            placeholder="(optional for split)"
          />
        </div>
        <div>
          <label className="label">Trade date</label>
          <input
            type="date"
            className="input w-full"
            value={form.executed_at}
            onChange={(e) => setForm({ ...form, executed_at: e.target.value })}
            required
          />
        </div>
        <div>
          <label className="label">Account</label>
          <input
            className="input w-full"
            value={form.account}
            onChange={(e) => setForm({ ...form, account: e.target.value })}
            placeholder="e.g. Joint JTWROS"
          />
        </div>
        <div>
          <label className="label">Fees</label>
          <input
            type="number"
            step="any"
            min={0}
            className="input w-full"
            value={form.fees || ""}
            onChange={(e) => setForm({ ...form, fees: Number(e.target.value) })}
            placeholder="0"
          />
        </div>
        <div className="md:col-span-4">
          <label className="label">Notes <span className="text-muted">(optional)</span></label>
          <input
            className="input w-full"
            value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
            placeholder="e.g. trimmed half on technical breakdown"
            maxLength={500}
          />
        </div>
        <div className="md:col-span-4 flex justify-end items-center gap-3">
          {create.isError && (
            <span className="text-danger text-sm">
              {(create.error as Error).message}
            </span>
          )}
          {create.isSuccess && create.data && (
            <span className="text-success text-sm">
              ✓ Logged {create.data.action} {create.data.shares} {create.data.ticker}
            </span>
          )}
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!form.ticker || !form.shares || create.isPending}
          >
            {create.isPending ? "Saving…" : "+ Log trade"}
          </button>
        </div>
      </form>

      {/* ─── Per-ticker realized P&L ─── */}
      {summaryRows.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3">Realized P&L by ticker</h2>
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-muted">
                <tr>
                  <th className="py-2">Ticker</th>
                  <th className="text-right">Trades</th>
                  <th className="text-right">Capital in</th>
                  <th className="text-right">Capital out</th>
                  <th className="text-right">Dividends</th>
                  <th className="text-right">Realized P&L</th>
                </tr>
              </thead>
              <tbody>
                {summaryRows.map((r) => (
                  <tr key={r.ticker} className="border-t border-border">
                    <td className="py-2 font-semibold">{r.ticker}</td>
                    <td className="text-right tabular-nums">{r.trade_count}</td>
                    <td className="text-right tabular-nums">{fmtUsd(r.capital_in)}</td>
                    <td className="text-right tabular-nums">{fmtUsd(r.capital_out)}</td>
                    <td className="text-right tabular-nums text-accent">{fmtUsd(r.dividends)}</td>
                    <td className={`text-right tabular-nums font-semibold ${r.net_pnl_realized > 0 ? "text-success" : r.net_pnl_realized < 0 ? "text-danger" : "text-muted"}`}>
                      {fmtUsd(r.net_pnl_realized)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ─── Trade list ─── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">All trades ({rows.length})</h2>
          <input
            className="input text-sm"
            value={filterTicker}
            onChange={(e) => setFilterTicker(e.target.value.toUpperCase())}
            placeholder="Filter by ticker"
          />
        </div>
        {trades.isLoading ? (
          <div className="text-muted text-sm">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="card text-sm text-muted">
            No trades logged yet. Add one above. You can also link trades
            back to a run by including its <code>run_id</code> in notes.
          </div>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-muted">
                <tr>
                  <th className="py-2">Date</th>
                  <th>Ticker</th>
                  <th>Action</th>
                  <th className="text-right">Shares</th>
                  <th className="text-right">Price</th>
                  <th className="text-right">Total</th>
                  <th>Account</th>
                  <th>Notes</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => {
                  const total = t.shares * (t.price ?? 0);
                  return (
                    <tr key={t.id} className="border-t border-border align-top">
                      <td className="py-2 text-xs">{t.executed_at}</td>
                      <td className="font-semibold">{t.ticker}</td>
                      <td>
                        <span className={`text-xs font-semibold ${ACTION_COLOR[t.action]}`}>
                          {t.action.toUpperCase()}
                        </span>
                      </td>
                      <td className="text-right tabular-nums">{t.shares.toLocaleString()}</td>
                      <td className="text-right tabular-nums">
                        {t.price !== null ? `$${t.price.toFixed(2)}` : "—"}
                      </td>
                      <td className="text-right tabular-nums">
                        {t.price !== null ? fmtUsd(total) : "—"}
                        {(t.fees || 0) > 0 && (
                          <div className="text-[10px] text-muted">+ ${t.fees.toFixed(2)} fees</div>
                        )}
                      </td>
                      <td className="text-xs text-muted">{t.account ?? "—"}</td>
                      <td className="text-xs max-w-xs whitespace-normal">
                        {t.notes ?? <span className="text-muted">—</span>}
                        {t.linked_run_id && (
                          <Link
                            href={`/history/${t.linked_run_id}`}
                            className="text-accent hover:underline block text-[10px]"
                          >
                            run: {t.linked_run_id.slice(0, 8)}…
                          </Link>
                        )}
                      </td>
                      <td className="text-right">
                        <button
                          className="btn text-xs"
                          onClick={() => {
                            if (confirm(`Delete trade ${t.action} ${t.shares} ${t.ticker}?`))
                              remove.mutate(t.id);
                          }}
                          disabled={remove.isPending}
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function Summary({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-muted">{label}</div>
      <div className={`text-2xl font-bold tabular-nums ${tone ?? ""}`}>{value}</div>
      {sub && <div className="text-xs text-muted mt-0.5">{sub}</div>}
    </div>
  );
}
