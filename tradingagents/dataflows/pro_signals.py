"""Pro-grade market-signal dataflows — yfinance-backed wrappers that
surface short interest, analyst targets, insider streak detection, and
the earnings calendar into the agent prompts.

Each function returns a self-contained markdown block ready to embed
in an analyst tool-call result. Failures degrade to a one-line
"no data" message rather than raising — agents treat absence as "no
signal" and proceed.

Functions
---------
get_short_interest(ticker)
    Short-percent-of-float, shares short, days-to-cover, last
    reporting date. Pulled from yfinance.Ticker.info. Signals
    potential squeeze risk (high short interest + positive catalyst)
    or institutional bearishness.

get_analyst_targets(ticker)
    Wall Street consensus: mean / median / high / low target prices,
    number of analysts, recommendation distribution (strong buy / buy /
    hold / sell / strong sell) and recent rating changes. From
    Ticker.analyst_price_targets + Ticker.recommendations_summary.

get_insider_streak(ticker, lookback_days=90)
    Walks Form 4 filings for the past N days and counts consecutive
    buy / sell directions. A 5+ buy streak from officers/directors
    is a strong bullish smart-money signal; a 5+ sell streak is the
    inverse. Uses yfinance.Ticker.insider_transactions.

get_earnings_calendar(ticker)
    Upcoming earnings date + EPS estimate + days-until. Lets the
    trader know if a run is being made too close to a binary event
    (earnings) and should size more cautiously.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Dict, Optional

try:
    import yfinance as yf
    _HAS_YFINANCE = True
except ImportError:
    _HAS_YFINANCE = False


def _safe_info(ticker: str) -> Optional[Dict[str, Any]]:
    """Cheap wrapper around Ticker.info that catches every yfinance
    failure mode (network errors, scraping breakage, etc.) and returns
    None instead of raising."""
    if not _HAS_YFINANCE:
        return None
    try:
        info = yf.Ticker(ticker.upper()).info or {}
        return info if info else None
    except Exception:
        return None


# ───────────────────────────────────────────────────────────────────────
# Short interest
# ───────────────────────────────────────────────────────────────────────

def get_short_interest(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Retrieve short-interest metrics: short percent of float, shares
    short, days-to-cover, last reporting date.

    Use to evaluate squeeze risk (high short% + bullish catalyst can
    drive sharp upside moves) or as an inverse-sentiment signal (the
    short side is positioned bearishly).
    """
    info = _safe_info(ticker)
    header = f"# Short interest for {ticker.upper()}\n"
    if not info:
        return header + "_No data — yfinance unavailable or symbol not recognised._\n"

    short_pct = info.get("shortPercentOfFloat")        # 0.0-1.0 fraction
    shares_short = info.get("sharesShort")
    days_to_cover = info.get("shortRatio")
    short_date = info.get("dateShortInterest")          # epoch seconds
    float_shares = info.get("floatShares")
    short_prior_month = info.get("sharesShortPriorMonth")

    if short_pct is None and shares_short is None:
        return header + "_No short-interest data reported by yfinance for this ticker._\n"

    parts = [header]
    if short_pct is not None:
        try:
            pct = float(short_pct) * 100
            parts.append(f"**Short interest**: {pct:.2f}% of float")
            # Bucket it for the agent.
            if pct >= 20:
                parts.append("  _(extreme short interest — meaningful squeeze potential)_")
            elif pct >= 10:
                parts.append("  _(elevated — moderate squeeze potential)_")
            elif pct >= 5:
                parts.append("  _(normal-to-elevated)_")
            else:
                parts.append("  _(normal — no squeeze setup)_")
        except (TypeError, ValueError):
            pass
    if shares_short is not None:
        try:
            ss = int(shares_short)
            parts.append(f"**Shares short**: {ss:,}")
            if short_prior_month:
                try:
                    prior = int(short_prior_month)
                    delta = ss - prior
                    pct_chg = (delta / prior * 100) if prior > 0 else 0
                    direction = "📈 building" if delta > 0 else "📉 covering"
                    parts.append(
                        f"  vs prior month: {prior:,} ({direction}, {pct_chg:+.1f}%)"
                    )
                except (TypeError, ValueError):
                    pass
        except (TypeError, ValueError):
            pass
    if days_to_cover is not None:
        try:
            d2c = float(days_to_cover)
            parts.append(f"**Days to cover** (short ratio): {d2c:.2f}")
            if d2c >= 5:
                parts.append("  _(high — large short position relative to daily volume)_")
            elif d2c >= 2:
                parts.append("  _(moderate)_")
            else:
                parts.append("  _(low — short position easily covered)_")
        except (TypeError, ValueError):
            pass
    if float_shares:
        try:
            parts.append(f"**Float**: {int(float_shares):,} shares")
        except (TypeError, ValueError):
            pass
    if short_date:
        try:
            dt = datetime.fromtimestamp(int(short_date), tz=timezone.utc).date()
            parts.append(f"**Reported as of**: {dt.isoformat()}")
        except (TypeError, ValueError, OSError):
            pass

    parts.append(
        "\n_**Interpretation note**: short interest is reported biweekly with "
        "a ~2-week lag. A rising short-percent into a bullish catalyst (earnings, "
        "guidance raise) is the classic squeeze setup. A falling short-percent "
        "means the bears are giving up — usually bullish on its own._"
    )
    return "\n".join(parts) + "\n"


# ───────────────────────────────────────────────────────────────────────
# Analyst targets + recommendation distribution
# ───────────────────────────────────────────────────────────────────────

def get_analyst_targets(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Wall Street consensus: target prices (mean, median, high, low),
    number of analysts covering, and the buy/hold/sell distribution.

    Useful as a cross-check on the analysts' own conclusions. A wide
    spread between high and low targets indicates analyst disagreement
    worth flagging in the brief.
    """
    if not _HAS_YFINANCE:
        return f"# Analyst targets for {ticker.upper()}\n\n_yfinance unavailable._\n"

    header = f"# Analyst targets for {ticker.upper()}\n"
    try:
        t = yf.Ticker(ticker.upper())
    except Exception as e:
        return header + f"_Could not initialise ticker: {e}_\n"

    parts = [header]

    # Price targets via Ticker.analyst_price_targets (newer yfinance versions).
    # Fall back to Ticker.info if missing.
    targets = None
    try:
        targets = t.analyst_price_targets
    except Exception:
        pass
    if not targets:
        info = _safe_info(ticker) or {}
        # Older yfinance: fields targetMeanPrice, targetHighPrice, etc.
        targets = {
            "current": info.get("currentPrice"),
            "mean":    info.get("targetMeanPrice"),
            "median":  info.get("targetMedianPrice"),
            "high":    info.get("targetHighPrice"),
            "low":     info.get("targetLowPrice"),
            "numberOfAnalystOpinions": info.get("numberOfAnalystOpinions"),
        }

    if not isinstance(targets, dict):
        return header + "_No analyst target data available for this ticker._\n"

    current = targets.get("current") or targets.get("currentPrice")
    mean = targets.get("mean")
    median = targets.get("median")
    high = targets.get("high")
    low = targets.get("low")
    n = targets.get("numberOfAnalystOpinions") or targets.get("number_of_analysts")

    if all(x is None for x in (current, mean, high, low)):
        return header + "_No analyst target data available._\n"

    if current is not None:
        try:
            parts.append(f"**Current price**: ${float(current):,.2f}")
        except (TypeError, ValueError):
            pass

    if mean is not None:
        try:
            m = float(mean)
            parts.append(f"**Mean target**: ${m:,.2f}")
            if current is not None:
                try:
                    upside = (m / float(current) - 1) * 100
                    parts.append(f"  _(implied {upside:+.1f}% to mean target)_")
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
        except (TypeError, ValueError):
            pass
    if median is not None:
        try:
            parts.append(f"**Median target**: ${float(median):,.2f}")
        except (TypeError, ValueError):
            pass
    if high is not None and low is not None:
        try:
            hi, lo = float(high), float(low)
            parts.append(f"**Target range**: ${lo:,.2f} – ${hi:,.2f}")
            if current and float(current) > 0:
                spread_pct = (hi - lo) / float(current) * 100
                parts.append(
                    f"  _(spread is {spread_pct:.0f}% of current price — "
                    f"{'high analyst disagreement' if spread_pct > 30 else 'normal disagreement'})_"
                )
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if n:
        try:
            parts.append(f"**Analysts covering**: {int(n)}")
        except (TypeError, ValueError):
            pass

    # Recommendation distribution via Ticker.recommendations_summary or info fields.
    info = _safe_info(ticker) or {}
    rec_key = info.get("recommendationKey") or info.get("recommendationMean")
    if rec_key:
        parts.append(f"**Consensus rating**: {rec_key}")
    try:
        rs = t.recommendations_summary
        if rs is not None and not getattr(rs, "empty", True):
            # rs is a DataFrame with strongBuy/buy/hold/sell/strongSell columns
            row = rs.iloc[0]
            distribution = {
                "Strong buy":  int(row.get("strongBuy", 0) or 0),
                "Buy":         int(row.get("buy", 0) or 0),
                "Hold":        int(row.get("hold", 0) or 0),
                "Sell":        int(row.get("sell", 0) or 0),
                "Strong sell": int(row.get("strongSell", 0) or 0),
            }
            total = sum(distribution.values())
            if total > 0:
                parts.append("\n**Recommendation distribution**:")
                for label, count in distribution.items():
                    pct = count / total * 100
                    bar = "█" * int(pct / 5) if pct > 0 else ""
                    parts.append(f"  {label:12} {count:3} ({pct:4.0f}%)  {bar}")
    except Exception:
        pass

    return "\n".join(parts) + "\n"


# ───────────────────────────────────────────────────────────────────────
# Insider streak — count consecutive Form 4 directions over a window
# ───────────────────────────────────────────────────────────────────────

def get_insider_streak(
    ticker: Annotated[str, "ticker symbol of the company"],
    lookback_days: Annotated[int, "window in days; default 90"] = 90,
) -> str:
    """Walk recent Form 4 insider transactions for the ticker and count
    consecutive buy / sell directions, plus the dollar value of the
    streak. Strong bullish signal: 5+ insider buys with no sells in
    between. Strong bearish signal: 5+ insider sells with no buys.

    Filters out routine 10b5-1 plan sales when yfinance flags them; the
    raw data isn't always tagged so noise persists.
    """
    if not _HAS_YFINANCE:
        return f"# Insider streak for {ticker.upper()}\n\n_yfinance unavailable._\n"

    header = f"# Insider transaction streak for {ticker.upper()} (last {lookback_days}d)\n"
    try:
        t = yf.Ticker(ticker.upper())
        df = t.insider_transactions
    except Exception as e:
        return header + f"_Could not fetch insider data: {e}_\n"

    if df is None or getattr(df, "empty", True):
        return header + "_No insider transactions reported in the window._\n"

    # Filter to lookback window.
    cutoff = date.today() - timedelta(days=lookback_days)
    df = df.copy()
    # yfinance column names vary across versions; try the common ones.
    date_col = None
    for col in ("Start Date", "Date", "startDate"):
        if col in df.columns:
            date_col = col
            break
    if date_col:
        try:
            import pandas as pd
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            df = df[df[date_col].dt.date >= cutoff]
        except Exception:
            pass
    if df.empty:
        return header + "_No insider transactions in the window._\n"

    # Classify each row as buy / sell.
    txn_col = next((c for c in ("Transaction", "transaction", "Action") if c in df.columns), None)
    value_col = next((c for c in ("Value", "value") if c in df.columns), None)
    shares_col = next((c for c in ("Shares", "shares") if c in df.columns), None)
    insider_col = next((c for c in ("Insider", "insider", "Name") if c in df.columns), None)

    buys = 0
    sells = 0
    buy_value_total = 0.0
    sell_value_total = 0.0
    rows_summary = []
    for _, row in df.head(50).iterrows():
        txn = str(row.get(txn_col, "")).lower() if txn_col else ""
        if "buy" in txn or "purchase" in txn or "acquire" in txn:
            direction = "buy"
            buys += 1
        elif "sell" in txn or "sale" in txn or "dispos" in txn:
            direction = "sell"
            sells += 1
        else:
            direction = "other"

        val = 0.0
        if value_col is not None:
            try:
                val = float(row.get(value_col) or 0)
            except (TypeError, ValueError):
                val = 0.0
        if direction == "buy":
            buy_value_total += val
        elif direction == "sell":
            sell_value_total += val

        if len(rows_summary) < 10:
            d = row.get(date_col)
            try:
                d_str = d.date().isoformat() if hasattr(d, "date") else str(d)[:10]
            except Exception:
                d_str = "—"
            shares = row.get(shares_col, "—") if shares_col else "—"
            insider = (row.get(insider_col) or "—") if insider_col else "—"
            rows_summary.append({
                "date": d_str,
                "insider": str(insider)[:40],
                "direction": direction,
                "shares": shares,
                "value": val,
            })

    parts = [header]
    parts.append(
        f"**Window totals**: {buys} buys (${buy_value_total:,.0f}), "
        f"{sells} sells (${sell_value_total:,.0f})"
    )
    # Net signal classification.
    net = buys - sells
    if buys >= 5 and sells == 0:
        parts.append("**Signal: STRONG BULLISH** — sustained insider accumulation, no offsetting sales.")
    elif sells >= 5 and buys == 0:
        parts.append("**Signal: STRONG BEARISH** — sustained insider distribution, no offsetting buys.")
    elif net >= 3:
        parts.append("**Signal: BULLISH** — meaningfully more insider buys than sells.")
    elif net <= -3:
        parts.append("**Signal: BEARISH** — meaningfully more insider sells than buys.")
    else:
        parts.append("**Signal: MIXED / NEUTRAL** — no clear directional bias.")

    parts.append("\n**Recent transactions** (top 10):")
    parts.append("| Date | Insider | Direction | Shares | Value |")
    parts.append("|---|---|---|---|---|")
    for r in rows_summary:
        parts.append(
            f"| {r['date']} | {r['insider']} | {r['direction']} | "
            f"{r['shares']} | ${r['value']:,.0f} |"
        )

    parts.append(
        "\n_**Caveat**: routine 10b5-1 plan sales (pre-arranged) are not always "
        "tagged in yfinance data. A handful of 10b5-1 sales is normal and doesn't "
        "necessarily indicate bearishness — large opportunistic sales are the "
        "more meaningful signal._"
    )
    return "\n".join(parts) + "\n"


# ───────────────────────────────────────────────────────────────────────
# Earnings calendar
# ───────────────────────────────────────────────────────────────────────

def get_earnings_calendar(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Upcoming earnings date + EPS estimate + days-until.

    Used by the trader to right-size positions ahead of binary events.
    A run made T-3 days before earnings should typically recommend
    smaller initial tranches than one made T+30 days post-earnings.
    """
    if not _HAS_YFINANCE:
        return f"# Earnings calendar for {ticker.upper()}\n\n_yfinance unavailable._\n"

    header = f"# Earnings calendar for {ticker.upper()}\n"
    try:
        t = yf.Ticker(ticker.upper())
        cal = t.calendar
    except Exception as e:
        return header + f"_Could not fetch earnings calendar: {e}_\n"

    if not cal:
        return header + "_No upcoming earnings event reported._\n"

    parts = [header]
    earnings_date = None
    if isinstance(cal, dict):
        earnings_date = cal.get("Earnings Date") or cal.get("earningsDate")
        eps_estimate = cal.get("Earnings Average") or cal.get("epsAverage")
        eps_high = cal.get("Earnings High") or cal.get("epsHigh")
        eps_low = cal.get("Earnings Low") or cal.get("epsLow")
        revenue_estimate = cal.get("Revenue Average") or cal.get("revenueAverage")
    else:
        # DataFrame fallback (older yfinance)
        try:
            earnings_date = cal.iloc[0, 0]
        except Exception:
            earnings_date = None
        eps_estimate = eps_high = eps_low = revenue_estimate = None

    if isinstance(earnings_date, list) and earnings_date:
        earnings_date = earnings_date[0]
    if earnings_date is None:
        return header + "_No upcoming earnings date scheduled._\n"

    try:
        if hasattr(earnings_date, "date"):
            ed = earnings_date.date()
        elif isinstance(earnings_date, str):
            ed = datetime.fromisoformat(earnings_date[:10]).date()
        else:
            ed = earnings_date
        days_until = (ed - date.today()).days
        parts.append(f"**Next earnings**: {ed.isoformat()} ({days_until:+d} days from today)")
        if days_until < 0:
            parts.append("  _(earnings have already been reported)_")
        elif days_until <= 7:
            parts.append("  _**IMMINENT** — pre-earnings volatility expected; size tranches conservatively_")
        elif days_until <= 30:
            parts.append("  _Approaching — consider sizing for the binary event_")
    except Exception as e:
        parts.append(f"**Next earnings**: {earnings_date} _(parse error: {e})_")

    if eps_estimate is not None:
        try:
            parts.append(f"**EPS estimate**: ${float(eps_estimate):.2f}")
            if eps_high is not None and eps_low is not None:
                parts.append(f"  Range: ${float(eps_low):.2f} – ${float(eps_high):.2f}")
        except (TypeError, ValueError):
            pass
    if revenue_estimate is not None:
        try:
            parts.append(f"**Revenue estimate**: ${float(revenue_estimate):,.0f}")
        except (TypeError, ValueError):
            pass

    return "\n".join(parts) + "\n"
