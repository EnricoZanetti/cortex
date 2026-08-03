---
name: verify
description: Run the full backend quality gate (ruff, ruff format check, mypy strict, pytest) and, if the stack is up, the MCP endpoint check too. Use before declaring backend work finished, or when the user asks to verify, check, or validate the codebase.
---

Run these in order from the repo root, stopping to report and fix on the first failure
rather than continuing past it:

```bash
cd backend
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run pytest -q
```

If any of the first three fail, fix the issue and re-run that step (not the whole
sequence) before moving on. `mypy` runs in strict mode; do not add `# type: ignore` to
silence a finding without confirming it is a genuine limitation of the type checker, not
a real bug.

If `pytest` reports integration tests skipped ("Postgres/Qdrant are not reachable" or
similar), that is expected when the stack is not running: report the skip count, don't
try to start the stack yourself unless the user asks.

After the suite passes, check whether the MCP server is reachable and, if so, run the
end-to-end check too:

```bash
curl -s -o /dev/null -w "%{http_code}" localhost:8080/mcp
```

If that returns anything other than a connection failure, run `make mcp-check` from the
repo root and report its output. If the MCP server is not running, skip this step and
say so rather than starting the stack unprompted.

Summarize at the end: what passed, what was fixed, and whether the MCP check ran.
