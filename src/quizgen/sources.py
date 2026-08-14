"""
Bridge to src/pdf_extractor.py — the team's blob-storage PDF reader.

Two deliberate differences from `extract_text_from_container()`:

1. **Per file, not per container.** That function concatenates every PDF in a container
   into one string, which is convenient for a single prompt but destroys document
   identity. Once five policies are one blob a question can no longer say which document
   and page it came from, and provenance is what makes a compliance question defensible.

2. **Container carries role scope.** The blob layout already encodes who each document
   applies to — `company-docs` is company-wide, `software-engineering-docs` is that role.
   That is a stronger signal than inferring roles from the prose, because it reflects a
   filing decision someone actually made. Chunks are tagged with it on the way in.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .config import CONFIG, DOCUMENT_DIR
from .ingest import chunks_from_text, ingest_directory
from .models import Chunk

# pdf_extractor.py has moved around as the repo was restructured (src/ -> quizgen/).
# Search the likely locations rather than hard-coding one, so a future move does not
# silently break blob ingestion again — the tests stub the module, so a broken import
# here would pass CI and only fail against real Azure.
_REPO = Path(__file__).resolve().parents[2]
for _candidate in (_REPO / "quizgen", _REPO / "src", _REPO / "backend", _REPO):
    if (_candidate / "pdf_extractor.py").exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
        break


def _require_storage() -> None:
    if not CONFIG.storage_connection_string:
        raise RuntimeError(
            "AZURE_STORAGE_CONNECTION_STRING is not set in .env, so the blob containers "
            "cannot be read.\n"
            "Get it with:  az storage account show-connection-string "
            "--name mobtrainingstorage --query connectionString -o tsv\n"
            "Or work offline: put files in data/documents/ and use --source local."
        )


def _extractor():
    try:
        from pdf_extractor import extract_text_from_blob_pdf, list_pdfs_in_container
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Could not import pdf_extractor.py ({}). Looked in quizgen/, src/, "
            "backend/ and the repo root. Run from the repo root, or install "
            "azure-storage-blob.".format(exc)
        )
    return list_pdfs_in_container, extract_text_from_blob_pdf


def _role_code_from_title(title: str) -> str:
    """
    Derive a role code from a document title.

    software-engineering-docs holds five *different* roles — SDE1, SDE2, SDE3, Manager,
    Director — not one. Scoping the whole container to "SWE" would test an SDE1 on
    Director-level material like board communication and organisational design. The
    document titles are already exactly the role names, so use them.
    """
    name = " ".join(title.strip().lower().split())

    patterns = [
        (r"\bdirector\b", "SWE_DIRECTOR"),
        (r"\bmanager\b", "SWE_MANAGER"),
        (r"\b(engineer|developer)\s*(1|i)\b", "SDE1"),
        (r"\b(engineer|developer)\s*(2|ii)\b", "SDE2"),
        (r"\b(engineer|developer)\s*(3|iii)\b", "SDE3"),
    ]
    for pattern, code in patterns:
        if re.search(pattern, name):
            return code

    # No match: fall back to a slug of the title rather than silently lumping it in
    # with an unrelated role.
    slug = re.sub(r"[^a-z0-9]+", "_", name).strip("_").upper()
    return slug[:30] or "UNKNOWN"


def chunks_from_container(container: str, role_scope: str = "ALL") -> List[Chunk]:
    """
    Read every PDF in one container, chunk each separately, tag with role scope.

    role_scope "BY_DOCUMENT" derives a distinct role per document instead of applying
    one code to the whole container.
    """
    _require_storage()
    list_pdfs, extract_text = _extractor()

    names = list_pdfs(container)
    if not names:
        print("  {}: no PDFs found".format(container))
        return []

    chunks: List[Chunk] = []
    skipped: List[str] = []
    for name in names:
        text = extract_text(container, name)
        if not text or not text.strip():
            # Almost always a scan. Skip loudly — one bad file must not kill the run.
            skipped.append(name)
            continue
        for chunk in chunks_from_text(text, source_name=name):
            chunk.container = container
            chunk.role_scope = (
                _role_code_from_title(chunk.doc_title)
                if role_scope == "BY_DOCUMENT"
                else role_scope
            )
            chunks.append(chunk)

    scopes = sorted({c.role_scope for c in chunks})
    print("  {}: {} PDF(s) -> {} chunk(s)  [scope: {}]".format(
        container, len(names) - len(skipped), len(chunks), ", ".join(scopes) or role_scope))
    for name in skipped:
        print("      skipped {} — no extractable text, probably a scan (needs OCR)".format(name))
    return chunks


def chunks_from_blob_container(container_name: Optional[str] = None) -> List[Chunk]:
    """
    Read the configured containers.

    Passing a name reads just that one; otherwise every container in
    DOCUMENT_CONTAINERS is read with its role scope.
    """
    if container_name:
        scope = dict(CONFIG.document_containers).get(container_name, "ALL")
        return chunks_from_container(container_name, scope)

    pairs: Sequence[Tuple[str, str]] = CONFIG.document_containers
    if not pairs:
        raise RuntimeError("DOCUMENT_CONTAINERS is empty in .env.")

    chunks: List[Chunk] = []
    for container, scope in pairs:
        chunks.extend(chunks_from_container(container, scope))

    if not chunks:
        raise RuntimeError(
            "No text extracted from any container ({}). Check the container names and "
            "that the PDFs are not scans.".format(", ".join(c for c, _ in pairs))
        )
    return chunks


def chunks_from_local(directory: Optional[Path] = None) -> List[Chunk]:
    """Read .pdf/.txt/.md from a local folder. The offline path — no Azure needed."""
    return ingest_directory(directory or DOCUMENT_DIR)


def load_chunks(source: str = "blob", container: Optional[str] = None) -> List[Chunk]:
    """`source` is "blob" or "local"."""
    if source == "blob":
        return chunks_from_blob_container(container)
    if source == "local":
        return chunks_from_local()
    raise ValueError("source must be 'blob' or 'local', got {!r}".format(source))
