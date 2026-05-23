// Universal finance glossary — definitions for every Wall Street term
// the analyst, trader, risk-debate, and PM agents might emit.
//
// Used by:
//   - components/Markdown.tsx — auto-wraps known terms in text-with-tooltips
//   - components/BriefPanel.tsx — same wrapping for structured brief fields
//   - any other place the LLM-generated text appears
//
// Audience reminder: a mechanical engineer. Definitions:
//   - 1 short sentence ideally; 2 sentences max
//   - Plain English; engineering analogies welcome when they clarify
//   - Bias toward "what does this MEAN for me" not textbook accuracy
//
// To add a term:
//   - Lowercase key
//   - Use \b word boundaries naturally — the matcher handles case-insensitive
//     full-word matching
//
// Keep entries sorted alphabetically within sections for easy maintenance.

export type GlossaryEntry = {
  /** What the term actually means in 1-2 plain-English sentences. */
  definition: string;
  /** Optional category for color-coding or filtering. */
  category?: "technical" | "fundamental" | "macro" | "options" | "rating" | "structure";
  /** Optional alias list — these phrases all point to the same definition. */
  aliases?: string[];
};

export const GLOSSARY: Record<string, GlossaryEntry> = {
  // ── Ratings & decisions ──
  "buy": {
    definition: "The framework's recommendation to start a new position or add a meaningful amount.",
    category: "rating",
  },
  "overweight": {
    definition: "Add more than your default allocation — gradually scale up.",
    category: "rating",
  },
  "underweight": {
    definition: "Reduce your position — typically sell about half over time.",
    category: "rating",
  },
  "hold": {
    definition: "Keep what you have, but don't add new money. The thesis is intact but no edge to push more.",
    category: "rating",
  },
  "sell": {
    definition: "Exit the position completely.",
    category: "rating",
  },
  "accumulate": {
    definition: "Buy gradually over time rather than all at once. A staged entry.",
    category: "rating",
  },
  "trim": {
    definition: "Sell part of the position — typically a quarter to a half.",
    category: "rating",
  },

  // ── Technical / price action ──
  "200-day sma": {
    definition: "Simple moving average over the last 200 trading days. A slow trend line; price above it is usually bullish.",
    category: "technical",
    aliases: ["200d sma", "200-day moving average", "200d ma", "sma 200", "200 sma"],
  },
  "50-day sma": {
    definition: "Simple moving average over the last 50 trading days. A medium-term trend line.",
    category: "technical",
    aliases: ["50d sma", "50-day moving average", "50d ma", "sma 50", "50 sma"],
  },
  "sma": {
    definition: "Simple Moving Average — the average closing price over a fixed number of days. Smooths price noise into a trend line.",
    category: "technical",
  },
  "ema": {
    definition: "Exponential Moving Average — like SMA but weights recent days more heavily, so it reacts faster to changes.",
    category: "technical",
  },
  "moving average": {
    definition: "Average closing price over a fixed lookback (e.g. 50, 200 days). Smooths noise to reveal the trend.",
    category: "technical",
  },
  "macd": {
    definition: "Momentum indicator — difference between two moving averages. Bullish crossover = price likely rising; bearish crossover = the opposite.",
    category: "technical",
  },
  "rsi": {
    definition: "Relative Strength Index — 0-100 oscillator. Above 70 = potentially overbought, below 30 = potentially oversold.",
    category: "technical",
  },
  "bollinger bands": {
    definition: "Three lines — a moving average with bands 2 standard deviations above and below. Wide bands = high volatility, narrow = low.",
    category: "technical",
    aliases: ["bollinger band", "bollinger"],
  },
  "atr": {
    definition: "Average True Range — average daily price swing over a lookback period. Used to size stops relative to typical volatility.",
    category: "technical",
  },
  "support": {
    definition: "Price level where buying has reliably stepped in before. Acts as a floor until it breaks.",
    category: "technical",
  },
  "resistance": {
    definition: "Price level where selling has reliably stepped in before. Acts as a ceiling until it breaks.",
    category: "technical",
  },
  "breakout": {
    definition: "Price closing decisively above a resistance level — often interpreted as the start of a new uptrend.",
    category: "technical",
  },
  "breakdown": {
    definition: "Price closing decisively below a support level — often interpreted as the start of a new downtrend.",
    category: "technical",
  },
  "golden cross": {
    definition: "When the 50-day moving average crosses above the 200-day moving average. A classic bullish trend signal.",
    category: "technical",
  },
  "death cross": {
    definition: "When the 50-day moving average crosses below the 200-day moving average. A classic bearish trend signal.",
    category: "technical",
  },
  "52-week high": {
    definition: "The highest price the stock has traded at in the past year. Breaks above this often attract trend-followers.",
    category: "technical",
  },
  "52-week low": {
    definition: "The lowest price the stock has traded at in the past year.",
    category: "technical",
  },
  "tradeable levels": {
    definition: "Specific prices that, if hit, change what you should do. Like control limits on a process chart — cross them and act.",
    category: "technical",
  },
  "trend break": {
    definition: "Price closing below the long-term trend (typically the 200-day moving average). Suggests the primary uptrend is in question.",
    category: "technical",
  },
  "primary trend": {
    definition: "The dominant multi-month direction of the stock, usually defined by where price sits relative to the 200-day average.",
    category: "technical",
  },
  "neutral chop": {
    definition: "Price oscillating in a range without a clear direction. Wait for a breakout or breakdown before acting.",
    category: "technical",
  },
  "daily close": {
    definition: "The official price at market close (4pm ET). Closing prices matter more than intraday wiggles for trend signals.",
    category: "technical",
  },

  // ── Order types ──
  "gtc": {
    definition: "Good-Till-Cancelled order — a standing buy or sell order that stays active until filled or you cancel it.",
    category: "structure",
    aliases: ["gtc limit", "gtc order"],
  },
  "limit order": {
    definition: "An order that only fills at your specified price or better. Lets you control entry/exit price but may never fill.",
    category: "structure",
  },
  "stop loss": {
    definition: "A pre-placed order to sell automatically if the price falls to a chosen level — caps how much you can lose.",
    category: "structure",
    aliases: ["stop-loss", "stop"],
  },
  "take profit": {
    definition: "A pre-placed order to sell when the price reaches a target — locks in gains automatically.",
    category: "structure",
    aliases: ["take-profit"],
  },
  "moc": {
    definition: "Market-On-Close — an order that fills at the day's closing price. Common for institutional rebalancing.",
    category: "structure",
  },
  "market order": {
    definition: "Buy/sell immediately at whatever price the market gives you. Guarantees fill but not price.",
    category: "structure",
  },

  // ── Sizing & position ──
  "tranche": {
    definition: "One piece of a staged buy. 'Tranche 1' = first piece, 'tranche 2' = second piece on a different trigger.",
    category: "structure",
    aliases: ["tranches"],
  },
  "dollar cost average": {
    definition: "Spreading purchases over time at a fixed dollar amount per interval, instead of all at once. Reduces timing risk.",
    category: "structure",
    aliases: ["dca", "dollar-cost average"],
  },

  // ── Fundamentals ──
  "p/e": {
    definition: "Price-to-Earnings ratio — stock price divided by past 12 months of earnings per share. Lower is cheaper for the same earnings.",
    category: "fundamental",
    aliases: ["p/e ratio", "pe ratio", "pe"],
  },
  "peg": {
    definition: "Price/Earnings-to-Growth ratio — P/E divided by expected earnings growth. Below 1.0 often considered cheap; lower is better.",
    category: "fundamental",
    aliases: ["peg ratio"],
  },
  "ev/ebitda": {
    definition: "Enterprise Value divided by earnings before interest/tax/depreciation. A debt-aware version of P/E; lower = cheaper.",
    category: "fundamental",
  },
  "fcf": {
    definition: "Free Cash Flow — cash a company generates after operating expenses and capital investment. Real money available to shareholders.",
    category: "fundamental",
    aliases: ["free cash flow"],
  },
  "fcf yield": {
    definition: "Annual free cash flow divided by market cap, as a percent. Like a dividend yield but using all distributable cash.",
    category: "fundamental",
    aliases: ["free cash flow yield"],
  },
  "operating margin": {
    definition: "Operating profit as a percentage of revenue. Higher = more efficient business model.",
    category: "fundamental",
  },
  "gross margin": {
    definition: "Revenue minus direct cost of goods, as a percentage of revenue. Higher = stronger pricing power.",
    category: "fundamental",
  },
  "roic": {
    definition: "Return on Invested Capital — profit generated per dollar of capital deployed. Above the cost of capital = value creation.",
    category: "fundamental",
  },
  "wacc": {
    definition: "Weighted Average Cost of Capital — the blended cost of company's debt and equity. The hurdle rate for new investments.",
    category: "fundamental",
  },
  "eps": {
    definition: "Earnings Per Share — total profit divided by shares outstanding. The 'E' in P/E.",
    category: "fundamental",
  },
  "yoy": {
    definition: "Year-over-Year — comparing a metric to the same period one year ago (e.g. Q3 2025 revenue vs Q3 2024).",
    category: "fundamental",
    aliases: ["year over year", "year-over-year"],
  },
  "qoq": {
    definition: "Quarter-over-Quarter — comparing a metric to the immediately prior quarter.",
    category: "fundamental",
    aliases: ["quarter over quarter"],
  },
  "ttm": {
    definition: "Trailing Twelve Months — the sum of the last four quarterly periods, regardless of fiscal year boundaries.",
    category: "fundamental",
  },
  "guidance": {
    definition: "Management's forward forecast for revenue and earnings, given on the quarterly earnings call. 'Raised guidance' = good signal.",
    category: "fundamental",
  },
  "consensus": {
    definition: "Average of analyst forecasts for revenue or EPS. 'Beat consensus' = reported number was above the average estimate.",
    category: "fundamental",
  },
  "revision breadth": {
    definition: "Net count of analysts revising their EPS estimates up minus down over a recent period. Strongly correlated with near-term price direction.",
    category: "fundamental",
  },

  // ── Risk / portfolio ──
  "alpha": {
    definition: "Return above what a benchmark (like SPY) returned over the same period. Positive alpha = beat the index.",
    category: "fundamental",
  },
  "beta": {
    definition: "How much a stock moves relative to the market. Beta of 1 = moves with the market; 1.5 = moves 50% more; 0.5 = half as much.",
    category: "fundamental",
  },
  "sharpe": {
    definition: "Sharpe ratio — return per unit of risk (volatility). Higher = better risk-adjusted return; above 1 is considered good.",
    category: "fundamental",
    aliases: ["sharpe ratio"],
  },
  "drawdown": {
    definition: "The peak-to-trough decline in value. 20% drawdown = lost 20% from the recent high before recovering.",
    category: "fundamental",
    aliases: ["max drawdown"],
  },
  "var": {
    definition: "Value-at-Risk — the maximum loss expected on a typical bad day (e.g. 95% confidence). A normal-day worst-case.",
    category: "fundamental",
  },
  "correlation": {
    definition: "How tightly two stocks move together (-1 to +1). Above 0.7 = essentially the same bet; below 0.3 = diversification.",
    category: "fundamental",
  },

  // ── Options ──
  "implied volatility": {
    definition: "What the options market is pricing in for future price swings. High IV = market expects big moves; low IV = expects calm.",
    category: "options",
    aliases: ["iv", "implied vol"],
  },
  "iv percentile": {
    definition: "Where current implied volatility sits in its 1-year range. Above 80 = unusually high; below 20 = unusually low.",
    category: "options",
  },
  "put/call ratio": {
    definition: "Volume of puts (bearish bets) divided by calls (bullish bets). Above 1 = bearish positioning, below 1 = bullish.",
    category: "options",
    aliases: ["put-call ratio"],
  },
  "vix": {
    definition: "Index of S&P 500 implied volatility — 'fear gauge'. Under 15 = calm, over 25 = stressed, over 35 = panic.",
    category: "options",
  },

  // ── Macro ──
  "yield curve": {
    definition: "Plot of Treasury bond yields by maturity. Normally upward-sloping; an inverted curve (long yields < short) often precedes a recession.",
    category: "macro",
  },
  "fed": {
    definition: "Federal Reserve — sets short-term interest rates. Rate cuts typically boost stocks; hikes typically pressure them.",
    category: "macro",
    aliases: ["federal reserve", "fomc"],
  },
  "qe": {
    definition: "Quantitative Easing — Fed buying bonds to inject money into the financial system. Boosts asset prices including stocks.",
    category: "macro",
    aliases: ["quantitative easing"],
  },
  "dxy": {
    definition: "US Dollar Index — measures USD strength vs a basket of major currencies. Rising USD often pressures US multinationals' earnings.",
    category: "macro",
  },

  // ── Patterns / jargon to plain English ──
  "mean reversion": {
    definition: "The tendency for prices to return to a long-run average after extreme moves. 'Buy the dip' is a mean-reversion strategy.",
    category: "technical",
  },
  "multiple compression": {
    definition: "When a stock's P/E ratio shrinks — typically the stock falls even though earnings stay the same. Often happens when growth slows.",
    category: "fundamental",
  },
  "multiple expansion": {
    definition: "Opposite of compression — P/E ratio grows, meaning price rises faster than earnings. Common in bull markets.",
    category: "fundamental",
  },
  "sector rotation": {
    definition: "Money flowing from one sector (e.g. tech) to another (e.g. financials). Often signals a change in market regime.",
    category: "structure",
  },
  "capex": {
    definition: "Capital Expenditure — money companies spend on long-term assets like factories or chips. High capex = investing for growth.",
    category: "fundamental",
    aliases: ["capital expenditure"],
  },
  "hyperscaler": {
    definition: "Big cloud-infrastructure provider (Amazon AWS, Microsoft Azure, Google Cloud, Meta). Their capex drives chip + datacenter demand.",
    category: "fundamental",
  },
  "defer to fundamentals": {
    definition: "When the technical signal (price chart) breaks down, fall back on the company's underlying business strength to decide.",
    category: "structure",
  },
};

// Build a flat lookup map: every term + alias points to a single entry.
// Keys are lowercase for case-insensitive matching.
type LookupEntry = { canonical: string; entry: GlossaryEntry };
let _lookup: Map<string, LookupEntry> | null = null;

function buildLookup(): Map<string, LookupEntry> {
  const m = new Map<string, LookupEntry>();
  for (const [canonical, entry] of Object.entries(GLOSSARY)) {
    m.set(canonical.toLowerCase(), { canonical, entry });
    for (const alias of entry.aliases || []) {
      m.set(alias.toLowerCase(), { canonical, entry });
    }
  }
  return m;
}

export function lookupTerm(text: string): LookupEntry | undefined {
  if (!_lookup) _lookup = buildLookup();
  return _lookup.get(text.toLowerCase());
}

/** All searchable forms (canonical + aliases), longest-first, for regex matching. */
export function allSearchablePhrases(): string[] {
  if (!_lookup) _lookup = buildLookup();
  return Array.from(_lookup.keys()).sort((a, b) => b.length - a.length);
}

/** Combine the global glossary with any brief-specific terms. */
export function mergeGlossary(
  briefGlossary: Record<string, string> | null | undefined,
): Map<string, LookupEntry> {
  const base = new Map(buildLookup());
  if (briefGlossary) {
    for (const [term, def] of Object.entries(briefGlossary)) {
      base.set(term.toLowerCase(), {
        canonical: term,
        entry: { definition: def },
      });
    }
  }
  return base;
}
