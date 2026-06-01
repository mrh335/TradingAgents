"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Tax } from "@/lib/api";
import type { DeriskResponse, CharitableResponse, TaxLotsResponse } from "@/lib/taxTypes";

// Tax-aware de-risking. Reads real per-lot cost basis (reconciled to the
// planner's authoritative book), then models the tax cost of trimming a
// concentrated position three ways (HIFO/FIFO/LIFO), loss harvesting, and
// donating appreciated shares. Audience: an engineer, not a finance pro —
// jargon gets a plain-English gloss inline.

const fmt$ = (n: number | null | undefined) =>
  n == null ? "—" : "$" + Math.round(n).toLocaleString();
const fmtPct = (n: number | null | undefined, d = 1) =>
  n == null ? "—" : (n * 100).toFixed(d) + "%";

const PRESETS = [
  { id: "ca_top", label: "CA top (37.1% / 54.1%)" },
  { id: "ca_mid", label: "CA mid (28.2% / 45.2%)" },
  { id: "fed_top_notax_state", label: "No-tax state, top (23.8% / 40.8%)" },
  { id: "fed_15_notax_state", label: "No-tax state, 15% (18.8% / 32%)" },
];

export default function TaxPage() {
  const [preset, setPreset] = useState("ca_top");
  const lotsQ = useQuery({ queryKey: ["tax-lots"], queryFn: () => Tax.lots() });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Tax-aware de-risking</h1>
        <p className="text-sm text-muted mt-1 max-w-3xl">
          Trimming a concentrated winner triggers capital-gains tax. This models the
          real tax cost using your actual cost-basis lots — which shares to sell
          (HIFO picks highest-cost first to realize the least gain), how losses can
          offset gains, and how donating appreciated shares avoids the tax entirely.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <label className="text-sm text-muted">Tax rates (long-term / short-term):</label>
        <select
          className="input text-sm"
          value={preset}
          onChange={(e) => setPreset(e.target.value)}
        >
          {PRESETS.map((p) => (
            <option key={p.id} value={p.id}>{p.label}</option>
          ))}
        </select>
      </div>

      {lotsQ.isLoading && <div className="card text-sm text-muted">Loading your lots…</div>}
      {lotsQ.isError && (
        <div className="card text-sm text-danger">
          Couldn’t load lots: {(lotsQ.error as Error)?.message ?? "unknown error"}
        </div>
      )}
      {lotsQ.data && (
        <>
          <BookSummary data={lotsQ.data} />
          <DeriskTool positions={lotsQ.data.positions.map((p) => p.symbol)} preset={preset} />
          <HarvestCard preset={preset} />
          <CharitableTool positions={lotsQ.data.positions.map((p) => p.symbol)} preset={preset} />
          <LotTables data={lotsQ.data} />
        </>
      )}
    </div>
  );
}

function BookSummary({ data }: { data: TaxLotsResponse }) {
  const conc = data.concentration;
  return (
    <div className="card">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="Book value" value={fmt$(data.total_value)} />
        <Stat
          label="Concentration"
          value={conc.symbol ? `${conc.symbol} ${conc.pct?.toFixed(1)}%` : "—"}
          accent={(conc.pct ?? 0) > 50 ? "danger" : undefined}
        />
        <Stat label="Embedded long-term gain" value={fmt$(data.embedded.long_term_gain)} accent="success" />
        <Stat
          label="Embedded losses (harvestable)"
          value={fmt$(data.embedded.long_term_loss + data.embedded.short_term_loss)}
          accent="danger"
        />
      </div>
      {(conc.pct ?? 0) > 50 && (
        <p className="text-xs text-muted mt-3">
          ⚠️ {conc.symbol} is {conc.pct?.toFixed(1)}% of your book — a large single-stock
          concentration. The tools below estimate what it costs in tax to reduce it.
        </p>
      )}
    </div>
  );
}

function DeriskTool({ positions, preset }: { positions: string[]; preset: string }) {
  const [symbol, setSymbol] = useState(positions[0] ?? "AAPL");
  const [target, setTarget] = useState(400_000);
  const m = useMutation<DeriskResponse, Error, void>({
    mutationFn: () => Tax.derisk({ symbol, target_value: target, rate_preset: preset }),
  });

  return (
    <div className="card space-y-3">
      <h2 className="font-semibold">De-risk a position</h2>
      <p className="text-xs text-muted">
        Pick how much of a position to sell. We compare three lot-selection methods:
        <b> HIFO</b> (sell highest-cost shares first → least taxable gain), <b>FIFO</b>
        (oldest first), and <b>LIFO</b> (newest first).
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs text-muted mb-1">Position</label>
          <select className="input text-sm" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {positions.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="flex-1 min-w-[240px]">
          <label className="block text-xs text-muted mb-1">
            Amount to sell: <b>{fmt$(target)}</b>
          </label>
          <input
            type="range" min={10000} max={1000000} step={10000}
            value={target} onChange={(e) => setTarget(Number(e.target.value))}
            className="w-full"
          />
        </div>
        <button className="btn btn-primary" onClick={() => m.mutate()} disabled={m.isPending}>
          {m.isPending ? "Calculating…" : "Calculate tax"}
        </button>
      </div>

      {m.isError && <div className="text-sm text-danger">{m.error.message}</div>}
      {m.data && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm mt-2">
            <thead>
              <tr className="text-left text-muted border-b border-border">
                <th className="py-1.5 pr-3">Method</th>
                <th className="py-1.5 px-3 text-right">Proceeds</th>
                <th className="py-1.5 px-3 text-right">Realized gain</th>
                <th className="py-1.5 px-3 text-right">Tax</th>
                <th className="py-1.5 px-3 text-right">Net cash</th>
                <th className="py-1.5 px-3 text-right">Tax drag</th>
              </tr>
            </thead>
            <tbody>
              {m.data.comparison.map((c) => {
                const best = m.data!.best?.method === c.method;
                return (
                  <tr key={c.method} className={`border-b border-border ${best ? "bg-success/10" : ""}`}>
                    <td className="py-1.5 pr-3 font-mono uppercase">
                      {c.method}{best && <span className="ml-2 text-xs text-success">best</span>}
                    </td>
                    <td className="py-1.5 px-3 text-right tabular-nums">{fmt$(c.proceeds)}</td>
                    <td className="py-1.5 px-3 text-right tabular-nums">{fmt$(c.realized_gain)}</td>
                    <td className="py-1.5 px-3 text-right tabular-nums text-danger">{fmt$(c.tax)}</td>
                    <td className="py-1.5 px-3 text-right tabular-nums">{fmt$(c.net_cash)}</td>
                    <td className="py-1.5 px-3 text-right tabular-nums">{fmtPct(c.tax_drag_pct)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {m.data.best && (
            <p className="text-xs text-muted mt-2">
              HIFO vs FIFO saves{" "}
              <b className="text-success">
                {fmt$(
                  Math.max(...m.data.comparison.map((c) => c.tax)) -
                  Math.min(...m.data.comparison.map((c) => c.tax))
                )}
              </b>{" "}
              in tax for the same {fmt$(target)} raised — just by choosing which shares to sell.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function HarvestCard({ preset }: { preset: string }) {
  const q = useQuery({ queryKey: ["tax-harvest", preset], queryFn: () => Tax.harvest(preset) });
  if (q.isLoading) return <div className="card text-sm text-muted">Scanning for harvestable losses…</div>;
  if (!q.data) return null;
  const h = q.data;
  return (
    <div className="card">
      <h2 className="font-semibold mb-1">Tax-loss harvesting</h2>
      <p className="text-xs text-muted mb-3">
        Selling a position that’s underwater realizes a loss that offsets your gains
        dollar-for-dollar, lowering the tax bill.
      </p>
      {h.harvestable_lots.length === 0 ? (
        <p className="text-sm text-muted">No positions are currently at a loss.</p>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <Stat label="Total harvestable loss" value={fmt$(h.total_loss)} accent="danger" />
          <Stat label="Tax it would offset" value={fmt$(h.tax_offset_value)} accent="success" />
          <Stat label="Lots at a loss" value={String(h.harvestable_lots.length)} />
        </div>
      )}
    </div>
  );
}

function CharitableTool({ positions, preset }: { positions: string[]; preset: string }) {
  const [symbol, setSymbol] = useState(positions[0] ?? "AAPL");
  const [amt, setAmt] = useState(100_000);
  const m = useMutation<CharitableResponse, Error, void>({
    mutationFn: () => Tax.charitable({ symbol, donate_value: amt, rate_preset: preset }),
  });
  return (
    <div className="card space-y-3">
      <h2 className="font-semibold">Donate appreciated shares (vs. selling)</h2>
      <p className="text-xs text-muted">
        Giving long-term shares to a charity or donor-advised fund avoids the
        capital-gains tax entirely <i>and</i> gives you a deduction for the full
        market value. This compares donating vs. selling the same dollar amount.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs text-muted mb-1">Position</label>
          <select className="input text-sm" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {positions.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="flex-1 min-w-[240px]">
          <label className="block text-xs text-muted mb-1">Donate: <b>{fmt$(amt)}</b></label>
          <input type="range" min={10000} max={500000} step={10000}
            value={amt} onChange={(e) => setAmt(Number(e.target.value))} className="w-full" />
        </div>
        <button className="btn btn-primary" onClick={() => m.mutate()} disabled={m.isPending}>
          {m.isPending ? "…" : "Compare"}
        </button>
      </div>
      {m.isError && <div className="text-sm text-danger">{m.error.message}</div>}
      {m.data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-1">
          <Stat label="Cap-gains tax avoided" value={fmt$(m.data.donate.cap_gains_tax_avoided)} accent="success" />
          <Stat label="Deduction value" value={fmt$(m.data.donate.income_deduction_value)} accent="success" />
          <Stat label="Total tax benefit" value={fmt$(m.data.donate.total_tax_benefit)} accent="success" />
          <Stat label="If sold instead: tax" value={fmt$(m.data.sell_equivalent.tax)} accent="danger" />
        </div>
      )}
    </div>
  );
}

function LotTables({ data }: { data: TaxLotsResponse }) {
  return (
    <div className="card">
      <h2 className="font-semibold mb-3">Cost-basis lots</h2>
      <div className="space-y-5">
        {data.positions.map((p) => (
          <div key={p.symbol}>
            <div className="flex items-baseline justify-between mb-1">
              <h3 className="font-mono font-semibold">{p.symbol}</h3>
              <span className="text-xs text-muted">
                {p.shares.toLocaleString()} sh · {fmt$(p.value)} · embedded {fmt$(p.embedded_gain)} ·{" "}
                {p.lot_count} lots
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-muted border-b border-border">
                    <th className="py-1 pr-3">Acquired</th>
                    <th className="py-1 px-3">Term</th>
                    <th className="py-1 px-3">Plan</th>
                    <th className="py-1 px-3 text-right">Shares</th>
                    <th className="py-1 px-3 text-right">Cost/sh</th>
                    <th className="py-1 px-3 text-right">Market value</th>
                    <th className="py-1 px-3 text-right">Embedded gain</th>
                  </tr>
                </thead>
                <tbody>
                  {p.lots.slice(0, 12).map((l, i) => (
                    <tr key={i} className="border-b border-border/50">
                      <td className="py-1 pr-3">{l.acquired_date}</td>
                      <td className="py-1 px-3">
                        <span className={l.term === "short" ? "text-danger" : "text-muted"}>{l.term}</span>
                      </td>
                      <td className="py-1 px-3 text-muted">{l.plan_type || "—"}</td>
                      <td className="py-1 px-3 text-right tabular-nums">{l.shares.toFixed(1)}</td>
                      <td className="py-1 px-3 text-right tabular-nums">${l.cost_basis_per_share.toFixed(2)}</td>
                      <td className="py-1 px-3 text-right tabular-nums">{fmt$(l.market_value)}</td>
                      <td className={`py-1 px-3 text-right tabular-nums ${l.embedded_gain >= 0 ? "text-success" : "text-danger"}`}>
                        {fmt$(l.embedded_gain)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {p.lots.length > 12 && (
                <p className="text-xs text-muted mt-1">+{p.lots.length - 12} more lots…</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: "success" | "danger" }) {
  const color = accent === "success" ? "text-success" : accent === "danger" ? "text-danger" : "";
  return (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}
