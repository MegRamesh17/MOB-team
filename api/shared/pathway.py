"""Deterministic selection rules for the document-to-training pathway.

The model writes questions. This module decides what a learner sees, in what order,
and whether two final-assessment forms are comparable. Keeping those decisions in
ordinary Python makes them cheap to test and independent of Azure SQL.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DIFFICULTIES = ("Easy", "Medium", "Hard")
_DIFFICULTY_INDEX = {name: index for index, name in enumerate(DIFFICULTIES)}
CHOICE_TYPES = frozenset(("MultipleChoice",))
AI_GRADED_TYPES = frozenset(("ShortAnswer", "PromptResponse", "PythonCode"))
FAST_MODULE_TYPES = frozenset(("MultipleChoice", "MultiSelect", "TrueFalse", "FillInBlank"))
MAX_FINAL_AI_GRADED = 3


def stable_module_id(company_id: int, doc_id: str, topic: str) -> str:
    raw = "{}|{}|{}".format(company_id, doc_id, topic).encode("utf-8")
    return "mod_" + hashlib.sha256(raw).hexdigest()[:24]


def next_difficulty(current: str, correct: bool) -> str:
    """Move one level after each module-checkpoint answer."""
    index = _DIFFICULTY_INDEX.get(current, 1)
    index += 1 if correct else -1
    return DIFFICULTIES[max(0, min(len(DIFFICULTIES) - 1, index))]


def diagnostic_pathway(
    modules: Sequence[Dict[str, Any]], scores: Dict[str, Dict[str, int]]
) -> List[str]:
    """Weakest diagnostic module first, source order as the stable tie-breaker.

    Every module id is returned exactly once. Diagnostic performance changes sequence,
    never membership, so doing well cannot skip required content.
    """
    ordered = sorted(
        modules,
        key=lambda module: (
            scores.get(module["module_id"], {}).get("correct", 0),
            module.get("source_order", 0),
            module["module_id"],
        ),
    )
    return [module["module_id"] for module in ordered]


def diagnostic_questions(
    pool: Sequence[Dict[str, Any]], modules: Sequence[Dict[str, Any]], seed: str
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """
    Choose one choice-only question per difficulty tier a module actually has.

    A module missing a tier (say, no Hard question survived generation) no longer
    blocks the whole diagnostic -- it used to require exactly Easy+Medium+Hard for
    EVERY module before the diagnostic could start at all, which meant one module
    short one difficulty locked every other module's diagnostic too, with no way to
    recover short of a full regeneration. Take whatever tiers exist; a module with
    zero choice questions of any difficulty just contributes nothing to the
    diagnostic and falls back to default ordering, same as ties do already.
    """
    chosen: List[Dict[str, Any]] = []
    missing: List[Tuple[str, str]] = []
    used = set()

    for module in modules:
        for difficulty in DIFFICULTIES:
            candidates = [
                q for q in pool
                if q.get("topic") == module.get("topic")
                and q.get("difficulty") == difficulty
                and q.get("question_type") in CHOICE_TYPES
                and q.get("question_id") not in used
            ]
            if not candidates:
                continue
            candidates.sort(key=lambda q: _stable_rank(seed, q["question_id"]))
            pick = candidates[0]
            used.add(pick["question_id"])
            chosen.append({**pick, "module_id": module["module_id"], "purpose": "diagnostic"})

    return chosen, missing


def initial_module_difficulty(correct: int, possible: int = 3) -> str:
    if possible and correct >= possible:
        return "Hard"
    if correct >= 2:
        return "Medium"
    return "Easy"


def choose_adaptive_question(
    pool: Sequence[Dict[str, Any]],
    wanted: str,
    current_attempt_ids: Iterable[str],
    historical_ids: Iterable[str],
    review_ids: Iterable[str],
    prefer_review: bool,
    seed: str,
) -> Optional[Dict[str, Any]]:
    """Pick one module question, preferring the target difficulty and fresh items.

    Retakes reserve three positions for prior mistakes. If that exact question is not
    available, selection falls back to another weak/fresh item without blocking the
    learner on a thin bank.
    """
    current = set(current_attempt_ids)
    history = set(historical_ids)
    review = set(review_ids)
    candidates = [q for q in pool if q.get("question_id") not in current]
    if not candidates:
        return None

    wanted_index = _DIFFICULTY_INDEX.get(wanted, 1)

    def rank(question: Dict[str, Any]) -> tuple:
        qid = question["question_id"]
        review_rank = 0 if prefer_review and qid in review else 1
        if not prefer_review:
            review_rank = 0
        freshness = 0 if qid not in history else 1
        distance = abs(_DIFFICULTY_INDEX.get(question.get("difficulty"), 1) - wanted_index)
        evidence = int(question.get("times_served") or 0)
        return review_rank, freshness, distance, evidence, _stable_rank(seed, qid)

    return min(candidates, key=rank)


def final_assessment_size(module_count: int) -> int:
    """Scale small documents down; use 25 for normal trainings, up to 35 for large ones."""
    if module_count <= 1:
        return 15
    if module_count == 2:
        return 20
    return max(25, min(35, module_count * 2))


def final_questions(
    pool: Sequence[Dict[str, Any]],
    modules: Sequence[Dict[str, Any]],
    learner_seed: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build an equivalent final form from shared anchors and variable questions.

    Anchor ranking does not include the learner seed, so every employee taking the same
    training version receives the same anchors. Variable ranking does include it. Both
    sets use one common topic/difficulty blueprint.
    """
    # Diagnostic results may reorder modules for an individual learner. Final-assessment
    # anchors must ignore that personalized order or two employees could receive
    # different "shared" questions from the same training.
    canonical_modules = sorted(
        modules,
        key=lambda module: (module.get("source_order", 0), module.get("module_id", "")),
    )
    topics = [module["topic"] for module in canonical_modules]
    total = final_assessment_size(len(topics))
    anchor_target = min(total, max(len(topics), int(round(total * 0.40))))
    slots = _blueprint_slots(topics, total)
    anchor_slots = set(range(anchor_target))
    chosen: List[Dict[str, Any]] = []
    used = set()
    shortages = []

    for index, (topic, difficulty) in enumerate(slots):
        purpose = "anchor" if index in anchor_slots else "variable"
        candidates = [
            q for q in pool
            if q.get("topic") == topic
            and q.get("difficulty") == difficulty
            and q.get("question_id") not in used
        ]
        if not candidates:
            # Preserve topic coverage first, then choose the nearest difficulty.
            candidates = [
                q for q in pool
                if q.get("topic") == topic and q.get("question_id") not in used
            ]
        if not candidates:
            shortages.append({"topic": topic, "difficulty": difficulty})
            continue

        # Shared anchors stay quick and identical for everybody. Variable slots may
        # include a small number of rubric-graded responses, capped so a final does not
        # become a wall of model waits.
        ai_count = sum(1 for question in chosen if is_ai_graded(question))
        if purpose == "anchor" or ai_count >= MAX_FINAL_AI_GRADED:
            fast = [question for question in candidates if not is_ai_graded(question)]
            if fast:
                candidates = fast

        rank_seed = "anchor" if purpose == "anchor" else learner_seed
        wanted_index = _DIFFICULTY_INDEX[difficulty]
        candidates.sort(key=lambda q: (
            abs(_DIFFICULTY_INDEX.get(q.get("difficulty"), 1) - wanted_index),
            _stable_rank(rank_seed, q["question_id"]),
        ))
        pick = candidates[0]
        used.add(pick["question_id"])
        chosen.append({**pick, "purpose": purpose})

    # Keep model-graded responses at the end. Learners can move through every instant
    # item first and only wait for rubric grading in the final portion.
    chosen.sort(key=is_ai_graded)

    difficulty_counts = {
        difficulty: sum(1 for _, value in slots if value == difficulty)
        for difficulty in DIFFICULTIES
    }
    blueprint = {
        "total": total,
        "anchorCount": anchor_target,
        "variableCount": total - anchor_target,
        "difficultyCounts": difficulty_counts,
        "shortages": shortages,
    }
    return chosen, blueprint


def is_ai_graded(question: Dict[str, Any]) -> bool:
    return question.get("question_type") in AI_GRADED_TYPES


def _blueprint_slots(topics: Sequence[str], total: int) -> List[Tuple[str, str]]:
    if not topics:
        return []
    # Medium carries most of the assessment, with matched Easy/Hard shoulders.
    difficulty_cycle = ("Medium", "Easy", "Hard", "Medium")
    return [
        (topics[index % len(topics)], difficulty_cycle[index % len(difficulty_cycle)])
        for index in range(total)
    ]


def _stable_rank(seed: str, question_id: str) -> str:
    return hashlib.sha256("{}|{}".format(seed, question_id).encode("utf-8")).hexdigest()
