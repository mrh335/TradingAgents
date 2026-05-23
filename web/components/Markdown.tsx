"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useMemo, type ReactNode } from "react";

import { mergeGlossary } from "@/lib/glossary";

// Wraps react-markdown with GFM (tables, strikethrough) and a tight
// prose style. ALL plain-text inside the rendered markdown gets passed
// through wrapText, which auto-detects known finance terms (from
// lib/glossary.ts) and turns them into hover-tooltips. This means the
// trader plan, risk debate, analyst reports — anywhere markdown is
// rendered — get the same engineer-friendly glossary treatment as the
// structured Brief panel.

export function Markdown({
  children,
  briefGlossary,
}: {
  children: string | null | undefined;
  /** Optional brief-specific terms to merge with the global glossary. */
  briefGlossary?: Record<string, string> | null;
}) {
  if (!children)
    return <span className="text-muted text-sm">_(no content)_</span>;

  // Merged lookup: global glossary + any brief-specific overrides.
  const lookup = useMemo(() => mergeGlossary(briefGlossary), [briefGlossary]);

  // Custom renderers wrap every text node with the glossary tooltip
  // logic so it works inside paragraphs, list items, table cells,
  // headings, bold, italic — everywhere react-markdown leaves text.
  const components = useMemo(
    () => ({
      p: ({ children }: any) => <p>{wrapChildren(children, lookup)}</p>,
      li: ({ children }: any) => <li>{wrapChildren(children, lookup)}</li>,
      td: ({ children }: any) => <td>{wrapChildren(children, lookup)}</td>,
      th: ({ children }: any) => <th>{wrapChildren(children, lookup)}</th>,
      h1: ({ children }: any) => <h1>{wrapChildren(children, lookup)}</h1>,
      h2: ({ children }: any) => <h2>{wrapChildren(children, lookup)}</h2>,
      h3: ({ children }: any) => <h3>{wrapChildren(children, lookup)}</h3>,
      h4: ({ children }: any) => <h4>{wrapChildren(children, lookup)}</h4>,
      strong: ({ children }: any) => (
        <strong>{wrapChildren(children, lookup)}</strong>
      ),
      em: ({ children }: any) => <em>{wrapChildren(children, lookup)}</em>,
    }),
    [lookup],
  );

  return (
    <div className="prose-tight">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components as any}>
        {children}
      </ReactMarkdown>
      <style jsx>{`
        .prose-tight :global(h1) { font-size: 1.25rem; font-weight: 700; margin: 1rem 0 0.5rem; }
        .prose-tight :global(h2) { font-size: 1.125rem; font-weight: 700; margin: 0.875rem 0 0.5rem; }
        .prose-tight :global(h3) { font-size: 1rem; font-weight: 600; margin: 0.75rem 0 0.375rem; }
        .prose-tight :global(p)  { margin: 0.5rem 0; line-height: 1.55; }
        .prose-tight :global(ul), .prose-tight :global(ol) { margin: 0.5rem 0 0.5rem 1.25rem; }
        .prose-tight :global(li) { margin: 0.125rem 0; }
        .prose-tight :global(code) { background: rgba(127,127,127,0.18); padding: 0 0.25rem; border-radius: 3px; font-size: 0.875em; }
        .prose-tight :global(pre) { background: rgba(127,127,127,0.12); padding: 0.75rem; border-radius: 6px; overflow-x: auto; font-size: 0.85em; }
        .prose-tight :global(blockquote) { border-left: 3px solid rgb(var(--accent)); padding-left: 0.75rem; color: rgb(var(--muted)); margin: 0.5rem 0; }
        .prose-tight :global(table) { border-collapse: collapse; }
        .prose-tight :global(th), .prose-tight :global(td) { border: 1px solid rgb(var(--border)); padding: 0.25rem 0.5rem; }
        .prose-tight :global(strong) { font-weight: 600; }
        .prose-tight :global(.glossary-term) {
          text-decoration: underline dotted rgb(var(--accent));
          text-underline-offset: 2px;
          cursor: help;
        }
      `}</style>
    </div>
  );
}

// Recursively walk children rendered by react-markdown and wrap any
// string nodes with auto-detected glossary tooltips.
function wrapChildren(
  children: ReactNode,
  lookup: ReturnType<typeof mergeGlossary>,
): ReactNode {
  if (typeof children === "string") return wrapText(children, lookup);
  if (Array.isArray(children))
    return children.map((c, i) => (
      <span key={i}>{wrapChildren(c, lookup)}</span>
    ));
  return children;
}

// Scan one string for any known glossary term and wrap with a tooltip.
// Longest-match-first so "200-day SMA" wins over "SMA" if both are
// keys. Case-insensitive matching; preserves original casing.
function wrapText(
  text: string,
  lookup: ReturnType<typeof mergeGlossary>,
): ReactNode {
  if (!text) return text;
  const phrases = Array.from(lookup.keys()).sort((a, b) => b.length - a.length);
  if (phrases.length === 0) return text;

  // Escape regex meta in each phrase.
  const escaped = phrases.map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  // Require non-alphanumeric on both sides (or string edges) so we
  // don't match mid-word (e.g. "pe" inside "pearl").
  const pattern = `(?<![A-Za-z0-9])(${escaped.join("|")})(?![A-Za-z0-9])`;
  let re: RegExp;
  try {
    re = new RegExp(pattern, "gi");
  } catch {
    return text;
  }

  const out: ReactNode[] = [];
  let lastIdx = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > lastIdx) out.push(text.slice(lastIdx, m.index));
    const matched = m[0];
    const found = lookup.get(matched.toLowerCase());
    if (found) {
      out.push(
        <span
          key={key++}
          className="glossary-term"
          title={found.entry.definition}
        >
          {matched}
        </span>,
      );
    } else {
      out.push(matched);
    }
    lastIdx = m.index + matched.length;
  }
  if (lastIdx < text.length) out.push(text.slice(lastIdx));
  return <>{out}</>;
}
