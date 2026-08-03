# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Everything runs through the Makefile from the repo root:

| Command | What it does |
| --- | --- |
| `make up` | Build and start all five services (postgres, qdrant, api, mcp, frontend) |
| `make seed` | Ingest the sample corpus; idempotent |
| `make test` | Backend test suite |
| `make lint` | ruff, ruff format check, and mypy strict |
| `make format` | Auto-format the backend |
| `make mcp-check` | Verify the MCP endpoint: auth rejection, handshake, tool list, a real tool call |
| `make clean` | Stop the stack and delete the data volumes |

Backend commands run under `uv` from `backend/` (Python pinned to 3.12 by `.python-version`).
A single test: `cd backend && uv run pytest -k test_name`.

The backend needs `MCP_API_KEY` set to run at all. Integration tests need Postgres and
Qdrant reachable and skip themselves when they are not.

## Running without API keys

`EMBEDDING_PROVIDER=hash` swaps the OpenAI embedder for a deterministic offline stand-in,
so the whole stack boots with no credentials. Retrieval then degrades to keyword-only:
the retrieval service skips the dense retriever entirely rather than fusing meaningless
vectors into the ranking. This is what CI uses. Chat models are separate: each provider
key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`) enables its own models, and
any subset may be set.

## Invariants worth knowing before you change things

These all look like cruft until you know why they are there.

- **The chat assistant reaches the MCP server over HTTP, not by import.** `kb/agent/mcp_client.py`
  opens a Streamable HTTP session with a bearer token instead of calling `RetrievalService`
  directly. The network hop is the point: it makes the chat a live test of the same surface
  external agents use, so tool descriptions cannot drift. Do not "optimise" it away.
- **`kb.mcp_server` is named that to avoid shadowing the `mcp` SDK package.** Do not rename
  it to `kb.mcp`.
- **`CHUNK_NAMESPACE` in `kb/ingestion/pipeline.py` is frozen.** Chunk IDs are a UUIDv5 of
  it, and are reused as Qdrant point IDs. Changing it orphans every stored vector.
- **`/healthz` must not touch the MCP server.** It backs the container healthcheck, and the
  MCP container waits for the API to be healthy, so probing MCP there deadlocks a cold boot.
  The MCP reachability check lives at `/chat/health`.
- **The MCP tool descriptions in `kb/mcp_server/server.py` are the product**, not comments.
  They are what an LLM reads to choose a tool, and `tests/test_mcp_tools.py` pins the tool
  set, required parameters, parameter bounds, and the presence of sibling redirection in
  each description. Expect that suite to fail if you reword one carelessly.
- **The API and MCP server are the same image** with different start commands, sharing the
  `kb` package. Retrieval and ingestion logic exists once; keep it that way.
- **Re-seed after changing the chunker, the parsers or a sample document.** Indexed chunks
  hold a copy of the text, so edits to `backend/sample_documents/` do not reach search until
  the corpus is re-ingested. Use `/reseed`.

## Migrations

Alembic autogenerate needs a live Postgres:
`cd backend && uv run alembic revision --autogenerate -m "..."` then `uv run alembic upgrade head`.
The `api` container runs `alembic upgrade head` on start, so a clean volume boots to a
working schema.

## Conventions

- **Never use the em dash character.** Use a colon or a semicolon instead. This applies to
  code, comments, docs and the sample documents alike; the repository was swept clean of it.
- **Comments explain why, not what.** Justify a non-obvious decision or record a constraint;
  do not restate the line below.
- **Run `make lint` before calling backend work finished.** mypy runs in strict mode.
- **Commit only when asked.** Do not commit or push on your own initiative.
