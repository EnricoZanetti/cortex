---
name: reseed
description: Wipe the Postgres and Qdrant data and re-ingest the sample corpus from scratch. Use after changing the chunker, the parsers, or any file under backend/sample_documents/, since indexed chunks hold a copy of the text and go stale silently otherwise. Also use when asked to reset, reseed, or refresh the knowledge base.
disable-model-invocation: true
---

This clears all documents and vectors, then re-ingests `backend/sample_documents/`. It is
destructive to anything currently in the local knowledge base (uploads made through the
UI included), so confirm with the user before running it unless they explicitly asked for
a reset.

1. Make sure the stack is up (`docker compose ps` from the repo root; start with `make up`
   if not).

2. Clear the stored documents, chunks and vectors:

```bash
docker compose exec api python -c "
from sqlalchemy import text
from kb.db.session import get_engine
from kb.retrieval.vector_store import VectorStore
with get_engine().begin() as c:
    c.execute(text('TRUNCATE documents, tags, chunks, document_tags CASCADE'))
vs = VectorStore()
vs.client.delete_collection(vs.collection)
vs.ensure_collection()
print('cleared')
"
```

3. Re-ingest the sample corpus:

```bash
make seed
```

4. Verify the result with a real search, so a chunking regression shows up immediately
   rather than being discovered later:

```bash
./scripts/check_mcp.sh
```

Report the document and chunk counts from `make seed`'s output, and confirm the
`tools/call search` result at the end of `check_mcp.sh` looks sensible.
