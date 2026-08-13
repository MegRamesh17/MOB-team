"""
Provider interface.

Two capabilities, deliberately separated:

  QuestionGenerator — turns a source passage into candidate questions. Expensive, run
                      offline at authoring time, output goes through human review.

  AnswerJudge       — decides whether a free-text answer means the same thing as the
                      answer key. Cheap, runs at grading time, bounded to a single
                      yes/no judgement.

Note what is NOT here: scoring. A judge says "this one answer is right"; it never
decides the mark. Totals, the pass bar, and mastery bands stay deterministic in code,
because a compliance result has to be reproducible and explainable to an auditor. Two
runs over identical answers must produce an identical score.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

try:  # Protocol is stdlib from 3.8, but keep the import defensive.
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

from ..models import Chunk, Difficulty, Question


class QuestionGenerator(Protocol):
    """Produces candidate questions grounded in one passage."""

    name: str

    def generate(
        self,
        chunk: Chunk,
        count: int = 2,
        difficulty: Optional[Difficulty] = None,
    ) -> List[Question]:
        ...


class AnswerJudge(Protocol):
    """Decides whether a free-text answer is semantically equivalent to the key."""

    name: str

    def judge(self, prompt: str, accepted: List[str], answer: str) -> Tuple[bool, str]:
        """Return (is_correct, short_reason)."""
        ...


def get_generator(corpus: List[Chunk]) -> QuestionGenerator:
    """
    Resolve the configured generator.

    Fails loudly when the Azure provider is requested without credentials rather than
    quietly falling back to the mock — silently shipping template-generated questions
    into a compliance product is precisely the failure nobody notices in time.
    """
    from ..config import CONFIG

    if CONFIG.provider == "mock":
        from .mock import MockGenerator

        return MockGenerator(corpus, seed=CONFIG.seed)

    if CONFIG.provider == "azure":
        CONFIG.require_azure()
        from .azure_openai import AzureOpenAIGenerator

        return AzureOpenAIGenerator()

    raise ValueError(
        "Unknown QUIZGEN_PROVIDER={!r}. Use 'mock' or 'azure'.".format(CONFIG.provider)
    )


def get_judge() -> AnswerJudge:
    from ..config import CONFIG

    if CONFIG.provider == "azure":
        CONFIG.require_azure()
        from .azure_openai import AzureOpenAIJudge

        return AzureOpenAIJudge()

    from .mock import LexicalJudge

    return LexicalJudge()
