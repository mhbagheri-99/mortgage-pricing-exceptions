---
description: Run every gate, as CI does, and report each one
---

Run `uv run python scripts/check.py --all`. It needs Docker for the integration tests.

Report each gate as PASS or FAIL. For a failure, show its output and propose a fix to the code. Never change a
guardrail file to make a gate pass; if a gate itself looks wrong, say why and ask the human.
