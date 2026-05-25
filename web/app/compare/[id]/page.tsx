"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Compare, type CompareRowState } from "@/lib/api";

// ──────────────────────────────────────────────────────────────────────
// /compare/[id] — side-by-side view of a model comparison.
//
// Polls every 5s while runs are still pending/claimed, then settles
// to a 30s refresh once everything is complete. Each model is a card
// in a horizontal grid; cards fill in as runs finish.
// ──────────────────────────────────────────────────────────────────────

const STATUS_LABEL: Record<string, string> = {
  pending: "⏳ Waiting in queue",
  in_progress: "🤖 Some runs in progress",
  partial: "◐ Partially complete",
  complete: "✓ All runs complete",
};

const DECISION_TONE: Record<string, string> = {
  Buy: "bg-success/20 text-success border-success",
  Overweight: "bg-success/10 text-success border-success",
  Hold: "bg-muted/20 text-muted border-muted",
  Underweight: "bg-warning/20 text-warning border-warning",
  Sell: "bg-danger/20 text-danger border-danger",
};

const QUEUE_STATUS_LABEL: Record<string, string> = {
  pending: "⏳ Queued",
  claimed: "🤖 Running",
  done: "✓ Done",
  error: "⚠ Error",
  cancelled: "✕ Cancelled",
};

function fmtNum(n: number | null): string {
  if (n === null || n === undefined) return "—";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

// Rough per-1M-token pricing by provider/model family. Used for relative
// cost cues, NOT billing — actual cost depends on tier + cache hits and
// is on your provider invoice. Returns null when we don't know the
// pricing (instead of guessing, which used to mislabel Ollama runs as
// costing money when they're actually free + local).
function pricingFor(provider: string | null, model: string | null): { inUsd: number; outUsd: number } | null {
  if (!provider) return null;
  const p = provider.toLowerCase();
  const m = (model ?? "").toLowerCase();
  if (p === "ollama") return null;           // free + local — return null → "free"
  if (p === "anthropic") {
    if (m.includes("opus")) return { inUsd: 15, outUsd: 75 };
    if (m.includes("sonnet")) return { inUsd: 3, outUsd: 15 };
    if (m.includes("haiku")) return { inUsd: 1, outUsd: 5 };
    return { inUsd: 3, outUsd: 15 };
  }
  if (p === "openai") {
    if (m.includes("gpt-5")) return { inUsd: 10, outUsd: 30 };
    if (m.includes("o1-mini")) return { inUsd: 3, outUsd: 12 };
    if (m.includes("o1")) return { inUsd: 15, outUsd: 60 };
    if (m.includes("gpt-4o-mini")) return { inUsd: 0.15, outUsd: 0.6 };
    if (m.includes("gpt-4o")) return { inUsd: 2.5, outUsd: 10 };
    if (m.includes("gpt-4")) return { inUsd: 10, outUsd: 30 };
    return { inUsd: 2.5, outUsd: 10 };
  }
  if (p === "google") {
    if (m.includes("flash")) return { inUsd: 0.075, outUsd: 0.3 };
    return { inUsd: 1.25, outUsd: 5 };
  }
  return null;
}

function fmtCostUsd(
  tokensIn: number | null,
  tokensOut: number | null,
  provider: string | null,
  model: string | null,
): string | null {
  if (!tokensIn && !tokensOut) return null;
  const p = (provider ?? "").toLowerCase();
  if (p === "ollama") return "free (local)";
  const price = pricingFor(provider, model);
  if (!price) return null;
  const cost =
    ((tokensIn ?? 0) / 1_000_000) * price.inUsd +
    ((tokensOut ?? 0) / 1_000_000) * price.outUsd;
  return `~$${cost.toFixed(3)}`;
}

export default function ComparisonDetailPage() {
  const params = useParams<{ id: string }>();
  const cid = params.id;
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["compare", cid],
    queryFn: () => Compare.get(cid),
    enabled: !!cid,
    refetchInterval: (q) => {
      const data = q.state.data;
      if (!data) return 5_000;
      return data.overall_status === "complete" ? 30_000 : 5_000;
    },
  });

  const retry = useMutation({
    mutationFn: () => Compare.retryFailed(cid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["compare", cid] }),
  });

  if (q.isLoading) return <div className="text-muted">Loading comparison…</div>;
  if (!q.data) return <div className="text-danger">Comparison not found.</div>;
  const d = q.data;

  // Count failed/cancelled rows so we can show the retry button when relevant.
  const failedCount = d.rows.filter(
    (r) => r.queue_status === "error" || r.queue_status === "cancelled",
  ).length;

  return (
    <div className="space-y-6">
      <header>
        <div className="flex items-baseline gap-3 flex-wrap">
          <Link href="/compare" className="text-sm text-accent hover:underline">
            ← All comparisons
          </Link>
          <h1 className="text-2xl font-bold">
            {d.ticker} · {d.trade_date}
          </h1>
          <span className="text-xs text-muted">
            comparison_id <code>{d.comparison_id}</code>
          </span>
        </div>
        <p className="text-muted text-sm mt-1">
          {STATUS_LABEL[d.overall_status]} ·{" "}
          {d.agreement.completed_runs}/{d.agreement.total_runs} runs done
        </p>
        {failedCount > 0 && (
          <div className="mt-2">
            <button
              className="btn text-xs"
              onClick={() => retry.mutate()}
              disabled={retry.isPending}
              title="Reset the failed/cancelled queue items back to pending. The server-side drainer will pick them up — Ollama items dispatch within ~5 min, paid providers need auto-drain on."
            >
              {retry.isPending
                ? "Resetting…"
                : retry.isSuccess
                  ? `Reset ${retry.data.items_reset} items — drainer picks them up next tick`
                  : `↻ Retry ${failedCount} failed run${failedCount === 1 ? "" : "s"}`}
            </button>
          </div>
        )}
      </header>

      {/* ─── Agreement summary ─── */}
      {d.agreement.completed_runs > 0 && (
        <div
          className={`card border-l-4 ${
            d.agreement.consensus
              ? DECISION_TONE[d.agreement.consensus] ?? "border-l-muted"
              : "border-l-warning"
          }`}
        >
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="text-xs uppercase text-muted">Consensus</span>
            {d.agreement.consensus ? (
              <>
                <span className="text-2xl font-bold">{d.agreement.consensus}</span>
                <span className="text-sm text-muted">
                  ({d.agreement.consensus_strength_pct}% of completed runs agree)
                </span>
              </>
            ) : (
              <span className="text-lg font-semibold text-warning">
                Split decision — no majority
              </span>
            )}
          </div>
          <div className="text-sm mt-2 flex gap-3 flex-wrap">
            {Object.entries(d.agreement.decisions).map(([decision, count]) => (
              <span key={decision} className="text-muted">
                <strong className={DECISION_TONE[decision]?.split(" ")[1] ?? ""}>
                  {decision}
                </strong>: {count}
              </span>
            ))}
          </div>
          {d.agreement.outliers.length > 0 && (
            <p className="text-sm mt-2">
              <strong>Outliers:</strong> {d.agreement.outliers.join(", ")} —
              worth reading their briefs to understand the disagreement.
            </p>
          )}
        </div>
      )}

      {/* ─── Per-model cards ─── */}
      <section
        className="grid gap-4"
        style={{
          gridTemplateColumns: `repeat(${Math.min(d.rows.length, 3)}, minmax(0, 1fr))`,
        }}
      >
        {d.rows.map((row, i) => (
          <ModelCard key={i} row={row} consensus={d.agreement.consensus} />
        ))}
      </section>

      {/* ─── Footer ─── */}
      <div className="card text-xs text-muted">
        <strong>How to read this:</strong> each card is the same ticker on the
        same date, with only the LLM model varying. Identical decisions across
        models = high confidence signal. Mixed decisions = the data is
        genuinely ambiguous and you should read the outlier briefs to
        understand what each model weighted differently.
        <br />
        <br />
        <strong>About cost:</strong> per-model pricing is now applied (Haiku
        / Sonnet / Opus / GPT-5 / etc. each priced separately); the dollar
        figure is a relative cue, not your bill. Ollama runs show{" "}
        <em>free (local)</em> since they execute on your own hardware.
      </div>
    </div>
  );
}

// ─── One model card ─────────────────────────────────────────────────

function ModelCard({
  row,
  consensus,
}: {
  row: CompareRowState;
  consensus: string | null;
}) {
  const isOutlier =
    consensus !== null && row.decision !== null && row.decision !== consensus;

  return (
    <div
      className={`card space-y-3 ${
        isOutlier ? "border-l-4 border-l-warning" : ""
      }`}
    >
      {/* Header */}
      <div>
        <div className="font-semibold text-lg">{row.label}</div>
        <div className="text-xs text-muted">
          {row.provider} / {row.deep_model}
        </div>
        {isOutlier && (
          <div className="text-xs text-warning mt-1 font-semibold">
            ⚠ outlier vs consensus ({consensus})
          </div>
        )}
      </div>

      {/* Status / decision */}
      {row.queue_status && row.queue_status !== "done" && (
        <div className="text-sm">
          {QUEUE_STATUS_LABEL[row.queue_status] ?? row.queue_status}
          {row.queue_error && (
            <div className="text-xs text-danger mt-1">{row.queue_error}</div>
          )}
        </div>
      )}

      {row.decision && (
        <div>
          <div className="text-xs uppercase text-muted">Decision</div>
          <div
            className={`text-2xl font-bold ${
              DECISION_TONE[row.decision]?.split(" ")[1] ?? ""
            }`}
          >
            {row.decision}
          </div>
        </div>
      )}

      {/* Brief excerpt */}
      {row.brief_tldr && (
        <div>
          <div className="text-xs uppercase text-muted">TL;DR</div>
          <div className="text-sm">{row.brief_tldr}</div>
        </div>
      )}

      {(row.brief_timeframe || row.brief_position_size) && (
        <div className="grid grid-cols-2 gap-2 text-xs">
          {row.brief_timeframe && (
            <div>
              <div className="text-muted">Timeframe</div>
              <div>{row.brief_timeframe}</div>
            </div>
          )}
          {row.brief_position_size && (
            <div>
              <div className="text-muted">Position size</div>
              <div>{row.brief_position_size}</div>
            </div>
          )}
        </div>
      )}

      {(row.brief_entry_strategy || row.brief_stop_loss || row.brief_take_profit) && (
        <div className="text-xs space-y-1">
          {row.brief_entry_strategy && (
            <div>
              <span className="text-muted">Entry: </span>
              <span>{row.brief_entry_strategy}</span>
            </div>
          )}
          {row.brief_stop_loss && (
            <div>
              <span className="text-muted">Stop: </span>
              <span>{row.brief_stop_loss}</span>
            </div>
          )}
          {row.brief_take_profit && (
            <div>
              <span className="text-muted">Take profit: </span>
              <span>{row.brief_take_profit}</span>
            </div>
          )}
        </div>
      )}

      {row.brief_benchmark_view && (
        <div className="text-xs">
          <span className="text-muted">vs SPY: </span>
          <span>{row.brief_benchmark_view}</span>
        </div>
      )}

      {(row.trigger_count !== null || row.risk_count !== null) && (
        <div className="text-xs text-muted">
          {row.trigger_count !== null && <>{row.trigger_count} triggers · </>}
          {row.risk_count !== null && <>{row.risk_count} risks</>}
        </div>
      )}

      {/* Run-level stats */}
      {row.queue_status === "done" && (
        <div className="text-xs text-muted border-t border-border pt-2">
          {row.llm_calls !== null && <>{row.llm_calls} LLM calls</>}
          {row.tool_calls !== null && <> · {row.tool_calls} tool calls</>}
          {(row.tokens_in || row.tokens_out) && (
            <>
              <br />
              {fmtNum(row.tokens_in)} in / {fmtNum(row.tokens_out)} out
              {fmtCostUsd(row.tokens_in, row.tokens_out, row.provider, row.deep_model) && (
                <>
                  {" "}
                  ({fmtCostUsd(row.tokens_in, row.tokens_out, row.provider, row.deep_model)})
                </>
              )}
            </>
          )}
        </div>
      )}

      {row.run_id && (
        <Link
          href={`/history/${row.run_id}`}
          className="btn text-xs"
          title="Open the full run analysis"
        >
          Full run details →
        </Link>
      )}
    </div>
  );
}
