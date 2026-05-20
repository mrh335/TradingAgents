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

  // Server-side drainer state (auto-process light modes via Anthropic API)
  const drainer = useQuery({
    queryKey: ["drainer-status"],
    queryFn: () => RunQueue.drainerStatus(),
    refetchInterval: 30_000,
  });
  const toggleDrainer = useMutation({
    mutationFn: (enabled: boolean) => RunQueue.drainerToggle(enabled),
    onSuccess: () => drainer.refetch(),
  });
  const setDrainerModel = useMutation({
    mutationFn: (model: string) => RunQueue.drainerSetModel(model),
    onSuccess: () => drainer.refetch(),
  });
  const processNow = useMutation({
    mutationFn: (id: string) => RunQueue.processNow(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["run-queue"] }),
  });

  // Human labels for the model picker
  const MODEL_LABELS: Record<string, { label: string; cost: string }> = {
    "claude-haiku-4-5": { label: "Haiku 4.5 (cheapest, fast)", cost: "$1 / $5 per 1M tokens" },
    "claude-sonnet-4-5": { label: "Sonnet 4.5 (balanced)", cost: "$3 / $15 per 1M tokens" },
    "claude-opus-4-5": { label: "Opus 4.5 (smartest)", cost: "$15 / $75 per 1M tokens" },
  };

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
          You have <strong>three ways</strong> to drain the queue, listed
          cheapest first:
        </p>
        <ul className="text-muted text-xs mt-1 ml-4 list-disc space-y-0.5">
          <li>
            <strong className="text-success">Free</strong>: open Claude
            Desktop with the <code>tradingagents-analyze</code> skill and say
            &quot;process the run queue&quot;. Best for heavy{" "}
            <code>analyze</code> runs. Your CD subscription covers it — zero
            API tokens.
          </li>
          <li>
            <strong>Paid auto</strong>: enable the server-side drainer below.
            Polls every 5 min, processes light modes (
            <code>ask_portfolio</code>, <code>earnings_summary</code>) via the
            Anthropic API. Pick any model — Haiku is cents/day, Opus
            costs more. Opt-in by toggling on.
          </li>
          <li>
            <strong>Paid per-item</strong>: click ⚡ <strong>Process now</strong>{" "}
            on any pending row to handle it immediately via Anthropic API.
          </li>
        </ul>
      </header>

      {/* ─── Server-side auto-drain status + toggle ─── */}
      {drainer.data && (
        <div
          className={`card border-l-4 ${
            drainer.data.enabled
              ? "border-l-success"
              : drainer.data.anthropic_key_present
                ? "border-l-muted"
                : "border-l-warning"
          }`}
        >
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="font-semibold">
              {drainer.data.enabled
                ? "🟢 Server auto-drain ON (paid)"
                : "⚪ Server auto-drain off"}
            </span>
            <span className="text-xs text-muted">
              light modes: <code>{drainer.data.light_modes.join(", ")}</code> ·
              ticks every {drainer.data.interval_seconds}s
            </span>
            <button
              className="btn text-xs ml-auto"
              onClick={() => toggleDrainer.mutate(!drainer.data.enabled)}
              disabled={
                toggleDrainer.isPending || !drainer.data.anthropic_key_present
              }
              title={
                !drainer.data.anthropic_key_present
                  ? "Set ANTHROPIC_API_KEY on the api container to enable"
                  : ""
              }
            >
              {drainer.data.enabled ? "Turn off" : "Turn on"}
            </button>
          </div>

          {/* Model picker for the API path */}
          <div className="flex items-baseline gap-3 flex-wrap mt-3">
            <span className="text-xs text-muted">Model for API path:</span>
            <select
              className="input text-xs"
              value={drainer.data.model}
              onChange={(e) => setDrainerModel.mutate(e.target.value)}
              disabled={setDrainerModel.isPending}
            >
              {drainer.data.supported_models.map((m) => (
                <option key={m} value={m}>
                  {MODEL_LABELS[m]?.label ?? m}
                </option>
              ))}
            </select>
            <span className="text-xs text-muted">
              {MODEL_LABELS[drainer.data.model]?.cost ?? ""}
            </span>
          </div>

          {!drainer.data.anthropic_key_present && (
            <p className="text-xs text-warning mt-2">
              <strong>ANTHROPIC_API_KEY not set in the api container.</strong>{" "}
              Auto-drain + Process now both need it. Add to{" "}
              <code>/volume1/docker/tradingagents/.env</code> on the NAS and
              restart the api container. (Not needed if you only use the free
              Claude Desktop path.)
            </p>
          )}

          <p className="text-xs text-muted mt-2">
            <strong className="text-success">Free Claude Desktop path is
            unchanged</strong> — open CD, run the skill, drain everything in
            the queue (including heavy <code>analyze</code> runs) with zero
            tokens. This server-side drainer is a <em>second</em> option for
            light modes when you don&apos;t want to open CD, not a
            replacement. Heavy modes (analyze, deep_dive) are <em>never</em>{" "}
            processed by the server drainer — they always need CD or the
            local multi-agent pipeline.
          </p>
        </div>
      )}

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
                    {it.options?.batch_label && (
                      <div className="text-muted mt-0.5">
                        <span className="text-accent">⊞</span>{" "}
                        <code>{String(it.options.batch_label)}</code>
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
                  <td className="text-right whitespace-nowrap space-x-1">
                    {it.status === "pending" &&
                      drainer.data?.anthropic_key_present && (
                        <button
                          className="btn text-xs"
                          onClick={() => {
                            const isHeavy = !drainer.data!.light_modes.includes(it.mode);
                            const msg = isHeavy
                              ? `Process ${it.ticker} (${it.mode}) NOW via Anthropic API? Heavy modes like '${it.mode}' need the multi-agent pipeline which only runs in Claude Desktop — server-side processing will reject this. Continue anyway?`
                              : `Process ${it.ticker} (${it.mode}) NOW via Anthropic API (Haiku, ~$0.005 per call)?`;
                            if (confirm(msg)) processNow.mutate(it.id);
                          }}
                          disabled={processNow.isPending}
                          title="Process via Anthropic API immediately"
                        >
                          {processNow.isPending && processNow.variables === it.id
                            ? "Processing…"
                            : "⚡ Process now"}
                        </button>
                      )}
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
