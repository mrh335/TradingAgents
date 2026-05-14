"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Batches } from "@/lib/api";
import { decisionColor, fmtDate, fmtTokens, statusColor } from "@/lib/format";

export default function BatchDetailPage() {
  const { batchId } = useParams<{ batchId: string }>();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["batch", batchId],
    queryFn: () => Batches.get(batchId),
    refetchInterval: 4000,
    enabled: !!batchId,
  });
  const cancel = useMutation({
    mutationFn: () => Batches.cancel(batchId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["batch", batchId] }),
  });

  if (q.isLoading) return <div className="text-muted">Loading…</div>;
  const b = q.data;
  if (!b) return <div className="text-danger">Batch not found.</div>;

  const counts = b.counts ?? {};
  const doneCount = counts.done ?? 0;
  const runningCount = counts.running ?? 0;
  const queuedCount = counts.queued ?? 0;
  const errorCount = counts.error ?? 0;
  const progressPct = b.total > 0 ? Math.round((doneCount / b.total) * 100) : 0;

  const isActive = b.status === "running";

  return (
    <div className="space-y-4">
      <header className="space-y-2">
        <Link href="/batch" className="text-sm text-accent hover:underline">← All batches</Link>
        <div className="flex items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold">{b.name ?? `Batch ${b.id.slice(0, 8)}`}</h1>
            <p className="text-sm text-muted">
              {b.total} tickers · {b.trade_date} · {b.provider ?? "—"} ({b.deep_model ?? "—"} / {b.quick_model ?? "—"})
              · {b.debate_rounds}/{b.risk_rounds} rounds
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className={`pill ${statusColor(b.status)}`}>{b.status}</span>
            {isActive && (
              <button className="btn btn-danger text-xs" onClick={() => { if (confirm("Cancel remaining runs?")) cancel.mutate(); }}>
                Cancel batch
              </button>
            )}
          </div>
        </div>
      </header>

      {/* progress bar */}
      <div className="card">
        <div className="flex items-center justify-between text-sm mb-2">
          <div>
            <span className="font-semibold">{doneCount}</span>
            <span className="text-muted"> / {b.total} done</span>
            {runningCount > 0 && <span className="text-accent ml-3">• {runningCount} running</span>}
            {queuedCount > 0 && <span className="text-muted ml-3">• {queuedCount} queued</span>}
            {errorCount > 0 && <span className="text-danger ml-3">• {errorCount} failed</span>}
          </div>
          <div className="text-xs text-muted">{progressPct}%</div>
        </div>
        <div className="h-2 bg-bg rounded overflow-hidden">
          <div
            className="h-full bg-accent transition-all"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs uppercase text-muted">
              <th className="text-left py-2 px-3 font-medium">Ticker</th>
              <th className="text-left py-2 px-3 font-medium">Status</th>
              <th className="text-left py-2 px-3 font-medium">Decision</th>
              <th className="text-right py-2 px-3 font-medium">Tokens</th>
              <th className="text-left py-2 px-3 font-medium">Started</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {b.runs.map((r) => (
              <tr key={r.run_id} className="border-t border-border">
                <td className="py-2 px-3 font-semibold">{r.ticker}</td>
                <td className="py-2 px-3">
                  <span className={`pill ${statusColor(r.status)}`}>{r.status}</span>
                </td>
                <td className={`py-2 px-3 font-semibold ${decisionColor(r.decision)}`}>{r.decision ?? "—"}</td>
                <td className="py-2 px-3 text-right text-muted">
                  {fmtTokens(r.tokens_in)}↑ / {fmtTokens(r.tokens_out)}↓
                </td>
                <td className="py-2 px-3 text-muted">{fmtDate(r.started_at)}</td>
                <td className="py-2 px-3 text-right">
                  {r.status === "done" || r.status === "error" ? (
                    <Link className="text-accent hover:underline" href={`/history/${r.run_id}`}>Open report →</Link>
                  ) : r.status === "running" ? (
                    <span className="text-xs text-muted">Streaming…</span>
                  ) : (
                    <span className="text-xs text-muted">Queued</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {errorCount > 0 && (
        <div className="card border-danger/30">
          <h3 className="font-semibold text-danger mb-2">Failed runs</h3>
          <ul className="text-sm space-y-1">
            {b.runs.filter((r) => r.status === "error").map((r) => (
              <li key={r.run_id}>
                <span className="font-semibold">{r.ticker}</span>{": "}
                <span className="text-muted">{r.error_message ?? "—"}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
