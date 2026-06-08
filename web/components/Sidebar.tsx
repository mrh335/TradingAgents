"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

// Collapsible, scrollable left sidebar.
//
// Scroll fix: the <aside> is a flex column pinned to the viewport (h-screen);
// the brand block is fixed-height and the <nav> is `flex-1 overflow-y-auto
// min-h-0`, so the link list ALWAYS gets its own scrollbar when it's taller
// than the viewport. (The old layout put a tall list in an h-screen aside with
// no overflow container, so on shorter viewports the bottom links were simply
// unreachable — the "sometimes it scrolls" bug.)

type NavItem = { href: string; label: string };
type NavSection = { id: string; title: string; items: NavItem[] };

// Always-visible quick links. "Past analyses" pinned near the top per request.
const PINNED: NavItem[] = [
  { href: "/", label: "Home" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/history", label: "Past analyses" },
];

const SECTIONS: NavSection[] = [
  {
    id: "portfolio",
    title: "Portfolio & Holdings",
    items: [
      { href: "/portfolio", label: "Portfolio" },
      { href: "/portfolio-analytics", label: "Analytics" },
      { href: "/tax", label: "Tax" },
      { href: "/trades", label: "Trades" },
      { href: "/holders", label: "Holders (13F)" },
      { href: "/restrictions", label: "Restrictions" },
      { href: "/watchlist", label: "Watchlist" },
    ],
  },
  {
    id: "run",
    title: "Run Analysis",
    items: [
      { href: "/run", label: "Run" },
      { href: "/batch", label: "Batch" },
      { href: "/compare", label: "Compare models" },
      { href: "/queue", label: "Queue" },
      { href: "/schedules", label: "Schedules" },
    ],
  },
  {
    id: "research",
    title: "Research & Insights",
    items: [
      { href: "/ask", label: "Ask" },
      { href: "/recommendations", label: "Recommendations" },
      { href: "/discover", label: "Discover" },
      { href: "/backtest", label: "Backtest" },
      { href: "/simulation", label: "Simulation" },
      { href: "/calendar", label: "Calendar" },
      { href: "/earnings", label: "Earnings" },
      { href: "/trends", label: "Trends" },
      { href: "/macro", label: "Macro" },
      { href: "/news", label: "News" },
      { href: "/news-alerts", label: "Alerts" },
    ],
  },
  {
    id: "workspace",
    title: "Workspace",
    items: [
      { href: "/notes", label: "Notes" },
      { href: "/memory", label: "Memory" },
      { href: "/tokens", label: "Tokens" },
      { href: "/tools", label: "Tools" },
      { href: "/references", label: "References" },
    ],
  },
  {
    id: "system",
    title: "System",
    items: [
      { href: "/settings", label: "Settings" },
      { href: "/docs", label: "Help / Docs" },
    ],
  },
];

const STORAGE_KEY = "ta_sidebar_collapsed_v1";

export function Sidebar() {
  const pathname = usePathname();
  // Set of section ids the user has collapsed. Empty = all expanded (default).
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  // Hydrate persisted collapse state AFTER mount so SSR and first client render
  // match (both start all-expanded), avoiding a hydration mismatch.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) setCollapsed(JSON.parse(raw));
    } catch {
      /* ignore malformed storage */
    }
  }, []);

  function toggle(id: string) {
    setCollapsed((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        /* storage may be unavailable; UI still works in-session */
      }
      return next;
    });
  }

  const isActive = (href: string) =>
    href === "/"
      ? pathname === "/"
      : pathname === href || pathname.startsWith(href + "/");

  const linkClass = (href: string) =>
    `block px-2 py-1.5 rounded text-sm transition-colors ${
      isActive(href) ? "bg-accent/15 text-accent font-medium" : "hover:bg-surface"
    }`;

  return (
    <aside className="w-56 shrink-0 border-r border-border sticky top-0 h-screen flex flex-col">
      <div className="p-4 pb-3 shrink-0">
        <div className="text-lg font-semibold">TradingAgents</div>
        <div className="text-xs text-muted">Recommendations, not orders</div>
      </div>

      <nav className="flex-1 overflow-y-auto min-h-0 px-3 pb-6 space-y-1">
        <div className="space-y-1">
          {PINNED.map((n) => (
            <Link key={n.href} href={n.href} className={linkClass(n.href)}>
              {n.label}
            </Link>
          ))}
        </div>

        {SECTIONS.map((section) => {
          const open = !collapsed[section.id];
          const hasActive = section.items.some((i) => isActive(i.href));
          return (
            <div key={section.id} className="pt-3">
              <button
                type="button"
                onClick={() => toggle(section.id)}
                className="w-full flex items-center justify-between px-2 py-1 text-xs font-semibold uppercase tracking-wide text-muted hover:text-accent"
                aria-expanded={open}
              >
                <span className="flex items-center gap-1.5">
                  {section.title}
                  {/* dot signals the active page lives in a collapsed section */}
                  {!open && hasActive && (
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent" />
                  )}
                </span>
                <span className={`transition-transform ${open ? "rotate-90" : ""}`}>
                  ›
                </span>
              </button>
              {open && (
                <div className="space-y-1 mt-1">
                  {section.items.map((n) => (
                    <Link key={n.href} href={n.href} className={linkClass(n.href)}>
                      {n.label}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
