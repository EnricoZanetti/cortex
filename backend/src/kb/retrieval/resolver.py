"""Resolving user/agent-supplied document references to document ids.

The ``search_by_document`` tool accepts "one or more specific documents **by name or
ID**". An LLM will supply whatever string appeared in an earlier tool result -- a UUID, a
full filename, or a paraphrase of the title. Resolution therefore cascades:

    exact UUID -> exact filename -> exact title -> unique substring match

and on failure returns *candidates*, so the agent can correct itself in a single follow-up
turn instead of guessing blindly or giving up.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from kb.db.models import Document
from kb.db.repositories import DocumentRepository


@dataclass(frozen=True, slots=True)
class ResolvedReference:
    """How one input string was interpreted."""

    query: str
    document_id: uuid.UUID
    filename: str
    matched_on: str  # "id" | "filename" | "title" | "partial-name"


@dataclass(frozen=True, slots=True)
class UnresolvedReference:
    """An input string that could not be resolved, with actionable candidates."""

    query: str
    reason: str
    candidates: list[tuple[uuid.UUID, str]]


@dataclass(frozen=True, slots=True)
class ResolutionOutcome:
    """Combined result of resolving a batch of references."""

    resolved: list[ResolvedReference]
    unresolved: list[UnresolvedReference]

    @property
    def document_ids(self) -> list[uuid.UUID]:
        # dict.fromkeys preserves the agent's ordering while removing duplicates.
        return list(dict.fromkeys(reference.document_id for reference in self.resolved))


class DocumentResolver:
    """Resolves document references supplied by an agent."""

    def __init__(self, session: Session) -> None:
        self.repo = DocumentRepository(session)

    def resolve_many(self, references: list[str]) -> ResolutionOutcome:
        resolved: list[ResolvedReference] = []
        unresolved: list[UnresolvedReference] = []
        for reference in references:
            outcome = self.resolve_one(reference)
            if isinstance(outcome, ResolvedReference):
                resolved.append(outcome)
            else:
                unresolved.append(outcome)
        return ResolutionOutcome(resolved=resolved, unresolved=unresolved)

    def resolve_one(self, reference: str) -> ResolvedReference | UnresolvedReference:
        needle = reference.strip()
        if not needle:
            return UnresolvedReference(query=reference, reason="Empty reference.", candidates=[])

        document = self._by_id(needle)
        if document is not None:
            return ResolvedReference(needle, document.id, document.filename, "id")

        exact = self.repo.get_by_filename(needle)
        if len(exact) == 1:
            return ResolvedReference(needle, exact[0].id, exact[0].filename, "filename")
        if len(exact) > 1:
            return UnresolvedReference(
                query=needle,
                reason=f"{len(exact)} documents share the filename {needle!r}; use a document_id.",
                candidates=[(item.id, item.filename) for item in exact],
            )

        partial = self.repo.search_by_filename_fragment(needle, limit=8)
        if len(partial) == 1:
            match = partial[0]
            matched_on = (
                "title"
                if match.title and match.title.strip().lower() == needle.lower()
                else "partial-name"
            )
            return ResolvedReference(needle, match.id, match.filename, matched_on)
        if len(partial) > 1:
            exact_title = [
                item for item in partial if (item.title or "").strip().lower() == needle.lower()
            ]
            if len(exact_title) == 1:
                match = exact_title[0]
                return ResolvedReference(needle, match.id, match.filename, "title")
            return UnresolvedReference(
                query=needle,
                reason=(
                    f"{needle!r} matches {len(partial)} documents. Re-call this tool with one of "
                    "the document_id values listed in candidates."
                ),
                candidates=[(item.id, item.filename) for item in partial],
            )

        return UnresolvedReference(
            query=needle,
            reason=(
                f"No document matches {needle!r}. Call list_documents to see what is available."
            ),
            candidates=[],
        )

    def _by_id(self, needle: str) -> Document | None:
        try:
            candidate = uuid.UUID(needle)
        except ValueError:
            return None
        return self.repo.get(candidate)
