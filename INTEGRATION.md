# Financial Planner ↔ TradingAgents integration

TradingAgents pulls account + holdings data from a separate
[Financial Planner](https://github.com/mrh335/financialplanner) instance
and reconciles it into the local `positions` table.

## Direction of data flow

```
                      GET /api/accounts
                      GET /api/investments/holdings
  ┌──────────────────┐ ◄──────────────────────────────── ┌──────────────────┐
  │ Financial Planner│                                   │  TradingAgents   │
  │  (sibling repo)  │   X-API-Key / Bearer header       │  (this repo)     │
  │  :8765           │                                   │  :8888 / :3000   │
  └──────────────────┘                                   └──────────────────┘
```

The planner is the source of truth for accounts + holdings. We pull and
upsert into our own positions table. Nothing writes back the other way.

## Setup

### TradingAgents side (this repo)

Add to `.env`:
```
PLANNER_API_URL=http://192.168.2.34:8765
PLANNER_API_KEY=<see planner side>
```

Then:
```
GET  /planner/status                  — configured + reachable check
POST /planner/sync?dry_run=true       — show diff
POST /planner/sync?dry_run=false      — apply changes (upsert into positions)
```

### Planner side (sibling repo)

The planner must have `INTEGRATION_API_KEY=<same-as-PLANNER_API_KEY>` in
its `.env`. If empty, it falls back to session-cookie auth and our
client will get 401 on every call.

## Sync semantics

For every `(planner_holding.symbol, planner_account_name)` pair:

- If a position exists with the same `(ticker, account)` that's still
  open, and `quantity` differs → update shares + cost_basis_per_share
- If no matching open position → insert a new one
- Closed positions are never reopened automatically
- We never auto-close TA positions that the planner no longer has —
  user closes those explicitly (safer; planner deletions can be
  transient e.g. mid-SimpleFIN-sync)

`dry_run=true` is the default. Returns the diff so the UI can show
"would create N, update M, leave K untouched" before commit.

## Files involved

- `service/planner_client.py` — HTTP client + auth header builder
- `service/routers/planner.py` — status + sync FastAPI routes
- `gui/storage.py` — `list_positions / add_position / update_position`

## Schema contract

See `service/planner_client.py:list_holdings()` docstring for the exact
fields we depend on. **If the planner renames or removes any of those
fields, this client breaks** — coordinate changes across both repos.
