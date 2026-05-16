# Data sources

Phase-1 sources only. Phase-3 adds congress trades, insider Form 4,
sector/macro depth, and earnings calendar — see the plan at
`C:\Users\markh\.claude\plans\is-it-possible-to-stateless-wombat.md`.

## yfinance (no auth)

Used by `fetch_market_data.py` for: price history, fundamentals,
financial statements, company profile, recent news, and a snapshot of
the major indices + sector ETFs in `global_macro`.

**Install:** `pip install yfinance`

**Failure modes:**
- Rate-limited if you hammer it. The skill makes ~13 calls per run
  (1 ticker + ~12 macro/sector ETFs). One run is fine; back-to-back
  ensembles may need short delays.
- Some tickers (ADRs, less-traded stocks) have sparse financial data —
  the script writes `null` and notes it in `fetch_warnings`.

## stockstats (no auth)

Used by `compute_indicators.py` to compute the indicator set
(`close_50_sma`, `macd`, `rsi`, `boll`, `atr`, `vwma`, etc.). Falls
back to a pandas-only implementation if not installed, but stockstats
matches the existing framework's indicator names exactly.

**Install:** `pip install stockstats`

## matplotlib (server-side only)

Used by the framework's `/charts/decisions/{ticker}.png` endpoint to
render the decision-history chart on the server. Listed in the
framework's `service` extras (pyproject.toml). The skill's
`plot_decision_history.py` script is a thin HTTP client; no matplotlib
needed on the machine running Claude Code.

## tiktoken (no auth)

Used by `token_logger.py` for input/output token estimation. Falls back
to a `word_count * 1.3` heuristic if not installed.

**Install:** `pip install tiktoken`

## jsonschema (no auth)

Used by `build_brief.py` and `build_archive.py` for shape validation.
Strongly recommended — without it the scripts run a much-reduced
structural check.

**Install:** `pip install jsonschema`

## PyYAML (no auth)

Used by `publish.py` and `token_logger.py` to read `config/defaults.yaml`.

**Install:** `pip install pyyaml`

---

## All-in-one install (skill side)

```powershell
pip install yfinance stockstats jsonschema pyyaml tiktoken
```

Total dependency footprint is small; pandas and numpy come transitively
with yfinance. **No matplotlib** — the decision-history chart is
rendered server-side.

---

## Phase-3 sources (not yet implemented)

| Source | Use | Endpoint / library | Auth |
|---|---|---|---|
| Capitol Trades | Congress trades | `https://bff.capitoltrades.com/trades` (unofficial) or RSS feed | None |
| SEC EDGAR | Insider Form 4 | `https://data.sec.gov/submissions/CIK<n>.json` | User-Agent header only |
| FRED | Macro indicators | `fredapi` lib | `FRED_API_KEY` (free) |
| Finnhub | News breadth | HTTP | Free tier key |
| Alpha Vantage | Backup intraday | Existing framework wrappers | `ALPHA_VANTAGE_API_KEY` |
