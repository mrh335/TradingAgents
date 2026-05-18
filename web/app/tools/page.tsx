"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RunQueue } from "@/lib/api";

type ToolMode =
  | "analyze"
  | "brief"
  | "refresh"
  | "news_fetch"
  | "deep_dive"
  | "earnings_recap"
  | "screener_query"
  | "portfolio_review";

const TOOLS: Array<{
  mode: ToolMode;
  title: string;
  description: string;
  takesTicker: boolean;
  example?: string;
}> = [
  {
    mode: "analyze",
    title: "Full analysis",
    description:
      "Complete multi-agent run: 4 analysts → bull/bear debate → research mgr → trader → 3-way risk debate → PM. Produces an archive + brief.",
    takesTicker: true,
    example: "Standard recommendation flow — pick this when you want a full take on a ticker.",
  },
  {
    mode: "refresh",
    title: "Incremental refresh",
    description:
      "Analysis with memory mode forced to incremental — Portfolio Manager sees prior decisions and only adjusts for new information.",
    takesTicker: true,
    example: "Faster than a full fresh run; use between scheduled fresh runs.",
  },
  {
    mode: "brief",
    title: "Brief regeneration",
    description:
      "Regenerate the plain-English brief for an existing run's analysis without re-running the multi-agent pipeline. Costs zero LLM tokens.",
    takesTicker: true,
    example: "Useful when you've updated the brief vocabulary rules in CLAUDE.md.",
  },
  {
    mode: "news_fetch",
    title: "News pulse",
    description:
      "Pull the latest news + sentiment for a ticker and post a sidecar markdown report. Quick read on what's moving.",
    takesTicker: true,
    example: "Use before manually deciding to size up or trim.",
  },
  {
    mode: "deep_dive",
    title: "Deep dive memo",
    description:
      "Long-form research memo on a ticker — uses fundamentals, news, insider data, analyst targets, but skips the bull/bear debate. Cheaper than a full analysis but richer than a news pulse.",
    takesTicker: true,
  },
  {
    mode: "earnings_recap",
    title: "Earnings recap",
    description:
      "Summarise the most recent earnings call + post-print reaction. Best run within a week after earnings.",
    takesTicker: true,
  },
  {
    mode: "portfolio_review",
    title: "Portfolio health check",
    description:
      "Across-book review synthesizing current positions + latest briefs + restrictions + risk metrics. No ticker — works on your whole book.",
    takesTicker: false,
  },
  {
    mode: "screener_query",
    title: "Screener query",
    description:
      "Run a custom screen against the universe (P/E, market cap, dividend, beta, sector filters). Returns ranked candidates.",
    takesTicker: false,
  },
];

const SUGGESTED_INSTRUCTIONS_PER_MODE: Partial<Record<ToolMode, string>> = {
  news_fetch:
    "Focus on news from the last 7 days. Include sentiment polarity and a one-line summary per headline. Flag anything that could be material to the next 30 days.",
  deep_dive:
    "Build a 1,500-word research memo: company overview, recent fundamentals trend, competitive position, key risks, valuation context.",
  earnings_recap:
    "Cover: revenue + EPS beat/miss vs consensus, guidance changes, management tone on the call, key sell-side questions, post-print price action.",
  portfolio_review:
    "Look across all open positions. Surface: concentration risks, sector imbalance, stale analyses (>14d), aging Hold positions worth re-evaluating, and the top 3 actions to take this week.",
  screener_query:
    "Surface the top 20 candidates from the S&P 500 ranked by your criteria. Include sector + market cap + a one-line thesis per candidate.",
};

export default function ToolsPage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<ToolMode>("analyze");
  const [ticker, setTicker] = useState("");
  const [instructions, setInstructions] = useState("");
  const [analysisMode, setAnalysisMode] = useState<"fresh" | "incremental">("fresh");

  const tool = TOOLS.find((t) => t.mode === selected)!;
  const suggested = SUGGESTED_INSTRUCTIONS_PER_MODE[selected] ?? "";

  const queue = useMutation({
    mutationFn: () =>
      RunQueue.create({
        ticker: tool.takesTicker ? ticker.toUpperCase() : "_PORTFOLIO",
        trade_date: new Date().toISOString().slice(0, 10),
        mode: selected as any,
        options: {
          analysis_mode: analysisMode,
          custom_instructions: instructions || suggested || undefined,
          provider: "anthropic",
          deep_model: "claude-sonnet-4-6",
          quick_model: "claude-haiku-4-5",
          debate_rounds: 1,
          risk_rounds: 1,
        },
        requested_by: "web-ui:/tools",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["run-queue"] });
      setTicker("");
      setInstructions("");
    },
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Tools</h1>
        <p className="text-muted text-sm">
          Queue any tool for the Claude Desktop / Claude Code worker to run.
          Each tool produces an artifact (archive, brief, sidecar markdown,
          or screener result) that lands in your History or attached to a
          ticker's run page.
        </p>
        <p className="text-muted text-xs mt-1">
          New tool requests show up at{" "}
          <Link href="/queue" className="text-accent hover:underline">/queue</Link>{" "}
          tagged with their mode. The worker drains them on its next cycle.
        </p>
      </header>

      {/* Tool picker */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {TOOLS.map((t) => (
          <button
            key={t.mode}
            onClick={() => setSelected(t.mode)}
            className={`text-left p-3 rounded-md border transition-colors ${
              selected === t.mode
                ? "border-accent bg-bg/30"
                : "border-border hover:border-fg/30"
            }`}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-semibold">{t.title}</span>
              <code className="text-[10px] text-muted">{t.mode}</code>
            </div>
            <div className="text-xs text-muted mt-1">{t.description}</div>
          </button>
        ))}
      </div>

      {/* Queue form for the selected tool */}
      <form
        className="card space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          queue.mutate();
        }}
      >
        <div className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold">Queue: {tool.title}</h2>
          <code className="text-xs text-muted">{tool.mode}</code>
        </div>

        {tool.takesTicker ? (
          <div>
            <label className="label">Ticker</label>
            <input
              className="input w-full"
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              placeholder="NVDA"
              required
            />
          </div>
        ) : (
          <div className="text-xs text-muted">
            This tool operates on your whole portfolio — no ticker needed.
          </div>
        )}

        {(tool.mode === "analyze" || tool.mode === "refresh") && (
          <div>
            <label className="label">Memory mode</label>
            <div className="flex gap-4">
              <label className="flex items-start gap-2 text-sm cursor-pointer">
                <input
                  type="radio"
                  checked={analysisMode === "fresh"}
                  onChange={() => setAnalysisMode("fresh")}
                  className="mt-1"
                />
                <span>
                  <span className="font-semibold">Fresh</span>
                  <span className="block text-xs text-muted">
                    PM re-evaluates from scratch — break anchoring drift.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2 text-sm cursor-pointer">
                <input
                  type="radio"
                  checked={analysisMode === "incremental"}
                  onChange={() => setAnalysisMode("incremental")}
                  className="mt-1"
                />
                <span>
                  <span className="font-semibold">Incremental</span>
                  <span className="block text-xs text-muted">
                    PM sees prior decisions for this ticker.
                  </span>
                </span>
              </label>
            </div>
          </div>
        )}

        <div>
          <label className="label">
            Custom instructions <span className="text-muted">(optional)</span>
          </label>
          <textarea
            className="input w-full h-24"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder={suggested || "e.g. focus on cash-flow trend, ignore options activity"}
          />
          {suggested && (
            <button
              type="button"
              className="text-xs text-accent hover:underline mt-1"
              onClick={() => setInstructions(suggested)}
            >
              Use suggested default
            </button>
          )}
        </div>

        <div className="flex justify-end items-center gap-3">
          {queue.isError && (
            <span className="text-danger text-sm">
              {(queue.error as Error).message}
            </span>
          )}
          {queue.isSuccess && queue.data && (
            <span className="text-success text-sm">
              ✓ Queued {queue.data.ticker}{" "}
              <Link href="/queue" className="text-accent hover:underline">
                view →
              </Link>
            </span>
          )}
          <button
            type="submit"
            className="btn btn-primary"
            disabled={(tool.takesTicker && !ticker) || queue.isPending}
          >
            {queue.isPending ? "Queueing…" : `🤖 Queue ${tool.title}`}
          </button>
        </div>
      </form>

      <div className="card text-xs text-muted">
        <strong>How modes flow through.</strong> Each queue item has a{" "}
        <code>mode</code> field that the Claude Desktop worker reads when it
        claims the item. The <code>tradingagents-analyze</code> skill's Phase 9
        dispatches on mode — full pipeline for <code>analyze</code>, brief
        regeneration for <code>brief</code>, news fetch for{" "}
        <code>news_fetch</code>, etc. The non-analysis modes produce sidecar
        markdown attached to the ticker's most recent run (or to a portfolio
        record for cross-ticker reviews). Any <code>custom_instructions</code>{" "}
        you provide are passed verbatim to the worker.
      </div>
    </div>
  );
}
