# tradingagents-analyze — Claude Code skill

A self-contained Claude Code skill that runs the full TradingAgents
multi-agent analysis (4 analysts → bull/bear debate → research manager →
trader → 3-way risk debate → portfolio manager → structured brief) with
**Claude playing every role**. Publishes the resulting archive + brief
into the existing TradingAgents webapp on the NAS, so runs surface in
the same UI as Python-framework runs.

## Phase 1 status (this directory)

✅ Single-ticker analysis, configurable depth
✅ SQLite + filesystem dual publish (webapp picks runs up via either)
✅ Token usage logging to two `io_tokens.md` files

🚧 Multi-ticker batch, ensemble, interrogation, backtest, congress/insider
trades, sector/macro, earnings/events, scheduled runs — Phase 2-5,
documented in
`C:\Users\markh\.claude\plans\is-it-possible-to-stateless-wombat.md`.

## One-time setup

### 1. Install Python dependencies (client side — the machine running Claude Code)

```powershell
pip install yfinance stockstats jsonschema pyyaml tiktoken
```

(pandas + numpy come transitively with yfinance. No matplotlib — chart
rendering happens server-side.)

The framework on the NAS needs `matplotlib>=3.7` in its `service`
extras for the decision-history chart endpoint. It's pinned in
`pyproject.toml`; next time you `pip install '.[service]'` on the NAS
it'll come along.

### 2. Deploy the new framework endpoint

The skill publishes runs by POSTing to `/runs/import` on the framework's
FastAPI service. That endpoint was added in this skill's companion PR to
the TradingAgents repo:

- New endpoint: `service/routers/runs.py` → `POST /runs/import`
- New schema: `service/schemas.py` → `RunImportRequest`

Both changes are **purely additive** — no existing route, function, or
behavior is modified. Existing `POST /runs`, `/sidecars/*`, etc. are
untouched.

To deploy:
1. Pull the latest in the framework repo on the NAS (or wherever the
   service runs).
2. Restart the FastAPI service (`docker compose restart api`, or
   re-launch `tradingagents-api`).
3. Verify with: `curl http://192.168.2.34:8001/openapi.json | grep import`
   — you should see `/runs/import` listed.

If you're running an older container without the endpoint, `publish.py`
will fail with a clear "/runs/import endpoint not deployed" message.

### 3. Verify the URLs

`config/defaults.yaml` has:

```yaml
api_base_url:    "http://192.168.2.34:8001"   # FastAPI
webapp_base_url: "http://192.168.2.34:3001"   # Next.js
```

Adjust if your services are on different hosts/ports.

## Using the skill

From Claude Code (this CLI), trigger by:

```
/tradingagents-analyze NVDA
```

Or natural language:

> analyze NVDA today

Or with explicit configuration:

```
/tradingagents-analyze NVDA 2026-05-15 --debate-rounds 3 --risk-rounds 2
```

The skill will:
1. Fetch market data (yfinance) and compute indicators.
2. Walk through 12+ analyst/researcher/risk/PM phases, with Claude playing
   each role.
3. Produce a structured Brief.
4. Publish archive + brief + brief.md to the NAS via SMB or SCP.
5. INSERT a row into `gui.db`'s `runs` table.
6. Append a token-usage entry to **both** `io_tokens.md` files.
7. Report the run URL on the existing webapp.

The full report is on the webapp at
`http://192.168.2.34:3000/runs/<run_id>`. The skill's final response in
chat is just the URL + a one-sentence tldr.

## Layout

```
tradingagents-analyze/
├── README.md                       (this file)
├── SKILL.md                        (orchestrator — what Claude follows)
├── io_tokens.md                    (skill-side token usage log)
├── prompts/
│   ├── 01-fundamentals.md … 14-brief-extractor.md
├── scripts/
│   ├── fetch_market_data.py        (yfinance → JSON)
│   ├── compute_indicators.py       (stockstats / pandas indicators)
│   ├── build_archive.py            (compose envelope)
│   ├── build_brief.py              (validate brief)
│   ├── publish.py                  (drop on NAS + SQLite INSERT)
│   └── token_logger.py             (append to both io_tokens.md files)
├── schemas/
│   ├── archive.schema.json
│   └── brief.schema.json
├── config/
│   └── defaults.yaml               (NAS paths, model identity, etc.)
└── reference/
    ├── brief-examples.md
    ├── data-sources.md
    └── modes.md
```

## Troubleshooting

**`publish.py` says "CONNECTION FAILED".**
The FastAPI service isn't reachable. Check:
- Service running? `curl http://192.168.2.34:8001/health`
- LAN reachable from this machine? `ping 192.168.2.34`
- Right port in `config/defaults.yaml > api_base_url`?

**`publish.py` says "/runs/import endpoint not deployed".**
The framework on the NAS is running an older version without the new
endpoint. Pull the latest framework code and restart the FastAPI service
(see "One-time setup" → step 2).

**HTTP 409 — run_id already exists.**
Each invocation should mint a fresh `claude-<8-char-uuid>`. If you see a
collision, the orchestrator likely reused an old run_id by mistake — start
a fresh skill invocation.

**HTTP 400 — bad request.**
The response body names the offending field. Most common cause: missing
`run_id`, `ticker`, or `trade_date` in `metadata`, or a malformed `state`
dict (must be an object, not a string).

**Brief validation fails after Claude wrote it.**
Read the stderr from `build_brief.py` — it names the offending field.
Common causes:
- `decision` not in {Buy, Overweight, Hold, Underweight, Sell}
- `triggers` has fewer than 3 items
- `key_risks` has more than 5 items
The skill should re-prompt itself once with the specific error and re-emit
the brief; if it persistently fails, the analysis output may be too thin
to extract a structured brief from.
