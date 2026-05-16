# Invocation modes

## Phase 1 (currently implemented)

```
/tradingagents-analyze <TICKER>
/tradingagents-analyze <TICKER> <YYYY-MM-DD>
/tradingagents-analyze <TICKER> --debate-rounds N --risk-rounds N
```

Natural-language phrasing maps to these — Claude interprets:
- "analyze NVDA" → `/tradingagents-analyze NVDA` (date defaults to today)
- "analyze NVDA on 2026-05-15" → `/tradingagents-analyze NVDA 2026-05-15`
- "run NVDA with deep debate" → `--debate-rounds 3 --risk-rounds 2`

## Phase 2 (planned)

```
/tradingagents-analyze <T1> <T2> <T3> [<DATE>]      # multi-ticker batch
/tradingagents-analyze <TICKER> --ensemble N        # run N times for consensus
/tradingagents-analyze portfolio <batch_id>          # synthesise across a batch
```

## Phase 4 (planned)

```
/tradingagents-analyze ask <run_id> "<question>"    # interrogate a past run
/tradingagents-analyze backtest <run_id>            # realised return vs SPY
/tradingagents-analyze status                       # show recent runs
```

## Phase 5 (planned)

Integrates with the `/schedule` skill so the trading skill can be invoked
on a cron schedule:

```
/schedule create "tradingagents-analyze NVDA AMD AVGO" --at "weekdays 06:00"
```
