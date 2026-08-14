"""
Adaptive quiz assembly.

The rule that keeps this coherent: **SQL decides what to ask about, retrieval decides
what the source says, and the model only writes prose.** A vector store has no idea who
the learner is; the learner's weaknesses live in their response history. Conflating the
two is the most common way this design goes wrong.

Selection works in four steps:

  1. Roll up the learner's responses into per-topic accuracy.
  2. Mark topics weak — but only with enough evidence. Two wrong out of two is noise.
  3. Allocate quiz slots, weighted toward weak topics.
  4. Within each topic, pick questions whose *measured* difficulty puts expected success
     near the target, skipping anything seen too recently.

Nothing here calls a model. Assembly is deterministic, fast, and free.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .bank import Bank
from .config import CONFIG
from .models import Difficulty, Question, ReviewStatus, TopicMastery


def scope_matches(chunk_scope: str, role: str) -> bool:
    """
    Whether a chunk's role scope covers a given role.

    A scope is "ALL", one role code, or several comma-separated — a vetted source can
    be approved for SDE1, SDE2 and SDE3 but not for a Director.
    """
    scope = (chunk_scope or "ALL").upper()
    if scope == "ALL" or not role:
        return True
    return role.upper() in {s.strip() for s in scope.split(",")}

# Probability a learner of "average" standing answers a question of each measured
# difficulty correctly. Used to steer a quiz toward the target success rate.
_EXPECTED_SUCCESS = {
    Difficulty.EASY: 0.85,
    Difficulty.MEDIUM: 0.65,
    Difficulty.HARD: 0.40,
}


@dataclass
class TopicPlan:
    topic: str
    slots: int
    reason: str
    accuracy: Optional[float]


@dataclass
class QuizPlan:
    learner_id: str
    questions: List[Question]
    topic_plans: List[TopicPlan]
    is_remedial: bool

    def explain(self) -> str:
        lines = []
        for tp in self.topic_plans:
            acc = "n/a" if tp.accuracy is None else "{:.0%}".format(tp.accuracy)
            lines.append(
                "  {:<38} {} question(s)  [accuracy {}, {}]".format(
                    tp.topic[:38], tp.slots, acc, tp.reason
                )
            )
        return "\n".join(lines)


def weak_topics(
    mastery: Dict[str, TopicMastery],
    threshold: Optional[float] = None,
    min_answers: Optional[int] = None,
) -> List[TopicMastery]:
    """
    Topics the learner is genuinely struggling with, worst first.

    The evidence floor matters. Without it a single unlucky wrong answer brands a topic
    weak and the learner gets drilled on something they actually know.
    """
    threshold = CONFIG.weak_threshold if threshold is None else threshold
    min_answers = CONFIG.min_answers_for_weakness if min_answers is None else min_answers

    weak = [
        m
        for m in mastery.values()
        if m.answered >= min_answers and m.accuracy < threshold
    ]
    return sorted(weak, key=lambda m: m.accuracy)


def _target_difficulty(accuracy: Optional[float]) -> Difficulty:
    """
    Aim at roughly CONFIG.target_success_rate. Someone at 40% on a topic gets easier
    questions to rebuild footing; someone at 95% gets harder ones or they learn nothing.
    """
    if accuracy is None:
        return Difficulty.MEDIUM
    if accuracy < 0.50:
        return Difficulty.EASY
    if accuracy < 0.85:
        return Difficulty.MEDIUM
    return Difficulty.HARD


def _score_question(
    question: Question,
    wanted: Difficulty,
    seen_recently: bool,
    rng: random.Random,
) -> float:
    """Lower is better. A ranking heuristic, not a probability."""
    measured = question.effective_difficulty
    order = [Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD]
    distance = abs(order.index(measured) - order.index(wanted))

    score = float(distance)

    # Strong penalty, not exclusion — a thin bank must still be able to fill a quiz.
    if seen_recently:
        score += 5.0

    # Prefer questions with little evidence, so difficulty estimates converge.
    if question.p_value is None:
        score -= 0.3

    score += rng.random() * 0.25  # break ties without being predictable
    return score


def under_sampled_topics(
    mastery: Dict[str, TopicMastery],
    all_topics: Sequence[str],
    min_answers: Optional[int] = None,
) -> List[str]:
    """
    Topics with too little evidence to judge, worst-known-accuracy first.

    These need slots as much as confirmed weak topics do. Without this, a topic the
    learner is failing but has only answered twice stays below the evidence floor
    forever: it is never "weak", so remedial allocation crowds it out, so it never
    accumulates the third answer that would reveal the problem. That is exactly what
    happened to Fire Safety in testing — 50% accuracy and dropped to zero questions.
    """
    min_answers = CONFIG.min_answers_for_weakness if min_answers is None else min_answers

    pending = []
    for topic in all_topics:
        m = mastery.get(topic)
        answered = m.answered if m else 0
        if answered < min_answers:
            # Fewest answers first, then worst accuracy. A never-seen topic is a total
            # blind spot and outranks a topic already sampled twice — sorting by
            # accuracy first put unseen topics (which default to 1.0) at the BACK, and
            # with more topics than quiz slots some were never served at all.
            pending.append((answered, m.accuracy if m else 0.0, topic))
    return [t for _, _, t in sorted(pending)]


def _allocate(
    weak: Sequence[TopicMastery],
    under_sampled: Sequence[str],
    all_topics: Sequence[str],
    total: int,
    weak_share: float,
) -> List[TopicPlan]:
    """
    Split quiz slots three ways:
      1. confirmed weak topics      (weak_share of the quiz)
      2. topics lacking evidence    (most of the remainder)
      3. everything else            (retention checks)
    """
    plans: List[TopicPlan] = []

    if not weak:
        # No history, or nothing is weak: spread for a baseline reading.
        #
        # Order by evidence need, NOT alphabetically. With more topics than quiz slots,
        # alphabetical order takes the same first N every round and the tail of the
        # alphabet is never served at all — "Phishing And Social Engineering" sat 13th
        # of 14 and got zero questions across six rounds. Least-sampled-first rotates
        # coverage automatically.
        seen_order = set(under_sampled)
        topics = list(under_sampled) + [t for t in all_topics if t not in seen_order]
        if not topics:
            return []
        remaining = total
        for topic in topics:
            if remaining <= 0:
                break
            plans.append(TopicPlan(topic, 1, "baseline coverage", None))
            remaining -= 1
        # Any slots left over (fewer topics than slots) go round-robin.
        i = 0
        while remaining > 0 and plans:
            plans[i % len(plans)].slots += 1
            remaining -= 1
            i += 1
        return plans

    weak_slots = max(1, int(round(total * weak_share)))
    other_slots = max(0, total - weak_slots)

    # Weight inversely to accuracy: the worst topic gets the most attention.
    weights = [max(0.05, 1.0 - m.accuracy) for m in weak]
    weight_sum = sum(weights)
    assigned = 0
    for m, w in zip(weak, weights):
        take = max(1, int(round(weak_slots * (w / weight_sum))))
        plans.append(
            TopicPlan(m.topic, take, "weak: {}/{} correct".format(m.correct, m.answered), m.accuracy)
        )
        assigned += take

    # Rounding can overshoot; trim from the strongest weak topic.
    while assigned > weak_slots and len(plans) > 1:
        for p in sorted(plans, key=lambda p: -(p.accuracy or 0)):
            if p.slots > 1:
                p.slots -= 1
                assigned -= 1
                break
        else:
            break

    covered = {p.topic for p in plans}

    # Evidence-gathering comes before retention checks: a topic we cannot yet judge is
    # a bigger blind spot than one we know is fine.
    left = other_slots
    for topic in under_sampled:
        if left <= 0:
            break
        if topic in covered:
            continue
        plans.append(TopicPlan(topic, 1, "gathering evidence", None))
        covered.add(topic)
        left -= 1

    rest = [t for t in all_topics if t not in covered]
    if left and rest:
        per = max(1, left // len(rest))
        for topic in rest:
            if left <= 0:
                break
            take = min(per, left)
            plans.append(TopicPlan(topic, take, "retention check", None))
            left -= take

    return plans


def build_quiz(
    bank: Bank,
    learner_id: str,
    length: Optional[int] = None,
    seed: Optional[int] = None,
    role: str = "",
) -> QuizPlan:
    """
    Assemble the next quiz for a learner.

    Only Approved questions are ever considered. Pending and Rejected questions are
    invisible here by construction, so an unreviewed generated question cannot reach a
    learner even by accident.

    `role` applies the same scoping guarantee the search index applies at retrieval
    time. A question inherits role_scope from the chunk it came from, so material
    approved only for SDE1-3 must not be served to a Director — and filtering only at
    the index was not enough, because the bank also holds questions generated before
    that filter existed. A question with no role_code applies to everyone.
    """
    length = length or CONFIG.quiz_length
    rng = random.Random(seed if seed is not None else "{}-{}".format(CONFIG.seed, learner_id))

    approved = bank.questions(status=ReviewStatus.APPROVED)
    if not approved:
        raise RuntimeError(
            "No approved questions in the bank. Generate some, then approve them "
            "(quizgen review --approve-all for a dry run)."
        )

    if role:
        in_scope = [q for q in approved if scope_matches(q.role_code, role)]
        # Refusing to fall back is the point. Silently widening to every question when
        # a role has none of its own would serve exactly the material the scope exists
        # to withhold, and it would look like it worked.
        if not in_scope:
            raise RuntimeError(
                "No approved questions are in scope for role {}. Generate questions "
                "from sources approved for that role, or start a quiz with no role "
                "selected.".format(role)
            )
        approved = in_scope

    # Questions must be grouped by the SAME key mastery is measured on, or allocation
    # asks for a group that the pool has no bucket for and every plan silently falls
    # through to the top-up path — which is untargeted, so the quiz looks like it
    # worked while being no better than random.
    grain = CONFIG.mastery_grain
    group_of = (
        (lambda q: q.source_doc_title or q.topic) if grain == "subject"
        else (lambda q: q.topic)
    )

    by_topic: Dict[str, List[Question]] = {}
    for q in approved:
        by_topic.setdefault(group_of(q), []).append(q)

    mastery = bank.mastery(learner_id, grain)
    weak = weak_topics(mastery)
    # Only target weak topics we can actually serve questions for.
    weak = [m for m in weak if m.topic in by_topic]

    topics = sorted(by_topic.keys())
    under = under_sampled_topics(mastery, topics)
    plans = _allocate(weak, under, topics, length, CONFIG.weak_topic_share)
    seen = set(bank.recently_seen(learner_id, CONFIG.repeat_cooldown_attempts))

    chosen: List[Question] = []
    used: set = set()

    for plan in plans:
        pool = [q for q in by_topic.get(plan.topic, []) if q.question_id not in used]
        if not pool:
            continue
        accuracy = mastery[plan.topic].accuracy if plan.topic in mastery else None
        wanted = _target_difficulty(accuracy)
        pool.sort(key=lambda q: _score_question(q, wanted, q.question_id in seen, rng))
        for q in pool[: plan.slots]:
            chosen.append(q)
            used.add(q.question_id)

    # Top up from anywhere if allocation under-filled (thin topics).
    if len(chosen) < length:
        spare = [q for q in approved if q.question_id not in used]
        spare.sort(key=lambda q: _score_question(q, Difficulty.MEDIUM, q.question_id in seen, rng))
        for q in spare[: length - len(chosen)]:
            chosen.append(q)
            used.add(q.question_id)

    rng.shuffle(chosen)
    return QuizPlan(
        learner_id=learner_id,
        questions=chosen[:length],
        topic_plans=plans,
        is_remedial=bool(weak),
    )


def coverage_gaps(bank: Bank, learner_id: str) -> List[Tuple[str, int]]:
    """
    Weak topics with too few approved questions to drill against.

    This is the trigger for generating *more* questions: the learner keeps failing a
    topic and the bank has nothing new to show them. Without this check, remediation
    silently recycles the same handful of questions until they are memorised rather
    than understood.
    """
    mastery = bank.mastery(learner_id)
    seen = set(bank.recently_seen(learner_id, CONFIG.repeat_cooldown_attempts))
    gaps: List[Tuple[str, int]] = []
    for m in weak_topics(mastery):
        pool = bank.questions(status=ReviewStatus.APPROVED, topic=m.topic)
        unseen = [q for q in pool if q.question_id not in seen]
        if len(unseen) < 3:
            gaps.append((m.topic, len(unseen)))
    return gaps
