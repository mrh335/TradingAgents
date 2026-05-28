"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Briefs,
  Earnings,
  Holders,
  NewsAlerts,
  Regime,
  Restrictions,
  Runs,
  Tickers,
  type RunSummary,
} from "@/lib/api";
import { decisionColor } from "@/lib/format";

// ──────────────────────────────────────────────────────────────────────
// /ticker/[ticker] — composite detail page for any ticker.
//
// Replaces the old "watchlist link → /run page" flow. Now clicking
// a ticker takes you here, where everything we know about the name
// is summarized: latest analysis, decision history, brief targets,
// technical metrics, earnings, holders, news, regime context. From
// here you can still kick off a new analysis or open a paper trade,
// but the default action is to LOOK BEFORE you ACT.
// ──────────────────────────────────────────────────────────────────────

function fmtUsd(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (Math.abs(n) >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  return `$${n.toFixed(2)}`;
}

function fmtPct(n: number | null | undefined, withSign = true): string {
  if (n === null || n === undefined) return "—";
  const sign = withSign && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function fmtTs(s: string | null | undefined): string {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleDateString();
  } catch {
    return s;
  }
}

const REGIME_TONE: Record<string, string> = {
  CALM_BULL: "text-success",
  VOLATILE_BULL: "text-warning",
  VOLATILE_BEAR: "text-danger",
  CALM_BEAR: "text-muted",
};

export default function TickerDetailPage() {
  const params = useParams<{ ticker: string }>();
  const ticker = (params.ticker || "").toUpperCase();

  // Fire all the read-only queries in parallel via TanStack Query.
  // Each renders independently as it arrives, so the user sees the
  // page filling in rather than waiting on the slowest call.
  const snapshot = useQuery({
    queryKey: ["ticker-snapshot", ticker],
    queryFn: () => Tickers.snapshot(ticker),
    enabled: !!ticker,
    refetchInterval: 60_000,
  });
  const runs = useQuery({
    queryKey: ["ticker-runs", ticker],
    queryFn: () => Runs.list(ticker),
    enabled: !!ticker,
  });
  const earnings = useQuery({
    queryKey: ["ticker-earnings", ticker],
    queryFn: () => Earnings.get(ticker),
    enabled: !!ticker,
  });
  const holders = useQuery({
    queryKey: ["ticker-holders", ticker],
    queryFn: () => Holders.tickerSummary(ticker),
    enabled: !!ticker,
  });
  const restrictions = useQuery({
    queryKey: ["ticker-restrictions", ticker],
    queryFn: () => Restrictions.list({ ticker, active_on: new Date().toLocaleDateString("sv-SE") }),
    enabled: !!ticker,
  });
  const news = useQuery({
    queryKey: ["ticker-news", ticker],
    queryFn: () => NewsAlerts.list({ ticker, impact: "high", limit: 5 }),
    enabled: !!ticker,
  });
  const regime = useQuery({
    queryKey: ["ticker-regime", ticker],
    queryFn: () => Regime.forTicker(ticker),
    enabled: !!ticker,
  });

  // Latest completed run, used as the brief anchor.
  const latestDoneRun: RunSummary | undefined = (runs.data ?? []).find(
    (r) => (r.status ?? "").toLowerCase() === "done",
  );
  const brief = useQuery({
    queryKey: ["ticker-brief", latestDoneRun?.run_id ?? ""],
    queryFn: () => Briefs.get(latestDoneRun!.run_id),
    enabled: !!latestDoneRun,
  });

  return (
    <div className="space-y-6">
      {/* ─── Header: ticker + price + change + quick actions ─── */}
      <header>
        <div className="flex items-baseline gap-3 flex-wrap">
          <h1 className="text-3xl font-bold">{ticker}</h1>
          {snapshot.data?.current_price !== null && (
            <span className="text-2xl tabular-nums">
              ${snapshot.data?.current_price?.toFixed(2) ?? "—"}
            </span>
          )}
          {snapshot.data?.change_pct_today !== null &&
            snapshot.data?.change_pct_today !== undefined && (
              <span
                className={`text-sm tabular-nums ${
                  snapshot.data.change_pct_today >= 0
                    ? "text-success"
                    : "text-danger"
                }`}
              >
                {fmtPct(snapshot.data.change_pct_today)} today
              </span>
            )}
        </div>
        <div className="flex flex-wrap gap-2 mt-3">
          <Link
            href={`/run?ticker=${ticker}`}
            className="btn text-sm btn-primary"
          >
            ▶ Run new analysis
          </Link>
          <Link href={`/earnings/${ticker}`} className="btn text-sm">
            📊 Earnings detail
          </Link>
          <Link href={`/holders?ticker=${ticker}`} className="btn text-sm">
            🏛 Smart-money holders
          </Link>
          <Link
            href={`/trades?ticker=${ticker}`}
            className="btn text-sm"
            title="Log a trade for this ticker"
          >
            📋 Log a trade
          </Link>
        </div>
      </header>

      {/* ─── Latest decision card (the headline takeaway) ─── */}
      {latestDoneRun ? (
        <section className="card">
          <div className="text-xs uppercase tracking-wider text-muted mb-1">
            Latest framework decision · {fmtTs(latestDoneRun.completed_at)}
          </div>
          <div className="flex items-baseline gap-3 flex-wrap">
            <span
              className={`text-2xl font-bold ${decisionColor(latestDoneRun.decision)}`}
            >
              {latestDoneRun.decision ?? "—"}
            </span>
            {brief.data?.brief?.action_plain && (
              <span className="text-lg text-muted">
                ({brief.data.brief.action_plain})
              </span>
            )}
            <Link
              href={`/history/${latestDoneRun.run_id}`}
              className="text-sm text-accent hover:underline ml-auto"
            >
              Full brief →
            </Link>
          </div>
          {brief.data?.brief?.tldr && (
            <p className="text-sm mt-2">{brief.data.brief.tldr}</p>
          )}
          {brief.data?.brief && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3 text-sm">
              <Field
                label="Timeframe"
                value={brief.data.brief.timeframe}
              />
              <Field
                label="Position size"
                value={brief.data.brief.position_size}
              />
              <Field label="Stop loss" value={brief.data.brief.stop_loss} />
              <Field label="Take profit" value={brief.data.brief.take_profit} />
            </div>
          )}
        </section>
      ) : runs.isLoading ? (
        <section className="card text-sm text-muted">Loading runs…</section>
      ) : (
        <section className="card text-sm text-muted">
          No completed analyses yet for {ticker}. Click{" "}
          <strong>Run new analysis</strong> above to generate one.
        </section>
      )}

      {/* ─── Two-column: technical + earnings ─── */}
      <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Technical snapshot */}
        <div className="card">
          <div className="font-semibold text-sm mb-2">Technical snapshot</div>
          {snapshot.isLoading ? (
            <div className="text-sm text-muted">Loading…</div>
          ) : !snapshot.data?.available ? (
            <div className="text-sm text-muted">
              {snapshot.data?.error ?? "no data"}
            </div>
          ) : (
            <table className="w-full text-sm">
              <tbody>
                <Row label="52-week range">
                  ${snapshot.data.low_52w} – ${snapshot.data.high_52w}
                  {snapshot.data.range_position_pct !== null && (
                    <span className="text-xs text-muted ml-2">
                      ({snapshot.data.range_position_pct.toFixed(0)}% of the way up)
                    </span>
                  )}
                </Row>
                <Row label="50-day average">
                  ${snapshot.data.sma_50?.toFixed(2) ?? "—"}{" "}
                  <span
                    className={`text-xs ${(snapshot.data.pct_vs_sma_50 ?? 0) >= 0 ? "text-success" : "text-danger"}`}
                  >
                    ({fmtPct(snapshot.data.pct_vs_sma_50)})
                  </span>
                </Row>
                <Row label="200-day average">
                  ${snapshot.data.sma_200?.toFixed(2) ?? "—"}{" "}
                  <span
                    className={`text-xs ${(snapshot.data.pct_vs_sma_200 ?? 0) >= 0 ? "text-success" : "text-danger"}`}
                  >
                    ({fmtPct(snapshot.data.pct_vs_sma_200)})
                  </span>
                </Row>
                <Row label="Trend">
                  {snapshot.data.golden_cross === true && (
                    <span className="text-success">
                      golden ↑ (50d above 200d — uptrend)
                    </span>
                  )}
                  {snapshot.data.golden_cross === false && (
                    <span className="text-danger">
                      death ↓ (50d below 200d — downtrend)
                    </span>
                  )}
                  {snapshot.data.golden_cross === null && (
                    <span className="text-muted">—</span>
                  )}
                </Row>
              </tbody>
            </table>
          )}
        </div>

        {/* Earnings snapshot */}
        <div className="card">
          <div className="font-semibold text-sm mb-2">Earnings</div>
          {earnings.isLoading ? (
            <div className="text-sm text-muted">Loading…</div>
          ) : !earnings.data ? (
            <div className="text-sm text-muted">no data</div>
          ) : (
            <table className="w-full text-sm">
              <tbody>
                <Row label="Next earnings">
                  {earnings.data.next_earnings_date ?? "—"}
                  {earnings.data.days_until_next !== null && (
                    <span className="text-xs text-muted ml-2">
                      ({earnings.data.days_until_next > 0
                        ? `in ${earnings.data.days_until_next}d`
                        : `${-earnings.data.days_until_next}d ago`})
                    </span>
                  )}
                </Row>
                {earnings.data.latest_quarter && (
                  <>
                    <Row label="Last EPS">
                      {earnings.data.latest_quarter.eps_actual ?? "—"} vs est{" "}
                      {earnings.data.latest_quarter.eps_estimate ?? "—"}
                      {earnings.data.latest_quarter.eps_surprise_pct !== null && (
                        <span
                          className={`text-xs ml-2 ${
                            (earnings.data.latest_quarter.eps_surprise_pct ?? 0) >= 0
                              ? "text-success"
                              : "text-danger"
                          }`}
                        >
                          ({fmtPct(earnings.data.latest_quarter.eps_surprise_pct)} surprise)
                        </span>
                      )}
                    </Row>
                    <Row label="Last revenue">
                      {fmtUsd(earnings.data.latest_quarter.revenue_actual)}
                    </Row>
                  </>
                )}
                {earnings.data.revisions.length > 0 && (
                  <Row label="Analyst revisions">
                    {earnings.data.revisions[0]?.direction === "up" && (
                      <span className="text-success">↑ trending up</span>
                    )}
                    {earnings.data.revisions[0]?.direction === "down" && (
                      <span className="text-danger">↓ trending down</span>
                    )}
                    {(!earnings.data.revisions[0]?.direction ||
                      earnings.data.revisions[0]?.direction === "flat") && (
                      <span className="text-muted">flat</span>
                    )}
                  </Row>
                )}
              </tbody>
            </table>
          )}
        </div>
      </section>

      {/* ─── Regime + restrictions + holders summary row ─── */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Per-ticker regime */}
        <div className="card">
          <div className="font-semibold text-sm mb-2">Regime (this ticker)</div>
          {regime.isLoading ? (
            <div className="text-sm text-muted">Loading…</div>
          ) : !regime.data?.available ? (
            <div className="text-sm text-muted">
              {regime.data?.error ?? "no data"}
            </div>
          ) : (
            <div className="text-sm">
              <div
                className={`text-lg font-semibold ${REGIME_TONE[regime.data.current_regime ?? ""] ?? "text-muted"}`}
              >
                {regime.data.current_label ?? regime.data.current_regime ?? "—"}
              </div>
              {regime.data.vol_ratio !== null && (
                <div className="text-xs text-muted mt-1">
                  30d vol is {regime.data.vol_ratio.toFixed(2)}× its 1-year median
                </div>
              )}
              {regime.data.current_blurb && (
                <div className="text-xs text-muted mt-2">
                  {regime.data.current_blurb}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Active restrictions */}
        <div className="card">
          <div className="font-semibold text-sm mb-2">Trade restrictions</div>
          {restrictions.isLoading ? (
            <div className="text-sm text-muted">Loading…</div>
          ) : (restrictions.data ?? []).length === 0 ? (
            <div className="text-sm text-success">✅ No active restrictions</div>
          ) : (
            <div className="text-sm space-y-1">
              {(restrictions.data ?? []).map((r) => (
                <div key={r.id}>
                  <span className="font-semibold">{r.kind}</span>
                  {r.kind === "earnings_window" && (
                    <>
                      {" "}
                      — open {r.earnings_window_open_offset_days}d after
                      earnings for {r.earnings_window_duration_days}d
                    </>
                  )}
                  {r.currently_open === true && (
                    <span className="text-success ml-2">✅ OPEN now</span>
                  )}
                  {r.currently_open === false && (
                    <span className="text-danger ml-2">🚫 CLOSED now</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Smart-money holders summary */}
        <div className="card">
          <div className="font-semibold text-sm mb-2">Smart-money holders</div>
          {holders.isLoading ? (
            <div className="text-sm text-muted">Loading…</div>
          ) : !holders.data || holders.data.manager_count === 0 ? (
            <div className="text-sm text-muted">
              No tracked institutional holder positions in {ticker}.
            </div>
          ) : (
            <div className="text-sm">
              <div className="text-lg font-semibold">
                {holders.data.manager_count} managers · {fmtUsd(holders.data.total_value)}
              </div>
              {holders.data.top_managers.length > 0 && (
                <div className="text-xs text-muted mt-1">
                  Top: {holders.data.top_managers[0].name}
                </div>
              )}
              <Link
                href={`/holders?ticker=${ticker}`}
                className="text-xs text-accent hover:underline mt-2 inline-block"
              >
                See all →
              </Link>
            </div>
          )}
        </div>
      </section>

      {/* ─── Recent decision history ─── */}
      <section>
        <h2 className="text-lg font-semibold mb-2">Recent analyses</h2>
        {runs.isLoading ? (
          <div className="card text-sm text-muted">Loading…</div>
        ) : (runs.data ?? []).length === 0 ? (
          <div className="card text-sm text-muted">
            No analyses yet for {ticker}.
          </div>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-muted">
                <tr>
                  <th className="py-2">Trade date</th>
                  <th>Decision</th>
                  <th>Provider / Model</th>
                  <th>Status</th>
                  <th>Completed</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {(runs.data ?? []).slice(0, 10).map((r) => (
                  <tr key={r.run_id} className="border-t border-border">
                    <td className="py-1.5">{r.trade_date}</td>
                    <td
                      className={`font-semibold ${decisionColor(r.decision)}`}
                    >
                      {r.decision ?? "—"}
                    </td>
                    <td className="text-xs text-muted">
                      {r.provider ?? "—"} / {r.deep_model ?? "—"}
                    </td>
                    <td className="text-xs">{r.status}</td>
                    <td className="text-xs text-muted">
                      {fmtTs(r.completed_at)}
                    </td>
                    <td className="text-right">
                      <Link
                        href={`/history/${r.run_id}`}
                        className="text-xs text-accent hover:underline"
                      >
                        View →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ─── Recent high-impact news ─── */}
      <section>
        <h2 className="text-lg font-semibold mb-2">Recent high-impact news</h2>
        {news.isLoading ? (
          <div className="card text-sm text-muted">Loading…</div>
        ) : (news.data ?? []).length === 0 ? (
          <div className="card text-sm text-muted">
            No high-impact news for {ticker}. The poller checks every 15
            min. Visit{" "}
            <Link href="/news-alerts" className="text-accent hover:underline">
              /news-alerts
            </Link>{" "}
            for the full feed.
          </div>
        ) : (
          <div className="card space-y-2">
            {(news.data ?? []).map((n) => (
              <div key={n.id} className="text-sm">
                <span className="text-xs text-muted mr-2">
                  {fmtDate(n.published_at)}
                </span>
                {n.url ? (
                  <a
                    href={n.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-accent hover:underline"
                  >
                    {n.headline}
                  </a>
                ) : (
                  <span>{n.headline}</span>
                )}
                <span className="text-xs text-muted ml-2">· {n.source}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <tr className="border-t border-border first:border-t-0">
      <td className="py-1.5 pr-3 text-muted text-xs uppercase tracking-wider w-1/3">
        {label}
      </td>
      <td className="py-1.5">{children}</td>
    </tr>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-muted">{label}</div>
      <div className="text-sm">{value}</div>
    </div>
  );
}
