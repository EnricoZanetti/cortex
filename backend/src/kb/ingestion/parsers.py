"""Document parsing.

A parser turns raw bytes into a ``ParsedDocument``: a list of ``Block`` objects that keep
the *structure* of the source (heading level, page number), not just a flat string.

Structure is what makes the rest of the pipeline good: the chunker splits on real section
boundaries instead of arbitrary character offsets, and every chunk can carry the heading
path it came from, which is what turns an isolated chunk into a self-describing citation.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from kb.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_CONTENT_TYPES: dict[str, str] = {
    "application/pdf": "pdf",
    "text/plain": "text",
    "text/markdown": "text",
    "text/x-markdown": "text",
    "application/octet-stream": "auto",  # browsers send this for .md/.txt sometimes
}

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
}


class UnsupportedDocumentError(ValueError):
    """Raised when a file type cannot be parsed."""


class DocumentParseError(RuntimeError):
    """Raised when a supported file type fails to parse."""


@dataclass(slots=True)
class Block:
    """A structural unit of text: a heading or a paragraph."""

    text: str
    level: int = 0  # 0 = body text, 1..6 = heading level
    page: int | None = None

    @property
    def is_heading(self) -> bool:
        return self.level > 0


@dataclass(slots=True)
class ParsedDocument:
    """Structured output of a parser."""

    blocks: list[Block] = field(default_factory=list)
    page_count: int | None = None
    title: str | None = None

    @property
    def text(self) -> str:
        """Flat text, used for the content-level dedup fingerprint."""
        return "\n\n".join(block.text for block in self.blocks)


class Parser(Protocol):
    """Structural interface every parser implements."""

    def parse(self, data: bytes, filename: str) -> ParsedDocument: ...


# --- text / markdown -------------------------------------------------------------

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_SETEXT_H1 = re.compile(r"^={3,}\s*$")
_SETEXT_H2 = re.compile(r"^-{3,}\s*$")
# Plain-text files (FAQ exports, policy dumps) often mark sections as "3.1 TITLE" or an
# ALL-CAPS line. These heuristics recover structure that would otherwise be lost.
#
# The numbering must be unambiguous -- either multi-level ("2.1 ...") or followed by a
# real separator ("2. ..." / "2) ..."). A bare leading integer is NOT enough: lines like
# "25 days per year plus public holidays" would otherwise be misread as a heading, which
# corrupts every heading_path downstream.
_NUMBERED_HEADING = re.compile(r"^\s*(?:(\d+(?:\.\d+)+)\.?|(\d+)[.)])\s+(\S.{0,80})$")


class TextParser:
    """Parses ``.txt`` and ``.md`` with heading recovery."""

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        raw = self._decode(data)
        lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        # If the file already uses markdown '#' headings, its structure is explicit, so
        # the fuzzy heuristics are switched off. Otherwise a numbered *list item*
        # ("3. Install the agent") would be promoted to a heading and poison every
        # heading_path below it. Plain .txt exports have no such markers, so there the
        # heuristics are the only structure available and are worth their false positives.
        has_markdown_headings = any(_MD_HEADING.match(line) for line in lines)
        blocks: list[Block] = []
        buffer: list[str] = []

        def flush() -> None:
            if buffer:
                text = "\n".join(buffer).strip()
                if text:
                    blocks.append(Block(text=text, level=0))
                buffer.clear()

        for index, line in enumerate(lines):
            heading = self._detect_heading(
                line,
                lines[index + 1] if index + 1 < len(lines) else "",
                allow_heuristics=not has_markdown_headings,
            )
            if heading is not None:
                flush()
                level, text = heading
                blocks.append(Block(text=text, level=level))
            elif not line.strip():
                flush()
            elif _SETEXT_H1.match(line) or _SETEXT_H2.match(line):
                continue  # underline consumed by the heading above
            else:
                buffer.append(line)
        flush()

        title = next((block.text for block in blocks if block.is_heading), None)
        return ParsedDocument(blocks=blocks, page_count=None, title=title)

    @staticmethod
    def _decode(data: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _detect_heading(
        line: str, next_line: str, *, allow_heuristics: bool = True
    ) -> tuple[int, str] | None:
        markdown = _MD_HEADING.match(line)
        if markdown:
            return len(markdown.group(1)), markdown.group(2).strip()
        stripped = line.strip()
        if not stripped or not allow_heuristics:
            return None
        if _SETEXT_H1.match(next_line):
            return 1, stripped
        if _SETEXT_H2.match(next_line) and not stripped.startswith("-"):
            return 2, stripped
        numbered = _NUMBERED_HEADING.match(line)
        if numbered and not stripped.endswith((".", ",", ";", ":")):
            number = numbered.group(1) or numbered.group(2)
            depth = number.count(".") + 1
            return min(depth, 6), stripped
        if (
            len(stripped) <= 80
            and stripped.upper() == stripped
            and any(char.isalpha() for char in stripped)
            and not stripped.endswith((".", ","))
        ):
            return 2, stripped
        return None


# --- pdf ---------------------------------------------------------------------------


class PdfParser:
    """Parses PDFs, recovering headings from font size where the layout allows it.

    ``pdfplumber`` exposes per-character font sizes, which lets us treat visually larger
    lines as headings. When that fails (scanned or unusual PDFs) we fall back to
    ``pypdf`` plain extraction and then to the text heuristics, so ingestion degrades
    gracefully rather than failing.
    """

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        try:
            parsed = self._parse_with_layout(data)
            if parsed.blocks:
                return parsed
        except Exception as exc:  # pragma: no cover - depends on the PDF
            logger.warning("pdfplumber_failed", filename=filename, error=str(exc))
        return self._parse_plain(data, filename)

    def _parse_with_layout(self, data: bytes) -> ParsedDocument:
        import pdfplumber

        blocks: list[Block] = []
        sizes: list[float] = []
        page_lines: list[tuple[int, str, float]] = []

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            page_count = len(pdf.pages)
            for page_number, page in enumerate(pdf.pages, start=1):
                words = page.extract_words(extra_attrs=["size"]) or []
                for line_text, size in self._group_words_into_lines(words):
                    page_lines.append((page_number, line_text, size))
                    sizes.append(size)

        if not page_lines:
            return ParsedDocument(blocks=[], page_count=page_count)

        body_size = _median(sizes)
        # Many business PDFs are printed from plain text and use a single font size, so
        # the visual heuristic finds nothing. Rather than lose all structure, fall back to
        # the textual heuristics (numbered sections, ALL-CAPS lines) used for .txt files.
        font_headings_exist = any(
            size > body_size * 1.15 and len(text) <= 120 for _, text, size in page_lines
        )
        buffer: list[str] = []
        buffer_page = page_lines[0][0]

        def flush() -> None:
            if buffer:
                text = " ".join(buffer).strip()
                if text:
                    blocks.append(Block(text=text, level=0, page=buffer_page))
                buffer.clear()

        for page_number, line_text, size in page_lines:
            if font_headings_exist:
                by_font = size > body_size * 1.15 and len(line_text) <= 120
                heading = (1 if size > body_size * 1.4 else 2, line_text) if by_font else None
            else:
                heading = TextParser._detect_heading(line_text, "", allow_heuristics=True)
            if heading is not None:
                flush()
                level, text = heading
                blocks.append(Block(text=text, level=level, page=page_number))
                buffer_page = page_number
            else:
                if not buffer:
                    buffer_page = page_number
                buffer.append(line_text)
        flush()

        title = next((block.text for block in blocks if block.is_heading), None)
        return ParsedDocument(blocks=blocks, page_count=page_count, title=title)

    @staticmethod
    def _group_words_into_lines(words: list[dict[str, Any]]) -> list[tuple[str, float]]:
        """Group extracted words into visual lines with a representative font size."""
        lines: dict[int, list[dict[str, Any]]] = {}
        for word in words:
            key = round(float(word.get("top", 0)) / 3.0)
            lines.setdefault(key, []).append(word)
        result: list[tuple[str, float]] = []
        for key in sorted(lines):
            group = sorted(lines[key], key=lambda item: float(item.get("x0", 0)))
            text = " ".join(str(item.get("text", "")) for item in group).strip()
            if not text:
                continue
            size = _median([float(item.get("size", 0) or 0) for item in group]) or 1.0
            result.append((text, size))
        return result

    def _parse_plain(self, data: bytes, filename: str) -> ParsedDocument:
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            blocks: list[Block] = []
            for page_number, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                if not text:
                    continue
                for paragraph in re.split(r"\n\s*\n", text):
                    cleaned = " ".join(paragraph.split())
                    if cleaned:
                        blocks.append(Block(text=cleaned, level=0, page=page_number))
            if not blocks:
                raise DocumentParseError(
                    f"No extractable text in {filename!r}. The PDF is likely a scan; "
                    "OCR is not enabled in this build."
                )
            return ParsedDocument(blocks=blocks, page_count=len(reader.pages))
        except DocumentParseError:
            raise
        except Exception as exc:
            raise DocumentParseError(f"Failed to parse PDF {filename!r}: {exc}") from exc


def _median(values: list[float]) -> float:
    filtered = sorted(value for value in values if value > 0)
    if not filtered:
        return 0.0
    middle = len(filtered) // 2
    if len(filtered) % 2:
        return filtered[middle]
    return (filtered[middle - 1] + filtered[middle]) / 2


# --- registry ----------------------------------------------------------------------

_PARSERS: dict[str, Parser] = {"pdf": PdfParser(), "text": TextParser()}


def resolve_parser_kind(filename: str, content_type: str | None) -> str:
    """Decide which parser to use, preferring the file extension over the browser MIME."""
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension in SUPPORTED_EXTENSIONS:
        return SUPPORTED_EXTENSIONS[extension]
    mapped = SUPPORTED_CONTENT_TYPES.get((content_type or "").split(";")[0].strip().lower())
    if mapped and mapped != "auto":
        return mapped
    raise UnsupportedDocumentError(
        f"Unsupported file type for {filename!r}. Supported: "
        f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}."
    )


def _strip_nul_bytes(parsed: ParsedDocument) -> ParsedDocument:
    """Drop NUL bytes that some PDF extractors (and malformed text files) emit.

    Postgres text columns reject 0x00 outright, so a stray NUL deep in a chunk fails the
    whole insert; stripping it here, right after extraction, is cheaper than validating at
    every downstream writer.
    """
    for block in parsed.blocks:
        if "\x00" in block.text:
            block.text = block.text.replace("\x00", "")
    if parsed.title and "\x00" in parsed.title:
        parsed.title = parsed.title.replace("\x00", "")
    return parsed


def parse_document(data: bytes, filename: str, content_type: str | None) -> ParsedDocument:
    """Parse ``data`` into a structured document, raising on unsupported/broken input."""
    kind = resolve_parser_kind(filename, content_type)
    parsed = _strip_nul_bytes(_PARSERS[kind].parse(data, filename))
    if not parsed.blocks:
        raise DocumentParseError(f"No text could be extracted from {filename!r}.")
    if parsed.title is None:
        parsed.title = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
    return parsed
