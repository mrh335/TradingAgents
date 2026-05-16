"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RunQueue, type QueueItem } from "@/lib/api";

const STATUS_COLORS: Record<QueueItem["status"], string> = {
  pending: "text-warning",
  claimed: "text-accent",
  done: "text-success",
  error: "text-danger",
  cancelled: "text-muted",
};

const STATUS_LABELS: Record<QueueItem["status"], string> = {
  pending: "⏳ Pending",
  claimed: "🤖 Running",
  done: "✓ Done",
  error: "⚠ Error",
  cancelled: "✕ Cancelled",
};

export default function QueuePage() {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<QueueItem["status"] | "all">("all");

  const q = useQuery({
    queryKey: ["run-queue", statusFilter],
    queryFn: () => RunQueue.list(statusFilter === "all" ? undefined : statusFilter),
    refetchInterval: 8000, // poll for state changes (worker may pick stuff up)
  });

  const cancel = useMutation({
    mutationFn: (id: string) => RunQueue.cancel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["run-queue"] }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => RunQueue.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["run-queue"] }),
  });

  const items = q.data ?? [];
  const counts = items.reduce(
    (acc, i) => {
      acc[i.status] = (acc[i.status] ?? 0) + 1;
      return acc;
    },
    {} as Partial<Record<QueueItem["status"], number>>,
  );

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Run queue</h1>
        <p className="text-muted text-sm">
          Analysis requests queued from the Run / Batch pages. An external
          worker (typically the <code>tradingagents-analyze</code> skill in
          Claude Desktop or Claude Code) claims pending items, runs the
          full pipeline using its own LLM budget, and posts the result back.
        </p>
        <p className="text-muted text-xs mt-2">
          Trigger the worker manually by saying <em>"process the run queue"</em>{" "}
          in a Claude session that has the skill loaded, or schedule it via{" "}
          Claude Code's <code>/loop</code> for unattended processing.
        </p>
      </header>

      {/* Status filter chips */}
      <div className="card flex flex-wrap gap-2 items-center">
        {(["all", "pending", "claimed", "done", "error", "cancelled"] as const).map(
          (s) => (
            <button
              key={s}
              className={`btn text-xs ${statusFilter === s ? "btn-primary" : ""}`}
              onClick={() => setStatusFilter(s)}
            >
              {s === "all"
                ? `All (${items.length})`
                : `${STATUS_LABELS[s as QueueItem["status"]]} (${counts[s as QueueItem["status"]] ?? 0})`}
            </button>
          ),
        )}
        <span className="text-xs text-muted ml-auto">
          Auto-refresh every 8s {q.isFetching ? "· refreshing…" : ""}
        </span>
      </div>

      {/* Empty / loading / list */}
      {q.isLoading ? (
        <div className="text-muted">Loading…</div>
      ) : items.length === 0 ? (
        <div className="card text-sm text-muted">
          No queue items{statusFilter !== "all" ? ` with status "${statusFilter}"` : ""}.{" "}
          Queue analyses from the{" "}
          <Link href="/run" className="text-accent hover:underline">
            Run page
          </Link>{" "}
          using the 🤖 Queue for Claude Desktop button.
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-muted text-xs uppercase tracking-wider">
              <tr>
                <th className="py-2">Ticker / Date</th>
                <th>Mode</th>
                <th>Status</th>
                <th>Requested</th>
                <th>Worker / Claimed</th>
                <th>Result</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id} className="border-t border-border align-top">
                  <td className="py-2 font-semibold">
                    {it.ticker}
                    <div className="text-xs text-muted">{it.trade_date}</div>
                  </td>
                  <td className="text-xs">
                    {it.mode}
                    {it.options?.provider && (
                      <div className="text-muted">
                        {String(it.options.provider)}/{String(it.options.deep_model ?? "—")}
                      </div>
                    )}
                  </td>
                  <td>
                    <span className={`text-xs font-semibold ${STATUS_COLORS[it.status]}`}>
                      {STATUS_LABELS[it.status]}
                    </span>
                    {it.error_message && (
                      <div className="text-xs text-danger mt-0.5 max-w-xs whitespace-normal">
                        {it.error_message.slice(0, 200)}
                      </div>
                    )}
                  </td>
                  <td className="text-xs text-muted">
                    <div>{it.requested_by ?? "—"}</div>
                    <div>{fmtTs(it.created_at)}</div>
                  </td>
                  <td className="text-xs text-muted">
                    {it.claimed_by ? (
                      <>
                        <div>{it.claimed_by}</div>
                        <div>{fmtTs(it.claimed_at)}</div>
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="text-xs">
                    {it.result_run_id ? (
                      <Link
                        href={`/history/${it.result_run_id}`}
                        className="text-accent hover:underline"
                      >
                        {it.result_run_id.slice(0, 10)}… →
                      </Link>
                    ) : (
                      <span className="text-muted">—</span>
                    )}
                  </td>
                  <td className="text-right whitespace-nowrap">
                    {(it.status === "pending" || it.status === "claimed") && (
                      <button
                        className="btn text-xs"
                        onClick={() => cancel.mutate(it.id)}
                        disabled={cancel.isPending}
                      >
                        Cancel
                      </button>
                    )}
                    {(it.status === "done" ||
                      it.status === "error" ||
                      it.status === "cancelled") && (
                      <button
                        className="btn text-xs"
                        onClick={() => {
                          if (confirm(`Delete queue item ${it.id.slice(0, 8)}…?`))
                            remove.mutate(it.id);
                        }}
                        disabled={remove.isPending}
                      >
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function fmtTs(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}
