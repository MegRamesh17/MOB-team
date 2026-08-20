"""
Safety checks applied to every generated question before it can be stored.

The conservative rule, stated once:

    A company-specific claim requires a source. General professional knowledge does not,
    but must not be dressed up as company policy.

That single line is what makes ungrounded role-knowledge questions safe enough to use.
A Documented question may say "the policy requires reporting within 72 hours" because a
sentence in a document says so. A RoleKnowledge question may ask what an analyst should
do when handling evidence, but may not invent a company retention period, deadline,
severity level, or named internal procedure.

These checks are mechanical and run regardless of provider. They are a floor, not a
substitute for the human review gate.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional, Sequence, Tuple

from .models import Chunk, ProvenanceClass, Question

# Phrasings that assert company-specific authority. Banned in RoleKnowledge questions.
_POLICY_VOICE = re.compile(
    r"\b("
    r"according to (the )?(company|policy|handbook|our)"
    r"|(the )?compan(y|y's)\s+(policy|rule|standard|procedure|requirement)"
    r"|our (policy|handbook|procedure|standard)"
    r"|(the )?(policy|handbook) (states|requires|says|mandates)"
    r"|per (the )?(policy|handbook)"
    r"|internal (policy|procedure|standard)"
    r")\b",
    re.I,
)

# A specific obligation with a number is a company rule unless a document says it.
_SPECIFIC_OBLIGATION = re.compile(
    r"\b(must|shall|required to|no later than|within)\b[^.?!]{0,40}?"
    r"\b\d{1,4}\s*(hours?|days?|weeks?|months?|years?|minutes?|characters?)\b",
    re.I,
)


class ValidationError(Exception):
    pass


def _text_of(question: Question) -> str:
    parts = [question.prompt, question.explanation]
    parts.extend(o.text for o in question.options)
    parts.extend(question.accepted_answers)
    for raw in (question.rubric_json, question.fallback_json):
        if not raw:
            continue
        try:
            parts.append(json.dumps(json.loads(raw), sort_keys=True))
        except (TypeError, ValueError):
            parts.append(raw)
    return " ".join(p for p in parts if p)


def check_role_knowledge_voice(question: Question) -> Optional[str]:
    """
    Reject a RoleKnowledge question that speaks with company authority.

    Returns a rejection reason, or None if the question is acceptable.
    """
    # ExternalSource is held to the same rule: it has a real citation (a URL), but a
    # vendor doc or a standards body is not your employer. "AWS recommends X" must
    # never render as "company policy requires X".
    if question.provenance_class not in (
        ProvenanceClass.ROLE_KNOWLEDGE,
        ProvenanceClass.EXTERNAL,
    ):
        return None

    body = _text_of(question)

    m = _POLICY_VOICE.search(body)
    if m:
        return (
            "asserts company authority ({!r}) but is not grounded in a document"
            .format(m.group(0))
        )

    m = _SPECIFIC_OBLIGATION.search(body)
    if m:
        return (
            "states a specific numeric obligation ({!r}) with no source document; "
            "that must be a Documented question".format(m.group(0).strip())
        )

    return None


def check_external_citation(question: Question) -> Optional[str]:
    """
    An ExternalSource question must carry a URL and a retrieval date.

    Without them the question is indistinguishable from something the model invented,
    and a learner cannot check it. The date matters as much as the URL: external pages
    change, and an annual re-certification needs to know whether the material behind a
    question has moved on since it was written.
    """
    if question.provenance_class != ProvenanceClass.EXTERNAL:
        return None
    if not question.source_url:
        return "external question has no source URL"
    if not question.source_fetched_at:
        return "external question has no retrieval date — staleness cannot be judged"
    return None


def check_grounding(question: Question, chunk_text: str) -> Optional[str]:
    """
    Documented questions must quote their source verbatim.

    Normalised comparison only — whitespace and case differ constantly between what a
    model echoes back and what the extractor produced.
    """
    # Grounding applies to anything with a retrieved source — a web page is checked the
    # same way a PDF is. Only ROLE_KNOWLEDGE is exempt, because by definition it has no
    # passage behind it.
    if question.provenance_class == ProvenanceClass.ROLE_KNOWLEDGE:
        return None
    # A missing quote is no longer a hard reject here. azure_openai.py's _to_question
    # already decided, per-chunk, whether an unverifiable quote should drop the
    # question outright (real source documents) or just be cleared (gpt-5's own
    # lesson prose, which a second gpt-5 call can't reasonably be held to quoting
    # verbatim). This check re-imposing "must have a quote" on top of that undid the
    # clear-instead-of-drop fix entirely -- every lesson-chunk question that took the
    # "clear the quote" path landed here and got rejected anyway, which is why yield
    # stayed at ~2-3 questions/module even after that fix shipped. Only check what we
    # CAN check: if a quote is present, it must actually appear in the passage.
    if question.source_quote and _flat(question.source_quote) not in _flat(chunk_text):
        return "source quote is not present verbatim in the cited passage"
    return None


def check_structure(question: Question) -> Optional[str]:
    """A question with no correct answer grades every learner to zero."""
    from .models import QuestionType

    if not question.prompt.strip():
        return "empty prompt"

    if question.question_type == QuestionType.FILL_IN_BLANK:
        if not question.accepted_answers:
            return "fill-in-blank with no accepted answers"
        return None

    if question.question_type in (
        QuestionType.SHORT_ANSWER,
        QuestionType.PROMPT_RESPONSE,
        QuestionType.PYTHON_CODE,
    ):
        if not question.rubric_json:
            return "AI-graded question has no locked rubric"
        if not question.fallback_json:
            return "AI-graded question has no multiple-choice fallback"
        return None

    if len(question.options) < 2:
        return "fewer than two options"

    correct = len(question.correct_option_ids())
    if question.question_type == QuestionType.MULTI_SELECT:
        if correct < 1:
            return "multi-select with no correct option"
    elif correct != 1:
        return "expected exactly one correct option, found {}".format(correct)
    return None


# --- contradiction ------------------------------------------------------------

_NUM_UNIT = re.compile(
    r"\b(\d{1,4})\s*(hours?|days?|weeks?|months?|years?|minutes?|characters?)\b", re.I
)


# Time units converted to hours so "30 days" and "72 hours" are comparable. Comparing
# only within matching units missed exactly the case that matters — a deadline restated
# in a different unit is the most common way two documents disagree.
_TO_HOURS = {
    "minute": 1 / 60.0,
    "hour": 1.0,
    "day": 24.0,
    "week": 168.0,
    "month": 730.0,
    "year": 8760.0,
}


# Words too common to indicate two statements are about the same rule.
_CONTEXT_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "at", "by", "with",
    "is", "are", "be", "been", "that", "this", "it", "as", "from", "must", "may",
    "any", "all", "not", "no", "if", "when", "which", "than", "then", "each", "every",
    "shall", "should", "will", "within", "least", "more", "less", "up", "least",
}


def _context_words(text: str, value: str, unit: str) -> set:
    """
    Content words surrounding a quantity — the subject of the rule it belongs to.

    "must be reported within 72 hours" yields {reported}; "renewed every 12 months"
    yields {renewed}. No overlap, so they are not in conflict.
    """
    pattern = re.compile(
        r"((?:\w+\W+){0,8})\b" + re.escape(value) + r"\s*" + re.escape(unit) + r"\b",
        re.I,
    )
    words = set()
    for m in pattern.finditer(text):
        for w in re.findall(r"[a-z]{4,}", m.group(1).lower()):
            if w not in _CONTEXT_STOP:
                words.add(w.rstrip("s"))
    return words


def _canonical(value: str, unit: str) -> Optional[Tuple[str, float]]:
    """Return (dimension, magnitude) so different units can be compared."""
    try:
        number = float(value)
    except ValueError:
        return None
    key = unit.lower().rstrip("s")
    if key in _TO_HOURS:
        return ("time", number * _TO_HOURS[key])
    return (key, number)


def find_numeric_contradictions(
    question: Question, related: Sequence[Chunk]
) -> List[str]:
    """
    Cheap, provider-free contradiction check.

    If a question asserts "within 30 days" and a related passage from any document says
    "within 14 days" for the same unit, that is worth a human's attention. This is the
    concrete version of "make sure questions adhere to company rules" — the common real
    failure is an industry-standard figure quietly overriding a stricter internal one.

    Deliberately noisy: it flags for review rather than deciding. A model-backed
    semantic check (see llm.azure_openai) catches the cases this cannot.
    """
    body = _text_of(question)
    asserted = [
        (v, u, _canonical(v, u), _context_words(body, v, u))
        for v, u in _NUM_UNIT.findall(body)
    ]
    asserted = [a for a in asserted if a[2]]
    if not asserted:
        return []

    problems: List[str] = []
    for chunk in related:
        for value, unit in _NUM_UNIT.findall(chunk.text):
            other = _canonical(value, unit)
            if not other:
                continue
            other_context = _context_words(chunk.text, value, unit)

            for a_value, a_unit, (a_dim, a_mag), a_context in asserted:
                dim, mag = other
                if dim != a_dim or a_mag == mag:
                    continue
                # Same dimension and different magnitude is not enough. "Renew training
                # every 12 months" and "report a breach within 72 hours" are both
                # durations and both different, but they are unrelated rules. Requiring
                # shared context words around the number is what separates a real
                # conflict from noise — without it roughly every flag was a false
                # positive and a reviewer would learn to ignore them all.
                if not (a_context & other_context):
                    continue
                problems.append(
                    "question says {} {} but '{}' (p.{}) says {} {} — shared context: {}".format(
                        a_value, a_unit, chunk.doc_title, chunk.page_start, value, unit,
                        ", ".join(sorted(a_context & other_context)[:3]),
                    )
                )
    return sorted(set(problems))


def validate(
    question: Question,
    chunk_text: str = "",
    related: Sequence[Chunk] = (),
) -> Tuple[bool, List[str]]:
    """
    Run every check. Returns (is_acceptable, notes).

    Structure, grounding and role-voice failures are hard rejects — the question is not
    stored. Contradictions are notes: they do not block storage, but a reviewer sees
    them, because a genuine conflict between documents is a finding worth surfacing
    rather than silently discarding.
    """
    for check in (
        check_structure(question),
        check_grounding(question, chunk_text) if chunk_text else None,
        check_role_knowledge_voice(question),
        check_external_citation(question),
    ):
        if check:
            return False, [check]

    return True, find_numeric_contradictions(question, related)


def _flat(text: str) -> str:
    return " ".join(text.lower().split())
