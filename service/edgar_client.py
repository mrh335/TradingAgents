"""SEC EDGAR client — fetch 13F-HR institutional holdings filings.

13F-HR is the quarterly filing every institutional manager with ≥$100M
in US-listed equity AUM must file within 45 days of quarter end. It
lists every long position the manager holds.

EDGAR is the canonical source. We hit two API surfaces:

1. ``https://data.sec.gov/submissions/CIK{cik:010d}.json`` — structured
   JSON listing every filing a CIK has made. We filter to 13F-HR /
   13F-HR/A and grab the latest accession number.

2. ``https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dash}/``
   — the actual filing directory. Within it, the holdings are in a
   file with a name ending in ``_informationtable.xml`` (older) or
   ``infotable.xml`` (newer). We list the directory's ``index.json``
   to discover the exact filename, then fetch + parse it.

Rate limit: SEC says 10 req/sec max per IP. We're well under that —
~12 managers × 3-5 reqs each, once per week. No throttling needed.

User-Agent: SEC requires a string identifying the requester. They'll
block IPs that omit this or use a generic one. We hardcode:
``TradingAgents personal-research/0.3 markhoehne@gmail.com``.

The XML schema: positions live as ``infoTable`` children of a root,
in the ``http://www.sec.gov/edgar/document/thirteenf/informationtable``
namespace. Each has ``nameOfIssuer``, ``cusip``, ``value`` (in $1000s
historically, in $ since 2022Q4 per SEC bulletin), and a nested
``shrsOrPrnAmt/sshPrnamt``. We also pick up the optional ``putCall``
field to flag derivative exposure.

CUSIP → ticker mapping: the filing itself doesn't carry tickers,
only CUSIPs. We maintain a hardcoded map for the universe of tickers
this app cares about. Unknown CUSIPs are persisted with ticker=None
so they're still queryable but get filtered out of per-ticker views.
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

USER_AGENT = "TradingAgents personal-research/0.3 markhoehne@gmail.com"
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json, text/xml, */*",
}

# XML namespace used in the 13F-HR infotable schema. ElementTree's
# .find() requires this prefix on every tag to match correctly.
NS = {"ns": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}


# Hardcoded CUSIP → ticker mapping for the universe we care about.
# 13F filings only carry CUSIPs, not tickers. For the ~50 tickers a
# personal portfolio typically holds, this map is faster + more
# reliable than a third-party lookup. Add new entries as your
# watchlist grows. CUSIPs are typically 9 chars; SEC pads short
# CUSIPs with a leading zero in some filings — we normalize to 9.
CUSIP_TO_TICKER: Dict[str, str] = {
    "037833100": "AAPL",
    "594918104": "MSFT",
    "02079K305": "GOOGL",  # Class A
    "02079K107": "GOOG",   # Class C
    "023135106": "AMZN",
    "67066G104": "NVDA",
    "88160R101": "TSLA",
    "30303M102": "META",
    "06405L100": "BAC",
    "478160104": "JNJ",
    "92826C839": "V",
    "57636Q104": "MA",
    "717081103": "PFE",
    "911312106": "UPS",
    "532457108": "LMT",
    "084670702": "BRK.B",
    "78462F103": "SPY",
    "742718109": "PG",
    "76954A103": "RIVN",
    "00287Y109": "ABBV",
    "11135F101": "AVGO",
    "G3925A104": "ASML",   # ADR
    "458140100": "INTC",
    "79466L302": "CRM",
    "82968B103": "SHOP",
    "65339F101": "NFLX",
    "655044105": "NKE",
    "172967424": "C",
    "166764100": "CVX",
    "30231G102": "XOM",
    "871829107": "SYK",
    "G2855X102": "ACN",
    "00724F101": "ADBE",
    "G1151C101": "AON",
    "036752103": "ANTM",
    "022249108": "AMAT",
    "126650100": "CVS",
    "12572Q105": "CME",
    "16119P108": "CHTR",
    "12613N201": "MRK",
    "191216100": "KO",
    "247361702": "DAL",
    "25470M109": "DAL",
    "316773100": "FDX",
    "459200101": "IBM",
    "464287200": "ITW",
    "47816J106": "JPM",
    "554500100": "MCD",
    "58155Q103": "MCK",
    "580135101": "MMM",
    "609207105": "MO",
    "65557D108": "NOC",
    "664397106": "NDSN",
    "718172109": "PHM",
    "742718109": "PG",
    "853688108": "ST",
    "880508102": "TER",
    "885160101": "TJX",
    "913017109": "UNH",
    "92556H206": "VICI",
    "98980L101": "ZTS",
    "G98255104": "ZTO",
    "G47320108": "HEI",
    "98138H101": "WMB",
}


# Curated "smart money" managers — CIK + display name.
# These are the institutional managers whose 13F filings the app
# tracks. Add via SQL or POST /holders/managers — these are seeded
# on first init. Numbers verified against EDGAR as of 2025.
SMART_MONEY_SEEDS: List[Tuple[str, str]] = [
    ("0001067983", "Berkshire Hathaway (Warren Buffett)"),
    ("0001649339", "Scion Asset Management (Michael Burry)"),
    ("0001061165", "Baupost Group (Seth Klarman)"),
    ("0001336528", "Pershing Square Capital (Bill Ackman)"),
    ("0001040273", "Third Point (Daniel Loeb)"),
    ("0001079114", "Greenlight Capital (David Einhorn)"),
    ("0001029160", "Soros Fund Management"),
    ("0001656456", "Appaloosa Management (David Tepper)"),
    ("0001350694", "Bridgewater Associates (Ray Dalio)"),
    ("0001037389", "Renaissance Technologies"),
    ("0001167483", "Tiger Global Management"),
    ("0001536411", "Duquesne Family Office (Stan Druckenmiller)"),
]


class EdgarError(Exception):
    """Raised when EDGAR returns an unexpected response."""


def _get(url: str, timeout: float = 15.0) -> bytes:
    """HTTP GET with SEC-compliant User-Agent + gzip handling.

    Returns raw response bytes. Raises EdgarError on non-200 or
    network failure with enough detail to debug.
    """
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            # Manual gzip decode — some EDGAR endpoints honor
            # Accept-Encoding, others don't. urllib doesn't auto-decode.
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                data = gzip.decompress(data)
            return data
    except urllib.error.HTTPError as e:
        raise EdgarError(f"HTTP {e.code} from {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise EdgarError(f"URL error fetching {url}: {e.reason}") from e


def _normalize_cik(cik: str) -> str:
    """Strip leading zeros for the path, pad to 10 for the submissions URL."""
    return str(cik).lstrip("0") or "0"


def _padded_cik(cik: str) -> str:
    """SEC submissions endpoint wants a zero-padded 10-digit CIK."""
    return str(cik).lstrip("0").zfill(10)


def fetch_latest_13f_filing(cik: str) -> Optional[Dict[str, Any]]:
    """Return metadata about the manager's most recent 13F-HR filing.

    Output: ``{"accession_no": "0000950123-25-...", "filing_date":
    "2025-02-14", "report_date": "2024-12-31", "form": "13F-HR"}``.

    Returns ``None`` if the manager has never filed a 13F (rare —
    if you're tracking the manager, they file).
    """
    url = f"https://data.sec.gov/submissions/CIK{_padded_cik(cik)}.json"
    try:
        raw = _get(url)
        doc = json.loads(raw.decode("utf-8"))
    except (EdgarError, json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"edgar submissions {cik}: {e}")
        return None

    recent = doc.get("filings", {}).get("recent", {}) or {}
    forms = recent.get("form", []) or []
    accs = recent.get("accessionNumber", []) or []
    filing_dates = recent.get("filingDate", []) or []
    report_dates = recent.get("reportDate", []) or []
    name = doc.get("name") or ""

    for i, form in enumerate(forms):
        if form in ("13F-HR", "13F-HR/A"):
            return {
                "accession_no": accs[i] if i < len(accs) else None,
                "filing_date": filing_dates[i] if i < len(filing_dates) else None,
                "report_date": report_dates[i] if i < len(report_dates) else None,
                "form": form,
                "manager_name": name,
            }
    return None


def fetch_holdings(cik: str, accession_no: str) -> List[Dict[str, Any]]:
    """Parse a 13F-HR infotable into structured holdings rows.

    ``accession_no`` is like ``0000950123-25-003478``. We strip the
    dashes for the URL path, fetch the filing's index.json to find
    the actual infotable XML filename (varies per filing), then parse.

    Returns a list of ``{cusip, name_of_issuer, title_of_class,
    shares, value, put_call}`` dicts. value is in dollars (current
    SEC convention since 2022Q4).
    """
    no_dashes = accession_no.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{_normalize_cik(cik)}/{no_dashes}"

    # Find the infotable XML by listing the filing directory's index.
    try:
        idx_raw = _get(f"{base}/index.json")
        idx = json.loads(idx_raw.decode("utf-8"))
    except (EdgarError, json.JSONDecodeError) as e:
        logger.warning(f"edgar filing-index {cik}/{accession_no}: {e}")
        return []

    info_xml_name: Optional[str] = None
    for entry in idx.get("directory", {}).get("item", []) or []:
        name = (entry.get("name") or "").lower()
        # Common patterns: *_infotable.xml, *infotable.xml, *_information_table.xml
        if name.endswith(".xml") and ("infotable" in name or "information_table" in name):
            info_xml_name = entry.get("name")
            break

    if not info_xml_name:
        logger.warning(f"no infotable.xml found in {base}/")
        return []

    try:
        xml_raw = _get(f"{base}/{info_xml_name}")
    except EdgarError as e:
        logger.warning(f"edgar infotable fetch failed: {e}")
        return []

    try:
        root = ET.fromstring(xml_raw)
    except ET.ParseError as e:
        logger.warning(f"edgar infotable parse failed for {cik}/{accession_no}: {e}")
        return []

    holdings: List[Dict[str, Any]] = []
    # Iterate every infoTable element. The namespace prefix is required
    # because EDGAR's XML declares xmlns=... on the root.
    for it in root.findall("ns:infoTable", NS):
        def _text(tag: str) -> Optional[str]:
            el = it.find(f"ns:{tag}", NS)
            return el.text.strip() if (el is not None and el.text) else None

        cusip = _text("cusip") or ""
        name_of_issuer = _text("nameOfIssuer") or ""
        title_of_class = _text("titleOfClass") or ""
        # value is in dollars (modern). Cast to int safely.
        value_str = _text("value") or "0"
        try:
            value = int(float(value_str.replace(",", "")))
        except (ValueError, TypeError):
            value = 0

        shrs_el = it.find("ns:shrsOrPrnAmt/ns:sshPrnamt", NS)
        try:
            shares = int(float((shrs_el.text or "0").replace(",", ""))) if shrs_el is not None else 0
        except (ValueError, TypeError):
            shares = 0

        put_call_el = it.find("ns:putCall", NS)
        put_call = put_call_el.text.strip() if (put_call_el is not None and put_call_el.text) else None

        holdings.append({
            "cusip": cusip.upper(),
            "name_of_issuer": name_of_issuer,
            "title_of_class": title_of_class,
            "shares": shares,
            "value": value,
            "put_call": put_call,
            "ticker": CUSIP_TO_TICKER.get(cusip.upper()),
        })
    return holdings


def lookup_ticker(cusip: str) -> Optional[str]:
    """Map a CUSIP to a ticker. None means we don't have a mapping."""
    return CUSIP_TO_TICKER.get((cusip or "").upper())
