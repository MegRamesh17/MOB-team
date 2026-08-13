"""
Grading and scoring.

The split that matters: a judge decides whether *one free-text answer* means the same
thing as the key. Everything else — totals, percentages, the pass bar — is arithmetic
here in code.

That boundary is not stylistic. A compliance result must be reproducible and
explainable: if someone fails at 78% and disputes it, you have to show which questions
were wrong and how the total was reached. If a model produced the score, the same
answers could yield a different result on a rerun and there is nothing to show an
auditor.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from .bank import utcnow
from .config import CONFIG
from .llm.base import AnswerJudge
from .models import Attempt, Question, QuestionType, Response, stable_id


def grade_one(
    question: Question,
    selected_option_ids: Sequence[str],
    text_answer: str,
    judge: Optional[AnswerJudge] = None,
) -> Response:
    selected = list(selected_option_ids)
    is_correct = False
    reason = ""

    if question.question_type == QuestionType.FILL_IN_BLANK:
        if judge is None:
            from .llm.mock import LexicalJudge

            judge = LexicalJudge()
        is_correct, reason = judge.judge(question.prompt, question.accepted_answers, text_answer)

    elif question.question_type == QuestionType.MULTI_SELECT:
        # All-or-nothing: partial credit on a multi-select compliance question lets
        # someone pass while still believing something false.
        correct_ids = set(question.correct_option_ids())
        is_correct = set(selected) == correct_ids
        reason = "exact set match required"

    else:  # MultipleChoice, TrueFalse
        correct_ids = set(question.correct_option_ids())
        is_correct = len(selected) == 1 and selected[0] in correct_ids
        reason = "single correct option"

    return Response(
        response_id=stable_id("resp", question.question_id, ",".join(selected), text_answer, utcnow()),
        attempt_id="",
        learner_id="",
        question_id=question.question_id,
        topic=question.topic,
        selected_option_ids=selected,
        text_answer=text_answer,
        is_correct=is_correct,
        points_awarded=question.points if is_correct else 0,
        answered_at=utcnow(),
    )


def score_attempt(
    learner_id: str,
    attempt_id: str,
    questions: Sequence[Question],
    responses: List[Response],
    started_at: str,
) -> Attempt:
    """Deterministic arithmetic. Same inputs, same score, every time."""
    possible = sum(q.points for q in questions)
    awarded = sum(r.points_awarded for r in responses)
    percent = (100.0 * awarded / possible) if possible else 0.0

    for r in responses:
        r.attempt_id = attempt_id
        r.learner_id = learner_id

    return Attempt(
        attempt_id=attempt_id,
        learner_id=learner_id,
        started_at=started_at,
        submitted_at=utcnow(),
        score_percent=round(percent, 2),
        points_awarded=awarded,
        points_possible=possible,
        passed=percent >= CONFIG.passing_score,
        responses=responses,
    )
