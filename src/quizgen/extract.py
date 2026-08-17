"""
PDF -> pages of text, via Azure Document Intelligence when it is configured.

WHY NOT JUST PYPDF
pypdf reads the text layer of a PDF. When there is no text layer — a scan, a photographed
page, anything that went through a printer — it returns nothing at all, silently. And when
there is one, it emits glyphs in the order the file stores them, which for a two-column
policy document interleaves the columns and for a table produces the cells in an order
nobody wrote.

That matters more here than in most places. Every question is grounded in a quoted passage
and validated by finding that quote verbatim in the source, so garbled extraction does not
degrade question quality gently — it fails the grounding check and the question is thrown
away. The failure looks like "the model is bad at this document" rather than "the text was
scrambled before the model saw it".

WHAT DOCUMENT INTELLIGENCE ADDS
  * OCR, so a scanned page produces text instead of nothing
  * reading order, so columns and tables come out as written
  * paragraph ROLES — it labels headings as headings

That last one is worth more than it sounds. ingest.py finds sections with
`looks_like_heading()`, a set of heuristics over line length and capitalisation, and its
own comment admits it is deliberately conservative because a false positive fragments a
section. When Document Intelligence has already labelled a paragraph `sectionHeading`,
that guess is replaced by something the extractor actually determined.

FALLING BACK IS A FEATURE
With no endpoint configured this uses pypdf and says so. `QUIZGEN_PROVIDER=mock` plus no
Azure account has to keep working — it is a hard constraint in PROJECT.md, and tests.yml
holds no Azure credentials by design, so a hard dependency here would mean the test suite
could not extract a PDF at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .config import CONFIG

# Marks a line the extractor itself labelled a heading, so ingest.py can stop guessing.
# looks_like_heading() otherwise decides from line length and capitalisation, and its own
# comment admits it is deliberately conservative.
#
# U+0002 START OF TEXT — a control character, so it cannot appear in extracted prose and
# cannot be typed into a document by accident. It is written as a literal character here,
# which means grep and most editors render it as nothing: the line below looks like an
# empty string and is not.
#
# It MUST NOT actually be empty. `"anything".startswith("")` is True, so an empty marker
# makes looks_like_heading() return True for EVERY line — every line becomes its own
# section, every section its own topic, and mastery ends up measured per line. Nothing
# raises; the pipeline runs to completion and produces nonsense. See
# tests/test_extract.py::TestTheHeadingSentinel, which fails four ways if it goes empty.
HEADING_PREFIX = ""


@dataclass
class Extraction:
    """Pages of text, plus how they were produced."""

    pages: List[str] = field(default_factory=list)
    #: "document-intelligence" or "pypdf". Recorded rather than inferred, because
    #: "why did this document extract badly" is the first question anyone asks.
    engine: str = "pypdf"
    #: Headings Document Intelligence labelled itself, in document order. Empty for pypdf.
    headings: List[str] = field(default_factory=list)
    #: Pages that produced nothing — a scan, when pypdf was the engine.
    empty_pages: List[int] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(p.strip() for p in self.pages)


def extract(pdf_path: Path, *, prefer_azure: Optional[bool] = None) -> Extraction:
    """
    Extract `pdf_path`, using Document Intelligence when configured.

    `prefer_azure=False` forces pypdf — used by tests, and by anyone who wants to see
    what the offline path produces without unsetting their environment.
    """
    use_azure = CONFIG.doc_intelligence_configured if prefer_azure is None else prefer_azure

    if use_azure:
        try:
            return _extract_azure(pdf_path)
        except Exception as exc:  # noqa: BLE001
            # Falling back rather than failing, but LOUDLY. A silent fallback means a
            # scanned document quietly produces nothing and the run looks successful,
            # which is the exact failure Document Intelligence was added to fix.
            print("  ! Document Intelligence failed on {} ({}: {}). "
                  "Falling back to pypdf — scanned pages will produce nothing."
                  .format(pdf_path.name, type(exc).__name__, str(exc)[:160]))

    return _extract_pypdf(pdf_path)


# ---------------------------------------------------------------------------
# pypdf — the offline path
# ---------------------------------------------------------------------------


def _extract_pypdf(pdf_path: Path) -> Extraction:
    from pypdf import PdfReader

    from .ingest import clean_text

    reader = PdfReader(str(pdf_path))
    pages: List[str] = []
    empty: List[int] = []

    for number, page in enumerate(reader.pages, start=1):
        try:
            text = clean_text(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            text = ""
        if not text.strip():
            empty.append(number)
        pages.append(text)

    return Extraction(pages=pages, engine="pypdf", empty_pages=empty)


# ---------------------------------------------------------------------------
# Document Intelligence
# ---------------------------------------------------------------------------


def _extract_azure(pdf_path: Path) -> Extraction:
    """
    Analyse with prebuilt-layout and rebuild pages in reading order.

    Paragraphs are used rather than the flat `result.content`, because paragraphs carry
    the page they belong to and the role they play. Page identity has to survive: a
    citation is "document, page 3, quote", and a question whose page number is wrong is
    worse than one with no citation — it looks verifiable and is not.
    """
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
    from azure.core.credentials import AzureKeyCredential

    from .ingest import clean_text

    client = DocumentIntelligenceClient(
        endpoint=CONFIG.doc_intelligence_endpoint,
        credential=AzureKeyCredential(CONFIG.doc_intelligence_key),
    )

    poller = client.begin_analyze_document(
        CONFIG.doc_intelligence_model,
        AnalyzeDocumentRequest(bytes_source=pdf_path.read_bytes()),
    )
    result = poller.result()

    page_count = len(result.pages or []) or 1
    buckets: List[List[str]] = [[] for _ in range(page_count)]
    headings: List[str] = []

    for paragraph in (result.paragraphs or []):
        text = (paragraph.content or "").strip()
        if not text:
            continue

        page_number = _page_of(paragraph) or 1
        index = min(max(page_number, 1), page_count) - 1

        role = (getattr(paragraph, "role", "") or "").lower()
        if role in ("title", "sectionheading"):
            headings.append(text)
            # Marked so the chunker takes it as a heading instead of guessing. pageHeader,
            # pageFooter and pageNumber are dropped entirely below — they repeat on every
            # page and once merged whole documents into a single bogus section.
            buckets[index].append(HEADING_PREFIX + text)
        elif role in ("pageheader", "pagefooter", "pagenumber", "footnote"):
            continue
        else:
            buckets[index].append(text)

    # Tables are appended per page, flattened row by row. A table rendered as prose is
    # worse than no table, but a table dropped entirely loses rules that only ever appear
    # in one — retention schedules and severity matrices are always tables.
    for table in (result.tables or []):
        rendered = _render_table(table)
        if not rendered:
            continue
        page_number = _table_page(table) or 1
        index = min(max(page_number, 1), page_count) - 1
        buckets[index].append(rendered)

    pages = [clean_text("\n".join(parts)) for parts in buckets]
    return Extraction(
        pages=pages,
        engine="document-intelligence",
        headings=headings,
        empty_pages=[i + 1 for i, p in enumerate(pages) if not p.strip()],
    )


def _page_of(paragraph) -> Optional[int]:
    regions = getattr(paragraph, "bounding_regions", None) or []
    return getattr(regions[0], "page_number", None) if regions else None


def _table_page(table) -> Optional[int]:
    regions = getattr(table, "bounding_regions", None) or []
    return getattr(regions[0], "page_number", None) if regions else None


def _render_table(table) -> str:
    """
    A table as one line per row, cells separated by " | ".

    Deliberately plain text and not markdown. The chunker splits on sentences and the
    grounding check looks for a quote verbatim in what was extracted, so pipe-separated
    cells survive both; markdown pipes and separator rows would end up inside quotes.
    """
    cells = getattr(table, "cells", None) or []
    if not cells:
        return ""

    rows: dict = {}
    for cell in cells:
        content = (getattr(cell, "content", "") or "").strip()
        if not content:
            continue
        rows.setdefault(getattr(cell, "row_index", 0), []).append(
            (getattr(cell, "column_index", 0), content))

    lines = []
    for row_index in sorted(rows):
        ordered = [text for _, text in sorted(rows[row_index])]
        if ordered:
            lines.append(" | ".join(ordered))
    return "\n".join(lines)
