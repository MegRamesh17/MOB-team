"""
Fetch vetted sources and turn them into chunks.

Runs at corpus-build time, never at generation time. The output goes into Azure AI
Search, and generation retrieves from the index — so a source going offline, or quietly
changing, cannot alter a quiz that is already running.

HTML is stripped with the standard library rather than a parsing dependency: these are
documentation pages, not arbitrary web design, and the pieces worth removing (nav,
script, style, footer) are identifiable by tag alone.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import List, Optional, Sequence

from .config import CONFIG
from .ingest import chunks_from_text
from .models import Chunk
from .registry import Source

# Tags whose contents are never teaching material.
_SKIP_CONTENT = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

# Tags that end a line of prose, so text does not run together.
_BREAKS = {
    "p", "div", "br", "li", "tr", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
}

# Headings become their own line, which is what the chunker uses to find sections.
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip_depth = 0
        self._heading = False
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_CONTENT:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in _HEADINGS:
            self._heading = True
            self._parts.append("\n\n")
        elif tag in _BREAKS:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_CONTENT and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in _HEADINGS:
            self._heading = False
            self._parts.append("\n")
        elif tag in _BREAKS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data.strip()
            return
        text = data.strip()
        if text:
            self._parts.append(text + " ")

    def text(self) -> str:
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_text(html: str) -> tuple:
    """Return (title, text). Headings stay on their own line for the chunker."""
    parser = _Extractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001
        # Malformed markup: keep whatever was parsed rather than losing the page.
        pass
    return parser.title, parser.text()


def fetch(url: str, timeout: float = 30.0) -> tuple:
    """Fetch one URL. Returns (title, text, fetched_at)."""
    import httpx

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    response = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "MOB-training-corpus-builder/1.0"},
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "html" in content_type:
        title, text = html_to_text(response.text)
    else:
        title, text = "", response.text

    return title, text, fetched_at


def chunks_from_source(source: Source, timeout: float = 30.0) -> List[Chunk]:
    """
    Fetch one vetted source and chunk it, tagged so every question can cite it.

    role_scope comes from the registry entry: a source approved only for SDE3 produces
    chunks only SDE3 can be tested on.
    """
    title, text, fetched_at = fetch(source.url, timeout=timeout)
    display_title = source.title or title or source.url

    chunks = chunks_from_text(text, source_name=display_title)
    for chunk in chunks:
        chunk.source_type = "web"
        chunk.source_url = source.url
        chunk.fetched_at = fetched_at
        chunk.doc_title = display_title
        chunk.container = "vetted-sources"
        # A source approved for a subset of roles must not leak into another's quiz.
        # Multiple roles are stored comma-separated; "ALL" only when the registry
        # entry genuinely applies to everyone. Defaulting multi-role sources to ALL
        # (an earlier bug) served SDE-only Git material to Directors.
        chunk.role_scope = (
            ",".join(r.upper() for r in source.roles) if source.roles else "ALL"
        )
        # Prefer the registry's topic when the page's own headings do not match the
        # taxonomy — retrieval and mastery both key on the topic name.
        if source.topics and chunk.topic not in source.topics:
            chunk.topic = source.topics[0]
    return chunks


def build_corpus(sources: Sequence[Source], limit: Optional[int] = None) -> List[Chunk]:
    """Fetch every vetted source. One failure does not stop the rest."""
    chunks: List[Chunk] = []
    failed: List[str] = []

    for i, source in enumerate(sources, 1):
        if limit and i > limit:
            break
        try:
            got = chunks_from_source(source)
            chunks.extend(got)
            print("  [{:>2}/{}] {:<44} {} chunk(s)".format(
                i, len(sources), source.title[:44], len(got)), flush=True)
        except Exception as exc:  # noqa: BLE001
            failed.append("{}: {}".format(source.title, type(exc).__name__))
            print("  [{:>2}/{}] {:<44} FAILED ({})".format(
                i, len(sources), source.title[:44], type(exc).__name__), flush=True)

    if failed:
        print("\n  {} source(s) unreachable — re-run to retry:".format(len(failed)))
        for f in failed:
            print("    - {}".format(f))
    return chunks
