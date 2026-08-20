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

import hashlib
import ipaddress
import posixpath
import re
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import List, Optional, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .config import CONFIG
from .ingest import chunks_from_text
from .models import Chunk, stable_id
from .registry import Source

# Tags whose contents are never teaching material.
_SKIP_CONTENT = {
    "aside", "button", "footer", "form", "header", "nav", "noscript", "script",
    "style", "svg",
}

# Tags that end a line of prose, so text does not run together.
_BREAKS = {
    "p", "div", "br", "li", "tr", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
}

# Headings become their own line, which is what the chunker uses to find sections.
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

_USER_AGENT = "MOB-training-corpus-builder/1.0"
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5
_MAX_SITE_CHARS = 500_000
_MAX_PAGE_BYTES = 2_000_000

# Pages that are useful to a visitor but almost never contain training material.
_SKIP_PATH = re.compile(
    r"/(?:login|log-in|logout|sign-in|signup|register|cart|checkout|search|feed|"
    r"tags?|authors?|privacy|terms|cookies?|about|services?|pricing|contact|careers?)"
    r"(?:/|$)",
    re.I,
)
_SKIP_SUFFIXES = {
    ".7z", ".avi", ".css", ".csv", ".doc", ".docx", ".gif", ".ico",
    ".jpeg", ".jpg", ".js", ".json", ".m4a", ".mov", ".mp3", ".mp4",
    ".pdf", ".png", ".ppt", ".pptx", ".rss", ".svg", ".tar", ".webp",
    ".xls", ".xlsx", ".xml", ".zip",
}
_GENERIC_PATH_ROOTS = {"docs", "documentation", "guide", "guides", "learn", "resources"}

# Short calls-to-action are common above documentation content and made one imported
# course inherit a discount banner as its title. They are not instructional material.
_PROMO_LINE = re.compile(
    r"(?:^\s*(?:buy now|enroll now|sign up|subscribe|start free|try free)\b|"
    r"\b\d{1,3}%\s+off\b|\bpromo(?:tional)? code\b)",
    re.I,
)


@dataclass(frozen=True)
class CrawledPage:
    url: str
    title: str
    text: str
    fetched_at: str


@dataclass
class CrawlResult:
    start_url: str
    title: str
    pages: List[CrawledPage] = field(default_factory=list)
    skipped: int = 0
    total_chars: int = 0
    truncated: bool = False


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip_depth = 0
        self._heading = False
        self.title = ""
        self._in_title = False
        self.links: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            values = dict(attrs)
            href = (values.get("href") or "").strip()
            rel = (values.get("rel") or "").lower().split()
            if href and "nofollow" not in rel:
                self.links.append(href)
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
        lines = []
        for line in raw.splitlines():
            clean = line.strip()
            if clean and len(clean) <= 300 and _PROMO_LINE.search(clean):
                continue
            lines.append(line)
        return "\n".join(lines).strip()


def _extract_html(html: str) -> tuple:
    """Return (title, text, links) while tolerating malformed documentation HTML."""
    parser = _Extractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001
        # Keep whatever was parsed rather than losing the entire page.
        pass
    return parser.title.strip(), parser.text(), parser.links


def html_to_text(html: str) -> tuple:
    """Return (title, text). Headings stay on their own line for the chunker."""
    title, text, _ = _extract_html(html)
    return title, text


def _validate_public_url(url: str) -> None:
    """Reject credentials, unusual ports, and addresses inside private networks."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only absolute http(s) URLs can be fetched")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid URL port") from exc
    if port is not None and port not in (80, 443):
        raise ValueError("Only standard web ports 80 and 443 are allowed")

    host = parsed.hostname.rstrip(".").lower()
    if host in ("localhost", "localhost.localdomain"):
        raise ValueError("Private network URLs are not allowed")

    try:
        addresses = socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError("The URL hostname could not be resolved") from exc
    if not addresses:
        raise ValueError("The URL hostname could not be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Private network URLs are not allowed")


def _request_url(url: str, timeout: float, client=None):
    """Fetch with every redirect target revalidated before the next request."""
    import httpx

    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=timeout,
        headers={"User-Agent": _USER_AGENT},
    )
    current = url
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            _validate_public_url(current)
            request = active_client.build_request("GET", current)
            response = active_client.send(request, stream=True)
            try:
                if response.status_code in _REDIRECT_CODES:
                    location = response.headers.get("location", "").strip()
                    if location:
                        current = urljoin(current, location)
                        continue
                response.raise_for_status()
                content_length = response.headers.get("content-length", "")
                if content_length.isdigit() and int(content_length) > _MAX_PAGE_BYTES:
                    raise ValueError("Page is larger than the 2 MB fetch limit")
                content = bytearray()
                for block in response.iter_bytes():
                    content.extend(block)
                    if len(content) > _MAX_PAGE_BYTES:
                        raise ValueError("Page is larger than the 2 MB fetch limit")
                decoded_headers = [
                    (name, value) for name, value in response.headers.multi_items()
                    if name.lower() not in ("content-encoding", "content-length", "transfer-encoding")
                ]
                return httpx.Response(
                    response.status_code,
                    headers=decoded_headers,
                    content=bytes(content),
                    request=request,
                )
            finally:
                response.close()
        raise ValueError("Too many redirects")
    finally:
        if owns_client:
            active_client.close()


def _normalise_url(url: str, base: str = "") -> str:
    """Canonical crawl URL: no fragment/query variants, repeated slashes, or dot paths."""
    parsed = urlsplit(urljoin(base, url))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    host = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port and not ((parsed.scheme == "http" and port == 80) or
                     (parsed.scheme == "https" and port == 443)):
        host = "{}:{}".format(host, port)
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    path = posixpath.normpath(path)
    if not path.startswith("/"):
        path = "/" + path
    if parsed.path.endswith("/") and path != "/":
        path += "/"
    normalised = urlunsplit((parsed.scheme.lower(), host, path, "", ""))
    return normalised if len(normalised) <= 1000 else ""


def _site_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _same_site(url: str, start_url: str) -> bool:
    return _site_host(url) == _site_host(start_url)


def _crawlable_link(url: str, start_url: str) -> bool:
    if not url or not _same_site(url, start_url):
        return False
    parsed = urlsplit(url)
    if _SKIP_PATH.search(parsed.path):
        return False
    suffix = posixpath.splitext(parsed.path.lower())[1]
    return suffix not in _SKIP_SUFFIXES


def _page_title(title: str, url: str) -> str:
    clean = re.sub(r"\s+", " ", title or "").strip()
    # Documentation sites commonly suffix every title with the site name. The first
    # half is the useful module label, while the full root title remains the course.
    for separator in (" | ", " - "):
        if separator in clean:
            first = clean.split(separator, 1)[0].strip()
            if len(first) >= 4:
                clean = first
                break
    if clean and not _PROMO_LINE.search(clean):
        return clean[:240]
    path = urlsplit(url).path.strip("/").split("/")[-1]
    return (path.replace("-", " ").replace("_", " ").title() or
            (urlsplit(url).hostname or "Trusted website"))[:240]


def _module_path(url: str) -> tuple:
    """Stable top-level site section used as a training module boundary."""
    parts = tuple(part for part in urlsplit(url).path.split("/") if part)
    if parts and parts[0].lower() in _GENERIC_PATH_ROOTS:
        parts = parts[1:]
    return parts


def _robots_policy(start_url: str, timeout: float, client) -> RobotFileParser:
    parsed = urlsplit(start_url)
    robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    policy = RobotFileParser()
    policy.set_url(robots_url)
    try:
        response = _request_url(robots_url, timeout, client=client)
        policy.parse(response.text.splitlines())
    except Exception as exc:  # noqa: BLE001
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", 0)
        if status in (401, 403, 429) or status >= 500:
            policy.parse(["User-agent: *", "Disallow: /"])
        else:
            # A missing robots file has no rules to obey.
            policy.parse([])
    return policy


def crawl_site(start_url: str, max_pages: int = 25, max_chars: int = _MAX_SITE_CHARS,
               timeout: float = 15.0, max_seconds: float = 90.0) -> CrawlResult:
    """
    Breadth-first crawl of public HTML pages on one hostname.

    The page and character caps bound Azure cost; the elapsed-time and request caps keep
    a slow or cyclic site from holding the HTTP function open indefinitely.
    """
    import httpx

    if max_pages not in (10, 25, 50):
        raise ValueError("max_pages must be 10, 25, or 50")
    if max_chars < 1 or max_chars > _MAX_SITE_CHARS:
        raise ValueError("max_chars must be between 1 and {}".format(_MAX_SITE_CHARS))

    normalised_start = _normalise_url(start_url)
    if not normalised_start:
        raise ValueError("A valid http(s) URL is required")
    _validate_public_url(normalised_start)

    result = CrawlResult(start_url=normalised_start, title="")
    pending = deque([normalised_start])
    queued = {normalised_start}
    seen = set()
    content_hashes = set()
    started = time.monotonic()
    request_cap = max_pages * 4
    requests = 0

    with httpx.Client(timeout=timeout, headers={"User-Agent": _USER_AGENT}) as client:
        robots = _robots_policy(normalised_start, timeout, client)
        delay = robots.crawl_delay(_USER_AGENT) or robots.crawl_delay("*") or 0.1
        delay = min(max(float(delay), 0.0), 2.0)

        while pending and len(result.pages) < max_pages and requests < request_cap:
            if time.monotonic() - started >= max_seconds:
                result.truncated = True
                break
            url = pending.popleft()
            if url in seen:
                continue
            seen.add(url)
            if not robots.can_fetch(_USER_AGENT, url):
                result.skipped += 1
                continue

            try:
                response = _request_url(url, timeout, client=client)
                requests += 1
            except Exception:  # noqa: BLE001
                result.skipped += 1
                continue

            final_url = _normalise_url(str(response.url))
            if not final_url or not _same_site(final_url, normalised_start):
                result.skipped += 1
                continue

            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                result.skipped += 1
                continue

            title, text, links = _extract_html(response.text)
            page_title = _page_title(title, final_url)
            if not result.title:
                result.title = page_title

            clean_text = text.strip()
            digest = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
            remaining = max_chars - result.total_chars
            if clean_text and digest not in content_hashes and remaining >= 250:
                if len(clean_text) > remaining:
                    clean_text = clean_text[:remaining]
                    result.truncated = True
                content_hashes.add(digest)
                fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                result.pages.append(CrawledPage(
                    url=final_url,
                    title=page_title,
                    text=clean_text,
                    fetched_at=fetched_at,
                ))
                result.total_chars += len(clean_text)
            elif clean_text:
                result.skipped += 1

            for href in links:
                candidate = _normalise_url(href, final_url)
                if candidate in queued or not _crawlable_link(candidate, normalised_start):
                    continue
                queued.add(candidate)
                pending.append(candidate)

            if result.total_chars >= max_chars:
                result.truncated = True
                break
            if pending and delay:
                time.sleep(delay)

    if pending:
        result.truncated = True
    if not result.title:
        result.title = _page_title("", normalised_start)
    return result


def fetch(url: str, timeout: float = 30.0) -> tuple:
    """Fetch one URL. Returns (title, text, fetched_at)."""
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    response = _request_url(url, timeout)

    content_type = response.headers.get("content-type", "").lower()
    if "html" in content_type:
        title, text = html_to_text(response.text)
    else:
        title, text = "", response.text

    return title, text, fetched_at


def chunks_from_crawl(result: CrawlResult, role_scope: str) -> List[Chunk]:
    """Chunk crawled pages as one course while retaining each exact source-page URL."""
    chunks: List[Chunk] = []
    course_doc_id = stable_id("doc", result.start_url)
    landing_titles = {}
    for page in result.pages:
        path = _module_path(page.url)
        if len(path) == 1:
            landing_titles[path[0].lower()] = page.title

    for page in result.pages:
        path = _module_path(page.url)
        if not path:
            module_topic = result.title
        else:
            key = path[0].lower()
            module_topic = landing_titles.get(
                key, key.replace("-", " ").replace("_", " ").title())
        page_text = page.text
        root_topic = "{} Material".format(page.title)
        if page.title == result.title:
            # The generic chunker treats a line identical to doc_title as a document
            # cover and skips it. On a website that line is usually the root page's H1,
            # so give its body a real section name instead of losing the whole page as
            # an "Introduction" metadata section.
            page_text = "{}\n{}".format(root_topic, page_text)
        page_chunks = chunks_from_text(
            page_text,
            source_name=page.url,
            doc_title=result.title,
        )
        for chunk in page_chunks:
            chunk.source_type = "web"
            # Every page belongs to one course. Page identity remains in chunk_id and
            # source_url; sharing doc_id prevents the pathway sync from turning ten
            # crawled pages into ten duplicate modules for the same top-level topic.
            chunk.doc_id = course_doc_id
            chunk.source_url = page.url
            chunk.fetched_at = page.fetched_at
            chunk.doc_title = result.title
            chunk.container = "trusted-site"
            chunk.role_scope = role_scope
            # Subheadings remain in the lesson text, but the adaptive pathway operates
            # at a human-sized module level. Grouping /techniques/fewshot and
            # /techniques/zeroshot under the /techniques landing page avoids turning a
            # 25-page site into a 75-question diagnostic (three questions per topic).
            chunk.topic = module_topic
        chunks.extend(page_chunks)
    return chunks


def chunks_from_source(source: Source, timeout: float = 30.0) -> List[Chunk]:
    """
    Fetch one vetted source and chunk it, tagged so every question can cite it.

    role_scope comes from the registry entry: a source approved only for SDE3 produces
    chunks only SDE3 can be tested on.
    """
    title, text, fetched_at = fetch(source.url, timeout=timeout)
    display_title = source.title or title or source.url

    chunks = chunks_from_text(text, source_name=display_title, doc_title=display_title)
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
