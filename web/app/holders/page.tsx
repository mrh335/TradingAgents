"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Holders,
  type Holding13F,
  type Manager13F,
  type TickerHoldersSummary,
} from "@/lib/api";

// ──────────────────────────────────────────────────────────────────────
// 13F institutional holdings — "who else owns this and how much?"
//
// What you're looking at: every position from the latest 13F-HR filing
// of each tracked institutional manager (Berkshire, Burry, Klarman,
// Ackman, etc.). 13F-HR is a quarterly disclosure required of every
// US institutional manager with ≥$100M in equities. Filed within 45
// days of quarter end — data here is typically 1-2 months stale.
//
// Use cases:
//   - "Is smart money buying or trimming NVDA this quarter?"
//   - "What's Burry's biggest position right now?"
//   - "Who are the largest holders of my AAPL position?"
// ──────────────────────────────────────────────────────────────────────

function fmtUsd(n: number | null): string {
  if (n === null || n === undefined) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

function fmtPct(n: number | null): string {
  if (n === null || n === undefined) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

function fmtDate(s: string | null): string {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleDateString();
  } catch {
    return s;
  }
}

function qoqTone(pct: number | null): string {
  if (pct === null || pct === undefined) return "text-muted"; // new position
  if (pct >= 25) return "text-success font-semibold";
  if (pct > 0) return "text-success";
  if (pct <= -50) return "text-danger font-semibold";
  if (pct < 0) return "text-danger";
  return "text-muted";
}

type Tab = "by-ticker" | "by-manager" | "managers";

export default function HoldersPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("by-ticker");
  const [ticker, setTicker] = useState("");
  const [selectedManager, setSelectedManager] = useState<string | null>(null);

  const managers = useQuery({
    queryKey: ["holders-managers"],
    queryFn: () => Holders.managers(false),
  });

  const refresh = useMutation({
    mutationFn: () => Holders.refresh(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["holders-managers"] });
      qc.invalidateQueries({ queryKey: ["holders-ticker"] });
      qc.invalidateQueries({ queryKey: ["holders-manager"] });
    },
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Institutional holdings (13F)</h1>
        <p className="text-muted text-sm">
          Latest quarterly 13F-HR filings from a curated list of
          institutional managers. Useful for sanity-checking your own
          positions against what smart money owns. Data refreshes weekly;
          filings themselves are 45-day-stale by SEC rules.
        </p>
        <p className="text-muted text-xs mt-1">
          A 13F-HR is the quarterly position disclosure every US
          manager with ≥$100M AUM must file within 45 days of quarter
          end. Long positions only — shorts and options are excluded.
          Source: SEC EDGAR.
        </p>
      </header>

      <div className="card flex flex-wrap items-center gap-3">
        <div className="flex gap-1">
          {(["by-ticker", "by-manager", "managers"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`btn text-xs ${tab === t ? "btn-primary" : ""}`}
            >
              {t === "by-ticker"
                ? "By ticker"
                : t === "by-manager"
                  ? "By manager"
                  : "Manager list"}
            </button>
          ))}
        </div>
        <span className="text-xs text-muted ml-auto">
          {managers.data?.length ?? 0} managers tracked ·{" "}
          {managers.data?.filter((m) => m.enabled).length ?? 0} enabled
        </span>
        <button
          className="btn text-xs"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          title="Force one EDGAR poll cycle now"
        >
          {refresh.isPending ? "Polling SEC…" : "↻ Refresh from EDGAR"}
        </button>
      </div>

      {refresh.isSuccess && (
        <div className="card border-l-4 border-l-success text-sm">
          Poll complete: checked {refresh.data.managers_checked} managers ·{" "}
          {refresh.data.filings_added} new filings ·{" "}
          {refresh.data.positions_added} positions inserted ·{" "}
          {refresh.data.errors} errors
          {refresh.data.error_details?.length > 0 && (
            <div className="text-xs text-muted mt-1">
              First few errors: {refresh.data.error_details.slice(0, 3).join(" | ")}
            </div>
          )}
        </div>
      )}

      {tab === "by-ticker" && (
        <ByTickerView ticker={ticker} setTicker={setTicker} />
      )}
      {tab === "by-manager" && (
        <ByManagerView
          managers={managers.data ?? []}
          selected={selectedManager}
          setSelected={setSelectedManager}
        />
      )}
      {tab === "managers" && (
        <ManagerList managers={managers.data ?? []} qc={qc} />
      )}
    </div>
  );
}

// ─── By-ticker view ─────────────────────────────────────────────────

function ByTickerView({
  ticker,
  setTicker,
}: {
  ticker: string;
  setTicker: (s: string) => void;
}) {
  const t = (ticker || "").trim().toUpperCase();
  const holders = useQuery({
    queryKey: ["holders-ticker", t],
    queryFn: () => Holders.tickerHolders(t),
    enabled: !!t,
  });
  const summary = useQuery({
    queryKey: ["holders-ticker-summary", t],
    queryFn: () => Holders.tickerSummary(t),
    enabled: !!t,
  });

  return (
    <div className="space-y-4">
      <div className="card flex gap-2 items-center">
        <input
          className="input flex-1"
          placeholder="Enter ticker (e.g. AAPL, NVDA, PG)"
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
        />
      </div>

      {!t ? (
        <div className="card text-sm text-muted">
          Type a ticker above to see which tracked institutional managers
          currently hold it (latest 13F filing only).
        </div>
      ) : holders.isLoading ? (
        <div className="text-sm text-muted">Loading…</div>
      ) : (holders.data?.length ?? 0) === 0 ? (
        <div className="card text-sm text-muted">
          No tracked manager has a current 13F position in {t}. Either
          (a) none of them hold it, or (b) the poller hasn't fetched
          their latest filing yet — hit Refresh above.
        </div>
      ) : (
        <>
          {summary.data && <TickerSummaryCard s={summary.data} />}
          <HoldingsTable rows={holders.data ?? []} hideTicker />
        </>
      )}
    </div>
  );
}

function TickerSummaryCard({ s }: { s: TickerHoldersSummary }) {
  const netTone =
    s.net_share_change_pct === null
      ? "text-muted"
      : s.net_share_change_pct > 0
        ? "text-success"
        : "text-danger";
  return (
    <div className="card">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h2 className="text-lg font-semibold">{s.ticker}</h2>
        <span className="text-xs text-muted">
          {s.manager_count} institutional manager{s.manager_count === 1 ? "" : "s"}
          {" hold this"}
        </span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3 text-sm">
        <div>
          <div className="text-xs text-muted">Combined value</div>
          <div className="font-semibold">{fmtUsd(s.total_value)}</div>
        </div>
        <div>
          <div className="text-xs text-muted">Total shares</div>
          <div className="font-semibold">{s.total_shares.toLocaleString()}</div>
        </div>
        <div>
          <div className="text-xs text-muted">QoQ share change</div>
          <div className={`font-semibold ${netTone}`}>
            {fmtPct(s.net_share_change_pct)}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted">New buys / big trims</div>
          <div className="font-semibold">
            <span className="text-success">{s.new_buys}</span>
            {" / "}
            <span className="text-danger">{s.large_trims}</span>
          </div>
        </div>
      </div>
      {s.top_managers.length > 0 && (
        <div className="mt-3 text-xs">
          <div className="text-muted mb-1">Top holders by value:</div>
          {s.top_managers.map((m, i) => (
            <div key={i} className="flex gap-3 items-baseline">
              <span className="font-medium">{m.name}</span>
              <span>{fmtUsd(m.value)}</span>
              <span className="text-muted">
                {m.shares.toLocaleString()} sh
              </span>
              <span className={qoqTone(m.qoq_change_pct)}>
                {fmtPct(m.qoq_change_pct)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── By-manager view ─────────────────────────────────────────────────

function ByManagerView({
  managers,
  selected,
  setSelected,
}: {
  managers: Manager13F[];
  selected: string | null;
  setSelected: (s: string | null) => void;
}) {
  const enabled = managers.filter((m) => m.enabled);
  const holdings = useQuery({
    queryKey: ["holders-manager", selected],
    queryFn: () => Holders.managerHoldings(selected!),
    enabled: !!selected,
  });

  return (
    <div className="space-y-4">
      <div className="card">
        <label className="text-xs text-muted block mb-1">Pick a manager</label>
        <select
          className="input w-full"
          value={selected ?? ""}
          onChange={(e) => setSelected(e.target.value || null)}
        >
          <option value="">— Select —</option>
          {enabled.map((m) => (
            <option key={m.cik} value={m.cik}>
              {m.name} {m.position_count ? `(${m.position_count} positions)` : ""}
            </option>
          ))}
        </select>
      </div>

      {!selected ? (
        <div className="card text-sm text-muted">
          Pick a manager above to see their latest 13F holdings, sorted
          by position value.
        </div>
      ) : holdings.isLoading ? (
        <div className="text-sm text-muted">Loading…</div>
      ) : (holdings.data?.length ?? 0) === 0 ? (
        <div className="card text-sm text-muted">
          No holdings on file. The poller may not have fetched this
          manager's latest 13F yet — hit Refresh above.
        </div>
      ) : (
        <HoldingsTable rows={holdings.data ?? []} hideManager />
      )}
    </div>
  );
}

// ─── Manager list ────────────────────────────────────────────────────

function ManagerList({
  managers,
  qc,
}: {
  managers: Manager13F[];
  qc: ReturnType<typeof useQueryClient>;
}) {
  const toggle = useMutation({
    mutationFn: ({ cik, enabled }: { cik: string; enabled: boolean }) =>
      Holders.toggleManager(cik, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["holders-managers"] }),
  });

  return (
    <div className="space-y-2">
      {managers.length === 0 && (
        <div className="card text-sm text-muted">
          No managers configured. Seeds should auto-populate on first boot —
          if this is empty there may be an init issue.
        </div>
      )}
      {managers.map((m) => (
        <div key={m.cik} className="card flex items-center gap-3 flex-wrap">
          <div className="flex-1 min-w-0">
            <div className="flex gap-2 items-baseline flex-wrap">
              <span className="font-semibold">{m.name}</span>
              <span className="text-xs text-muted">CIK {m.cik}</span>
              {!m.enabled && (
                <span className="text-xs uppercase text-muted">disabled</span>
              )}
            </div>
            <div className="text-xs text-muted">
              Latest filing: {fmtDate(m.last_filing_date)} (reports{" "}
              {fmtDate(m.last_report_date)}) ·{" "}
              {m.position_count ?? "—"} positions · AUM{" "}
              {fmtUsd(m.total_value)}
            </div>
            {m.last_error && (
              <div className="text-xs text-danger mt-1">
                Error: {m.last_error}
              </div>
            )}
          </div>
          <a
            className="btn text-xs"
            href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${m.cik}&type=13F-HR`}
            target="_blank"
            rel="noopener noreferrer"
            title="Open this manager's 13F filings page on SEC EDGAR"
          >
            EDGAR ↗
          </a>
          <a
            className="btn text-xs"
            href={`https://13f.info/manager/${m.cik}`}
            target="_blank"
            rel="noopener noreferrer"
            title="Open this manager on 13f.info for richer drilldown"
          >
            13f.info ↗
          </a>
          <button
            className="btn text-xs"
            onClick={() => toggle.mutate({ cik: m.cik, enabled: !m.enabled })}
            disabled={toggle.isPending}
          >
            {m.enabled ? "Disable" : "Enable"}
          </button>
        </div>
      ))}
    </div>
  );
}

// ─── Holdings table (shared) ─────────────────────────────────────────

function HoldingsTable({
  rows,
  hideTicker = false,
  hideManager = false,
}: {
  rows: Holding13F[];
  hideTicker?: boolean;
  hideManager?: boolean;
}) {
  const totalValue = useMemo(
    () => rows.reduce((s, r) => s + (r.value || 0), 0),
    [rows],
  );
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-muted uppercase">
            {!hideManager && <th className="text-left pb-2">Manager</th>}
            {!hideTicker && <th className="text-left pb-2">Ticker</th>}
            <th className="text-left pb-2">Issuer</th>
            <th className="text-right pb-2">Shares</th>
            <th className="text-right pb-2">Value</th>
            <th className="text-right pb-2">% of AUM</th>
            <th className="text-right pb-2">QoQ Δ</th>
            <th className="text-left pb-2 pl-3">Type</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.manager_cik}-${r.cusip}-${i}`} className="border-t border-border">
              {!hideManager && (
                <td className="py-1.5 pr-3">{r.manager_name}</td>
              )}
              {!hideTicker && (
                <td className="py-1.5 pr-3 font-mono font-semibold">
                  {r.ticker ?? <span className="text-muted">{r.cusip}</span>}
                </td>
              )}
              <td className="py-1.5 pr-3 text-muted truncate max-w-[14rem]">
                {r.name_of_issuer}
              </td>
              <td className="py-1.5 pr-3 text-right">
                {r.shares.toLocaleString()}
              </td>
              <td className="py-1.5 pr-3 text-right">{fmtUsd(r.value)}</td>
              <td className="py-1.5 pr-3 text-right">
                {r.pct_of_manager_aum?.toFixed(1) ?? "—"}%
              </td>
              <td className={`py-1.5 pr-3 text-right ${qoqTone(r.qoq_change_pct)}`}>
                {r.prev_shares === null
                  ? <span title="New position vs prior filing">NEW</span>
                  : fmtPct(r.qoq_change_pct)}
              </td>
              <td className="py-1.5 pl-3 text-xs text-muted">
                {r.put_call ?? r.title_of_class ?? ""}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-border text-xs text-muted">
            <td
              colSpan={(hideManager ? 0 : 1) + (hideTicker ? 0 : 1) + 3}
              className="pt-2"
            >
              {rows.length} positions
            </td>
            <td className="pt-2 text-right font-semibold">
              {fmtUsd(totalValue)}
            </td>
            <td colSpan={3} />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
