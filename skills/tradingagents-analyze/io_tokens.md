# Token usage log — skill-side

Each line records one run executed by the `tradingagents-analyze` skill.
Estimates are approximate (tiktoken `cl100k_base` encoding, ±10%) and
intended for cost tracking, not billing reconciliation.

| timestamp (UTC) | ticker | trade_date | provider/model | tokens | run_id |
|---|---|---|---|---|---|
- 2026-05-16T06:31:53Z | NVDA | 2026-05-15 | claude-desktop-skill/claude-opus-4-7 | in=7414 out=12903 calls=12 | run_id=claude-ca9a53b1
- 2026-05-16T06:55:54Z | NVDA | 2026-05-15 | claude-desktop-skill/claude-opus-4-7 | in=46750 out=11117 calls=0 | run_id=claude-c65fda2b
