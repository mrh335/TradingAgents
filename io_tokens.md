# Token usage log — framework-side

This file is maintained by `scripts/token_logger.py` in the
`tradingagents-analyze` Claude skill, plus the planned
`scripts/poll_framework_tokens.py` (Phase 1.5) which catches up any rows
in `gui.db`'s `runs` table that haven't been logged yet.

Each line records one run. Provider is `claude-desktop-skill` for skill
runs, or whichever LLM provider the Python framework used (openai, google,
anthropic, xai, deepseek, qwen, glm, openrouter, ollama, azure) for
framework runs.

Estimates for skill rows are approximate (tiktoken `cl100k_base` encoding,
±10%). Framework rows use the actual token counts the provider returned
and stored in `gui.db`.

| timestamp (UTC) | ticker | trade_date | provider/model | tokens | run_id |
|---|---|---|---|---|---|
- 2026-05-16T06:31:53Z | NVDA | 2026-05-15 | claude-desktop-skill/claude-opus-4-7 | in=7414 out=12903 calls=12 | run_id=claude-ca9a53b1
- 2026-05-16T06:55:54Z | NVDA | 2026-05-15 | claude-desktop-skill/claude-opus-4-7 | in=46750 out=11117 calls=0 | run_id=claude-c65fda2b
