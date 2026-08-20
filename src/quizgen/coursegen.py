"""Build a teachable, cited course before generating any assessment questions.

Raw source chunks are evidence.  They are intentionally not learner-facing lessons and
they do not determine question volume.  This module turns related evidence into a small
set of versioned modules, validates that each module actually teaches enough material,
and exposes one assessment chunk per finalized module.

The Azure path may enrich a thin source with web results, but the web-search request only
receives a generic topic.  Company text is never sent to the search tool.  Every accepted
claim is then tied back to an exact quote from a fetched source before the course can be
marked ready.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlsplit

from .config import CONFIG
from .models import Chunk, stable_id


_PAGE_TYPES = ("concept", "worked-example", "practice", "common-mistakes", "recap")
_COMPANY_LANGUAGE = re.compile(
    r"\b(?:our company|the company|our employees?|employees? (?:must|required)|"
    r"company policy|internal policy|we require|our process)\b",
    re.I,
)
_PROMO_OR_OBJECTIVE = re.compile(
    r"\b(?:enroll now|\d{1,3}% off|promo code|learn about|understand the basics of|"
    r"this module will|by the end of this lesson)\b",
    re.I,
)


def _normalise(value: str) -> str:
    return " ".join((value or "").lower().split())


def _word_count(value: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", value or ""))


def _course_requirements() -> tuple:
    return (
        CONFIG.course_min_pages,
        CONFIG.course_max_pages,
        CONFIG.course_min_learning_points,
        CONFIG.course_min_words,
    )


def _module_id(company_id: int, doc_id: str, topic: str, generation_id: str) -> str:
    # A regenerated course is staged beside the currently published course. Versioned
    # module ids let the database keep the old path available until the new lessons and
    # their assessment bank have both passed publication checks.
    raw = "{}|{}|{}|{}".format(company_id, doc_id, topic, generation_id).encode("utf-8")
    return "mod_" + hashlib.sha256(raw).hexdigest()[:24]


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source_type: str
    title: str
    text: str
    url: str = ""
    fetched_at: str = ""

    @property
    def is_company(self) -> bool:
        return self.source_type == "company"


@dataclass(frozen=True)
class Citation:
    evidence_id: str
    quote: str

    def to_dict(self) -> Dict[str, str]:
        return {"evidenceId": self.evidence_id, "quote": self.quote}


@dataclass
class LearningPoint:
    learning_point_id: str
    order: int
    statement: str
    citations: List[Citation] = field(default_factory=list)


@dataclass
class LessonPage:
    page_id: str
    order: int
    title: str
    page_type: str
    body: str
    learning_point_ids: List[str] = field(default_factory=list)
    citations: List[Citation] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return _word_count(self.body)


@dataclass
class ModuleDraft:
    module_id: str
    doc_id: str
    doc_title: str
    topic: str
    heading: str
    source_order: int
    source_topics: List[str]
    generation_id: str
    summary: str = ""
    status: str = "draft"
    pages: List[LessonPage] = field(default_factory=list)
    learning_points: List[LearningPoint] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    quality_notes: List[str] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(page.word_count for page in self.pages)


@dataclass
class CourseBuildResult:
    doc_id: str
    doc_title: str
    generation_id: str
    modules: List[ModuleDraft] = field(default_factory=list)

    @property
    def ready_modules(self) -> List[ModuleDraft]:
        return [module for module in self.modules if module.status == "ready"]


def _sentences(text: str) -> List[str]:
    candidates = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [" ".join(item.split()) for item in candidates if _word_count(item) >= 10]


def _source_groups(chunks: Sequence[Chunk]) -> List[List[Chunk]]:
    """Merge shallow adjacent topics and cap a course at a human-sized module count."""
    by_topic: Dict[str, List[Chunk]] = {}
    order: List[str] = []
    for chunk in sorted(chunks, key=lambda item: (item.page_start, item.topic, item.chunk_id)):
        if chunk.topic not in by_topic:
            by_topic[chunk.topic] = []
            order.append(chunk.topic)
        by_topic[chunk.topic].append(chunk)

    raw = [by_topic[topic] for topic in order]
    merged: List[List[Chunk]] = []
    pending: List[Chunk] = []
    floor = max(250, CONFIG.course_min_words // 2)
    for group in raw:
        words = sum(_word_count(chunk.text) for chunk in group)
        if words < floor:
            pending.extend(group)
            if sum(_word_count(chunk.text) for chunk in pending) >= floor:
                merged.append(pending)
                pending = []
            continue
        if pending:
            group = pending + group
            pending = []
        merged.append(group)
    if pending:
        if merged:
            merged[-1].extend(pending)
        else:
            merged.append(pending)

    while len(merged) > CONFIG.course_max_modules:
        index = min(
            range(len(merged) - 1),
            key=lambda i: sum(_word_count(c.text) for c in merged[i] + merged[i + 1]),
        )
        merged[index:index + 2] = [merged[index] + merged[index + 1]]
    return merged


def _evidence_from_chunks(chunks: Sequence[Chunk]) -> List[Evidence]:
    out: List[Evidence] = []
    for chunk in chunks:
        source_type = "trusted" if chunk.source_type == "web" else "company"
        out.append(Evidence(
            evidence_id=stable_id("ev", chunk.chunk_id),
            source_type=source_type,
            title=chunk.section or chunk.doc_title,
            text=chunk.text,
            url=chunk.source_url,
            fetched_at=chunk.fetched_at,
        ))
    return out


def _topic_label(chunks: Sequence[Chunk]) -> str:
    topics = list(dict.fromkeys(chunk.topic.strip() for chunk in chunks if chunk.topic.strip()))
    if len(topics) == 1:
        return topics[0][:120]
    if len(topics) == 2:
        return "{} and {}".format(topics[0], topics[1])[:120]
    return "{} and related concepts".format(topics[0])[:120]


def _official_source(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return (
        host.endswith((".gov", ".edu"))
        or host.startswith(("docs.", "developer.", "learn."))
        or host in {
            "www.iso.org", "iso.org", "www.nist.gov", "nist.gov",
            "www.w3.org", "w3.org", "www.rfc-editor.org", "rfc-editor.org",
            "docs.python.org", "kubernetes.io", "developer.mozilla.org",
        }
    )


def _external_support_is_sufficient(
    citations: Sequence[Citation], evidence: Dict[str, Evidence]
) -> bool:
    """Require claim-level corroboration for AI-discovered web evidence."""
    cited = [evidence.get(citation.evidence_id) for citation in citations]
    cited = [item for item in cited if item is not None]
    if not cited or any(item.source_type != "web" for item in cited):
        # Company documents and manager-supplied trusted links are primary inputs to
        # this course. This extra rule applies only to broad-web enrichment.
        return True
    if any(_official_source(item.url) for item in cited):
        return True
    domains = {
        (urlsplit(item.url).hostname or "").lower().removeprefix("www.")
        for item in cited if item.url
    }
    return len(domains) >= 2


def _response_urls(response: Any) -> List[str]:
    urls: List[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            for annotation in getattr(content, "annotations", []) or []:
                kind = getattr(annotation, "type", "")
                url = getattr(annotation, "url", "")
                if kind == "url_citation" and url and url not in urls:
                    urls.append(url)
    return urls


def _web_evidence(topic: str, doc_title: str) -> List[Evidence]:
    """Search only with a generic topic, then fetch and retain verifiable citations."""
    if not CONFIG.web_enrichment or CONFIG.provider != "azure":
        return []
    try:
        from openai import OpenAI

        endpoint = CONFIG.azure_openai_endpoint.rstrip("/") + "/openai/v1/"
        client = OpenAI(
            api_key=CONFIG.azure_openai_key,
            base_url=endpoint,
            timeout=180.0,
            max_retries=2,
        )
        # Privacy boundary: source text is deliberately absent from this request.
        query = (
            "Find authoritative, instructional sources for the professional topic "
            "{!r} in a course broadly titled {!r}. Prefer official documentation, "
            "standards bodies, universities, and primary sources."
        ).format(topic, doc_title)
        response = client.responses.create(
            model=CONFIG.azure_chat_deployment,
            tools=[{"type": "web_search"}],
            input=query,
            max_output_tokens=1200,
        )
        urls = _response_urls(response)[: CONFIG.course_max_web_sources * 2]
    except Exception:  # noqa: BLE001
        return []

    from .web import fetch

    fetched: List[Evidence] = []
    for url in urls:
        try:
            title, text, fetched_at = fetch(url, timeout=20.0)
        except Exception:  # noqa: BLE001
            continue
        if _word_count(text) < 150:
            continue
        fetched.append(Evidence(
            evidence_id=stable_id("ev", url),
            source_type="web",
            title=(title or url)[:300],
            text=text[:12000],
            url=url,
            fetched_at=fetched_at,
        ))
        if len(fetched) >= CONFIG.course_max_web_sources:
            break

    domains = {(urlsplit(item.url).hostname or "").lower() for item in fetched}
    if not any(_official_source(item.url) for item in fetched) and len(domains) < 2:
        return []
    return fetched


def _citation(raw: Dict[str, Any]) -> Citation:
    return Citation(
        evidence_id=str(raw.get("evidence_id") or raw.get("evidenceId") or "").strip(),
        quote=str(raw.get("quote") or "").strip(),
    )


def _needs_web_enrichment(evidence: Sequence[Evidence]) -> bool:
    source_words = sum(_word_count(item.text) for item in evidence)
    # A source that already meets the complete lesson's word floor can support a fully
    # grounded draft without paying for a separate research request. Thin sources still
    # get the normal evidence expansion; standard mode keeps the more conservative 2x
    # buffer used before the recording optimization existed.
    word_floor = CONFIG.course_min_words * (1 if CONFIG.demo_fast else 2)
    return (
        source_words < word_floor
        and not any(item.source_type == "web" for item in evidence)
    )


def _author_with_azure(module: ModuleDraft, revision_notes: Optional[List[str]] = None) -> None:
    from .llm.azure_openai import _chat_kwargs, _client

    evidence = list(module.evidence)
    if _needs_web_enrichment(evidence):
        evidence.extend(_web_evidence(module.heading, module.doc_title))
    module.evidence = evidence

    excerpts = []
    remaining = 36000
    for item in evidence:
        text = item.text[: min(len(item.text), 9000, remaining)]
        if not text:
            continue
        excerpts.append(
            "[{}] type={} title={} url={}\n{}".format(
                item.evidence_id, item.source_type, item.title, item.url or "(internal)", text)
        )
        remaining -= len(text)
        if remaining <= 0:
            break

    system = (
        "You author concise professional training lessons from supplied evidence. "
        "Teach the material directly; never write a syllabus, advertisement, or a list "
        "of things the learner should later research. Every learning point and lesson "
        "page must cite at least one exact, verbatim quote from the supplied evidence. "
        "Only company evidence may establish a company-specific rule. Web or trusted "
        "evidence may teach general practice but must never be phrased as company policy. "
        "When a claim relies only on AI-discovered web evidence, cite either one official "
        "source or matching support from at least two independent domains for that claim."
    )
    schema = {
        "summary": "one sentence",
        "learning_points": [{
            "key": "LP1", "statement": "assessable fact or skill",
            "citations": [{"evidence_id": "ev_...", "quote": "exact quote"}],
        }],
        "pages": [{
            "title": "specific lesson title",
            "page_type": "concept|worked-example|practice|common-mistakes|recap",
            "body": "complete instructional prose",
            "learning_point_keys": ["LP1"],
            "citations": [{"evidence_id": "ev_...", "quote": "exact quote"}],
        }],
    }
    revision = ""
    if revision_notes:
        revision = (
            "\nA previous draft failed these mechanical checks. Correct every item: "
            + "; ".join(revision_notes[:12]) + "\n"
        )
    min_pages, max_pages, min_points, min_words = _course_requirements()
    user = (
        "Course: {}\nModule topic: {}\n\nCreate {}-{} lesson pages, at least {} "
        "assessable learning points, and at least {} total instructional words. Include "
        "concept explanation, a concrete example, practice/application, common mistakes, "
        "and a recap when the page count permits. Do not pad or repeat. Return JSON only "
        "with this shape:\n{}\n{}\nEVIDENCE:\n{}"
    ).format(
        module.doc_title, module.heading, min_pages,
        max_pages, min_points, min_words, json.dumps(schema), revision,
        "\n\n".join(excerpts),
    )
    response = _client().chat.completions.create(
        model=CONFIG.azure_chat_deployment,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        **_chat_kwargs(CONFIG.azure_chat_deployment, 12000, 0.2),
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    module.summary = str(payload.get("summary") or "").strip()[:1000]

    key_to_id: Dict[str, str] = {}
    points: List[LearningPoint] = []
    for order, raw in enumerate(payload.get("learning_points") or [], 1):
        key = str(raw.get("key") or "LP{}".format(order)).strip()
        point_id = stable_id("lp", module.module_id, module.generation_id, key)
        key_to_id[key] = point_id
        points.append(LearningPoint(
            learning_point_id=point_id,
            order=order,
            statement=str(raw.get("statement") or "").strip(),
            citations=[_citation(item) for item in (raw.get("citations") or [])],
        ))

    pages: List[LessonPage] = []
    for order, raw in enumerate((payload.get("pages") or [])[: CONFIG.course_max_pages], 1):
        page_id = stable_id("page", module.module_id, module.generation_id, str(order))
        page_type = str(raw.get("page_type") or "concept").strip().lower()
        pages.append(LessonPage(
            page_id=page_id,
            order=order,
            title=str(raw.get("title") or "Lesson {}".format(order)).strip()[:300],
            page_type=page_type if page_type in _PAGE_TYPES else "concept",
            body=str(raw.get("body") or "").strip(),
            learning_point_ids=[
                key_to_id[key] for key in (raw.get("learning_point_keys") or [])
                if key in key_to_id
            ],
            citations=[_citation(item) for item in (raw.get("citations") or [])],
        ))
    module.learning_points = points
    module.pages = pages


def _author_from_source(module: ModuleDraft) -> None:
    """Deterministic offline author used by tests and local mock development."""
    paragraphs: List[tuple] = []
    for evidence in module.evidence:
        for paragraph in re.split(r"\n{2,}", evidence.text):
            clean = " ".join(paragraph.split())
            if _word_count(clean) >= 20:
                paragraphs.append((evidence, clean))

    all_words = sum(_word_count(text) for _, text in paragraphs)
    min_pages, max_pages, _, min_words = _course_requirements()
    if all_words < min_words:
        return
    page_count = min(max_pages, max(min_pages, math.ceil(all_words / 300)))
    buckets: List[List[tuple]] = [[] for _ in range(page_count)]
    bucket_words = [0] * page_count
    for item in paragraphs:
        target = min(range(page_count), key=lambda index: bucket_words[index])
        buckets[target].append(item)
        bucket_words[target] += _word_count(item[1])

    # Round-robin evidence rather than exhausting the first chunk. A multi-page mock
    # lesson otherwise derived every learning point from page one and then failed its
    # own publication gate because later pages had no assessable point mapped to them.
    sentence_groups = [
        [(evidence, sentence) for sentence in _sentences(evidence.text)]
        for evidence in module.evidence
    ]
    sentence_items: List[tuple] = []
    for index in range(max((len(group) for group in sentence_groups), default=0)):
        sentence_items.extend(group[index] for group in sentence_groups if index < len(group))
    seen = set()
    for evidence, sentence in sentence_items:
        key = _normalise(sentence)
        if key in seen:
            continue
        seen.add(key)
        order = len(module.learning_points) + 1
        point_id = stable_id("lp", module.module_id, module.generation_id, str(order))
        module.learning_points.append(LearningPoint(
            learning_point_id=point_id,
            order=order,
            statement=sentence[:1000],
            citations=[Citation(evidence.evidence_id, sentence)],
        ))
        if len(module.learning_points) >= max(CONFIG.course_min_learning_points, 8):
            break

    for order, items in enumerate(buckets, 1):
        body = "\n\n".join(text for _, text in items)
        citations = []
        for evidence, text in items[:3]:
            quote = next(iter(_sentences(text)), text[:240])
            citations.append(Citation(evidence.evidence_id, quote))
        point_ids = [
            point.learning_point_id for point in module.learning_points
            if any(
                _normalise(citation.quote) in _normalise(body)
                for citation in point.citations
            )
        ]
        module.pages.append(LessonPage(
            page_id=stable_id("page", module.module_id, module.generation_id, str(order)),
            order=order,
            title="{}: Part {}".format(module.heading, order),
            page_type="recap" if order == page_count else "concept",
            body=body,
            learning_point_ids=point_ids,
            citations=citations,
        ))
    module.summary = "A source-grounded lesson on {}.".format(module.heading)


def validate_module(module: ModuleDraft) -> List[str]:
    """Mechanical publication gate.  Any finding keeps the module out of learner paths."""
    findings: List[str] = []
    evidence = {item.evidence_id: item for item in module.evidence}
    min_pages, max_pages, min_points, min_words = _course_requirements()
    if not (min_pages <= len(module.pages) <= max_pages):
        findings.append("needs {}-{} lesson pages".format(
            min_pages, max_pages))
    if module.word_count < min_words:
        findings.append("needs at least {} instructional words (has {})".format(
            min_words, module.word_count))
    if len(module.learning_points) < min_points:
        findings.append("needs at least {} assessable learning points (has {})".format(
            min_points, len(module.learning_points)))

    point_ids = {point.learning_point_id for point in module.learning_points}
    covered_point_ids = {
        point_id for page in module.pages for point_id in page.learning_point_ids
    }
    uncovered = point_ids - covered_point_ids
    if uncovered:
        findings.append("every learning point must be taught on a lesson page")
    for label, citations in [
        *(("learning point {}".format(point.order), point.citations)
          for point in module.learning_points),
        *(("page {}".format(page.order), page.citations) for page in module.pages),
    ]:
        if not citations:
            findings.append("{} has no citation".format(label))
            continue
        for citation in citations:
            item = evidence.get(citation.evidence_id)
            if item is None:
                findings.append("{} cites unknown evidence".format(label))
            elif _word_count(citation.quote) < 5:
                findings.append("{} citation is too short to verify".format(label))
            elif _normalise(citation.quote) not in _normalise(item.text):
                findings.append("{} citation is not verbatim".format(label))
        if citations and not _external_support_is_sufficient(citations, evidence):
            findings.append(
                "{} needs an official source or two independent domains".format(label))

    for page in module.pages:
        if not page.body.strip():
            findings.append("page {} is blank".format(page.order))
        if not page.learning_point_ids:
            findings.append("page {} has no mapped learning point".format(page.order))
        if _PROMO_OR_OBJECTIVE.search(page.title + " " + page.body[:300]):
            findings.append("page {} is promotional or objective-only".format(page.order))
        missing = set(page.learning_point_ids) - point_ids
        if missing:
            findings.append("page {} references unknown learning points".format(page.order))
        if _COMPANY_LANGUAGE.search(page.body):
            cited = [evidence.get(citation.evidence_id) for citation in page.citations]
            if not any(item and item.is_company for item in cited):
                findings.append("page {} presents an unsupported company rule".format(page.order))
    return list(dict.fromkeys(findings))


def build_instructional_course(chunks: Sequence[Chunk], company_id: int) -> CourseBuildResult:
    if not chunks:
        raise ValueError("No source chunks were supplied")
    doc_id = chunks[0].doc_id
    doc_title = chunks[0].doc_title
    generation_id = stable_id(
        "gen", str(company_id), doc_id,
        datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    )
    result = CourseBuildResult(doc_id=doc_id, doc_title=doc_title, generation_id=generation_id)

    modules: List[ModuleDraft] = []
    for source_order, group in enumerate(_source_groups(chunks), 1):
        topic = _topic_label(group)
        modules.append(ModuleDraft(
            module_id=_module_id(company_id, doc_id, topic, generation_id),
            doc_id=doc_id,
            doc_title=doc_title,
            topic=topic,
            heading=topic,
            source_order=source_order,
            source_topics=list(dict.fromkeys(chunk.topic for chunk in group)),
            generation_id=generation_id,
            evidence=_evidence_from_chunks(group),
        ))

    def author(module: ModuleDraft) -> ModuleDraft:
        if CONFIG.provider == "azure":
            _author_with_azure(module)
        else:
            _author_from_source(module)
        module.quality_notes = validate_module(module)
        if CONFIG.provider == "azure" and module.quality_notes:
            _author_with_azure(module, module.quality_notes)
            module.quality_notes = validate_module(module)
        module.status = "ready" if not module.quality_notes else "insufficient"
        return module

    # Each module is independent until persistence. Two concurrent Azure authoring
    # calls cut wall-clock time without reducing topic coverage, lesson length, citation
    # rules, or the revision pass. Keep mock generation sequential and deterministic.
    if CONFIG.provider == "azure" and CONFIG.demo_fast and len(modules) > 1:
        workers = max(1, min(CONFIG.demo_fast_author_workers, len(modules)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            result.modules.extend(executor.map(author, modules))
    else:
        result.modules.extend(author(module) for module in modules)
    return result


def assessment_chunks(course: CourseBuildResult, role_assignments: Dict[str, List[str]]) -> List[Chunk]:
    """One finalized, tagged lesson chunk per module for bounded question generation."""
    chunks: List[Chunk] = []
    for module in course.ready_modules:
        roles: List[str] = []
        for topic in module.source_topics:
            roles.extend(role_assignments.get(topic) or ["ALL"])
        roles = list(dict.fromkeys(role.upper() for role in roles if role)) or ["ALL"]
        if "ALL" in roles:
            roles = ["ALL"]

        page_by_point: Dict[str, str] = {}
        page_parts = []
        for page in module.pages:
            for point_id in page.learning_point_ids:
                page_by_point.setdefault(point_id, page.page_id)
            page_parts.append(
                "[LESSON_PAGE {}] {}\n{}".format(page.page_id, page.title, page.body))
        point_parts = [
            "[LEARNING_POINT {}] {}".format(point.learning_point_id, point.statement)
            for point in module.learning_points
        ]
        first_external = next(
            (item for item in module.evidence if item.url), None)
        chunk = Chunk(
            chunk_id=stable_id("lesson", module.module_id, module.generation_id),
            doc_id=course.doc_id,
            doc_title=course.doc_title,
            topic=module.topic,
            section=module.heading,
            page_start=module.source_order,
            page_end=module.source_order,
            text="{}\n\n{}".format("\n".join(point_parts), "\n\n".join(page_parts)),
            container="generated-lessons",
            role_scope=roles[0],
            company_id="",
            source_type="web" if first_external else "document",
            source_url=first_external.url if first_external else "",
            fetched_at=first_external.fetched_at if first_external else "",
        )
        # Runtime-only metadata consumed by the question provider. SourceChunks keeps
        # the normal stable fields; GeneratedQuestions stores these normalized ids.
        chunk.module_id = module.module_id
        chunk.learning_point_ids = [point.learning_point_id for point in module.learning_points]
        chunk.lesson_page_by_learning_point = page_by_point
        chunks.append(chunk)
    return chunks
