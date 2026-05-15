"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Briefs } from "@/lib/api";
import { Markdown } from "@/components/Markdown";

export function BriefPanel({ runId }: { runId: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["brief", runId],
    queryFn: () => Briefs.get(runId),
    enabled: !!runId,
    // Poll while a Claude-Code request is pending so the sidecar drop is
    // picked up automatically — every 8s is light enough.
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
        <div className="text-sm text-muted">
          No brief yet. Two options:
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn"
            onClick={() => requestCC.mutate()}
            disabled={requestCC.isPending}
            title="Drops a marker file next to the archive — handle in Claude Code, no API tokens used."
          >
            {requestCC.isPending ? "Requesting…" : "🤖 Request via Claude Code (no tokens)"}
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
            {(generate.error as Error)?.message ?? (requestCC.error as Error)?.message}
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

  // -------- Structured Brief ------------------------------------------
  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-3xl font-bold tracking-tight">{brief!.decision}</div>
          <div className="text-sm leading-relaxed mt-1">{brief!.tldr}</div>
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          <SourceBadge source={source} />
          <button
            className="btn text-xs"
            onClick={() => generate.mutate(true)}
            disabled={generate.isPending}
            title="Replace with a fresh LLM-generated brief"
          >
            🔄 Regenerate via API
          </button>
        </div>
      </div>

      <div className="grid sm:grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <Field label="Timeframe" value={brief!.timeframe} />
        <Field label="Position size" value={brief!.position_size} />
        <Field label="Entry" value={brief!.entry_strategy} />
        <Field label="Stop loss" value={brief!.stop_loss} />
        <Field label="Take profit" value={brief!.take_profit} />
      </div>

      <div>
        <div className="font-semibold text-sm mb-1">Trigger points</div>
        <ul className="text-sm space-y-1">
          {brief!.triggers.map((t, i) => (
            <li key={i}>
              <span className="text-warning">If</span> {t.condition}{" "}
              <span className="text-accent">→</span> {t.action}
            </li>
          ))}
        </ul>
      </div>

      <div>
        <div className="font-semibold text-sm mb-1">Key risks</div>
        <ul className="text-sm space-y-1 list-disc list-inside text-muted">
          {brief!.key_risks.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      </div>

      <div className="text-sm border-t border-border pt-3">
        <span className="text-muted">vs S&amp;P 500: </span>
        {brief!.benchmark_view}
      </div>
    </div>
  );
}

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
    <span className={`pill ${m.cls}`} title={m.help}>{m.text}</span>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-muted">{label}</div>
      <div>{value}</div>
    </div>
  );
}
