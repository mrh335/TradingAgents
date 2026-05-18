from langchain_core.tools import tool
from typing import Annotated
from tradingagents.dataflows.interface import route_to_vendor

@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve news data for a given ticker symbol.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data
    """
    return route_to_vendor("get_news", ticker, start_date, end_date)

@tool
def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back"] = 7,
    limit: Annotated[int, "Maximum number of articles to return"] = 5,
) -> str:
    """
    Retrieve global news data.
    Uses the configured news_data vendor.
    Args:
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Number of days to look back (default 7)
        limit (int): Maximum number of articles to return (default 5)
    Returns:
        str: A formatted string containing global news data
    """
    return route_to_vendor("get_global_news", curr_date, look_back_days, limit)

@tool
def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """
    Retrieve insider transaction information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A report of insider transaction data
    """
    return route_to_vendor("get_insider_transactions", ticker)


@tool
def get_congress_trades(
    ticker: Annotated[str, "ticker symbol"],
    lookback_days: Annotated[int, "how many days of history to fetch (default 90)"] = 90,
) -> str:
    """
    Retrieve congressional stock trading disclosures for a ticker — recent
    buy/sell transactions reported by US House and Senate members under the
    STOCK Act. Returns aggregated party / chamber bias plus a per-trade
    table.

    Filings are reported 30-45 days AFTER the trade, so treat heavy clusters
    of same-direction activity as a lagging confirmation signal rather than
    a leading indicator. A quiet window may just reflect filing lag.

    Args:
        ticker (str): Ticker symbol of the company
        lookback_days (int): How many days of history to fetch (default 90)
    Returns:
        str: A markdown report with summary stats and the per-trade table
    """
    return route_to_vendor("get_congress_trades", ticker, lookback_days)


@tool
def get_short_interest(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """
    Retrieve short-interest metrics for a ticker: short percent of float,
    shares short, days-to-cover, vs prior month change.

    High short interest (>10% of float) plus a bullish catalyst is the
    classic squeeze setup. Falling short interest typically signals
    bears giving up — usually bullish on its own.

    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A markdown report bucketed by short% / days-to-cover severity
    """
    return route_to_vendor("get_short_interest", ticker)


@tool
def get_analyst_targets(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """
    Wall Street consensus for a ticker: mean / median / high / low target
    prices, number of analysts covering, recommendation distribution
    (strong-buy / buy / hold / sell / strong-sell).

    Use as a cross-check on the in-house analyst conclusions. A wide
    target spread (>30% of current price) indicates meaningful Wall Street
    disagreement worth flagging in the brief.

    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A markdown report with target prices + implied upside + recommendation distribution
    """
    return route_to_vendor("get_analyst_targets", ticker)


@tool
def get_insider_streak(
    ticker: Annotated[str, "ticker symbol"],
    lookback_days: Annotated[int, "window in days (default 90)"] = 90,
) -> str:
    """
    Walk recent Form 4 insider transactions for a ticker and count
    consecutive buy / sell directions over the window. Classifies the net
    activity as STRONG BULLISH (5+ buys, 0 sells), BULLISH, NEUTRAL,
    BEARISH, or STRONG BEARISH.

    Form 4 filings are fast (<= 2 business days), making this a leading
    smart-money signal — much sharper than the 13F (quarterly) or
    congressional (30-45d lag) feeds.

    Args:
        ticker (str): Ticker symbol of the company
        lookback_days (int): Days of history to scan (default 90)
    Returns:
        str: A markdown report with the net signal + per-transaction detail
    """
    return route_to_vendor("get_insider_streak", ticker, lookback_days)


@tool
def get_earnings_calendar(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """
    Upcoming earnings date for a ticker plus EPS / revenue estimates and
    days-until. Used by the trader to right-size positions ahead of
    binary events — a run made T-3 days before earnings should recommend
    smaller initial tranches than one T+30 days post-earnings.

    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A markdown report with earnings date + estimate + days-until severity flag
    """
    return route_to_vendor("get_earnings_calendar", ticker)
