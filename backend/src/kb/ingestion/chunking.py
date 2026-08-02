"""Structure-aware chunking.

Strategy (rationale in the project README)
------------------------------------------
1. **Split on structure first.** Headings define sections; sections are split into
   paragraphs; over-long paragraphs into sentences; and only as a last resort do we cut
   mid-sentence. Corporate policies and manuals are heavily sectioned, and a section
   boundary is almost always a semantic boundary, so respecting it produces chunks that
   are *about one thing* -- which is exactly what a single embedding can represent well.

2. **Measure in tokens, not characters,** using the embedding model's own tokenizer.
   Character budgets silently produce 2x variance in real token length across documents.

3. **~800 tokens is a ceiling, not a quota, with ~120 tokens of overlap.** Large enough
   to contain a complete policy clause with its conditions; small enough that one vector
   is not an average of three unrelated topics. Small sibling subsections are packed
   together up to that ceiling, but a chunk is never grown across a top-level section
   boundary just to fill the budget -- so a document of short sections yields short
   chunks, and that is the intended behaviour. Overlap catches answers that straddle a
   boundary; the ``fetch_chunk_context`` MCP tool covers the residual cases without
   inflating every chunk.

4. **Prepend the heading path to the embedded text.** A chunk reading "must be reported
   within 24 hours" is nearly unretrievable in isolation; the same chunk prefixed with
   "AML Policy > 4. Reporting > 4.2 Thresholds" matches short natural-language queries.

5. **Merge runt chunks.** Fragments under ~150 tokens (a stray heading, a list stub) are
   merged forward so we never store a vector that carries no information.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from kb.config import get_settings
from kb.ingestion.parsers import Block, ParsedDocument

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?;])\s+(?=[A-Z(\[])")


@dataclass(slots=True)
class TextChunk:
    """A chunk ready to be embedded and stored."""

    index: int
    text: str
    heading_path: str | None
    page: int | None
    token_count: int
    # True when this chunk is a whole section rather than a slice of an over-long one.
    # Only whole sections are eligible to be packed together with a neighbour.
    whole_section: bool = True

    @property
    def embedding_text(self) -> str:
        """Text actually sent to the embedding model: heading path + body."""
        if self.heading_path:
            return f"{self.heading_path}\n\n{self.text}"
        return self.text


@lru_cache(maxsize=4)
def _encoder(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        # All current OpenAI embedding models use cl100k_base; this is also a safe
        # approximation for third-party models, which is all a chunk budget needs.
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str | None = None) -> int:
    """Count tokens with the embedding model's tokenizer."""
    return len(_encoder(model or get_settings().embedding_model).encode(text))


@dataclass(slots=True)
class _Section:
    """A heading path plus the body text that sits underneath it."""

    heading_path: str | None
    page: int | None
    paragraphs: list[str]


class Chunker:
    """Turns a :class:`ParsedDocument` into overlapping, structure-aware chunks."""

    def __init__(
        self,
        *,
        target_tokens: int | None = None,
        overlap_tokens: int | None = None,
        min_tokens: int | None = None,
        model: str | None = None,
    ) -> None:
        settings = get_settings()
        self.target_tokens = target_tokens or settings.chunk_target_tokens
        self.overlap_tokens = (
            overlap_tokens if overlap_tokens is not None else settings.chunk_overlap_tokens
        )
        self.min_tokens = min_tokens if min_tokens is not None else settings.chunk_min_tokens
        self.model = model or settings.embedding_model
        self._enc = _encoder(self.model)

    # --- public API ----------------------------------------------------------------

    def chunk(self, document: ParsedDocument) -> list[TextChunk]:
        """Chunk a parsed document. Returns chunks in reading order, index starting at 0."""
        sections = self._build_sections(document.blocks, document.title)
        raw: list[TextChunk] = []
        for section in sections:
            section_chunks = self._chunk_section(section)
            if len(section_chunks) > 1:
                # A section that had to be split is already at the token target; its
                # pieces must not be packed with anything else.
                for piece in section_chunks:
                    piece.whole_section = False
            raw.extend(section_chunks)

        packed = self._pack_sections(raw)
        merged = self._merge_runts(packed)
        for position, chunk in enumerate(merged):
            chunk.index = position
        return merged

    # --- internals -----------------------------------------------------------------

    def _build_sections(self, blocks: list[Block], title: str | None) -> list[_Section]:
        """Walk blocks, maintaining a heading stack, and group body text per section."""
        sections: list[_Section] = []
        stack: list[tuple[int, str]] = []
        current: _Section | None = None

        for block in blocks:
            if block.is_heading:
                while stack and stack[-1][0] >= block.level:
                    stack.pop()
                stack.append((block.level, block.text))
                current = _Section(
                    heading_path=self._render_path(stack, title),
                    page=block.page,
                    paragraphs=[],
                )
                sections.append(current)
            else:
                if current is None:
                    current = _Section(heading_path=title, page=block.page, paragraphs=[])
                    sections.append(current)
                if current.page is None:
                    current.page = block.page
                current.paragraphs.append(block.text)

        return [section for section in sections if section.paragraphs]

    @staticmethod
    def _render_path(stack: list[tuple[int, str]], title: str | None) -> str:
        parts = [text for _, text in stack]
        if title and (not parts or parts[0].strip().lower() != title.strip().lower()):
            parts.insert(0, title)
        return " > ".join(part.strip() for part in parts if part.strip())[:1024]

    def _chunk_section(self, section: _Section) -> list[TextChunk]:
        """Pack a section's paragraphs into token-budgeted chunks with overlap."""
        units = self._split_into_units(section.paragraphs)
        chunks: list[TextChunk] = []
        buffer: list[tuple[str, int]] = []
        buffer_tokens = 0

        def emit() -> None:
            nonlocal buffer, buffer_tokens
            if not buffer:
                return
            text = "\n\n".join(unit for unit, _ in buffer).strip()
            if text:
                chunks.append(
                    TextChunk(
                        index=0,
                        text=text,
                        heading_path=section.heading_path,
                        page=section.page,
                        token_count=buffer_tokens,
                    )
                )
            tail = self._overlap_tail(buffer)
            buffer = tail
            buffer_tokens = sum(tokens for _, tokens in tail)

        for unit, tokens in units:
            if buffer and buffer_tokens + tokens > self.target_tokens:
                emit()
            buffer.append((unit, tokens))
            buffer_tokens += tokens
            if buffer_tokens >= self.target_tokens:
                emit()

        # Flush whatever is left, but ignore a tail that is purely overlap.
        if buffer and not self._is_pure_overlap(buffer, chunks):
            text = "\n\n".join(unit for unit, _ in buffer).strip()
            if text:
                chunks.append(
                    TextChunk(
                        index=0,
                        text=text,
                        heading_path=section.heading_path,
                        page=section.page,
                        token_count=buffer_tokens,
                    )
                )
        return chunks

    @staticmethod
    def _is_pure_overlap(buffer: list[tuple[str, int]], chunks: list[TextChunk]) -> bool:
        if not chunks:
            return False
        candidate = "\n\n".join(unit for unit, _ in buffer).strip()
        return bool(candidate) and candidate in chunks[-1].text

    def _overlap_tail(self, buffer: list[tuple[str, int]]) -> list[tuple[str, int]]:
        """Return the trailing units that fit inside the overlap budget."""
        if self.overlap_tokens <= 0:
            return []
        tail: list[tuple[str, int]] = []
        total = 0
        for unit, tokens in reversed(buffer):
            if total + tokens > self.overlap_tokens:
                break
            tail.insert(0, (unit, tokens))
            total += tokens
        return tail

    def _split_into_units(self, paragraphs: list[str]) -> list[tuple[str, int]]:
        """Split section text into atomic packing units, each within the token budget."""
        units: list[tuple[str, int]] = []
        for paragraph in paragraphs:
            cleaned = paragraph.strip()
            if not cleaned:
                continue
            tokens = len(self._enc.encode(cleaned))
            if tokens <= self.target_tokens:
                units.append((cleaned, tokens))
                continue
            for sentence in self._split_sentences(cleaned):
                sentence_tokens = len(self._enc.encode(sentence))
                if sentence_tokens <= self.target_tokens:
                    units.append((sentence, sentence_tokens))
                else:
                    units.extend(self._hard_split(sentence))
        return units

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        parts = [part.strip() for part in _SENTENCE_BOUNDARY.split(text)]
        return [part for part in parts if part]

    def _hard_split(self, text: str) -> list[tuple[str, int]]:
        """Last resort: cut a single over-long sentence on token boundaries."""
        token_ids = self._enc.encode(text)
        pieces: list[tuple[str, int]] = []
        for start in range(0, len(token_ids), self.target_tokens):
            window = token_ids[start : start + self.target_tokens]
            pieces.append((self._enc.decode(window).strip(), len(window)))
        return [(text, tokens) for text, tokens in pieces if text]

    def _pack_sections(self, chunks: list[TextChunk]) -> list[TextChunk]:
        """Combine consecutive small sections that live under the same parent heading.

        Documents like policies are made of many short subsections (2.1, 2.2, 2.3). Left
        alone, each becomes a 40-150 token chunk: precise, but wasteful of the token
        budget and prone to splitting a rule from its exceptions. Packing siblings back
        together up to the target -- and only siblings, never across a top-level section
        boundary -- keeps chunks topically coherent while actually using the budget.

        The resulting chunk is labelled with the deepest heading path the packed pieces
        share, so the citation stays truthful about what the chunk covers.
        """
        packed: list[TextChunk] = []
        for chunk in chunks:
            previous = packed[-1] if packed else None
            if (
                previous is not None
                and previous.whole_section
                and chunk.whole_section
                and previous.token_count + chunk.token_count <= self.target_tokens
                and self._share_parent(previous.heading_path, chunk.heading_path)
            ):
                previous.text = f"{previous.text}\n\n{chunk.text}".strip()
                previous.token_count += chunk.token_count
                previous.heading_path = self._common_prefix(
                    previous.heading_path, chunk.heading_path
                )
                continue
            packed.append(chunk)
        return packed

    @staticmethod
    def _split_path(path: str | None) -> list[str]:
        return [part.strip() for part in (path or "").split(">") if part.strip()]

    @classmethod
    def _common_prefix(cls, left: str | None, right: str | None) -> str | None:
        shared: list[str] = []
        for a, b in zip(cls._split_path(left), cls._split_path(right), strict=False):
            if a != b:
                break
            shared.append(a)
        return " > ".join(shared) or None

    @classmethod
    def _is_ancestor(cls, left: str | None, right: str | None) -> bool:
        """True when ``left``'s heading path is a strict prefix of ``right``'s."""
        head, tail = cls._split_path(left), cls._split_path(right)
        return len(head) < len(tail) and tail[: len(head)] == head

    @classmethod
    def _share_parent(cls, left: str | None, right: str | None) -> bool:
        """True when two heading paths sit under the same top-level section.

        Requires at least two shared components (document title + one heading), so
        unrelated top-level sections are never merged into a single chunk.
        """
        return len(cls._split_path(cls._common_prefix(left, right))) >= 2

    def _merge_runts(self, chunks: list[TextChunk]) -> list[TextChunk]:
        """Merge chunks below ``min_tokens`` into a neighbour sharing the same section."""
        if not chunks:
            return []
        merged: list[TextChunk] = []
        for chunk in chunks:
            previous = merged[-1] if merged else None
            if (
                previous is not None
                and chunk.token_count < self.min_tokens
                # Siblings only: without this, every runt would chain into its neighbour,
                # the shared path would erode to the document title, and the whole
                # document would collapse into one chunk.
                and self._share_parent(previous.heading_path, chunk.heading_path)
                and previous.token_count + chunk.token_count <= self.target_tokens * 1.3
            ):
                previous.text = f"{previous.text}\n\n{chunk.text}".strip()
                previous.token_count += chunk.token_count
                previous.heading_path = self._common_prefix(
                    previous.heading_path, chunk.heading_path
                )
                continue
            merged.append(chunk)

        # A leading runt has no previous sibling to merge into, so fold it forward -- but
        # only when it is genuinely a preamble, i.e. its heading path is an *ancestor* of
        # the next chunk's (a title block sitting above the first section). A short first
        # section that merely sits beside the next one keeps its own identity, otherwise
        # its heading would be erased from the citation.
        if len(merged) > 1 and merged[0].token_count < self.min_tokens:
            head, following = merged[0], merged[1]
            if self._is_ancestor(head.heading_path, following.heading_path) and (
                head.token_count + following.token_count <= self.target_tokens * 1.3
            ):
                following.text = f"{head.text}\n\n{following.text}".strip()
                following.token_count += head.token_count
                following.heading_path = (
                    self._common_prefix(head.heading_path, following.heading_path)
                    or following.heading_path
                )
                merged.pop(0)
        return merged
