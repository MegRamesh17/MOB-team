"""
PDF -> text -> topic-tagged chunks.

This is the step people skip when they say "the PDF goes into the LLM". A PDF is not
text, and a 200-page handbook does not fit in a context window. Extraction and chunking
decide the ceiling on question quality: a question can only be as good as the passage
it was grounded in.

Chunking here is structure-aware rather than fixed-width. Policy documents are written
in headed sections, and a chunk that straddles two headings produces questions that
blend unrelated rules — which is how you get a fire-safety question with a GDPR answer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from pypdf import PdfReader

from .config import CONFIG, DOCUMENT_DIR
from .extract import HEADING_PREFIX as _HEADING_MARKER
from .models import Chunk, stable_id

# Ligatures and layout artefacts pypdf leaves behind.
_REPLACEMENTS = {
    "ﬁ": "fi",
    "ﬂ": "fl",
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    " ": " ",
}


def clean_text(text: str) -> str:
    for bad, good in _REPLACEMENTS.items():
        text = text.replace(bad, good)
    # Words split across a line break by hyphenation.
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # Line breaks are load-bearing: headings sit on their own line, and that is the only
    # signal available for section structure. Collapsing them here (an earlier mistake)
    # makes every chunk land in a single nameless section. Lines are joined later, once
    # headings have been identified.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def looks_like_heading(line: str) -> bool:
    """
    Headings in policy PDFs are short, title-cased or upper-cased, and unpunctuated.
    Deliberately conservative — a false positive fragments a section, which costs more
    than a false negative.

    Document Intelligence labels headings itself, and extract.py marks those with a
    sentinel. When the marker is there this stops guessing: an extractor that examined
    the page geometry beats heuristics over capitalisation, and the heuristics stay for
    the pypdf path where nothing else knows.
    """
    if line.startswith(_HEADING_MARKER):
        return True

    s = line.strip()
    if not (3 <= len(s) <= 70):
        return False
    if s.endswith((".", ",", ";", ":")):
        return False
    words = s.split()
    if not (1 <= len(words) <= 9):
        return False
    if s.isupper():
        return len(s) > 3

    # A single title-case word is almost never a heading in these documents — it is a
    # wrapped line or a table cell. The real corpus produced junk topics like "This",
    # "Team", "Status" and "Workplace" this way, each becoming its own bogus topic.
    if len(words) == 1:
        return False
    # Title Case: most words capitalised, ignoring short connectives.
    significant = [w for w in words if len(w) > 3]
    if not significant:
        return False
    capitalised = sum(1 for w in significant if w[0].isupper())
    return capitalised >= max(1, int(len(significant) * 0.75))


# Stand-in for the "." inside a known abbreviation, so the splitter does not treat
# it as a sentence end. Must be a character that cannot occur in extracted text.
_ABBR_SENTINEL = "\u0001"

# Files that had no detectable headings; surfaced as a warning after ingest.
_NO_HEADINGS: List[str] = []


def split_sentences(text: str) -> List[str]:
    """
    Regex sentence splitting with guards for the abbreviations that actually appear in
    corporate policy text. Good enough here; a full NLP dependency is not worth it.
    """
    protected = text
    for abbr in ("e.g.", "i.e.", "etc.", "Dr.", "Mr.", "Mrs.", "Ms.", "No.", "vs.", "approx."):
        protected = protected.replace(abbr, abbr.replace(".", _ABBR_SENTINEL))
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", protected)
    return [p.replace(_ABBR_SENTINEL, ".").strip() for p in parts if p.strip()]


def _pages(pdf_path: Path) -> List[str]:
    """
    Pages of text, via Document Intelligence when configured and pypdf otherwise.

    The engine is reported per document rather than left implicit: "why did this file
    extract badly" is the first question anyone asks, and the answer is usually which
    engine ran. A scan that produced nothing is called out for the same reason — it used
    to be indistinguishable from a document that simply had no teachable content.
    """
    from .extract import extract

    result = extract(pdf_path)

    if result.is_empty and result.engine == "pypdf":
        print("      {} produced no text with pypdf — probably a scan. "
              "Set DOCUMENT_INTELLIGENCE_ENDPOINT and _KEY to OCR it."
              .format(pdf_path.name))
    elif result.empty_pages and result.engine == "pypdf":
        print("      {}: {} page(s) empty under pypdf ({}) — likely scanned."
              .format(pdf_path.name, len(result.empty_pages),
                      ", ".join(str(p) for p in result.empty_pages[:6])))

    return result.pages


# Letterhead lines: the same on every document a company produces, so using one as
# a title merges unrelated documents into a single training.
_LETTERHEAD = re.compile(
    r"internal use|confidential|proprietary|all rights reserved|^page\b|"
    r"^document\b|^\s*\d{1,2}/\d{1,2}/\d{2,4}\s*$",
    re.IGNORECASE,
)


def _looks_like_letterhead(line: str) -> bool:
    """A banner, not a title: company header, classification stamp, or a date line."""
    s = line.strip()
    if _LETTERHEAD.search(s):
        return True
    # "LatticePeak Systems | Internal Use Only", "Role Brief | August 2026" — a
    # pipe-separated banner is a letterhead, never a document title.
    return "|" in s


def _title_from(pdf_path: Path, pages: List[str]) -> str:
    """
    The document's title, from the first substantive line of page 1.

    Letterhead lines are skipped. A real corpus of role briefs all began
    "LatticePeak Systems | Internal Use Only", so every one of them derived the SAME
    title and merged into one training — sixteen roles in a single module, with
    sections from different roles sitting side by side. The filename is the fallback
    because it is the one thing guaranteed to differ between two uploads.
    """
    if pages:
        for line in pages[0].split("\n"):
            s = line.strip()
            if len(s) > 4 and not _looks_like_letterhead(s):
                return s
    return pdf_path.stem.replace("-", " ").replace("_", " ").title()


def _sections(pages: List[str], doc_title: str) -> List[Tuple[str, int, str]]:
    """Return (section_name, page_number, body_text)."""
    sections: List[Tuple[str, int, List[str]]] = []
    current_name = "Introduction"
    current_page = 1
    buffer: List[str] = []

    for page_no, page_text in enumerate(pages, start=1):
        for raw_line in page_text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line == doc_title:
                continue
            if looks_like_heading(line):
                if buffer:
                    sections.append((current_name, current_page, buffer))
                # Strip the extractor's marker — it exists to tell the chunker this is a
                # heading, not to appear in a topic name, a citation or a question.
                current_name = line.lstrip(_HEADING_MARKER).strip()
                current_page = page_no
                buffer = []
            else:
                buffer.append(line)
    if buffer:
        sections.append((current_name, current_page, buffer))

    return [(name, page, " ".join(lines)) for name, page, lines in sections]


def _pack(sentences: Iterable[str], target: int, overlap: int, minimum: int) -> List[str]:
    """
    Pack sentences into chunks near the target size, never splitting a sentence, with a
    sentence-level overlap so a rule stated across a boundary is not lost.
    """
    chunks: List[str] = []
    current: List[str] = []
    size = 0

    for sentence in sentences:
        if size + len(sentence) > target and current:
            chunks.append(" ".join(current))
            carry: List[str] = []
            carried = 0
            for prev in reversed(current):
                if carried >= overlap:
                    break
                carry.insert(0, prev)
                carried += len(prev)
            current = carry[:]
            size = sum(len(s) for s in current)
        current.append(sentence)
        size += len(sentence)

    if current:
        tail = " ".join(current)
        # Fold a runt into the previous chunk rather than emit something too thin to
        # ground a question.
        if len(tail) < minimum and chunks:
            chunks[-1] = chunks[-1] + " " + tail
        else:
            chunks.append(tail)
    return chunks


# Sections that describe the course rather than teach anything. Generating from these
# produces questions like "what does this training promise to teach you?" — technically
# grounded, completely useless as an assessment. Seen in the real corpus, where every
# document opens with an overview and per-module objective lists.
_METADATA_SECTIONS = re.compile(
    r"^(course overview|overview|core objectives?|objectives?|learning outcomes?|"
    r"duration|status|prerequisites?|who should attend|target audience|"
    r"table of contents|contents|introduction|about this (course|program|training))\b",
    re.I,
)


# Metadata that appears INSIDE a content-named section. Filtering on the section name
# alone was not enough: the real corpus puts the course header ("Duration: 2-3 hours |
# Level: All Employees | Frequency: Annual refresher required") and per-module
# "Core Objectives:" lists inside sections called things like "Module 1: Code of
# Conduct". gpt-5 dutifully generated "What is the stated duration of the program?" —
# perfectly grounded, and worthless as an assessment.
_METADATA_MARKERS = re.compile(
    r"(core objectives?\s*:|learning outcomes?\s*:|duration\s*:|frequency\s*:|"
    r"level\s*:\s*all employees|prerequisites?\s*:|estimated time\s*:|"
    r"this (module|program|course|training) (covers|will|introduces|establishes))",
    re.I,
)


def is_teachable_section(section: str, text: str) -> bool:
    """
    Whether a section carries testable content.

    Three rejections: sections named as course metadata, sections whose *body* is
    dominated by metadata markers, and sections too thin to support a question.
    """
    if _METADATA_SECTIONS.match(section.strip()):
        return False

    body = text.strip()
    if len(body) < 120:
        return False

    # A section is metadata if the markers appear early and it is short — that is the
    # shape of a header block or an objectives list, not of teaching content.
    match = _METADATA_MARKERS.search(body)
    if match and (match.start() < 200 or len(body) < 500):
        return False

    return True


def derive_topic(section: str, doc_title: str) -> str:
    """
    Topic tag used for retrieval, mastery tracking and remedial targeting.

    One shared vocabulary across chunks, questions and mastery is essential — if these
    drift apart, remediation silently retrieves the wrong material. Section headings are
    the taxonomy; keep them stable in the source documents.
    """
    name = section.strip() or doc_title
    # Strip leading numbering: "1.2 Foo", "3) Foo", and bare "1 Foo". The real documents
    # carry module numbers that would otherwise split one topic across several labels
    # ("1 Agile Fundamentals" vs "Agile Fundamentals").
    name = re.sub(r"^\d+(?:[\.\)]\d*)*[\.\)]?\s+", "", name)
    return " ".join(w.capitalize() if w.islower() else w for w in name.split())[:60]


def _paragraph_sections(pages: List[str]) -> List[Tuple[str, int, str]]:
    """
    Fallback when a document has no detectable headings — which is common once a PDF
    has been flattened to plain text by an external converter.

    Topics come from the most distinctive words in each paragraph block instead. This
    is measurably worse than real headings (targeting gets coarser), so ingest warns
    when it has to fall back.
    """
    sections: List[Tuple[str, int, str]] = []
    for page_no, page_text in enumerate(pages, start=1):
        blocks = [b.strip() for b in re.split(r"\n\s*\n", page_text) if len(b.strip()) > 120]
        for block in blocks:
            flat = " ".join(block.split())
            words = [w for w in re.findall(r"\b[a-z]{5,}\b", flat.lower())]
            counts: dict = {}
            for w in words:
                counts[w] = counts.get(w, 0) + 1
            top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            name = " ".join(w.capitalize() for w, _ in top) or "General"
            sections.append((name, page_no, flat))
    return sections


def _read_plain_text(path: Path) -> List[str]:
    """
    Read a .txt/.md file. Form feeds are treated as page breaks — most PDF-to-text
    converters emit them — so page provenance survives the conversion where possible.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    pages = raw.split("\f") if "\f" in raw else [raw]
    return [clean_text(p) for p in pages]


def ingest_pdf(path: Path) -> List[Chunk]:
    """Backwards-compatible alias."""
    return ingest_document(path)


def ingest_document(path: Path) -> List[Chunk]:
    """Ingest one .pdf, .txt or .md file."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = _pages(path)
        empty_msg = (
            "No extractable text in {}. It is probably a scan — that needs OCR, which "
            "this pipeline does not do yet.".format(path.name)
        )
    elif suffix in (".txt", ".md", ".text"):
        pages = _read_plain_text(path)
        empty_msg = "{} is empty.".format(path.name)
    else:
        raise ValueError("Unsupported file type: {}".format(path.name))

    if not any(p.strip() for p in pages):
        raise ValueError(empty_msg)

    return _chunk_pages(pages, source_name=path.name, doc_title=_title_from(path, pages))


def chunks_from_text(text: str, source_name: str,
                     doc_title: Optional[str] = None) -> List[Chunk]:
    """
    Chunk raw text that came from somewhere other than a local file — in practice, the
    output of src/pdf_extractor.py reading a PDF out of blob storage.

    `source_name` becomes the stable source identity. `doc_title` can group several
    independently sourced pages into one course while each page keeps its own identity.
    """
    pages = [clean_text(p) for p in (text.split("\f") if "\f" in text else [text])]
    if not any(p.strip() for p in pages):
        return []
    return _chunk_pages(
        pages,
        source_name=source_name,
        doc_title=doc_title or _title_from(Path(source_name), pages),
    )


def _chunk_pages(pages: List[str], source_name: str, doc_title: str) -> List[Chunk]:
    """Shared tail of both ingest paths: sections -> packed chunks."""
    doc_id = stable_id("doc", source_name)
    chunks: List[Chunk] = []

    sections = _sections(pages, doc_title)
    # One nameless section means heading detection found nothing — typical of text that
    # has been flattened by a PDF converter. Fall back rather than lump every chunk
    # under a single topic, which silently destroys all topic targeting.
    if len(sections) <= 1:
        fallback = _paragraph_sections(pages)
        if len(fallback) > len(sections):
            sections = fallback
            _NO_HEADINGS.append(source_name)

    for section_name, page_no, body in sections:
        if not is_teachable_section(section_name, body):
            continue
        sentences = split_sentences(body)
        if not sentences:
            continue
        for text in _pack(
            sentences,
            CONFIG.chunk_target_chars,
            CONFIG.chunk_overlap_chars,
            CONFIG.chunk_min_chars,
        ):
            if len(text) < 80:
                continue
            chunks.append(
                Chunk(
                    chunk_id=stable_id("chunk", source_name, section_name, text[:120]),
                    doc_id=doc_id,
                    doc_title=doc_title,
                    topic=derive_topic(section_name, doc_title),
                    section=section_name,
                    page_start=page_no,
                    page_end=page_no,
                    text=text,
                    # Tagged at creation, not stamped on later. A chunk that exists
                    # untagged is a chunk that can be handed to something which forgets
                    # to tag it — the window is small, and closing it costs one line.
                    company_id=CONFIG.company_id,
                )
            )
    return chunks


def ingest_directory(source_dir: Optional[Path] = None,
                     role_scope: str = "ALL") -> List[Chunk]:
    """
    Ingest every .pdf, .txt and .md in a directory.

    `role_scope` tags the batch. Blob ingestion gets this for free — the container a
    document was filed in IS the role decision (sources.py). A local folder carries no
    such signal, so it has to be passed, and it defaults to ALL: company-wide material
    that every role takes.

    That default is why local sample data shows the same trainings to everyone. Ingest
    a folder per role to see role scoping actually do something.
    """
    directory = Path(source_dir) if source_dir else DOCUMENT_DIR
    del _NO_HEADINGS[:]

    scope = (role_scope or "ALL").strip().upper() or "ALL"

    files = sorted(
        p for p in directory.iterdir()
        if p.suffix.lower() in (".pdf", ".txt", ".md", ".text")
    )
    if not files:
        raise FileNotFoundError(
            "No .pdf/.txt/.md files in {}. Run scripts/make_sample_pdfs.py, or "
            "drop the converted documents there.".format(directory)
        )
    out: List[Chunk] = []
    for path in files:
        for chunk in ingest_document(path):
            # Tagged here rather than inside ingest_document, which has no idea which
            # folder it was handed. Mirrors sources.py, where the blob container carries
            # the same decision.
            chunk.role_scope = scope
            out.append(chunk)
    return out


def files_without_headings() -> List[str]:
    """Names of documents that fell back to paragraph segmentation."""
    return list(_NO_HEADINGS)
