"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Runs, Sidecars } from "@/lib/api";
import type { RunSummary } from "@/lib/types";
import { decisionColor, fmtDate, fmtTokens, statusColor } from "@/lib/format";

type SortKey =
  | "ticker" | "trade_date" | "decision" | "status"
  | "provider" | "deep_model" | "tokens_in" | "tokens_out"
  | "started_at";
type SortDir = "asc" | "desc";

const COLUMNS: { key: SortKey; label: string; align?: "right" }[] = [
  { key: "ticker", label: "Ticker" },
  { key: "trade_date", label: "Trade date" },
  { key: "decision", label: "Decision" },
  { key: "provider", label: "Provider/Model" },
  { key: "tokens_in", label: "Tokens", align: "right" },
  { key: "status", label: "Status" },
  { key: "started_at", label: "Started" },
];

function compare(a: any, b: any): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}

function tokenTotal(r: RunSummary): number {
  return (r.tokens_in || 0) + (r.tokens_out || 0);
}

export default function HistoryPage() {
  const qc = useQueryClient();
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => Runs.list(),
    refetchInterval: 10_000,
  });
  const pending = useQuery({
    queryKey: ["sidecars-pending"],
    queryFn: () => Sidecars.pending(),
    refetchInterval: 15_000,
  });

  // Filters
  const [tickerFilter, setTickerFilter] = useState("");
  const [decisionFilter, setDecisionFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [providerFilter, setProviderFilter] = useState<string>("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  // Sort
  const [sortKey, setSortKey] = useState<SortKey>("started_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // Expanded rows + grouping
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [groupBy, setGroupBy] = useState<"none" | "ticker" | "date">("none");
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());

  // Delete
  const deleteRun = useMutation({
    mutationFn: ({ runId, deleteFiles }: { runId: string; deleteFiles: boolean }) =>
      Runs.delete(runId, deleteFiles),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });

  // Bulk Claude Code request
  const requestAll = useMutation({
    mutationFn: (includeExisting: boolean) => Sidecars.requestAllMissing(includeExisting),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sidecars-pending"] }),
  });

  const data = runs.data ?? [];
  const allDecisions = useMemo(() => Array.from(new Set(data.map((r) => r.decision).filter(Boolean))) as string[], [data]);
  const allStatuses = useMemo(() => Array.from(new Set(data.map((r) => r.status).filter(Boolean))), [data]);
  const allProviders = useMemo(() => Array.from(new Set(data.map((r) => r.provider).filter(Boolean))) as string[], [data]);

  const filtered = useMemo(() => {
    let rows = data;
    if (tickerFilter) {
      const f = tickerFilter.toUpperCase();
      rows = rows.filter((r) => r.ticker.includes(f));
    }
    if (decisionFilter) rows = rows.filter((r) => r.decision === decisionFilter);
    if (statusFilter) rows = rows.filter((r) => r.status === statusFilter);
    if (providerFilter) rows = rows.filter((r) => r.provider === providerFilter);
    if (dateFrom) rows = rows.filter((r) => r.trade_date >= dateFrom);
    if (dateTo) rows = rows.filter((r) => r.trade_date <= dateTo);
    return rows;
  }, [data, tickerFilter, decisionFilter, statusFilter, providerFilter, dateFrom, dateTo]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      let av: any, bv: any;
      if (sortKey === "tokens_in") {
        av = tokenTotal(a); bv = tokenTotal(b);
      } else {
        av = (a as any)[sortKey]; bv = (b as any)[sortKey];
      }
      const cmp = compare(av, bv);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sortKey, sortDir]);

  const grouped = useMemo(() => {
    if (groupBy === "none") return [{ key: "", rows: sorted }];
    const map = new Map<string, RunSummary[]>();
    for (const r of sorted) {
      const k = groupBy === "ticker" ? r.ticker : r.trade_date;
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(r);
    }
    return Array.from(map.entries()).map(([key, rows]) => ({ key, rows }));
  }, [sorted, groupBy]);

  const pendingByRun = useMemo(() => {
    const map = new Map<string, true>();
    (pending.data ?? []).forEach((p) => map.set(p.run_id, true));
    return map;
  }, [pending.data]);

  function setSort(k: SortKey) {
    if (sortKey === k) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(k);
      setSortDir(k === "started_at" || k === "trade_date" || k === "tokens_in" ? "desc" : "asc");
    }
  }

  function toggleExpand(runId: string) {
    const next = new Set(expanded);
    if (next.has(runId)) next.delete(runId);
    else next.add(runId);
    setExpanded(next);
  }

  function toggleGroup(key: string) {
    const next = new Set(collapsedGroups);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setCollapsedGroups(next);
  }

  function clearAllFilters() {
    setTickerFilter(""); setDecisionFilter(""); setStatusFilter("");
    setProviderFilter(""); setDateFrom(""); setDateTo("");
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-bold">Run history</h1>
        <p className="text-muted text-sm">
          Every analysis ever recorded. Click a header to sort. Each row
          expands to a quick preview; click "Open report →" for the full view.
          Archives never overwrite — re-running the same ticker+date creates
          a new row. Delete prunes the SQLite row AND on-disk archive.
        </p>
      </header>

      {/* Claude Code controls — always visible, even with zero pending. */}
      <div className="card border-accent/30">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="text-sm flex-1 min-w-0">
            {(pending.data?.length ?? 0) > 0 ? (
              <>
                <span className="text-warning font-semibold">
                  ⏳ {pending.data!.length} pending Claude Code request{pending.data!.length === 1 ? "" : "s"}
                </span>
                <div className="text-muted text-xs mt-1">
                  Open Claude Code and tell it:{" "}
                  <em>"process every pending brief request at <code>http://192.168.2.34:8001/sidecars/pending</code> — for each, fetch the archive, build a Brief per CLAUDE.md, and POST it back."</em>
                </div>
              </>
            ) : (
              <>
                <span className="font-semibold">🤖 Claude Code briefs</span>
                <div className="text-muted text-xs mt-1">
                  Drop request markers in bulk for every completed run that doesn't have a Claude-Code-generated brief yet. Claude Code processes them from <code>/sidecars/pending</code> — zero API tokens.
                </div>
              </>
            )}
          </div>
          <div className="flex gap-2 shrink-0">
            <button
              className="btn text-xs"
              onClick={() => {
                if (confirm("Drop a brief request marker on every completed run missing a Claude-Code brief?")) {
                  requestAll.mutate(false);
                }
              }}
              disabled={requestAll.isPending}
              title="Adds a marker to runs that don't have a brief.json sidecar yet"
            >
              {requestAll.isPending ? "Requesting…" : "🤖 Request all missing"}
            </button>
            <button
              className="btn text-xs"
              onClick={() => {
                if (confirm("Re-request briefs for EVERY run — including ones that already have a Claude-Code brief? Existing sidecars stay until Claude Code overwrites them.")) {
                  requestAll.mutate(true);
                }
              }}
              disabled={requestAll.isPending}
              title="Re-runs Claude Code for all completed runs, even where a brief already exists"
            >
              🔄 Re-request all
            </button>
          </div>
        </div>
        {requestAll.data && (
          <div className="text-xs text-muted mt-3 pt-3 border-t border-border">
            <span className="text-success">✓ Requested:</span> {requestAll.data.requested.length}
            {" · "}
            <span>Skipped (already briefed):</span> {requestAll.data.skipped.length}
            {requestAll.data.no_archive.length > 0 && (
              <>{" · "}<span className="text-warning">No archive:</span> {requestAll.data.no_archive.length}</>
            )}
          </div>
        )}
      </div>

      {/* ---- Filters ---- */}
      <div className="card grid grid-cols-2 md:grid-cols-7 gap-3 items-end">
        <div>
          <label className="label">Ticker</label>
          <input className="input w-full" placeholder="filter…" value={tickerFilter}
                 onChange={(e) => setTickerFilter(e.target.value)} />
        </div>
        <div>
          <label className="label">Decision</label>
          <select className="input w-full" value={decisionFilter}
                  onChange={(e) => setDecisionFilter(e.target.value)}>
            <option value="">all</option>
            {allDecisions.map((d) => <option key={d}>{d}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Status</label>
          <select className="input w-full" value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">all</option>
            {allStatuses.map((s) => <option key={s}>{s}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Provider</label>
          <select className="input w-full" value={providerFilter}
                  onChange={(e) => setProviderFilter(e.target.value)}>
            <option value="">all</option>
            {allProviders.map((p) => <option key={p}>{p}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Date from</label>
          <input className="input w-full" type="date" value={dateFrom}
                 onChange={(e) => setDateFrom(e.target.value)} />
        </div>
        <div>
          <label className="label">Date to</label>
          <input className="input w-full" type="date" value={dateTo}
                 onChange={(e) => setDateTo(e.target.value)} />
        </div>
        <div className="flex items-end gap-2">
          <button className="btn text-xs" onClick={clearAllFilters}>Clear filters</button>
        </div>
        <div className="col-span-full text-xs text-muted flex flex-wrap items-center gap-4">
          <span>{filtered.length} of {data.length} runs</span>
          <span>·</span>
          <span>
            Group by:{" "}
            {(["none", "ticker", "date"] as const).map((g) => (
              <button key={g}
                      className={`px-2 ${groupBy === g ? "text-accent font-semibold" : "hover:text-fg"}`}
                      onClick={() => setGroupBy(g)}>{g}</button>
            ))}
          </span>
          <span>·</span>
          <span>
            Sort:{" "}
            <code className="text-accent">{sortKey}</code>{" "}
            <button className="px-1 hover:text-fg"
                    onClick={() => setSortDir(sortDir === "asc" ? "desc" : "asc")}>
              {sortDir === "asc" ? "↑" : "↓"}
            </button>
          </span>
        </div>
      </div>

      {/* ---- Table ---- */}
      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs uppercase text-muted">
              <th className="w-8"></th>
              {COLUMNS.map((c) => (
                <th key={c.key}
                    className={`py-2 px-3 font-medium cursor-pointer hover:text-fg select-none ${c.align === "right" ? "text-right" : "text-left"}`}
                    onClick={() => setSort(c.key)}>
                  {c.label}
                  {sortKey === c.key && <span className="ml-1 text-accent">{sortDir === "asc" ? "↑" : "↓"}</span>}
                </th>
              ))}
              <th className="text-right py-2 px-3"></th>
            </tr>
          </thead>
          <tbody>
            {runs.isLoading && (
              <tr><td colSpan={COLUMNS.length + 2} className="py-6 text-center text-muted">Loading…</td></tr>
            )}
            {!runs.isLoading && filtered.length === 0 && (
              <tr><td colSpan={COLUMNS.length + 2} className="py-6 text-center text-muted">
                No runs match these filters.
              </td></tr>
            )}
            {grouped.map((group) => {
              const groupCollapsed = collapsedGroups.has(group.key);
              return (
                <Fragment key={group.key || "_"}>
                  {groupBy !== "none" && (
                    <tr className="bg-bg/60 cursor-pointer hover:bg-surface" onClick={() => toggleGroup(group.key)}>
                      <td colSpan={COLUMNS.length + 2} className="py-2 px-3 text-xs font-semibold uppercase tracking-wider text-muted">
                        <span className="mr-2">{groupCollapsed ? "▶" : "▼"}</span>
                        {groupBy}: <span className="text-fg">{group.key}</span>
                        <span className="ml-2 text-muted normal-case font-normal">({group.rows.length} runs)</span>
                      </td>
                    </tr>
                  )}
                  {!groupCollapsed && group.rows.map((r) => {
                    const isOpen = expanded.has(r.run_id);
                    const isPending = pendingByRun.has(r.run_id);
                    return (
                      <Fragment key={r.run_id}>
                        <tr className="border-t border-border hover:bg-bg/40">
                          <td className="py-2 px-2 text-muted cursor-pointer" onClick={() => toggleExpand(r.run_id)}>
                            {isOpen ? "▼" : "▶"}
                          </td>
                          <td className="py-2 px-3 font-semibold">
                            {r.ticker}
                            {isPending && <span title="Claude Code request pending" className="ml-2 text-warning">⏳</span>}
                          </td>
                          <td className="py-2 px-3">{r.trade_date}</td>
                          <td className={`py-2 px-3 font-semibold ${decisionColor(r.decision)}`}>{r.decision ?? "—"}</td>
                          <td className="py-2 px-3 text-muted text-xs">
                            {r.provider ?? "—"}
                            <br />
                            <span className="text-muted">{r.deep_model ?? "—"}</span>
                          </td>
                          <td className="py-2 px-3 text-right text-muted text-xs tabular-nums">
                            {fmtTokens(r.tokens_in)}↑<br/>{fmtTokens(r.tokens_out)}↓
                          </td>
                          <td className="py-2 px-3">
                            <span className={`pill ${statusColor(r.status)}`}>{r.status}</span>
                          </td>
                          <td className="py-2 px-3 text-muted text-xs">{fmtDate(r.started_at)}</td>
                          <td className="py-2 px-3 text-right whitespace-nowrap">
                            <Link className="text-accent hover:underline mr-3" href={`/history/${r.run_id}`}>Open →</Link>
                            <button
                              className="text-danger hover:underline"
                              onClick={() => {
                                if (confirm(`Delete ${r.ticker} run from ${r.trade_date}?\nThis removes the SQLite row AND the on-disk archive + sidecars. Cannot be undone.`)) {
                                  deleteRun.mutate({ runId: r.run_id, deleteFiles: true });
                                }
                              }}>
                              ✕
                            </button>
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="bg-bg/40">
                            <td></td>
                            <td colSpan={COLUMNS.length + 1} className="py-3 px-3">
                              <ExpandedPreview row={r} pending={isPending} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

import { Fragment } from "react";

function ExpandedPreview({ row, pending }: { row: RunSummary; pending: boolean }) {
  return (
    <div className="grid grid-cols-3 gap-6 text-xs">
      <div>
        <div className="text-muted uppercase tracking-wider mb-1">Run metadata</div>
        <div className="space-y-0.5">
          <div><span className="text-muted">id:</span> <code className="text-[10px]">{row.run_id.slice(0, 12)}…</code></div>
          <div><span className="text-muted">debate:</span> {row.debate_rounds ?? "—"} rounds</div>
          <div><span className="text-muted">risk:</span> {row.risk_rounds ?? "—"} rounds</div>
          <div><span className="text-muted">quick:</span> {row.quick_model ?? "—"}</div>
          <div><span className="text-muted">deep:</span> {row.deep_model ?? "—"}</div>
        </div>
      </div>
      <div>
        <div className="text-muted uppercase tracking-wider mb-1">Cost</div>
        <div className="space-y-0.5">
          <div><span className="text-muted">tokens in:</span> {fmtTokens(row.tokens_in)}</div>
          <div><span className="text-muted">tokens out:</span> {fmtTokens(row.tokens_out)}</div>
          <div><span className="text-muted">llm calls:</span> {row.llm_calls}</div>
          <div><span className="text-muted">tool calls:</span> {row.tool_calls}</div>
        </div>
      </div>
      <div>
        <div className="text-muted uppercase tracking-wider mb-1">Timing</div>
        <div className="space-y-0.5">
          <div><span className="text-muted">started:</span> {fmtDate(row.started_at)}</div>
          <div><span className="text-muted">completed:</span> {fmtDate(row.completed_at)}</div>
          {row.error_message && (
            <div className="text-danger mt-2">⚠ {row.error_message.slice(0, 200)}</div>
          )}
          {pending && (
            <div className="text-warning mt-2">⏳ Pending Claude Code request</div>
          )}
        </div>
      </div>
      {row.log_path && (
        <div className="col-span-3 text-muted">
          <span className="uppercase tracking-wider text-[10px]">archive:</span>{" "}
          <code className="text-[10px]">{row.log_path}</code>
        </div>
      )}
    </div>
  );
}
