"""Planner integration: status check + sync holdings into TA positions.

    GET  /planner/status                    — is it configured + reachable?
    POST /planner/sync?dry_run=true|false   — pull holdings, upsert into positions

Sources of holdings, in order of preference:
1. ``/api/investment-ledger/summary`` — the planner's **consolidated** view
   the frontend Investments page renders. Walks the InvestmentLot ledger
   (RSU / Stock Plan / manual uploads aggregated per ticker across every
   account that owns it). Comprehensive for symbols the user has imported
   via Excel/PDF.
2. ``/api/investments/holdings`` — the SimpleFIN-fed Holding rows.
   Supplements (1) for symbols held only in standard brokerage accounts
   that SimpleFIN can see directly but that the user hasn't imported into
   the ledger.

A ticker is taken from (1) if present, otherwise from (2). This avoids
the double-counting that would occur if both sources independently
report the same RSU/Stock Plan position (the ledger is the canonical one).

Sync semantics for the TA ``positions`` table:
- One TA position per (ticker, account_label) pair. The consolidated
  ledger collapses multiple accounts into one row per ticker, so when
  the ledger is the source we use the joined accounts list as the
  account label (e.g. "consolidated: Joint Stock Plan, TOD"). SimpleFIN
  supplements still get per-account labels.
- If a matching open TA position exists and quantity / cost basis
  differs, update it. Otherwise insert a new open position.
- We don't auto-close TA positions that the planner no longer reports —
  let the user do that explicitly. Planner deletions can be transient.

``dry_run=true`` (default) returns the diff without applying it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from gui import storage
from service import planner_client

router = APIRouter(prefix="/planner", tags=["planner"])


class PlannerStatus(BaseModel):
    configured: bool
    url: Optional[str] = None
    reachable: bool
    error: Optional[str] = None


class SyncDiffEntry(BaseModel):
    ticker: str
    account: str
    action: str  # create | update | unchanged
    planner_shares: float
    planner_cost_basis: Optional[float] = None
    existing_shares: Optional[float] = None
    existing_cost_basis: Optional[float] = None


class SyncResult(BaseModel):
    dry_run: bool
    fetched_holdings: int
    accounts: int
    diff: List[SyncDiffEntry]
    applied: int = 0
    skipped: int = 0
    errors: List[str] = []


@router.get("/status", response_model=PlannerStatus)
def status() -> PlannerStatus:
    if not planner_client.is_configured():
        return PlannerStatus(
            configured=False,
            url=planner_client.planner_url(),
            reachable=False,
            error="Set PLANNER_API_URL and PLANNER_API_KEY in the API container's .env",
        )
    health = planner_client.healthcheck()
    return PlannerStatus(
        configured=True,
        url=planner_client.planner_url(),
        reachable=bool(health.get("ok")),
        error=health.get("error") or (health.get("body") if not health.get("ok") else None),
    )


def _account_label(account: Dict[str, Any]) -> str:
    """Build a human-readable account label that doubles as our position
    ``account`` field. Matches by-name when re-syncing."""
    name = account.get("name") or account.get("nickname") or f"account_{account.get('id')}"
    typ = account.get("account_type")
    if typ:
        return f"{name} ({typ})"
    return name


def _consolidated_label(accounts: List[str]) -> str:
    """Pick a reasonable account label for a consolidated-ledger row.

    The ledger collapses multiple accounts into one row per ticker (e.g.
    AAPL across Joint Stock Plan + TOD brokerage). We want the TA
    positions row to surface that fact, so the label includes "consolidated"
    plus the underlying account list (truncated if long).
    """
    if not accounts:
        return "consolidated (planner ledger)"
    if len(accounts) == 1:
        return f"consolidated: {accounts[0]}"
    if len(accounts) <= 3:
        return "consolidated: " + ", ".join(accounts)
    return f"consolidated: {accounts[0]}, {accounts[1]}, +{len(accounts) - 2} more"


@router.post("/sync", response_model=SyncResult)
def sync(dry_run: bool = Query(True)) -> SyncResult:
    """Pull holdings from the planner and reconcile against our positions table.

    Prefers the consolidated InvestmentLot ledger view (the same one the
    planner's Investments page renders); falls back to SimpleFIN Holdings
    for tickers not present in the ledger. See module docstring for the
    full source-precedence rules.
    """
    if not planner_client.is_configured():
        raise HTTPException(
            status_code=412,
            detail="Planner not configured. Set PLANNER_API_URL and PLANNER_API_KEY.",
        )

    try:
        accounts = planner_client.list_accounts()
        # Pull both sources. We use the consolidated ledger as primary
        # (per-ticker aggregated) and SimpleFIN holdings as the supplement
        # for tickers not in the ledger.
        consolidated_resp = planner_client.list_consolidated_holdings()
        holdings_resp = planner_client.list_holdings()
    except planner_client.PlannerClientError as e:
        raise HTTPException(status_code=502, detail=str(e))

    accounts_by_id: Dict[int, Dict[str, Any]] = {}
    for a in accounts:
        try:
            accounts_by_id[int(a["id"])] = a
        except (KeyError, ValueError, TypeError):
            continue

    consolidated = consolidated_resp.get("holdings") or []
    simplefin_holdings = holdings_resp.get("holdings") or []

    # Tickers covered by the consolidated ledger — used to skip the
    # SimpleFIN supplement for the same symbol (the ledger is canonical
    # when it has data on a ticker, so we don't double-count).
    consolidated_tickers = {
        (h.get("symbol") or "").upper()
        for h in consolidated
        if (h.get("symbol") or "").strip()
    }

    # Index existing TA open positions by (ticker, account-string).
    existing = storage.list_positions(include_closed=False)
    existing_by_key: Dict[tuple, Dict[str, Any]] = {}
    for p in existing:
        key = ((p["ticker"] or "").upper(), p.get("account") or "")
        existing_by_key[key] = p

    diff: List[SyncDiffEntry] = []
    actions: List[Dict[str, Any]] = []  # what to do if not dry_run

    # ────────── Pass 1: consolidated ledger rows (one per ticker) ──────────
    for h in consolidated:
        ticker = (h.get("symbol") or "").upper()
        if not ticker:
            continue
        qty = float(h.get("shares") or 0)
        if qty <= 0:
            continue
        # Ledger gives total_cost_basis (already prorated to currently-held
        # shares); divide for per-share basis.
        total_cb = h.get("total_cost_basis")
        cost_f = float(total_cb) / qty if total_cb and qty > 0 else None
        if cost_f is None or cost_f <= 0:
            # Fall back to current_price so the position has *some* basis.
            cost_f = float(h.get("current_price") or 0) or None

        account_label = _consolidated_label(h.get("accounts") or [])

        key = (ticker, account_label)
        existing_p = existing_by_key.get(key)

        if existing_p is None:
            diff.append(SyncDiffEntry(
                ticker=ticker, account=account_label, action="create",
                planner_shares=qty, planner_cost_basis=cost_f,
            ))
            actions.append({
                "kind": "create", "ticker": ticker, "account": account_label,
                "shares": qty, "cost_basis": cost_f or 1e-9,
            })
        else:
            same_qty = abs(existing_p["shares"] - qty) < 1e-9
            same_cost = (
                cost_f is None
                or abs((existing_p.get("cost_basis_per_share") or 0) - cost_f) < 1e-2
            )
            if same_qty and same_cost:
                diff.append(SyncDiffEntry(
                    ticker=ticker, account=account_label, action="unchanged",
                    planner_shares=qty, planner_cost_basis=cost_f,
                    existing_shares=existing_p["shares"],
                    existing_cost_basis=existing_p.get("cost_basis_per_share"),
                ))
            else:
                diff.append(SyncDiffEntry(
                    ticker=ticker, account=account_label, action="update",
                    planner_shares=qty, planner_cost_basis=cost_f,
                    existing_shares=existing_p["shares"],
                    existing_cost_basis=existing_p.get("cost_basis_per_share"),
                ))
                actions.append({
                    "kind": "update", "id": existing_p["id"],
                    "shares": qty,
                    "cost_basis": cost_f if cost_f else existing_p.get("cost_basis_per_share"),
                })

    # ────────── Pass 2: SimpleFIN supplement (tickers NOT in ledger) ──────
    for h in simplefin_holdings:
        ticker = (h.get("symbol") or "").upper()
        if not ticker:
            continue
        if ticker in consolidated_tickers:
            # Skip — the ledger already covers this symbol. Avoids
            # double-counting RSU/Stock Plan shares that show up in both.
            continue
        qty = float(h.get("quantity") or 0)
        cost = h.get("avg_cost_basis")
        cost_f = float(cost) if cost is not None else None
        if qty <= 0:
            continue
        account_id = h.get("account_id")
        account = accounts_by_id.get(int(account_id)) if account_id is not None else None
        account_label = _account_label(account or {"id": account_id})

        key = (ticker, account_label)
        existing_p = existing_by_key.get(key)

        # Cost basis fallback: if planner doesn't have one, use current price
        # so the position has *some* basis and unrealized P&L can be 0 at
        # snapshot time. We'd rather have an obvious "0% return" position
        # than fail the create entirely.
        effective_cost = cost_f if cost_f else float(h.get("current_price") or 0)

        if existing_p is None:
            diff.append(SyncDiffEntry(
                ticker=ticker, account=account_label, action="create",
                planner_shares=qty, planner_cost_basis=cost_f,
            ))
            actions.append({
                "kind": "create", "ticker": ticker, "account": account_label,
                "shares": qty, "cost_basis": effective_cost,
            })
        else:
            same_qty = abs(existing_p["shares"] - qty) < 1e-9
            same_cost = (
                cost_f is None
                or abs((existing_p.get("cost_basis_per_share") or 0) - cost_f) < 1e-6
            )
            if same_qty and same_cost:
                diff.append(SyncDiffEntry(
                    ticker=ticker, account=account_label, action="unchanged",
                    planner_shares=qty, planner_cost_basis=cost_f,
                    existing_shares=existing_p["shares"],
                    existing_cost_basis=existing_p.get("cost_basis_per_share"),
                ))
            else:
                diff.append(SyncDiffEntry(
                    ticker=ticker, account=account_label, action="update",
                    planner_shares=qty, planner_cost_basis=cost_f,
                    existing_shares=existing_p["shares"],
                    existing_cost_basis=existing_p.get("cost_basis_per_share"),
                ))
                actions.append({
                    "kind": "update", "id": existing_p["id"],
                    "shares": qty,
                    "cost_basis": cost_f if cost_f else existing_p.get("cost_basis_per_share"),
                })

    applied = 0
    errors: List[str] = []
    if not dry_run:
        for a in actions:
            try:
                if a["kind"] == "create":
                    storage.add_position(
                        ticker=a["ticker"], shares=a["shares"],
                        cost_basis_per_share=a["cost_basis"] or 1e-9,
                        account=a["account"],
                        notes="synced from planner",
                    )
                elif a["kind"] == "update":
                    storage.update_position(
                        a["id"],
                        shares=a["shares"],
                        cost_basis_per_share=a["cost_basis"],
                    )
                applied += 1
            except Exception as e:
                errors.append(f"{a}: {e}")

    return SyncResult(
        dry_run=dry_run,
        fetched_holdings=len(consolidated) + sum(
            1 for h in simplefin_holdings
            if (h.get("symbol") or "").upper() not in consolidated_tickers
        ),
        accounts=len(accounts_by_id),
        diff=diff,
        applied=applied,
        skipped=sum(1 for d in diff if d.action == "unchanged"),
        errors=errors,
    )
