"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ask, type AskQuestion, type AskMode } from "@/lib/api";

// ──────────────────────────────────────────────────────────────────────
// /ask — free-form portfolio Q&A
//
// Two modes:
//   - queue (default): drops a run_queue item, Claude Desktop answers
//     during its next drain. Free, async, polling for the answer.
//   - sync: hits the Anthropic API directly. Instant but costs tokens.
//     Falls back to queue if SDK or API key is unavailable.
//
// Conversations group multi-turn threads. Pick one from the sidebar
// to continue, or start a new one from the input.
// ──────────────────────────────────────────────────────────────────────

const SAMPLES = [
  "Which of my holdings has the strongest analyst upgrade momentum this week?",
  "Summarize my recent recommendations and which ones I actually traded on.",
  "What is the biggest concentration risk in my current book?",
  "How does my NVDA position compare to what institutional managers hold?",
  "Which holdings are closest to their 52-week high, and is that a sell signal or a strength signal?",
  "Across my recent analyses, where did the framework miss most badly vs SPY?",
];

function fmtTs(s: string): string {
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}

export default function AskPage() {
  const qc = useQueryClient();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<AskMode>("queue");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Sidebar: recent threads
  const conversationsQuery = useQuery({
    queryKey: ["ask-conversations"],
    queryFn: () => Ask.listConversations(50),
    refetchInterval: 30_000,
  });

  // Active thread
  const threadQuery = useQuery({
    queryKey: ["ask-conversation", conversationId],
    queryFn: () => Ask.getConversation(conversationId!),
    enabled: !!conversationId,
    // Poll faster when an answer is pending so it shows up quickly
    refetchInterval: (q) => {
      const rows = (q.state.data as AskQuestion[] | undefined) ?? [];
      const pending = rows.some((r) => r.status === "pending");
      return pending ? 5_000 : 60_000;
    },
  });

  // Submit
  const submitQ = useMutation({
    mutationFn: () =>
      Ask.submit({
        question: draft.trim(),
        conversation_id: conversationId ?? undefined,
        mode,
      }),
    onSuccess: (newRow) => {
      setDraft("");
      setConversationId(newRow.conversation_id);
      qc.invalidateQueries({ queryKey: ["ask-conversation", newRow.conversation_id] });
      qc.invalidateQueries({ queryKey: ["ask-conversations"] });
    },
  });

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [threadQuery.data?.length]);

  const conversations = conversationsQuery.data ?? [];
  const thread = threadQuery.data ?? [];
  const lastQ = thread[thread.length - 1];
  const isPending = lastQ?.status === "pending";

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Ask the portfolio</h1>
        <p className="text-muted text-sm">
          Free-form Q&A grounded in your positions, recent analysis runs,
          trades, restrictions, news alerts, and 13F overlaps. Default
          mode is <strong>queue</strong> (Claude Desktop answers it for free
          on next drain). Toggle to <strong>sync</strong> for an immediate
          answer that costs Anthropic API tokens.
        </p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-[260px_1fr] gap-4">
        {/* ─── Sidebar: conversations ─── */}
        <aside className="card space-y-2 max-h-[80vh] overflow-y-auto">
          <button
            className="btn w-full text-sm"
            onClick={() => setConversationId(null)}
          >
            + New conversation
          </button>
          <div className="text-xs uppercase text-muted mt-3 mb-1">
            Recent threads
          </div>
          {conversations.length === 0 && (
            <div className="text-xs text-muted">No conversations yet.</div>
          )}
          {conversations.map((c) => (
            <button
              key={c.conversation_id}
              onClick={() => setConversationId(c.conversation_id)}
              className={`w-full text-left text-sm p-2 rounded hover:bg-surface ${
                conversationId === c.conversation_id ? "bg-surface" : ""
              }`}
            >
              <div className="text-xs text-muted">
                {fmtTs(c.last_question_at)} · {c.turn_count} turn{c.turn_count === 1 ? "" : "s"}
              </div>
              <div className="line-clamp-2">{c.preview}</div>
            </button>
          ))}
        </aside>

        {/* ─── Main: thread ─── */}
        <div className="card flex flex-col h-[80vh]">
          {/* Thread scroll area */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-4 pr-2">
            {!conversationId && (
              <div className="text-center py-8 space-y-4">
                <h2 className="text-lg font-semibold">Start a new conversation</h2>
                <p className="text-muted text-sm">
                  Try one of these or write your own:
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {SAMPLES.map((s) => (
                    <button
                      key={s}
                      onClick={() => setDraft(s)}
                      className="text-left text-sm card hover:bg-surface"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {thread.map((r) => (
              <QuestionTurn key={r.id} row={r} />
            ))}

            {threadQuery.isLoading && conversationId && (
              <div className="text-muted text-sm">Loading thread…</div>
            )}
          </div>

          {/* Composer */}
          <form
            className="border-t border-border pt-3 mt-3 space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (draft.trim() && !submitQ.isPending) submitQ.mutate();
            }}
          >
            <textarea
              className="input w-full"
              rows={3}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={
                conversationId
                  ? "Ask a follow-up…"
                  : "Ask a portfolio-wide question…"
              }
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  if (draft.trim() && !submitQ.isPending) submitQ.mutate();
                }
              }}
            />
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex items-center gap-2 text-sm">
                <label className="flex items-center gap-1 cursor-pointer">
                  <input
                    type="radio"
                    checked={mode === "queue"}
                    onChange={() => setMode("queue")}
                  />
                  <span>Queue (free, async)</span>
                </label>
                <label className="flex items-center gap-1 cursor-pointer">
                  <input
                    type="radio"
                    checked={mode === "sync"}
                    onChange={() => setMode("sync")}
                  />
                  <span>Sync (Anthropic API, instant)</span>
                </label>
              </div>
              <span className="text-xs text-muted ml-auto">
                Ctrl/⌘+Enter to send
              </span>
              <button
                type="submit"
                className="btn btn-primary text-sm"
                disabled={!draft.trim() || submitQ.isPending || isPending}
                title={
                  isPending
                    ? "Wait for the current question to be answered first"
                    : ""
                }
              >
                {submitQ.isPending
                  ? "Submitting…"
                  : isPending
                    ? "Waiting…"
                    : mode === "sync"
                      ? "Send (sync)"
                      : "Send → Queue"}
              </button>
            </div>
            {submitQ.isError && (
              <div className="text-danger text-xs">
                {(submitQ.error as Error).message}
              </div>
            )}
          </form>
        </div>
      </div>

      <div className="card text-xs text-muted">
        <strong>How this works:</strong> when you submit a question, the
        backend snapshots your positions, recent analysis runs, trade
        journal, active restrictions, high-impact news, and smart-money
        overlaps into a markdown context block. That context + your
        question is either (a) queued for Claude Desktop to answer for
        free on its next drain, or (b) sent to the Anthropic API for an
        immediate response that costs tokens. Sync falls back to queue
        if ANTHROPIC_API_KEY isn&apos;t set.
      </div>
    </div>
  );
}

// ─── One Q+A turn ────────────────────────────────────────────────────

function QuestionTurn({ row }: { row: AskQuestion }) {
  return (
    <div className="space-y-2">
      {/* User question */}
      <div className="flex justify-end">
        <div className="max-w-[80%] bg-accent/10 rounded p-3">
          <div className="text-xs text-muted mb-1">
            You · {fmtTs(row.requested_at)} · {row.mode}
          </div>
          <div className="whitespace-pre-wrap text-sm">{row.question}</div>
        </div>
      </div>

      {/* Answer */}
      <div className="flex justify-start">
        <div className="max-w-[85%] bg-surface rounded p-3">
          {row.status === "complete" && row.answer_md && (
            <>
              <div className="text-xs text-muted mb-1">
                {row.source ?? "claude"} · {row.answered_at ? fmtTs(row.answered_at) : ""}
                {row.tokens_in !== null && row.tokens_out !== null && (
                  <> · {row.tokens_in}+{row.tokens_out} tokens</>
                )}
              </div>
              <div className="whitespace-pre-wrap text-sm">{row.answer_md}</div>
            </>
          )}
          {row.status === "pending" && (
            <div className="text-sm text-muted">
              {row.mode === "queue" ? (
                <>
                  ⏳ Queued for Claude Desktop. Question id={row.id}, queue id={row.queue_id?.slice(0, 8)}…
                  Page polls every 5s while pending.
                </>
              ) : (
                <>⏳ Awaiting Anthropic API response…</>
              )}
            </div>
          )}
          {row.status === "error" && (
            <div className="text-sm text-danger">
              Error: {row.error_message ?? "unknown"}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
