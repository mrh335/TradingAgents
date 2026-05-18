"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Streaming, type LivePrice } from "@/lib/api";

/**
 * Horizontal scrolling strip of live prices for the broadcaster's
 * pre-warmed tickers (watchlist + open positions). Polls
 * /streaming/state every 10s for the snapshot — actual price ticks
 * arrive via the broadcaster's internal poll loop on the API side, so
 * we get fresh numbers without needing a WebSocket here.
 *
 * Lives in the root layout so it's visible on every page.
 */
export function LiveTickerStrip() {
  const q = useQuery({
    queryKey: ["streaming-state"],
    queryFn: () => Streaming.state(),
    refetchInterval: 10_000,
    refetchOnWindowFocus: false,
    retry: false,
  });

  const prices = q.data?.prices ?? {};
  const rows = Object.values(prices)
    .filter((p): p is LivePrice => !!p && p.price !== null)
    .sort((a, b) => a.ticker.localeCompare(b.ticker));

  if (rows.length === 0) {
    return null; // hide quietly if there's nothing to show
  }

  return (
    <div className="bg-surface border-b border-border overflow-hidden">
      <div className="flex overflow-x-auto whitespace-nowrap text-xs py-1.5 px-2 gap-4 scrollbar-thin">
        {rows.map((p) => (
          <Link
            key={p.ticker}
            href={`/trends`}
            className="inline-flex items-baseline gap-1.5 hover:bg-bg px-2 py-1 rounded"
            title={`Last polled: ${p.polled_at ? new Date(p.polled_at * 1000).toLocaleTimeString() : "—"}`}
          >
            <span className="font-mono font-semibold">{p.ticker}</span>
            <span className="tabular-nums">${p.price.toFixed(2)}</span>
            {p.change_pct !== null && (
              <span
                className={`tabular-nums ${
                  p.change_pct > 0
                    ? "text-success"
                    : p.change_pct < 0
                      ? "text-danger"
                      : "text-muted"
                }`}
              >
                {p.change_pct > 0 ? "+" : ""}
                {p.change_pct.toFixed(2)}%
              </span>
            )}
          </Link>
        ))}
      </div>
    </div>
  );
}
