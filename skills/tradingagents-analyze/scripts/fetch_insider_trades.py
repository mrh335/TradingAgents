"""fetch_insider_trades.py — pull recent SEC Form 4 (insider) transactions for a ticker.

Uses the SEC EDGAR submissions API (https://data.sec.gov/submissions/CIK<n>.json)
and the ticker→CIK mapping from www.sec.gov/files/company_tickers.json. No
API key required, but SEC requires a User-Agent header identifying the
caller.

Form 4 covers transactions by officers/directors/10%+ owners. Reporting
is fast (typically <= 2 business days after the trade).

Usage:
    python fetch_insider_trades.py <TICKER> [--lookback-days N] [--since-iso ISO]
                                             [--output <path>]

Output JSON:
    {
      "ticker": "NVDA",
      "cik": "0001045810",
      "fetched_at": "<UTC ISO>",
      "lookback_days": 90,
      "since_iso": "<optional>",
      "filings": [
        {
          "accession_number": "0001127602-25-...",
          "form": "4",
          "filed_date": "2026-05-10",
          "report_date": "2026-05-08",
          "primary_doc_url": "https://www.sec.gov/Archives/edgar/data/...",
          "filer_name": "...",
          "filer_role": "Director|Officer|10%+ Owner|...",
          "note": "..."
        }
      ],
      "fetch_warnings": [...]
    }

NOTE: We surface the filing metadata, not the parsed transaction details.
Parsing Form 4 XML to extract specific transaction types/amounts/prices is
more involved (one filing can have multiple transactions); we link to the
primary doc URL so the analyst can read it directly when needed. A later
enhancement can deep-parse the XML.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_TPL = "https://data.sec.gov/submissions/CIK{cik}.json"
USER_AGENT = "tradingagents-analyze/0.1 (Claude Code skill; contact: skill-author@example.com)"


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _http_get_json(url: str, timeout: int = 20):
    req = urllib.request.Request(
        url, method="GET",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ticker_to_cik(ticker: str, warnings: list[str]) -> str | None:
    """SEC's tickers.json maps ticker → CIK. Cache hit isn't critical for
    one-shot use; just fetch fresh each time."""
    try:
        doc = _http_get_json(SEC_TICKERS_URL)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        warnings.append(f"SEC tickers fetch failed: {e}")
        return None
    upper = ticker.upper()
    for entry in (doc or {}).values():
        if (entry.get("ticker") or "").upper() == upper:
            cik = entry.get("cik_str")
            if cik is not None:
                return str(int(cik)).zfill(10)
    warnings.append(f"ticker {ticker} not found in SEC tickers.json")
    return None


def _fetch_form4_filings(cik: str, lookback_days: int,
                         warnings: list[str]) -> list[dict]:
    url = SEC_SUBMISSIONS_TPL.format(cik=cik)
    _eprint(f"GET {url}")
    try:
        doc = _http_get_json(url)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        warnings.append(f"SEC submissions fetch failed: {e}")
        return []

    recent = (doc.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    filed_dates = recent.get("filingDate") or []
    report_dates = recent.get("reportDate") or []
    accession = recent.get("accessionNumber") or []
    primary_doc = recent.get("primaryDocument") or []
    primary_doc_desc = recent.get("primaryDocDescription") or []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()
    out: list[dict] = []
    for i, form in enumerate(forms):
        if form not in ("4", "4/A"):
            continue
        if i >= len(filed_dates):
            continue
        try:
            filed = datetime.fromisoformat(filed_dates[i]).date()
        except ValueError:
            continue
        if filed < cutoff:
            continue
        acc = accession[i] if i < len(accession) else ""
        acc_clean = acc.replace("-", "")
        pdoc = primary_doc[i] if i < len(primary_doc) else ""
        url_pdoc = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{pdoc}"
            if pdoc and acc_clean else ""
        )
        out.append({
            "accession_number": acc,
            "form": form,
            "filed_date": filed.isoformat(),
            "report_date": (report_dates[i] if i < len(report_dates) else None),
            "primary_doc_url": url_pdoc,
            "primary_doc_description": (
                primary_doc_desc[i] if i < len(primary_doc_desc) else None
            ),
        })
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("ticker")
    p.add_argument("--lookback-days", type=int, default=90)
    p.add_argument("--since-iso", default=None,
                   help="Filter to filings on/after this ISO date.")
    p.add_argument("--output", "-o", default=None)
    args = p.parse_args()

    warnings: list[str] = []
    cik = _ticker_to_cik(args.ticker, warnings)

    filings = []
    if cik:
        filings = _fetch_form4_filings(cik, args.lookback_days, warnings)

    # Secondary filter by --since-iso
    if args.since_iso:
        try:
            since_date = datetime.fromisoformat(args.since_iso.replace("Z", "+00:00")).date()
            filings = [f for f in filings if f["filed_date"] >= since_date.isoformat()]
        except ValueError:
            warnings.append(f"invalid --since-iso: {args.since_iso!r}")

    out = {
        "ticker": args.ticker.upper(),
        "cik": cik,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lookback_days": args.lookback_days,
        "since_iso": args.since_iso,
        "filings": filings,
        "fetch_warnings": warnings,
    }

    if args.output:
        out_path = Path(args.output)
    else:
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"insider_form4_{args.ticker}_", suffix=".json",
            delete=False, mode="w", encoding="utf-8",
        )
        out_path = Path(tmp.name)
        tmp.close()

    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    _eprint(f"OK: {len(filings)} Form 4 filings; wrote {out_path}")
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
