"""
The generation pipeline — chunks in, validated questions in the bank.

Extracted from the CLI so the command line and the upload endpoint run the SAME code.
Two copies of this loop would drift, and the half that drifted would be the one that
skips a validation step: the cross-document contradiction check and the
exactly-one-correct-answer rule are the only things standing between a generated
question and a learner now that human review has been dropped.

Progress is reported through a callback rather than printed, because the caller
decides what to do with it — the CLI prints a line per chunk, the HTTP layer updates
a job record that a browser polls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from .bank import Bank
from .config import CONFIG
from .llm.base import get_generator
from .models import Chunk, Difficulty, Question, QuestionType
from .retrieval import BM25
from .validators import validate


@dataclass
class GenerationResult:
    kept: List[Question] = field(default_factory=list)
    written: int = 0
    rejected: List[str] = field(default_factory=list)
    notes: Dict[str, List[str]] = field(default_factory=dict)
    failed: List[str] = field(default_factory=list)
    skipped: int = 0


@dataclass
class Progress:
    index: int          # 1-based chunk number in this run
    total: int
    chunk: Chunk
    kept_in_batch: int
    rejected_total: int
    error: Optional[str] = None


def select_chunks(
    bank: Bank,
    topic: str = "",
    scope: str = "",
    doc_title: str = "",
    regenerate: bool = False,
    limit: Optional[int] = None,
) -> tuple:
    """Choose what to generate from. Returns (chunks, skipped_count)."""
    chunks = bank.all_chunks()
    if topic:
        chunks = [c for c in chunks if c.topic.lower() == topic.lower()]
    if scope:
        chunks = [c for c in chunks if c.role_scope.upper() == scope.upper()]
    if doc_title:
        chunks = [c for c in chunks if c.doc_title == doc_title]

    skipped = 0
    if not regenerate:
        # Resume cheaply: a chunk that already produced questions is skipped, so a run
        # killed part-way does not re-pay for what it already generated.
        done = bank.chunk_ids_with_questions()
        before = len(chunks)
        chunks = [c for c in chunks if c.chunk_id not in done]
        skipped = before - len(chunks)

    if limit:
        # Real generation is slow and billed. A limit is how you sanity-check the
        # provider on a handful of chunks before committing to the whole corpus.
        chunks = chunks[:limit]
    return chunks, skipped


def representative_chunks_by_topic(chunks: Sequence[Chunk],
                                   max_per_topic: int = 5) -> List[Chunk]:
    """Evenly sample a large website while retaining coverage of every module."""
    if max_per_topic < 1:
        raise ValueError("max_per_topic must be positive")

    buckets: Dict[str, List[Chunk]] = {}
    for chunk in chunks:
        buckets.setdefault(chunk.topic, []).append(chunk)

    selected: List[Chunk] = []
    for topic in sorted(buckets):
        available = sorted(
            buckets[topic],
            key=lambda chunk: (chunk.source_url, chunk.page_start, chunk.chunk_id),
        )
        if len(available) <= max_per_topic:
            selected.extend(available)
            continue
        indexes = {
            round(i * (len(available) - 1) / (max_per_topic - 1))
            for i in range(max_per_topic)
        } if max_per_topic > 1 else {0}
        selected.extend(available[index] for index in sorted(indexes))
    return selected


def _generate_candidate_batch(generator, chunk: Chunk, per_chunk: int,
                              difficulty_ladder: bool) -> List[Question]:
    if not difficulty_ladder:
        return generator.generate(chunk, count=per_chunk)
    if not CONFIG.demo_fast:
        produced: List[Question] = []
        for difficulty in (Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD):
            produced.extend(generator.generate(
                chunk, count=per_chunk, difficulty=difficulty
            ))
        return produced

    # One balanced response is dramatically faster on gpt-5 than three sequential
    # difficulty-specific responses. Only repair a missing diagnostic band when the
    # provider ignored the explicit balanced-batch instruction.
    produced = generator.generate(chunk, count=per_chunk * 3)
    covered = {
        question.difficulty for question in produced
        if question.question_type == QuestionType.MULTIPLE_CHOICE
    }
    for difficulty in (Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD):
        if difficulty not in covered:
            produced.extend(generator.generate(chunk, count=1, difficulty=difficulty))
    return produced


def generate_questions(
    bank: Bank,
    chunks: Sequence[Chunk],
    per_chunk: int = 2,
    difficulty_ladder: bool = False,
    on_progress: Optional[Callable[[Progress], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> GenerationResult:
    """
    Generate, validate and store questions for `chunks`.

    Saves after every chunk rather than at the end. A 45-minute run that only writes on
    completion loses everything to one crash, and those tokens are already paid for.
    Saving as it goes also makes re-running cheap, because save_questions skips what
    already exists.
    """
    result = GenerationResult()
    if not chunks:
        return result

    corpus = bank.all_chunks()
    generator = get_generator(corpus)
    index = BM25(corpus)
    by_id = {c.chunk_id: c for c in corpus}
    total = len(chunks)

    for i, chunk in enumerate(chunks, 1):
        if should_stop and should_stop():
            break

        # One flaky call must not abort the whole run. Failures are collected and
        # reported; re-running picks them up because nothing was saved for them.
        try:
            produced = _generate_candidate_batch(
                generator, chunk, per_chunk, difficulty_ladder
            )
        except Exception as exc:  # noqa: BLE001
            label = "{}: {}".format(chunk.topic[:34], type(exc).__name__)
            result.failed.append(label)
            if on_progress:
                on_progress(Progress(i, total, chunk, 0, len(result.rejected), type(exc).__name__))
            continue

        batch: List[Question] = []
        batch_notes: Dict[str, List[str]] = {}

        for q in produced:
            source_text = by_id.get(q.source_chunk_id, chunk).text
            # Cross-document check: what do OTHER documents say about this?
            related = index.related_to_question(q.prompt, q.source_chunk_id, limit=4)
            ok, findings = validate(q, source_text, related)
            if not ok:
                result.rejected.append("{}: {}".format(q.topic, findings[0]))
                continue
            if findings:
                batch_notes[q.question_id] = findings
                result.notes[q.question_id] = findings
                q.checked_against_chunk_ids = [c.chunk_id for c in related]
            batch.append(q)
            result.kept.append(q)

        result.written += bank.save_questions(batch, notes=batch_notes)
        if on_progress:
            on_progress(Progress(i, total, chunk, len(batch), len(result.rejected)))

    return result


def generator_name() -> str:
    """Which provider will run, without generating anything."""
    try:
        return get_generator([]).name
    except Exception:  # noqa: BLE001
        return CONFIG.provider
