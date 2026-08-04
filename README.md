# Document Intelligence Server

A knowledge base for internal company documents, exposed to AI agents through an **MCP
server over Streamable HTTP**.

Employees upload and tag documents through a small web UI. The backend parses, chunks and
embeds them. An AI agent connects to the MCP server and answers questions in natural
language, grounded in the actual documents, with citations.

```
┌────────────────┐      REST       ┌──────────────────────────────┐
│  Next.js UI    │ ──────────────▶ │  FastAPI                     │
│  Ask (chat)    │   SSE stream    │  /documents /tags  (manage)  │
│  Documents     │ ◀────────────── │  /chat             (assist)  │
└────────────────┘                 └───────────────┬──────────────┘
                                                   │
                        the assistant is itself an MCP client:
                        it calls the tools over HTTP with a bearer
                        token, exactly as an external agent would
                                                   │
                                     shared `kb` core package
                                                   │
┌────────────────┐  Streamable HTTP ┌──────────────┴──────────────┐
│  MCP client    │ ─── Bearer ────▶ │  MCP server  (query plane)  │
│  Claude Code / │                  │  7 tools at /mcp            │
│  Desktop /curl │ ◀─────────────── │                             │
└────────────────┘                  └──────────────┬──────────────┘
                                                   │
                      ┌────────────────────────────┴───────────────────────────────┐
                      │  Postgres: documents, tags, chunks + full-text index       │
                      │  Qdrant:   chunk embeddings + tag/document payload filters │
                      └────────────────────────────────────────────────────────────┘
```

The API and the MCP server are two processes running **the same image** with different
start commands, both importing the same `kb` package. Ingestion and retrieval logic exists
exactly once.

The built-in assistant does **not** shortcut that boundary. It connects to the MCP server
over Streamable HTTP with the same bearer token any other client uses, so the chat exercises
the exact surface external agents consume. A weak tool description or a broken auth header
shows up in our own UI before a customer finds it.

---

## Quick start

```bash
cp .env.example .env      # set MCP_API_KEY, plus at least one LLM provider key
make up                   # builds and starts all five services
make seed                 # loads six realistic sample documents
```

| What | Where |
| --- | --- |
| Ask the knowledge base | http://localhost:3000/chat |
| Manage documents | http://localhost:3000/documents |
| API docs (OpenAPI) | http://localhost:8000/docs |
| MCP endpoint | http://localhost:8080/mcp |

Verify the MCP endpoint, including that it rejects unauthenticated calls:

```bash
make mcp-check
```

**No OpenAI key?** Set `EMBEDDING_PROVIDER=hash` in `.env`. The whole stack then runs
offline with a deterministic stand-in embedder; retrieval degrades cleanly to keyword-only
search (see [Offline mode](#offline-mode)). This is also what CI uses.

`make help` lists every target.

---

## The assistant

Employees ask questions at `/chat` and get answers grounded in the documents, with
citations. The model is theirs to choose.

### Choosing a model

The picker lists models from three providers. Each provider is enabled by one
environment variable, and **any subset may be configured**: models whose key is
missing stay in the list, greyed out, labelled with the variable that would enable
them. Nothing else breaks.

| Provider | Variable | Models |
| --- | --- | --- |
| Anthropic | `ANTHROPIC_API_KEY` | Claude Opus 5, Claude Sonnet 5, Claude Haiku 4.5 |
| OpenAI | `OPENAI_API_KEY` | GPT-5.1, GPT-5 mini (this key also drives embeddings) |
| Google | `GOOGLE_API_KEY` | Gemini 2.5 Pro, Gemini 2.5 Flash |

Adding a model is a one-line entry in `backend/src/kb/agent/catalog.py`. Adding a
whole provider is one adapter in `providers.py` implementing a single method.

### Why the assistant goes over HTTP

The obvious implementation imports `RetrievalService` and calls it directly. This one
opens an MCP session instead, over Streamable HTTP, authenticated with `MCP_API_KEY`.

That costs a network hop and buys two things. The chat becomes a live test of the
graded surface: the tool descriptions the models read are the ones the MCP server
serves, not a second copy maintained for the UI, so there is no way for them to drift.
And the assistant can be pointed at a deployed MCP server by changing one URL.

The same design keeps the providers honest. Each adapter translates the MCP tool
schemas into its vendor's dialect and runs the tool loop; **none of them rewrites the
descriptions**. Tool-selection behaviour therefore comes from the tool design, not from
three separately-tuned prompts.

### What the UI shows

Each turn streams over Server-Sent Events, so tokens appear as they are produced and
tool calls appear as they happen. Every tool call is rendered as a chip with its
arguments on hover, and sources are listed under the answer.

That visibility is deliberate: it is how you watch the agent pick `search` for a broad
question, `list_tags` then `search_by_tag` for a topic-scoped one, and
`search_by_document` for a follow-up. The tool-design decisions are visible in the UI
rather than buried in a log.

### Guardrails

- **The agent loop is bounded** by `CHAT_MAX_TOOL_ITERATIONS` (default 8), so a model
  that keeps calling tools terminates rather than looping.
- **Nothing raises into the stream.** A failure arrives as an error event the page can
  render. Provider errors are unwrapped from the MCP transport's nested
  `ExceptionGroup`s first, so the user sees "the OpenAI account has no remaining quota"
  rather than "unhandled errors in a TaskGroup".
- **Tool failures go to the model, not the user.** The tools explain how to recover, and
  the model can only act on that if it sees the message.

### Checking it works

```bash
curl -s localhost:8000/chat/health
```

Reports whether the assistant can reach the MCP server and which tools it found. It is
deliberately separate from `/healthz`: that endpoint backs the container healthcheck, and
the MCP container waits on the API, so probing it there would deadlock a cold boot.

---

## MCP tool design

The tool definitions are the only thing an LLM sees when
deciding what to call, so they are treated as a product surface.

### The tools

| Tool | Required | Purpose |
| --- | --- | --- |
| `search` | `query` | **Default entry point.** Hybrid search across every document. |
| `search_by_tag` | `query`, `tags` | The same search, restricted to a topic area. |
| `search_by_document` | `query`, `documents` | The same search, restricted to named documents (by id **or** name). |
| `list_documents` | – | Inventory: what exists, its tags, its status. |
| `list_tags` | – | The tag vocabulary, with document counts. |
| `get_document_summary` | `document` | Summary + section outline of one document. |
| `fetch_chunk_context` | `chunk_id` | Expand a retrieved passage with its neighbours. |

The first five are the tools the brief specifies. The last two are additions, justified
below.

### The principles behind them

**1. Every description says when *not* to use the tool, and names the alternative.**
Telling a model what a tool does is easy; the hard part is stopping it reaching for the
wrong one. So `search_by_tag` says, in as many words, *"do not use this as your first move
on a general question; narrowing too early is the most common way to miss an answer filed
under a different tag. If this returns nothing, fall back to `search`."* Redirection is the
single highest-leverage sentence in a tool description.

**2. Three search tools, not one tool with optional filters.**
The brief specifies this split, and it is also the better interface. A model reliably fills
in a **required** parameter on a distinctly-named tool. It routinely *omits* an optional
one, and an omitted `tags` filter fails silently, returning plausible results from the
wrong part of the corpus. Making the filter required makes the narrowing decision explicit,
and makes the name itself carry the intent.

**3. The three search tools return an identical schema.**
The agent learns one result shape. Switching from `search` to `search_by_tag` costs it
nothing. Underneath they are one `RetrievalService` call differing only in the filter, so
the ranking behaviour really is identical; the shared schema is not a convenient fiction.

**4. Every response carries a `guidance` field.**
An empty result set is the moment an agent is most likely to either give up or invent an
answer. So the server never returns a bare empty list:

> *"None of these tags exist: `polcy`. The available tags are: compliance, hr, onboarding,
> policy, product, security. Re-call `search_by_tag` with one of them, or use `search` to
> query every document instead."*

The guidance also distinguishes *"nothing matched"* from *"the knowledge base is empty"*
from *"documents are still being processed, retry shortly"*: three situations that call
for three different things to tell the user.

**5. Two scores, because they answer two different questions.**
Every hit carries two scores. `relevance` is normalised against the best hit in *this*
result set, for comparing results with each other; `similarity` is the raw cosine, absolute,
for judging whether anything relevant exists at all. This distinction matters: a normalised score
*cannot* express "nothing here is relevant", because the top hit always scores 1.0 even for
a query the corpus knows nothing about. When the best absolute similarity is weak, the
`guidance` says so explicitly and tells the agent to say the knowledge base has no answer
rather than dress up the closest passage as one.

**6. Errors are recoverable in one turn.**
`search_by_document` accepts ids *or* names, because a model will pass back whatever string
it saw earlier. Resolution cascades: exact id → exact filename → exact title → unique
substring. On a miss or an ambiguity it returns the actual candidates with their ids, so
the model corrects itself immediately instead of guessing.

**7. Parameters are constrained, defaulted and documented.**
`top_k` is bounded `1..20`, `before`/`after` `0..5`, `tag_match` is an enum. Bounds stop a
model inventing `top_k=500` and blowing up its own context window. Every field has a
description explaining not just what it is but when to change it.

**8. Results are token-budgeted, with an escape hatch.**
Passages are truncated to `max_chars_per_result` and flagged `truncated: true`. Rather than
inflating every response for the rare case, `fetch_chunk_context` lets the agent expand
exactly the passage that needs it.

**9. Every tool is annotated `readOnlyHint`,** so clients know none of them mutate anything
and can skip approval prompts.

### Why the two extra tools

- **`get_document_summary`**: lets an agent decide *whether* a document is worth searching,
  and answers "what's in this document?" without spending a retrieval call. The section
  outline is the fastest way to orient in a long policy.
- **`fetch_chunk_context`**: fixes the classic RAG failure where the answer straddles a
  chunk boundary (a requirement in one chunk, its exception clause in the next). Without
  it, the only remedies are bigger chunks (worse retrieval) or bigger responses (wasted
  context).

A `get_document_text` full-read tool was considered and rejected: it is a context-window
hazard, and `fetch_chunk_context` already covers the legitimate need.

### A typical agent flow

> *"What's our policy on carrying over annual leave?"*

1. `search("policy on carrying over unused annual leave")` → passages from the HR FAQ and
   the onboarding guide, each with filename and section heading.
2. If the results look thin, the returned `guidance` points at `list_tags` → `search_by_tag`.
3. Follow-up *"and what does the onboarding guide say about probation?"* →
   `search_by_document(query=..., documents=["employee_onboarding_guide.md"])`.

The server also ships **instructions** (returned during `initialize`) describing this
workflow, so the agent has the strategy before it sees the first tool.

---

## RAG architecture

### Chunking

Documents are split **on structure first**, not on character offsets.

1. **Parse to a structure, not a string.** Markdown headings are read directly. Plain-text
   exports get numbered-section and ALL-CAPS heuristics. PDFs get headings from font size,
   falling back to the text heuristics when the PDF uses a single font (very common for
   documents printed from Word), and to plain `pypdf` extraction if the layout parse fails.
   Page numbers are preserved throughout.

2. **Split on headings → paragraphs → sentences → hard cut.** Compliance policies and
   manuals are heavily sectioned, and a section boundary is almost always a semantic
   boundary. Respecting it produces chunks that are *about one thing*, which is exactly
   what a single embedding can represent.

3. **~800 tokens is a ceiling, not a quota.** Measured with `tiktoken` against the
   embedding model's own tokenizer, because character budgets produce roughly 2× variance
   in real token length. Small sibling subsections (2.1, 2.2, 2.3) are packed together up
   to that ceiling, but a chunk is **never** grown across a top-level section boundary
   just to fill the budget. A document of short sections therefore yields short chunks;
   that is the intended behaviour.

4. **~120 tokens of overlap** catches answers that straddle a boundary;
   `fetch_chunk_context` covers the rest without inflating every chunk.

5. **The heading path is prepended to the embedded text.** A chunk reading *"must be
   reported within 24 hours"* is nearly unretrievable in isolation. The same chunk embedded
   as `"AML Policy > 4. Reporting > 4.2 Thresholds\n\nmust be reported within 24 hours"`
   matches short natural-language queries, and the path doubles as the citation the agent
   shows the user.

6. **Runt chunks are merged into a sibling** so no vector is stored for a stray heading.

### Embeddings

`text-embedding-3-small` (1536-d). Roughly 5× cheaper than `-large` at close to the same
retrieval quality on prose like policies and manuals, and cheap enough that re-embedding
the whole corpus after a chunker change is a non-decision. The pipeline depends on an
`Embedder` protocol, never on OpenAI directly, so a local model can be substituted by
implementing two methods.

### Hybrid retrieval

Two retrievers, fused with **Reciprocal Rank Fusion**:

- **Dense**: Qdrant cosine similarity, with tag and document filters applied *inside* the
  ANN search. Filtering during search (rather than over-fetching and discarding) is what
  makes `search_by_tag` return a true top-k of the filtered subset instead of progressively
  fewer, worse hits as the filter narrows.
- **Lexical**: Postgres `tsvector` full-text search over a `GENERATED` column, so the index
  cannot drift out of sync with the text.

Why both: financial documents are full of exact identifiers such as *MiFID II*,
*Form ADV* and *EUR 10,000*, where pure semantic search underperforms. Why RRF rather than a weighted
score blend: cosine similarity and `ts_rank_cd` are not comparable quantities, and any
weighting needs recalibration as the corpus grows. RRF uses only each result's *rank within
its own list*, so both retrievers keep their own scoring semantics and a passage has to do
well in both to win.

One detail worth calling out, because it was a real bug: the lexical query is built as an
**OR of the query's lexemes**. Postgres' `websearch_to_tsquery` and `plainto_tsquery` both
produce an AND, and agents send whole questions: no single passage contains every word of
*"how quickly must I report a suspicious transaction?"*, so the AND matched nothing and
silently disabled half the hybrid search. With OR semantics, `ts_rank_cd` still ranks
passages containing more query terms higher.

Also applied at query time: a **per-document diversity cap** (max 3 chunks from one
document in an unscoped search) so one verbose policy cannot crowd out the answer, and
score normalisation so `min_relevance` is meaningful.

### Deduplication

*"Re-uploading a document should not create duplicates"* holds regardless of **how** it is
re-uploaded, because the guarantee is enforced at three levels:

| Level | Mechanism | Catches |
| --- | --- | --- |
| File | SHA-256 of raw bytes, `UNIQUE` in Postgres | The same file uploaded again. Returns the existing document and **merges any new tags** rather than erroring. |
| Content | SHA-256 of normalised extracted text | The same document re-exported or re-saved: different bytes, identical content. The upload row is kept (status `duplicate`, linked to the original) so the user can see what happened, but it holds no chunks and no vectors, and is invisible to agents. |
| Chunk | Deterministic `uuid5(document_id, chunk_hash)`, reused as the Qdrant point id | Re-processing. Writes become upserts, so duplicate vectors are structurally impossible rather than merely unlikely. |

Deletion is the mirror image: vectors are removed first (and the delete is aborted if that
fails, rather than leaving orphaned embeddings), then rows cascade, then orphaned tags are
pruned.

---

## Stack choices

| Choice | Why |
| --- | --- |
| **Python 3.12 + FastAPI** | Required for the backend; FastAPI gives typed request/response models and OpenAPI docs for free. |
| **MCP Python SDK, Streamable HTTP** | Required transport. Run **stateless**, so the server scales horizontally behind an ordinary load balancer with no sticky sessions. |
| **Qdrant** | First-class *payload filtering*, which is what makes tag- and document-scoped search correct rather than approximate. Runs as one container locally and has a managed cloud tier. |
| **Postgres** | Document/tag/chunk metadata, plus the lexical half of the hybrid search. Using one database for both means a delete is transactional, and tag counts are exact. |
| **`text-embedding-3-small`** | Best quality-per-euro for prose at this scale; swappable behind a protocol. |
| **Next.js** | Two screens: ask and manage. App Router plus a thin fetch client, no state library. |
| **Multi-provider chat** | One adapter per vendor behind a `ChatProvider` protocol, and a model catalogue that is one line per model. An employee picks the model; an operator picks which providers exist by setting keys, or an employee adds a personal key in Settings. |
| **uv** | Fast, lockfile-based, reproducible installs; the same lock drives local dev, CI and the Docker image. |

---

## Authentication

The MCP endpoint is protected by a pre-shared bearer token (`MCP_API_KEY`), enforced by
ASGI middleware in front of the transport. The comparison is constant-time, the presented
credential is never logged, and `/healthz` stays open for container health checks.

```bash
curl -X POST http://localhost:8080/mcp \
  -H "Authorization: Bearer $MCP_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

`X-API-Key: <token>` is also accepted, for clients that make custom headers easier than
`Authorization`. Anything else gets `401` with a `WWW-Authenticate` header.

A static token is the right level here: the client is a trusted internal agent deployment,
not an end-user browser flow, and it is what every MCP client can send today. Per-user
authorisation is the natural next step: scoping which documents a given employee's agent
may retrieve. It would slot in at the same place, as a token-to-principal lookup.

---

## Connecting an MCP client

**Claude Code**

```bash
claude mcp add --transport http kb http://localhost:8080/mcp \
  --header "Authorization: Bearer $MCP_API_KEY"
claude mcp list        # kb: ... - ✔ Connected
```

**Claude Desktop**, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kb": {
      "type": "http",
      "url": "http://localhost:8080/mcp",
      "headers": { "Authorization": "Bearer YOUR_MCP_API_KEY" }
    }
  }
}
```

**curl / Postman**: see the `Authentication` section above, or run `make mcp-check`,
which walks the full handshake and prints every tool.

---

## Running locally

### With Docker (recommended)

```bash
cp .env.example .env    # set MCP_API_KEY, and OPENAI_API_KEY unless using hash mode
make up                 # postgres, qdrant, api, mcp, frontend
make seed               # sample corpus
make mcp-check          # verify the MCP endpoint
make down               # stop  (make clean also drops the volumes)
```

Migrations run automatically on API start, so a clean volume boots to a working system.

### Without Docker

Requires Postgres and Qdrant reachable, plus [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run kb-seed
uv run uvicorn kb.api.main:app --port 8000            # management API
uv run uvicorn kb.mcp_server.server:app --port 8080   # MCP server
cd ../frontend && npm install && npm run dev          # UI on :3000
```

### Offline mode

`EMBEDDING_PROVIDER=hash` swaps the OpenAI embedder for a deterministic stand-in that needs
no API key or network. Vectors are then meaningless, so the retrieval service **skips the
dense retriever entirely** rather than fusing noise into the ranking; search degrades to
keyword-only, which on a corpus of this size still returns the right passage first. This is
what CI runs, and it lets a reviewer boot the project before adding credentials.

### Tests

```bash
make test     # 89 tests; integration tests skip if Postgres/Qdrant/MCP are down
make lint     # ruff + ruff format + mypy (strict)
```

The suite covers chunking boundaries and heading recovery, the three dedup levels, RRF
ordering, and, importantly, the **MCP tool interface itself**: tool names, required
parameters, parameter bounds, the presence of sibling-redirection in each description, and
that the three search tools share one output schema. A refactor that quietly drops a
description or loosens a bound fails CI.

The chat is tested with a stub provider standing in for the LLM, which lets the rest of the
turn run for real against a live MCP server: the session, the bearer auth, tool dispatch,
citation extraction and the event stream. The vendor SDK call is the only part not covered,
since exercising it would mean spending money on every test run.

---

## Deployment

`render.yaml` is a Render blueprint deploying the API, the MCP server and the frontend as
public URLs against managed Postgres and a Qdrant Cloud cluster. Both Python services run
the same image with different start commands, identical to compose, so local and deployed
behaviour cannot diverge. Secrets are marked `sync: false` and are entered in the Render
dashboard, never committed.

To deploy: create a Qdrant Cloud cluster (free tier is enough), push this repo to GitHub,
then in Render use **New > Blueprint** pointed at the repo and fill in the `sync: false`
values when prompted (`OPENAI_API_KEY`, `MCP_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`, and
whichever chat provider keys you want enabled by default).

Since the app is then reachable by anyone with the URL, an operator does not have to fund
every visitor's chat usage: the **Settings** page lets an employee paste their own
Anthropic/OpenAI/Google key, kept in that browser's local storage and sent only as a
per-request field on `/chat`, never persisted or logged server-side (`kb/agent/catalog.py`,
`resolve()`). The MCP bearer token stays a single shared secret regardless; it gates the
retrieval tools, not the chat providers.

---

## Known limitations, and what I would do next

**Retrieval quality**
- No reranker. A cross-encoder over the top ~20 fused candidates is the single biggest
  quality win available, and slots in cleanly after fusion.
- No retrieval evaluation harness. A small labelled question→passage set with recall@k
  would turn chunker and embedding changes from judgement calls into measurements. This is
  what I would build first with more time.
- `get_document_summary` is extractive (the document's opening prose). Faithful, free and
  instant, but a generated abstractive summary would be genuinely more useful.

**Ingestion**
- Background processing uses FastAPI `BackgroundTasks`. Fine at this scale; a restart
  mid-ingestion leaves a document in `processing` until someone hits retry. Celery or ARQ
  with a durable queue is the production answer.
- No OCR, so scanned PDFs are rejected with a clear error rather than silently indexing
  nothing.
- A chunker change requires re-processing every document (`POST /documents/{id}/reprocess`,
  or the retry button in the UI). Deterministic ids make this safe but not automatic.

**Security and multi-tenancy**
- One shared API key, so every agent sees the whole corpus. Per-user tokens mapped to
  document ACLs is the obvious next step for a financial-services client, with the filter
  pushed into the Qdrant payload query so it cannot be bypassed.
- No rate limiting or per-client quotas on the MCP endpoint.

**The assistant**
- Conversations are not persisted. Reloading the page starts fresh, and history lives only
  in the browser tab. Storing threads per user is the obvious next step.
- Only Anthropic streams token by token. The OpenAI and Google adapters emit each message
  in one piece, because their tool loops are simpler to keep correct non-streamed; the
  event contract already supports deltas, so this is an adapter change, not a redesign.
- No per-user rate limiting on `/chat`, and no cost accounting per employee. A personal
  key entered in Settings at least keeps one employee's usage off the operator's bill;
  it does not add a quota of its own.

**Operations**
- Structured JSON logs, but no metrics or tracing. Retrieval latency percentiles and a
  "queries that returned nothing" counter would be the first two dashboards; the second
  is the best available signal for what the knowledge base is missing.
- The UI has no auth; it assumes deployment behind a corporate SSO proxy.
