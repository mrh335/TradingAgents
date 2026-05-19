// External-references hub — curated set of sites a self-managed
// investor should know about, organized by what you're trying to do.

type Ref = {
  title: string;
  url: string;
  blurb: string;
  paywall?: boolean;
};

type Section = {
  heading: string;
  intro: string;
  refs: Ref[];
};

const SECTIONS: Section[] = [
  {
    heading: "Earnings",
    intro:
      "When the market's reaction to a specific quarter matters more than headlines.",
    refs: [
      {
        title: "Earnings Whispers",
        url: "https://www.earningswhispers.com",
        blurb:
          "Whisper numbers (informal pre-print estimates) + earnings calendar with confirmed times. The whisper vs official-consensus gap is often a stronger pre-print signal than consensus alone.",
      },
      {
        title: "Stockanalysis.com",
        url: "https://stockanalysis.com",
        blurb:
          "Clean financial statements (5+ years of income/balance/cash flow), ratios, and a good earnings transcript archive. Free.",
      },
      {
        title: "Seeking Alpha — Earnings Calendar",
        url: "https://seekingalpha.com/earnings/earnings-calendar",
        blurb:
          "Calendar + analyst-revision visibility. Their transcripts are paywalled but the headline pages are free.",
        paywall: true,
      },
      {
        title: "Zacks — Earnings ESP",
        url: "https://www.zacks.com/earnings/earnings-calendar",
        blurb:
          "Their 'Earnings ESP' (the most-recent-analyst-revision number) has academic support as a beat-rate predictor.",
      },
    ],
  },
  {
    heading: "13F & institutional holdings",
    intro:
      "Who else owns this stock and how their position is changing each quarter.",
    refs: [
      {
        title: "13f.info",
        url: "https://13f.info",
        blurb:
          "Best free reader for 13F-HR filings. Per-manager and per-stock views with QoQ changes. Use after the in-app /holders page when you want to dig into a manager's full history.",
      },
      {
        title: "WhaleWisdom",
        url: "https://whalewisdom.com",
        blurb:
          "Top-holders rankings, position-tracking alerts. Some features paywalled but the basic search is free.",
        paywall: true,
      },
      {
        title: "SEC EDGAR — full filings",
        url: "https://www.sec.gov/edgar/search/",
        blurb:
          "The canonical source. Search by company name or CIK. The /holders page in this app reads directly from EDGAR.",
      },
    ],
  },
  {
    heading: "Insider trading & Congress",
    intro: "Who's actually moving their own money in this stock right now.",
    refs: [
      {
        title: "OpenInsider",
        url: "http://openinsider.com",
        blurb:
          "Best free aggregation of Form 4 insider buys/sells. Sortable by cluster size, value, % of holdings.",
      },
      {
        title: "CapitolTrades",
        url: "https://www.capitoltrades.com",
        blurb:
          "Congress-member trades disclosed under the STOCK Act. Filterable by member, party, transaction date.",
      },
      {
        title: "Quiver Quantitative",
        url: "https://www.quiverquant.com",
        blurb:
          "Aggregates Congress, insider, Senate, lobbying data. Some free dashboards; deeper data tier paywalled.",
        paywall: true,
      },
    ],
  },
  {
    heading: "News & analysis",
    intro:
      "Reading rotation for the trading day. Mix free wires + a few paid for depth.",
    refs: [
      {
        title: "Reuters Business",
        url: "https://www.reuters.com/business/",
        blurb: "Wire-quality news, free, fast.",
      },
      {
        title: "Bloomberg",
        url: "https://www.bloomberg.com/markets",
        blurb: "Highest-signal market reporting but mostly paywalled.",
        paywall: true,
      },
      {
        title: "Yahoo Finance — quote pages",
        url: "https://finance.yahoo.com",
        blurb:
          "Free quote pages with reasonable news aggregation. This app pulls from yfinance which sources from here.",
      },
      {
        title: "Benzinga",
        url: "https://www.benzinga.com",
        blurb:
          "Trade-focused news flow with strong analyst-rating change coverage. Free tier covers most of it.",
      },
      {
        title: "Financial Times",
        url: "https://www.ft.com",
        blurb: "European angle + thoughtful longform. Mostly paywalled.",
        paywall: true,
      },
    ],
  },
  {
    heading: "Charts",
    intro: "When you need a chart that's better than Yahoo's.",
    refs: [
      {
        title: "TradingView",
        url: "https://www.tradingview.com/chart",
        blurb:
          "Most-flexible free charts. Multi-pane studies, drawing tools, screener. Paid tier unlocks alerts + intraday data.",
      },
      {
        title: "StockCharts.com",
        url: "https://stockcharts.com",
        blurb:
          "Old-school but pristine technical-analysis tooling. Strong point-and-figure charts which most platforms ignore.",
      },
      {
        title: "Finviz",
        url: "https://finviz.com/map.ashx",
        blurb:
          "Free market heatmap (sector and stock-level), good for at-a-glance regime checks. Screener is excellent.",
      },
    ],
  },
  {
    heading: "Short interest & dark pool",
    intro:
      "Off-exchange volume + bearish-positioning signals that don't show up in price.",
    refs: [
      {
        title: "Fintel — short interest",
        url: "https://fintel.io/screen/us/short-interest",
        blurb:
          "Per-ticker short-interest history + days-to-cover. Some data tier-gated but the headline numbers are free.",
      },
      {
        title: "Stockanalysis.com short interest",
        url: "https://stockanalysis.com/markets/most-shorted-stocks/",
        blurb: "Quick top-shorted-stocks list. Free.",
      },
      {
        title: "FINRA OTC Transparency",
        url: "https://otctransparency.finra.org",
        blurb:
          "Official source for off-exchange (ATS / dark pool) volume reported weekly. Use to spot stocks where the off-exchange ratio is unusually high.",
      },
    ],
  },
  {
    heading: "Macro & rates",
    intro: "Cross-checks for whether the market is in a normal regime.",
    refs: [
      {
        title: "FRED (St. Louis Fed)",
        url: "https://fred.stlouisfed.org",
        blurb:
          "Authoritative free database of every macro series you'd want — rates, employment, inflation, money supply. Bookmark.",
      },
      {
        title: "U.S. Treasury — yield curve",
        url: "https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics",
        blurb:
          "Official daily Treasury yield curve table. 2/10 spread inversion is a classic recession lead indicator.",
      },
      {
        title: "BEA",
        url: "https://www.bea.gov",
        blurb: "GDP + personal income data direct from the source.",
      },
    ],
  },
  {
    heading: "Options & volatility",
    intro:
      "What the options market is paying for protection. Often leads price.",
    refs: [
      {
        title: "Barchart — options",
        url: "https://www.barchart.com/options",
        blurb:
          "Free implied-vol percentile, put/call ratios, unusual-activity scans. Good free tier.",
      },
      {
        title: "MarketChameleon",
        url: "https://marketchameleon.com",
        blurb:
          "Best free implied-volatility surface visualizer. Useful around earnings to gauge expected move.",
      },
      {
        title: "CBOE VIX dashboard",
        url: "https://www.cboe.com/tradable_products/vix/",
        blurb: "Live VIX, VIX9D, term structure. Source for the macro dashboard.",
      },
    ],
  },
  {
    heading: "ETF flows & sector rotation",
    intro: "Where money is actually moving at the asset-class level.",
    refs: [
      {
        title: "ETF.com",
        url: "https://www.etf.com",
        blurb:
          "Daily ETF flows, asset-class movers, sector rotation views. Free.",
      },
      {
        title: "VettaFi (ETF Trends)",
        url: "https://www.etftrends.com",
        blurb: "ETF news with a particular slant toward fund-flow analysis.",
      },
    ],
  },
  {
    heading: "Tools we already use",
    intro:
      "These power data inside this app — you'll rarely need to visit them directly, but useful to know.",
    refs: [
      {
        title: "yfinance (Yahoo Finance Python)",
        url: "https://github.com/ranaroussi/yfinance",
        blurb:
          "Source for live prices, news, earnings dates, calendar, and historical price data throughout this app.",
      },
      {
        title: "SEC EDGAR submissions API",
        url: "https://www.sec.gov/edgar/sec-api-documentation",
        blurb:
          "Source for /holders 13F filings. No API key needed, just a User-Agent header.",
      },
    ],
  },
];

export default function ReferencesPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">External references</h1>
        <p className="text-muted text-sm">
          Curated list of sites worth bookmarking for anything this app
          doesn&apos;t already do. Organized by what you&apos;re trying to
          accomplish — earnings, 13F, insider trades, charts, options,
          macro, etc.
        </p>
      </header>

      {SECTIONS.map((s) => (
        <section key={s.heading} className="space-y-2">
          <h2 className="text-lg font-semibold">{s.heading}</h2>
          <p className="text-muted text-xs">{s.intro}</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {s.refs.map((r) => (
              <a
                key={r.url}
                href={r.url}
                target="_blank"
                rel="noopener noreferrer"
                className="card hover:bg-surface transition-colors group"
              >
                <div className="flex items-baseline gap-2">
                  <span className="font-semibold group-hover:text-accent">
                    {r.title} ↗
                  </span>
                  {r.paywall && (
                    <span className="text-xs text-warning">$ paywall</span>
                  )}
                </div>
                <div className="text-xs text-muted mt-1">{r.blurb}</div>
              </a>
            ))}
          </div>
        </section>
      ))}

      <div className="card text-xs text-muted">
        <strong>Missing something you use regularly?</strong> Edit{" "}
        <code>web/app/references/page.tsx</code> in the repo and add it.
        Section list lives at the top of the file — keep blurbs short and
        say what the site is actually <em>for</em>, not what it claims to be.
      </div>
    </div>
  );
}
