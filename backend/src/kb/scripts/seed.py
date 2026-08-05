"""Seed the knowledge base with the sample corpus.

Run with ``make seed`` (or ``uv run kb-seed``). Safe to run repeatedly: the ingestion
pipeline's file-hash deduplication turns a second run into a no-op.
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
from pathlib import Path

from kb.db.session import session_scope
from kb.ingestion.pipeline import IngestionService
from kb.logging import configure_logging, get_logger
from kb.retrieval.vector_store import VectorStore

logger = get_logger(__name__)

DEFAULT_CORPUS = Path(__file__).resolve().parents[3] / "sample_documents"

# Tags for the sample corpus. Documents intentionally carry more than one tag so the
# demo can show tag_match="all" narrowing meaningfully.
SAMPLE_TAGS: dict[str, list[str]] = {
    "aml_policy.md": ["compliance", "policy"],
    "information_security_policy.md": ["compliance", "policy", "security"],
    "client_onboarding_procedure.md": ["compliance", "onboarding", "operations"],
    "employee_onboarding_guide.md": ["hr", "onboarding"],
    "hr_faq_export.txt": ["hr", "faq"],
    "vault_product_manual.md": ["product"],
    "regulatory_filing_calendar.pdf": ["compliance", "policy"],
    "vault_fee_schedule.pdf": ["product"],
}


def seed(corpus: Path, *, tags: dict[str, list[str]] | None = None) -> int:
    """Ingest every supported file in ``corpus``. Returns the number newly ingested."""
    tags = tags or SAMPLE_TAGS
    service = IngestionService()
    VectorStore().ensure_collection()

    files = sorted(
        path
        for path in corpus.iterdir()
        if path.is_file() and path.suffix.lower() in {".md", ".txt", ".pdf"}
    )
    if not files:
        logger.warning("seed_no_files", corpus=str(corpus))
        return 0

    ingested = 0
    for path in files:
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "text/plain"
        with session_scope() as session:
            result = service.upload(
                session,
                data=data,
                filename=path.name,
                content_type=content_type,
                tags=tags.get(path.name, ["uncategorised"]),
            )
            document_id = result.document.id
            duplicate = result.duplicate
        if duplicate:
            print(f"  skip  {path.name}  (already ingested)")
            continue
        print(f"  add   {path.name}  -> processing")
        service.process_document(document_id)
        ingested += 1

    return ingested


def main() -> int:
    """CLI entrypoint."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Seed the knowledge base with sample documents.")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help=f"Directory of documents to ingest (default: {DEFAULT_CORPUS}).",
    )
    args = parser.parse_args()

    if not args.corpus.is_dir():
        print(f"Corpus directory not found: {args.corpus}", file=sys.stderr)
        return 1

    print(f"Seeding from {args.corpus}")
    count = seed(args.corpus)
    print(f"Done. {count} document(s) newly ingested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
