"""13F holdings poller — refreshes institutional manager filings weekly.

13F-HR filings are quarterly. Managers file within 45 days of quarter
end, so meaningful new data appears at most ~4× per year. Polling
weekly is plenty — once a quarter the new filings drop in, the rest
of the time the poll is a no-op (the latest accession number on
EDGAR matches the one we already stored).

Runs as an asyncio task spawned in app startup. Default cadence:
7 days. Initial delay: 60 minutes (the app has more urgent work
during startup; 13F data is days-stale anyway, an hour doesn't
matter).

For each enabled manager:
  1. GET https://data.sec.gov/submissions/CIK{cik}.json
  2. Find the most recent 13F-HR accession number
  3. If accession_no matches the manager's last_accession_no in
     SQLite, skip — we already have this filing
  4. Otherwise: fetch and parse the infotable XML, bulk-insert
     positions with prev_shares + qoq_change_pct computed against
     the prior filing we already have

Errors are recorded in smart_money_managers.last_error for the UI
to surface, but they don't poison the rest of the cycle — each
manager is independent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from gui import storage
from service import edgar_client

logger = logging.getLogger(__name__)


def _tick() -> Dict[str, Any]:
    """One pass: re-check every enabled manager for a new filing.

    Returns a summary dict for logging / the /refresh response:
    ``{"managers_checked": N, "filings_added": M, "errors": K}``.
    """
    summary: Dict[str, Any] = {
        "managers_checked": 0,
        "managers_skipped": 0,   # already had the latest
        "filings_added": 0,
        "positions_added": 0,
        "errors": 0,
        "error_details": [],
    }
    try:
        managers = storage.list_smart_money_managers(enabled_only=True)
    except Exception as e:
        logger.exception("holdings_13f_poller: list_managers failed: %s", e)
        return summary

    for m in managers:
        cik = m["cik"]
        name = m["name"]
        prior_accession = m.get("last_accession_no")
        try:
            latest = edgar_client.fetch_latest_13f_filing(cik)
            summary["managers_checked"] += 1
            if not latest:
                storage.update_manager_after_fetch(
                    cik=cik, filing_date=None, report_date=None,
                    accession_no=None, total_value=None, position_count=None,
                    error="no 13F-HR filings found in submissions feed",
                )
                summary["errors"] += 1
                continue

            # Skip if we already have this accession AND the prior parse
            # succeeded (positions > 0). If position_count is 0 or NULL
            # for this accession, a previous attempt failed to extract
            # positions — re-try the fetch+parse so a fixed parser can
            # rescue the row without manual intervention.
            prior_count = m.get("position_count") or 0
            prior_error = m.get("last_error")
            if latest["accession_no"] == prior_accession and prior_count > 0 and not prior_error:
                summary["managers_skipped"] += 1
                # Refresh the last_refreshed_at timestamp so the UI shows
                # the check actually happened.
                storage.update_manager_after_fetch(
                    cik=cik, filing_date=latest["filing_date"],
                    report_date=latest["report_date"],
                    accession_no=latest["accession_no"],
                    total_value=m.get("total_value"),
                    position_count=m.get("position_count"),
                    error=None,
                )
                continue

            # New filing — fetch + parse + insert.
            holdings = edgar_client.fetch_holdings(cik, latest["accession_no"])
            if not holdings:
                storage.update_manager_after_fetch(
                    cik=cik, filing_date=latest["filing_date"],
                    report_date=latest["report_date"],
                    accession_no=latest["accession_no"],
                    total_value=None, position_count=None,
                    error="infotable.xml parse returned 0 positions",
                )
                summary["errors"] += 1
                continue

            # Resolve manager name from EDGAR if we don't have a curated one
            # (or if EDGAR's official name is more recent / accurate). We
            # keep the user's friendly seed label intact though — only
            # override if the seed name is empty.
            effective_name = name or latest.get("manager_name") or "Unknown"

            inserted = storage.insert_holdings_snapshot(
                manager_cik=cik, manager_name=effective_name,
                accession_no=latest["accession_no"],
                filing_date=latest["filing_date"],
                report_date=latest["report_date"],
                holdings=holdings,
            )

            total_value = float(sum(h.get("value") or 0 for h in holdings))
            storage.update_manager_after_fetch(
                cik=cik, filing_date=latest["filing_date"],
                report_date=latest["report_date"],
                accession_no=latest["accession_no"],
                total_value=total_value, position_count=len(holdings),
                error=None,
            )
            summary["filings_added"] += 1
            summary["positions_added"] += inserted
            logger.info(
                "13F poller: %s (%s) — new filing %s (%d positions, $%sM AUM)",
                name, cik, latest["accession_no"], len(holdings),
                f"{total_value/1_000_000:.0f}",
            )

        except Exception as e:
            logger.warning("13F poller: %s (%s) failed: %s", name, cik, e)
            try:
                storage.update_manager_after_fetch(
                    cik=cik, filing_date=None, report_date=None,
                    accession_no=None, total_value=None, position_count=None,
                    error=str(e)[:300],
                )
            except Exception:
                pass
            summary["errors"] += 1
            summary["error_details"].append(f"{name}: {str(e)[:200]}")
            continue

    return summary


async def run(interval_seconds: int = 7 * 24 * 3600) -> None:
    """Run the 13F poller loop forever.

    Each manager check is 1-2 HTTP calls to EDGAR (~500ms each).
    With ~12 managers and a hit on 1-2 of them per quarter, a typical
    tick takes ~10s. That's offloaded to a worker thread to avoid
    blocking the FastAPI event loop.

    Default cadence: weekly. Default initial delay: 60 minutes so app
    boot is fast and 13F data (which is days-stale anyway) isn't on
    the hot startup path.
    """
    logger.info("holdings_13f_poller started (interval=%ds)", interval_seconds)
    await asyncio.sleep(3600)  # let the app fully come up first
    while True:
        try:
            summary = await asyncio.to_thread(_tick)
            if summary["filings_added"] > 0 or summary["errors"] > 0:
                logger.info(
                    "holdings_13f_poller: checked=%d skipped=%d new=%d positions=%d errors=%d",
                    summary["managers_checked"], summary["managers_skipped"],
                    summary["filings_added"], summary["positions_added"],
                    summary["errors"],
                )
        except Exception as e:
            logger.exception("holdings_13f_poller tick failed: %s", e)
        await asyncio.sleep(interval_seconds)
