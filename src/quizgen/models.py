"""
Core data types.

These deliberately mirror the shape of the Azure SQL schema (Quizzes, QuizQuestions,
QuizOptions, QuizAnswerKeys, QuizAttempts, QuizResponses) so that when this moves off
SQLite the migration is a transport change, not a redesign.

Python 3.9 target — no PEP 604 unions, no match statements.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class QuestionType(str, Enum):
    MULTIPLE_CHOICE = "MultipleChoice"
    MULTI_SELECT = "MultiSelect"
    TRUE_FALSE = "TrueFalse"
    FILL_IN_BLANK = "FillInBlank"
    SHORT_ANSWER = "ShortAnswer"
    PROMPT_RESPONSE = "PromptResponse"
    PYTHON_CODE = "PythonCode"


class Difficulty(str, Enum):
    EASY = "Easy"
    MEDIUM = "Medium"
    HARD = "Hard"


class ProvenanceClass(str, Enum):
    """
    Where a question's authority comes from. This is the central safety distinction.

    DOCUMENTED  — the answer is traceable to a verbatim sentence in a source document.
                  May state company policy. Review is a quick check against the quote.

    ROLE_KNOWLEDGE — general professional practice a role is expected to know, which the
                  documents do not state. May NOT assert any company-specific rule,
                  number, deadline or procedure; that is what `validators` enforces
                  mechanically. Always requires expert review, and must be surfaced to
                  the learner as professional practice rather than company policy.

    The failure this prevents: a model inferring a plausible-sounding internal rule that
    does not exist, and the company then certifying people against it.
    """

    DOCUMENTED = "Documented"
    ROLE_KNOWLEDGE = "RoleKnowledge"

    # Sourced from outside the company: vendor docs, standards bodies, framework
    # documentation. Has a real citation (a URL, retrieved on a date) but is NOT
    # company policy, and must never be phrased as if it were.
    #
    # This becomes the primary class once course material is assembled mainly from
    # online sources rather than internal PDFs. The company documents then define
    # which roles exist and what they must cover; the external sources supply the
    # substance.
    EXTERNAL = "ExternalSource"


class ReviewStatus(str, Enum):
    """
    Generated questions are NOT live until a human approves them.

    This is the single most important field in this module. A model that invents a
    plausible-but-wrong answer key for a fire-safety question certifies people on false
    information, and "the model wrote it" is not a defence to an auditor. Nothing with
    status PENDING is ever served to a learner.
    """

    PENDING = "PendingReview"
    APPROVED = "Approved"
    REJECTED = "Rejected"


@dataclass
class Chunk:
    """A passage of source material, small enough to ground a single question."""

    chunk_id: str
    doc_id: str
    doc_title: str
    topic: str
    section: str
    page_start: int
    page_end: int
    text: str

    # Which container this came from, and who it applies to.
    #
    # The blob layout carries this for free: company-docs applies to everyone,
    # software-engineering-docs applies to that role. That is a far stronger signal
    # than inferring roles by pattern-matching the prose, because it is a deliberate
    # decision someone made when filing the document rather than a guess.
    #
    # role_scope is a role code, or "ALL" for company-wide material.
    container: str = ""
    role_scope: str = "ALL"

    # Which company owns this passage. Note the asymmetry with role_scope, which is
    # deliberate and is the whole security property (see isolation.py and
    # docs/company-isolation-gap.md):
    #
    #   role_scope  defaults to "ALL" — no stated audience means everyone WITHIN one
    #               company should see it. A permissive default is correct.
    #   company_id  defaults to EMPTY, which is invalid. "Visible to every company" is
    #               never a sensible default, so an untagged chunk must fail rather
    #               than fall back to something permissive.
    #
    # The empty default exists only because a dataclass cannot put a required field
    # after defaulted ones. It is not a usable value: isolation.validate_company_id
    # rejects it, and search_index.upload refuses to index it.
    company_id: str = ""

    # Where this passage came from. "document" today; "web" once online sourcing is
    # wired up. Kept on the chunk rather than inferred later, because a question's
    # citation and its staleness both depend on it.
    source_type: str = "document"
    source_url: str = ""

    # When the source was retrieved. Meaningless for a static PDF, essential for the
    # web: an external page can change under you, and a certification that has to be
    # renewed annually needs to know whether the material behind it has moved on.
    fetched_at: str = ""

    def to_row(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Option:
    option_id: str
    text: str
    is_correct: bool


@dataclass
class Question:
    question_id: str
    topic: str
    question_type: QuestionType
    difficulty: Difficulty
    prompt: str

    # Choice-based questions use options; FillInBlank uses accepted_answers.
    options: List[Option] = field(default_factory=list)
    accepted_answers: List[str] = field(default_factory=list)

    # AI-graded responses use an immutable rubric and a pre-authored equivalent
    # multiple-choice fallback. Both stay server-side; the browser receives the
    # fallback only when two grading passes genuinely cannot resolve the answer.
    rubric_json: str = ""
    fallback_json: str = ""
    grading_version: str = ""

    explanation: str = ""
    points: int = 1

    # Provenance. Without this you cannot audit why a question exists, regenerate it
    # when the source document changes, or show the learner the passage they missed.
    source_chunk_id: str = ""
    source_doc_title: str = ""
    source_page: int = 0
    source_quote: str = ""
    source_url: str = ""       # populated for EXTERNAL questions
    source_fetched_at: str = ""

    generator: str = "mock"  # which provider produced it
    review_status: ReviewStatus = ReviewStatus.PENDING

    provenance_class: ProvenanceClass = ProvenanceClass.DOCUMENTED

    # For ROLE_KNOWLEDGE questions: which role requirement prompted it, and which
    # documents were consulted for the contradiction check. Empty for DOCUMENTED.
    role_code: str = ""
    role_requirement: str = ""
    checked_against_chunk_ids: List[str] = field(default_factory=list)

    # Populated for questions generated from a finalized instructional course. Legacy
    # questions remain valid with these fields empty.
    module_id: str = ""
    lesson_page_id: str = ""
    learning_point_id: str = ""

    # Empirical difficulty, filled in from real responses. A model's guess at whether a
    # question is hard is unreliable; the observed pass rate is not.
    times_served: int = 0
    times_correct: int = 0

    @property
    def p_value(self) -> Optional[float]:
        """Observed proportion answered correctly. None until there is evidence."""
        if self.times_served < 3:
            return None
        return self.times_correct / self.times_served

    @property
    def effective_difficulty(self) -> Difficulty:
        """
        Prefer measured difficulty over the label the generator assigned.
        Bands follow classical test theory convention: p > 0.8 easy, p < 0.5 hard.
        """
        p = self.p_value
        if p is None:
            return self.difficulty
        if p >= 0.80:
            return Difficulty.EASY
        if p >= 0.50:
            return Difficulty.MEDIUM
        return Difficulty.HARD

    def correct_option_ids(self) -> List[str]:
        return [o.option_id for o in self.options if o.is_correct]


@dataclass
class Response:
    """One learner's answer to one question."""

    response_id: str
    attempt_id: str
    learner_id: str
    question_id: str
    topic: str
    selected_option_ids: List[str] = field(default_factory=list)
    text_answer: str = ""
    is_correct: bool = False
    points_awarded: int = 0
    answered_at: str = ""


@dataclass
class Attempt:
    attempt_id: str
    learner_id: str
    started_at: str
    submitted_at: str = ""
    score_percent: float = 0.0
    points_awarded: int = 0
    points_possible: int = 0
    passed: bool = False
    responses: List[Response] = field(default_factory=list)


@dataclass
class TopicMastery:
    """Rolled up from responses. Drives which topics get re-tested."""

    topic: str
    answered: int
    correct: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.answered if self.answered else 0.0

    @property
    def level(self) -> str:
        # Same bands as the SQL schema: <60 Beginner, 60-84 Mediocre, >=85 Expert.
        pct = self.accuracy * 100
        if pct >= 85:
            return "Expert"
        if pct >= 60:
            return "Mediocre"
        return "Beginner"


def stable_id(prefix: str, *parts: str) -> str:
    """
    Deterministic IDs derived from content.

    Re-ingesting the same PDF must produce the same chunk IDs, or every run orphans the
    previous question bank and the learner's history stops lining up with anything.
    """
    digest = hashlib.sha1("||".join(parts).encode("utf-8")).hexdigest()[:12]
    return "{}_{}".format(prefix, digest)
