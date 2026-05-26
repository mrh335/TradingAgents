"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Briefs, Regime } from "@/lib/api";
import { Markdown } from "@/components/Markdown";
import { mergeGlossary } from "@/lib/glossary";
import { useMemo, type ReactNode } from "react";
import type {
  Brief,
  EntryStep,
  ExitRule,
  KeyNumber,
  LongTermView,
  Trigger,
} from "@/lib/types";

// ──────────────────────────────────────────────────────────────────────
// BriefPanel — v2 tables-first rendering.
//
// Design goals (from user feedback 2026-05-20):
//   1. Lead with action_plain ("buy a starter position") in big text,
//      not the jargon-y decision ("Buy"/"Overweight").
//   2. Render structured data as TABLES, not prose blobs. Eyes scan
//      tables 10x faster than paragraphs.
//   3. Glossary tooltips on any technical term so an engineer doesn't
//      have to leave the page to look up "200-day SMA".
//   4. Graceful fallback: old briefs (pre-v2) still render, just
//      without the table benefits.
// ──────────────────────────────────────────────────────────────────────

// Map jargon decisions to plain-English fallbacks for briefs that
// don't have action_plain filled in (legacy briefs from before the
// schema change).
const DECISION_PLAIN_FALLBACK: Record<string, string> = {
  Buy: "buy a starter position",
  Overweight: "add more than usual — gradually scale up",
  Hold: "keep what you have, no new money",
  Underweight: "sell about half",
  Sell: "sell out completely",
};

const DECISION_TONE: Record<string, string> = {
  Buy: "text-success",
  Overweight: "text-success",
  Hold: "text-muted",
  Underweight: "text-warning",
  Sell: "text-danger",
};

const EXIT_KIND_LABEL: Record<string, { label: string; tone: string }> = {
  stop_loss: { label: "Stop loss", tone: "text-danger" },
  take_profit: { label: "Take profit", tone: "text-success" },
  time_based: { label: "Time-based", tone: "text-muted" },
  thesis_break: { label: "Thesis break", tone: "text-warning" },
};

export function BriefPanel({ runId }: { runId: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["brief", runId],
    queryFn: () => Briefs.get(runId),
    enabled: !!runId,
    refetchInterval: (q) => (q.state.data?.request_pending ? 8_000 : false),
  });
  const generate = useMutation({
    mutationFn: (force: boolean) => Briefs.generate(runId, force),
    onSuccess: (data) => qc.setQueryData(["brief", runId], data),
  });
  const requestCC = useMutation({
    mutationFn: () => Briefs.requestClaudeCode(runId),
    onSuccess: (data) => qc.setQueryData(["brief", runId], data),
  });
  const cancelCC = useMutation({
    mutationFn: () => Briefs.cancelClaudeCodeRequest(runId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["brief", runId] }),
  });

  const brief = q.data?.brief ?? null;
  const markdown = q.data?.markdown ?? null;
  const source = q.data?.source ?? null;
  const requestPending = q.data?.request_pending ?? false;

  if (q.isLoading) {
    return <div className="text-sm text-muted">Loading brief…</div>;
  }

  // -------- Empty state ------------------------------------------------
  if (!brief && !markdown) {
    if (requestPending) {
      return (
        <div className="card border-warning/40">
          <div className="text-sm">
            <span className="text-warning">⏳ Pending Claude Code analysis.</span>
            <span className="text-muted ml-2">
              A request file is sitting next to the archive. Open Claude Code in
              the repo and process it; this panel polls every 8s and will pick
              up the brief automatically.
            </span>
          </div>
          <div className="flex gap-2 mt-3">
            <button
              className="btn text-xs"
              onClick={() => cancelCC.mutate()}
              disabled={cancelCC.isPending}
            >
              Cancel request
            </button>
            <button
              className="btn btn-primary text-xs"
              onClick={() => generate.mutate(false)}
              disabled={generate.isPending}
              title="Falls back to the quick-think LLM — uses API tokens"
            >
              {generate.isPending ? "Generating…" : "Generate via API instead"}
            </button>
          </div>
        </div>
      );
    }
    return (
      <div className="card space-y-2">
        <div className="text-sm text-muted">No brief yet. Two options:</div>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn"
            onClick={() => requestCC.mutate()}
            disabled={requestCC.isPending}
            title="Drops a marker file next to the archive — handle in Claude Code, no API tokens used."
          >
            {requestCC.isPending
              ? "Requesting…"
              : "🤖 Request via Claude Code (no tokens)"}
          </button>
          <button
            className="btn btn-primary"
            onClick={() => generate.mutate(false)}
            disabled={generate.isPending}
            title="Calls the quick-think provider configured in Settings."
          >
            {generate.isPending ? "Generating…" : "✨ Generate via API"}
          </button>
        </div>
        {(generate.isError || requestCC.isError) && (
          <div className="text-sm text-danger">
            {(generate.error as Error)?.message ??
              (requestCC.error as Error)?.message}
          </div>
        )}
      </div>
    );
  }

  // -------- Markdown-only sidecar (Claude Code free-form) --------------
  if (!brief && markdown) {
    return (
      <div className="card space-y-3">
        <SourceBadge source={source} />
        <Markdown>{markdown}</Markdown>
        <div className="flex justify-end">
          <button
            className="btn text-xs"
            onClick={() => generate.mutate(true)}
            disabled={generate.isPending}
          >
            🔄 Replace with API-generated structured brief
          </button>
        </div>
      </div>
    );
  }

  // -------- Structured Brief (v2) -------------------------------------
  const b = brief!;
  const glossary = b.jargon_glossary || {};
  const hasV2 =
    (b.entry_plan && b.entry_plan.length > 0) ||
    (b.exit_plan && b.exit_plan.length > 0) ||
    (b.key_numbers && b.key_numbers.length > 0);

  const actionPlain =
    b.action_plain ||
    DECISION_PLAIN_FALLBACK[b.decision] ||
    "(action mapping unavailable — see decision)";

  return (
    <div className="card space-y-4">
      {/* ─── Header: plain-English action + rating + tldr ─── */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1">
          <div className="text-xs uppercase tracking-wider text-muted">
            What to do
          </div>
          <div className="text-2xl font-bold leading-tight">
            {actionPlain}
          </div>
          <div className="text-xs text-muted mt-1">
            (rating:{" "}
            <span className={`font-semibold ${DECISION_TONE[b.decision] || ""}`}>
              {b.decision}
            </span>
            {!b.action_plain && (
              <span className="ml-2 text-warning">
                ⚠ legacy brief — re-request to get the new tables
              </span>
            )}
            )
          </div>
          <div className="text-sm mt-3">
            <RenderWithGlossary text={b.tldr} glossary={glossary} />
          </div>
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          <SourceBadge source={source} />
          <button
            className="btn text-xs"
            onClick={() => requestCC.mutate()}
            disabled={requestCC.isPending}
            title="Drop a request marker — Claude Code will rewrite the brief sidecar, no API tokens used."
          >
            🤖 Re-request via Claude Code
          </button>
          <button
            className="btn text-xs"
            onClick={() => generate.mutate(true)}
            disabled={generate.isPending}
            title="Replace with a fresh LLM-generated brief (uses API tokens)"
          >
            ✨ Regenerate via API
          </button>
        </div>
      </div>

      {/* ─── Regime context badge ─── */}
      <RegimeBadge runId={runId} />

      {/* ─── Quick facts strip ─── */}
      <div className="grid grid-cols-2 md:grid-cols-2 gap-3 text-sm bg-surface rounded p-3">
        <Field label="Timeframe" value={b.timeframe} glossary={glossary} />
        <Field
          label="Position size"
          value={b.position_size}
          glossary={glossary}
        />
      </div>

      {/* ─── Key numbers table (v2) ─── */}
      {b.key_numbers && b.key_numbers.length > 0 && (
        <KeyNumbersTable rows={b.key_numbers} glossary={glossary} />
      )}

      {/* ─── Entry plan table (v2) or prose fallback ─── */}
      {b.entry_plan && b.entry_plan.length > 0 ? (
        <EntryPlanTable rows={b.entry_plan} glossary={glossary} />
      ) : (
        <Field
          label="How to enter"
          value={b.entry_strategy}
          glossary={glossary}
          block
        />
      )}

      {/* ─── Exit plan table (v2) or prose fallback ─── */}
      {b.exit_plan && b.exit_plan.length > 0 ? (
        <ExitPlanTable rows={b.exit_plan} glossary={glossary} />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
          <Field label="Stop loss" value={b.stop_loss} glossary={glossary} block />
          <Field
            label="Take profit"
            value={b.take_profit}
            glossary={glossary}
            block
          />
        </div>
      )}

      {/* ─── Triggers table ─── */}
      {b.triggers && b.triggers.length > 0 && (
        <TriggersTable rows={b.triggers} glossary={glossary} />
      )}

      {/* ─── Key risks ─── */}
      {b.key_risks && b.key_risks.length > 0 && (
        <RisksTable
          rows={b.key_risks}
          glossary={glossary}
          title={
            b.long_term_view
              ? "What could go wrong (short-term setbacks)"
              : "What could go wrong"
          }
        />
      )}

      {/* ─── vs SPY ─── */}
      {b.benchmark_view && (
        <div className="text-sm border-t border-border pt-3">
          <span className="text-muted">vs S&amp;P 500: </span>
          <RenderWithGlossary text={b.benchmark_view} glossary={glossary} />
        </div>
      )}

      {/* ─── Long-term-investor lens (v3) ─── */}
      {b.long_term_view && (
        <LongTermLens view={b.long_term_view} glossary={glossary} />
      )}

      {/* ─── Glossary footer ─── */}
      {Object.keys(glossary).length > 0 && (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer hover:text-fg">
            Glossary ({Object.keys(glossary).length} terms)
          </summary>
          <dl className="mt-2 space-y-1">
            {Object.entries(glossary).map(([term, def]) => (
              <div key={term} className="grid grid-cols-[150px_1fr] gap-2">
                <dt className="font-semibold">{term}</dt>
                <dd>{def}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}

      {!hasV2 && (
        <div className="text-xs text-muted border-t border-border pt-2">
          This is a legacy brief without structured tables. Click{" "}
          <strong>Re-request via Claude Code</strong> or <strong>Regenerate via API</strong>{" "}
          above to get the v2 tables-first format.
        </div>
      )}
    </div>
  );
}

// ─── Sub-components ─────────────────────────────────────────────────

function SourceBadge({ source }: { source: string | null | undefined }) {
  if (!source) return null;
  const LABELS: Record<string, { text: string; cls: string; help: string }> = {
    sidecar: {
      text: "🤖 from Claude Code",
      cls: "bg-success/15 text-success",
      help: "Loaded from a brief.json sidecar — no API tokens used.",
    },
    markdown_sidecar: {
      text: "🤖 Claude Code (markdown)",
      cls: "bg-success/15 text-success",
      help: "Free-form markdown sidecar — no API tokens used.",
    },
    llm: {
      text: "✨ from API",
      cls: "bg-accent/15 text-accent",
      help: "Generated by the quick-think provider — used API tokens.",
    },
  };
  const m = LABELS[source];
  if (!m) return null;
  return (
    <span className={`pill ${m.cls}`} title={m.help}>
      {m.text}
    </span>
  );
}

function Field({
  label,
  value,
  glossary,
  block = false,
}: {
  label: string;
  value: string;
  glossary: Record<string, string>;
  block?: boolean;
}) {
  return (
    <div className={block ? "" : ""}>
      <div className="text-xs uppercase tracking-wider text-muted">{label}</div>
      <div className="text-sm">
        <RenderWithGlossary text={value} glossary={glossary} />
      </div>
    </div>
  );
}

function KeyNumbersTable({
  rows,
  glossary,
}: {
  rows: KeyNumber[];
  glossary: Record<string, string>;
}) {
  return (
    <section>
      <div className="font-semibold text-sm mb-1">Key numbers at a glance</div>
      <table className="w-full text-sm bg-surface rounded overflow-hidden">
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-border first:border-t-0">
              <td className="py-1.5 px-3 text-muted">
                <RenderWithGlossary text={r.label} glossary={glossary} />
              </td>
              <td className="py-1.5 px-3 text-right font-mono tabular-nums">
                {r.value}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function EntryPlanTable({
  rows,
  glossary,
}: {
  rows: EntryStep[];
  glossary: Record<string, string>;
}) {
  return (
    <section>
      <div className="font-semibold text-sm mb-1">How to enter the position</div>
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-muted">
          <tr>
            <th className="py-1.5 px-2">Step</th>
            <th className="px-2">When</th>
            <th className="px-2">Price</th>
            <th className="px-2">Size</th>
            <th className="px-2">Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-border align-top">
              <td className="py-1.5 px-2 font-semibold">{r.label}</td>
              <td className="px-2">
                <RenderWithGlossary text={r.when} glossary={glossary} />
              </td>
              <td className="px-2 font-mono tabular-nums">{r.price || "—"}</td>
              <td className="px-2 font-mono tabular-nums">{r.size_pct || "—"}</td>
              <td className="px-2 text-xs text-muted">{r.notes || ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function ExitPlanTable({
  rows,
  glossary,
}: {
  rows: ExitRule[];
  glossary: Record<string, string>;
}) {
  return (
    <section>
      <div className="font-semibold text-sm mb-1">How to exit the position</div>
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-muted">
          <tr>
            <th className="py-1.5 px-2">Type</th>
            <th className="px-2">Condition</th>
            <th className="px-2">Price</th>
            <th className="px-2">What to do</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const meta = EXIT_KIND_LABEL[r.kind] || {
              label: r.kind,
              tone: "text-muted",
            };
            return (
              <tr key={i} className="border-t border-border align-top">
                <td className={`py-1.5 px-2 font-semibold ${meta.tone}`}>
                  {meta.label}
                </td>
                <td className="px-2">
                  <RenderWithGlossary text={r.condition} glossary={glossary} />
                </td>
                <td className="px-2 font-mono tabular-nums">
                  {r.price || "—"}
                </td>
                <td className="px-2 font-semibold">
                  {r.action}
                  {r.notes && (
                    <div className="text-xs text-muted font-normal">{r.notes}</div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function TriggersTable({
  rows,
  glossary,
}: {
  rows: Trigger[];
  glossary: Record<string, string>;
}) {
  return (
    <section>
      <div className="font-semibold text-sm mb-1">Things to watch for</div>
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-muted">
          <tr>
            <th className="py-1.5 px-2">If this happens</th>
            <th className="px-2">Then do this</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t, i) => (
            <tr key={i} className="border-t border-border align-top">
              <td className="py-1.5 px-2">
                <span className="text-warning font-semibold">IF</span>{" "}
                <RenderWithGlossary text={t.condition} glossary={glossary} />
              </td>
              <td className="px-2">
                <span className="text-accent font-semibold">→</span>{" "}
                <RenderWithGlossary text={t.action} glossary={glossary} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function RisksTable({
  rows,
  glossary,
  title = "What could go wrong",
}: {
  rows: string[];
  glossary: Record<string, string>;
  title?: string;
}) {
  return (
    <section>
      <div className="font-semibold text-sm mb-1">{title}</div>
      <ul className="text-sm space-y-1 list-disc list-inside text-muted">
        {rows.map((r, i) => (
          <li key={i}>
            <RenderWithGlossary text={r} glossary={glossary} />
          </li>
        ))}
      </ul>
    </section>
  );
}

const CORE_POSITION_LABEL: Record<string, { label: string; tone: string; help: string }> = {
  core_position: {
    label: "Core holding",
    tone: "text-success",
    help: "Worth holding as a permanent part of your portfolio — don't sell on short-term noise.",
  },
  satellite: {
    label: "Satellite",
    tone: "text-accent",
    help: "Worth holding but not core — could be cycled out for better opportunities later.",
  },
  avoid_long_term: {
    label: "Not for long-term",
    tone: "text-warning",
    help: "Don't hold this for years. Could be a short-term trade, but better long-term plays exist elsewhere.",
  },
  unclear: {
    label: "Unclear",
    tone: "text-muted",
    help: "The multi-year thesis isn't strong enough either way — needs more time or data.",
  },
};

function LongTermLens({
  view,
  glossary,
}: {
  view: LongTermView;
  glossary: Record<string, string>;
}) {
  const core = CORE_POSITION_LABEL[view.core_position] || {
    label: view.core_position,
    tone: "text-muted",
    help: "",
  };
  return (
    <section className="border-t-2 border-accent/40 pt-4 mt-4">
      <div className="flex items-baseline gap-2 mb-2 flex-wrap">
        <h2 className="text-lg font-semibold">For long-term investors</h2>
        <span className="text-xs text-muted">
          (1-3 year horizon — review quarterly, not daily)
        </span>
      </div>

      {/* Thesis paragraph */}
      <div className="text-sm mb-3">
        <RenderWithGlossary text={view.thesis_summary} glossary={glossary} />
      </div>

      {/* Status row + key facts table */}
      <table className="w-full text-sm bg-surface rounded overflow-hidden mb-3">
        <tbody>
          <tr>
            <td className="py-1.5 px-3 text-muted w-1/3">Worth holding long-term?</td>
            <td className="py-1.5 px-3">
              <span
                className={`font-semibold ${core.tone}`}
                title={core.help}
              >
                {core.label}
              </span>
              <span className="text-xs text-muted ml-2">— {core.help}</span>
            </td>
          </tr>
          <tr className="border-t border-border">
            <td className="py-1.5 px-3 text-muted">Multi-year outlook</td>
            <td className="py-1.5 px-3">
              <RenderWithGlossary
                text={view.multi_year_horizon}
                glossary={glossary}
              />
            </td>
          </tr>
          {view.accumulation_plan && (
            <tr className="border-t border-border">
              <td className="py-1.5 px-3 text-muted">How to build position</td>
              <td className="py-1.5 px-3">
                <RenderWithGlossary
                  text={view.accumulation_plan}
                  glossary={glossary}
                />
              </td>
            </tr>
          )}
          <tr className="border-t border-border">
            <td className="py-1.5 px-3 text-muted">When to revisit</td>
            <td className="py-1.5 px-3">
              <RenderWithGlossary text={view.review_cadence} glossary={glossary} />
            </td>
          </tr>
        </tbody>
      </table>

      {/* Structural risks — multi-year, not daily wiggles */}
      {view.structural_risks && view.structural_risks.length > 0 && (
        <div>
          <div className="font-semibold text-sm mb-1">
            What could break the multi-year story
          </div>
          <ul className="text-sm space-y-1 list-disc list-inside text-muted">
            {view.structural_risks.map((r, i) => (
              <li key={i}>
                <RenderWithGlossary text={r} glossary={glossary} />
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-xs text-muted mt-3 italic">
        Don&apos;t base long-term decisions on the daily-price tables above —
        those are for active traders. This section is the right lens for
        buy-and-hold investors who can&apos;t (or don&apos;t want to) trade
        daily.
      </p>
    </section>
  );
}

// Renders text with auto-detected glossary tooltips. Uses the merged
// lookup: global finance glossary (lib/glossary.ts) + any brief-specific
// terms the LLM included in jargon_glossary. Longest-match-first so
// "200-day SMA" wins over "SMA" when both are defined.
//
// The `glossary` param is just the brief-specific overrides — global
// terms are always available without the LLM having to know them.
function RenderWithGlossary({
  text,
  glossary,
}: {
  text: string;
  glossary: Record<string, string>;
}) {
  const lookup = useMemo(() => mergeGlossary(glossary), [glossary]);
  if (!text) return null;
  const phrases = Array.from(lookup.keys()).sort(
    (a, b) => b.length - a.length,
  );
  if (phrases.length === 0) return <>{text}</>;

  const escaped = phrases.map((p) =>
    p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
  );
  // Require non-alphanumeric on both edges so "pe" inside "pearl" doesn't match.
  const pattern = `(?<![A-Za-z0-9])(${escaped.join("|")})(?![A-Za-z0-9])`;
  let re: RegExp;
  try {
    re = new RegExp(pattern, "gi");
  } catch {
    return <>{text}</>;
  }

  const parts: ReactNode[] = [];
  let lastIdx = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > lastIdx) parts.push(text.slice(lastIdx, m.index));
    const matched = m[0];
    const found = lookup.get(matched.toLowerCase());
    if (found) {
      parts.push(
        <span
          key={key++}
          className="underline decoration-dotted decoration-accent cursor-help"
          title={found.entry.definition}
        >
          {matched}
        </span>,
      );
    } else {
      parts.push(matched);
    }
    lastIdx = m.index + matched.length;
  }
  if (lastIdx < text.length) parts.push(text.slice(lastIdx));

  return <>{parts}</>;
}


// ──────────────────────────────────────────────────────────────────────
// RegimeBadge — calibration context on every brief.
//
// Surfaces "this recommendation was made in {regime}; framework's
// historical hit rate in this regime is {X}% vs {baseline}%".
// Tells the user to up-weight or down-weight conviction based on
// regime, without changing the underlying recommendation.
// ──────────────────────────────────────────────────────────────────────

const REGIME_BORDER_TONE: Record<string, string> = {
  CALM_BULL: "border-l-success",
  VOLATILE_BULL: "border-l-warning",
  VOLATILE_BEAR: "border-l-danger",
  CALM_BEAR: "border-l-muted",
};

function RegimeBadge({ runId }: { runId: string }) {
  const q = useQuery({
    queryKey: ["regime-for-run", runId],
    queryFn: () => Regime.forRun(runId),
    enabled: !!runId,
    staleTime: 60 * 60_000,  // 1h — regime data is stable
  });

  if (q.isLoading || !q.data || !q.data.regime) return null;

  const d = q.data;
  const border = REGIME_BORDER_TONE[d.regime!] ?? "border-l-muted";

  // Compute the conviction adjustment based on hit-rate delta.
  let adjustment: ReactNode = null;
  if (d.regime_hit_rate_pct !== null && d.baseline_hit_rate_pct !== null) {
    const delta = d.regime_hit_rate_pct - d.baseline_hit_rate_pct;
    if (delta >= 5) {
      adjustment = (
        <span className="text-success font-semibold">
          ↑ Up-weight conviction — framework excels in this regime (+{delta.toFixed(0)}pp vs baseline)
        </span>
      );
    } else if (delta <= -5) {
      adjustment = (
        <span className="text-danger font-semibold">
          ↓ Down-weight conviction — framework underperforms in this regime ({delta.toFixed(0)}pp vs baseline)
        </span>
      );
    } else {
      adjustment = (
        <span className="text-muted">
          Framework performance in this regime is near baseline ({delta > 0 ? "+" : ""}{delta.toFixed(0)}pp)
        </span>
      );
    }
  }

  return (
    <div className={`card border-l-4 ${border} text-sm`}>
      <div className="flex items-baseline gap-3 flex-wrap">
        <span className="text-xs uppercase tracking-wider text-muted">
          Market regime
        </span>
        <span className="font-semibold">{d.regime_label ?? d.regime}</span>
        {d.regime_hit_rate_pct !== null && (
          <span className="text-xs text-muted">
            framework hit rate in this regime:{" "}
            <strong>{d.regime_hit_rate_pct}%</strong>
            {d.baseline_hit_rate_pct !== null && (
              <> (baseline: {d.baseline_hit_rate_pct}%)</>
            )}
            {d.regime_calls_count !== null && d.regime_calls_count > 0 && (
              <> over {d.regime_calls_count} historical calls</>
            )}
          </span>
        )}
      </div>
      {adjustment && <div className="mt-1.5 text-xs">{adjustment}</div>}
      {d.regime_blurb && (
        <div className="text-xs text-muted mt-1.5">{d.regime_blurb}</div>
      )}
    </div>
  );
}
