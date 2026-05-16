"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Runs, Watchlist } from "@/lib/api";
import { DecisionHistoryChart } from "@/components/DecisionHistoryChart";

export default function TrendsPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => Runs.list(undefined) });
  const watchlist = useQuery({ queryKey: ["watchlist"], queryFn: () => Watchlist.list() });

  // Build a unique ticker list, watchlist first then ones with runs.
  const tickers = useMemo(() => {
    const set = new Set<string>();
    (watchlist.data ?? []).forEach((w) => set.add(w.ticker));
    (runs.data ?? []).forEach((r) => set.add(r.ticker));
    return Array.from(set).sort();
  }, [runs.data, watchlist.data]);

  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-bold">Trends</h1>
        <p className="text-muted text-sm">
          Interactive decision history — every past run's recommendation
          overlaid on the price line. Click a dot to open that run.
        </p>
      </header>

      <div className="card">
        <div className="flex flex-wrap gap-2">
          {tickers.length === 0 && (
            <span className="text-sm text-muted">
              No tickers yet — start a run or add to the watchlist.
            </span>
          )}
          {tickers.map((t) => (
            <button
              key={t}
              onClick={() => setSelected(t)}
              className={`btn text-xs ${selected === t ? "btn-primary" : ""}`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {selected ? (
        <DecisionHistoryChart ticker={selected} />
      ) : (
        <div className="card text-sm text-muted">
          Pick a ticker above to see its decision history.
        </div>
      )}

      {selected && (
        <div className="text-xs text-muted">
          Need a static image for an email or report?{" "}
          <Link
            className="text-accent hover:underline"
            href={`/api/charts/decisions/${encodeURIComponent(selected)}.png`}
            target="_blank"
          >
            Open PNG of this chart →
          </Link>
        </div>
      )}
    </div>
  );
}
