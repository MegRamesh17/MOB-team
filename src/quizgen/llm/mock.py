"""
Offline question generator. No API, no key, no network.

It is not pretending to be a language model. It exploits the fact that policy prose is
highly patterned — definitions, obligations with modal verbs, deadlines with numbers,
acronym expansions — and turns those patterns into questions with mechanically correct
answer keys. Because the key is lifted from the source rather than invented, a mock
question cannot hallucinate; it can only be dull or awkward.

That trade is the right way round for a test harness. The whole adaptive loop —
weak-topic detection, difficulty targeting, remediation — can be exercised and debugged
today, and swapping in Azure OpenAI later changes question *prose*, not pipeline shape.

Deterministic: same corpus and seed produce the same bank, so tests are stable.
"""

from __future__ import annotations

import random
import re
from typing import Dict, List, Optional, Sequence, Tuple

from ..models import (
    Chunk, Difficulty, Option, ProvenanceClass, Question, QuestionType, stable_id,
)

# --- patterns in policy prose -------------------------------------------------

# "X is Y", "X means Y", "X refers to Y" — the definitional backbone.
_DEFINITION = re.compile(
    r"^(?P<term>[A-Z][^.]{2,60}?)\s+(?P<verb>is|are|means|refers to)\s+(?P<body>[^.]{25,220})\.?$"
)

# Deadlines and thresholds: "within 30 days", "at least 14 characters".
_QUANTITY = re.compile(
    r"\b(?P<value>\d{1,4})\s+(?P<unit>days?|hours?|weeks?|months?|years?|minutes?|characters?|people|levels?)\b",
    re.I,
)

# "PPE stands for personal protective equipment", "the acronym PASS".
_ACRONYM_EXPANSION = re.compile(
    r"\b(?P<acronym>[A-Z]{2,6})\b\s+(?:stands for|is short for)\s+(?P<expansion>[^.]{5,90})"
)

# Obligations — the sentences a compliance quiz actually cares about.
_MODAL = re.compile(r"\b(must not|must never|must|may not|should not|should|shall)\b", re.I)

# "Every employee is responsible for..." parses as a definition but yields the nonsense
# stem "What is every employee?". Definitions worth asking about name a thing; these
# openers signal a rule about people instead.
_NOT_A_DEFINABLE_TERM = re.compile(
    r"^(every|all|any|each|some|no|this|these|those|there|it|they|we|you|he|she|"
    r"where|when|if|because|however|therefore)\b",
    re.I,
)

_STOPWORDS = {
    "the", "and", "for", "that", "with", "this", "from", "are", "was", "were", "which",
    "their", "they", "them", "have", "has", "had", "not", "any", "all", "must", "may",
    "can", "will", "shall", "should", "would", "could", "been", "being", "into", "than",
    "then", "when", "where", "what", "who", "whom", "its", "it's", "you", "your", "our",
}


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    return [p.strip() for p in parts if len(p.strip()) > 30]


def _usable(sentence: str) -> bool:
    """
    Filter to sentences dense enough to carry a question.

    The lower bound is 45, not 60: short obligations like "Lifts must never be used
    during a fire evacuation" are among the best true/false material in a safety
    document, and a 60-char floor silently discarded them.
    """
    if not (45 <= len(sentence) <= 300):
        return False
    if sentence.count(",") > 5:  # list-like, makes a muddy question
        return False
    return True


def _definition_stem(term: str, verb: str) -> str:
    """Build a grammatical question stem for a definitional sentence."""
    verb = verb.lower().strip()
    if verb in ("means", "refers to"):
        return "what does {} mean?".format(term)
    return "what {} {}?".format("are" if verb == "are" else "is", term)


def _shorten(text: str, limit: int = 150) -> str:
    text = text.strip().rstrip(".")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "..."


class _Corpus:
    """Cross-document index used to mine plausible distractors."""

    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self.definitions: List[Tuple[str, str, str]] = []  # (topic, term, body)
        self.quantities: List[Tuple[str, str, str]] = []  # (topic, value, unit)
        self.terms: Dict[str, List[str]] = {}

        for chunk in chunks:
            for sentence in _sentences(chunk.text):
                m = _DEFINITION.match(sentence)
                if m:
                    self.definitions.append(
                        (chunk.topic, m.group("term").strip(), m.group("body").strip())
                    )
                for q in _QUANTITY.finditer(sentence):
                    self.quantities.append(
                        (chunk.topic, q.group("value"), q.group("unit").lower())
                    )
            words = [
                w
                for w in re.findall(r"\b[a-z]{5,}\b", chunk.text.lower())
                if w not in _STOPWORDS
            ]
            self.terms[chunk.topic] = sorted(set(words))

    def distractor_definitions(self, exclude_term: str, count: int, rng: random.Random) -> List[str]:
        pool = [b for _, t, b in self.definitions if t.lower() != exclude_term.lower()]
        pool = list(dict.fromkeys(pool))
        rng.shuffle(pool)
        return [_shorten(p) for p in pool[:count]]

    def distractor_quantities(self, value: str, unit: str, count: int, rng: random.Random) -> List[str]:
        """Prefer real values from elsewhere in the corpus; fall back to arithmetic."""
        same_unit = [v for _, v, u in self.quantities if u == unit and v != value]
        pool = list(dict.fromkeys(same_unit))
        rng.shuffle(pool)
        out = pool[:count]

        if len(out) < count:
            try:
                n = int(value)
            except ValueError:
                n = 0
            for factor in (2, 3, 4):
                for candidate in (n * factor, max(1, n // factor)):
                    s = str(candidate)
                    if s != value and s not in out:
                        out.append(s)
                    if len(out) >= count:
                        break
                if len(out) >= count:
                    break
        return ["{} {}".format(v, unit) for v in out[:count]]


class MockGenerator:
    """Template-based generator. Interface-compatible with the Azure OpenAI one."""

    name = "mock"

    def __init__(self, corpus: Sequence[Chunk], seed: int = 1337) -> None:
        self._corpus = _Corpus(corpus)
        self._seed = seed

    # -- individual strategies -------------------------------------------------

    def _from_definition(self, chunk: Chunk, sentence: str, rng: random.Random) -> Optional[Question]:
        m = _DEFINITION.match(sentence)
        if not m:
            return None
        term = m.group("term").strip()
        body = m.group("body").strip()
        if len(term.split()) > 8:
            return None
        if _NOT_A_DEFINABLE_TERM.match(term):
            return None

        correct = _shorten(body)
        distractors = self._corpus.distractor_definitions(term, 3, rng)
        if len(distractors) < 2:
            return None

        # Option ids hang off the QUESTION id, not the chunk id. Two questions built
        # from the same chunk routinely share a distractor, and keying on the chunk made
        # those two options collide on one id — which the bank's plain INSERT now
        # rejects outright rather than silently moving the row and leaving a question
        # with no correct answer.
        question_id = stable_id("q", chunk.chunk_id, "def", term)
        options = [Option(stable_id("opt", question_id, correct), correct, True)] + [
            Option(stable_id("opt", question_id, d), d, False) for d in distractors
        ]
        rng.shuffle(options)

        return Question(
            question_id=question_id,
            topic=chunk.topic,
            question_type=QuestionType.MULTIPLE_CHOICE,
            difficulty=Difficulty.MEDIUM,
            # Casing is preserved verbatim — lowercasing the first letter turned "PPE"
            # into "pPE". "means"/"refers to" need a different stem from "is"/"are".
            prompt="According to {}, {}".format(chunk.doc_title, _definition_stem(term, m.group("verb"))),
            options=options,
            explanation="Stated in '{}', section '{}'.".format(chunk.doc_title, chunk.section),
            source_chunk_id=chunk.chunk_id,
            source_doc_title=chunk.doc_title,
            source_page=chunk.page_start,
            role_code=chunk.role_scope or "",
            source_quote=sentence,
            generator=self.name,
        )

    def _from_quantity(self, chunk: Chunk, sentence: str, rng: random.Random) -> Optional[Question]:
        m = _QUANTITY.search(sentence)
        if not m:
            return None
        value, unit = m.group("value"), m.group("unit").lower()
        correct = "{} {}".format(value, unit)

        distractors = self._corpus.distractor_quantities(value, unit, 3, rng)
        if len(distractors) < 2:
            return None

        # Blank the quantity out of the sentence so the stem reads naturally.
        stem = sentence[: m.start()] + "______" + sentence[m.end() :]

        question_id = stable_id("q", chunk.chunk_id, "qty", correct, sentence[:40])
        options = [Option(stable_id("opt", question_id, correct), correct, True)] + [
            Option(stable_id("opt", question_id, d), d, False) for d in distractors
        ]
        rng.shuffle(options)

        return Question(
            question_id=question_id,
            topic=chunk.topic,
            question_type=QuestionType.MULTIPLE_CHOICE,
            difficulty=Difficulty.MEDIUM,
            prompt="Fill the gap: {}".format(_shorten(stem, 260)),
            options=options,
            explanation="The document specifies {}.".format(correct),
            source_chunk_id=chunk.chunk_id,
            source_doc_title=chunk.doc_title,
            source_page=chunk.page_start,
            role_code=chunk.role_scope or "",
            source_quote=sentence,
            generator=self.name,
        )

    def _from_obligation(self, chunk: Chunk, sentence: str, rng: random.Random) -> Optional[Question]:
        """
        True/false from a stated obligation. The false variant inverts the modal, which
        reliably reverses the meaning of a compliance rule without touching anything else.
        """
        m = _MODAL.search(sentence)
        if not m:
            return None
        modal = m.group(1).lower()

        flips = {
            "must not": "must",
            "must never": "must always",
            "must": "must not",
            "may not": "may",
            "should not": "should",
            "should": "should not",
            "shall": "shall not",
        }
        if modal not in flips:
            return None

        make_false = rng.random() < 0.5
        if make_false:
            statement = sentence[: m.start(1)] + flips[modal] + sentence[m.end(1) :]
            answer_is_true = False
        else:
            statement = sentence
            answer_is_true = True

        question_id = stable_id("q", chunk.chunk_id, "tf", sentence[:60])
        opts = [
            Option(stable_id("opt", question_id, "true"), "True", answer_is_true),
            Option(stable_id("opt", question_id, "false"), "False", not answer_is_true),
        ]

        return Question(
            question_id=question_id,
            topic=chunk.topic,
            question_type=QuestionType.TRUE_FALSE,
            difficulty=Difficulty.EASY,
            prompt="True or false: {}".format(_shorten(statement, 260)),
            options=opts,
            explanation="The document states: \"{}\"".format(_shorten(sentence, 200)),
            source_chunk_id=chunk.chunk_id,
            source_doc_title=chunk.doc_title,
            source_page=chunk.page_start,
            role_code=chunk.role_scope or "",
            source_quote=sentence,
            generator=self.name,
        )

    # -- public API ------------------------------------------------------------

    def generate(
        self,
        chunk: Chunk,
        count: int = 2,
        difficulty: Optional[Difficulty] = None,
    ) -> List[Question]:
        # Seed per chunk: regenerating one passage does not disturb the others.
        rng = random.Random("{}::{}".format(self._seed, chunk.chunk_id))

        candidates = [s for s in _sentences(chunk.text) if _usable(s)]
        rng.shuffle(candidates)

        strategies = (
            self._from_definition,
            self._from_quantity,
            self._from_obligation,
        )

        produced: List[Question] = []
        seen_ids = set()
        for sentence in candidates:
            for strategy in strategies:
                if len(produced) >= count:
                    break
                try:
                    question = strategy(chunk, sentence, rng)
                except Exception:
                    question = None
                if question and question.question_id not in seen_ids:
                    if difficulty is not None:
                        question.difficulty = difficulty
                    if chunk.container == "generated-lessons":
                        # The ordinary mock generator is intentionally deterministic,
                        # so three difficulty-ladder calls would otherwise produce the
                        # same ids and only the first set would persist. Course-mode ids
                        # include difficulty and carry the same normalized provenance as
                        # Azure-generated questions.
                        question.question_id = stable_id(
                            "q", question.question_id, question.difficulty.value)
                        for option in question.options:
                            option.option_id = stable_id(
                                "opt", question.question_id, option.text)
                        point_ids = list(getattr(chunk, "learning_point_ids", []) or [])
                        point_id = point_ids[len(produced) % len(point_ids)] if point_ids else ""
                        question.module_id = str(getattr(chunk, "module_id", "") or "")
                        question.learning_point_id = point_id
                        question.lesson_page_id = (
                            getattr(chunk, "lesson_page_by_learning_point", {}) or {}
                        ).get(point_id, "")
                    if chunk.source_type == "web":
                        question.provenance_class = ProvenanceClass.EXTERNAL
                        question.source_url = chunk.source_url
                        question.source_fetched_at = chunk.fetched_at
                    seen_ids.add(question.question_id)
                    produced.append(question)
            if len(produced) >= count:
                break
        return produced


# --- grading ------------------------------------------------------------------


def normalise(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    # British/American spellings that appear constantly in policy text.
    for a, b in (("minimisation", "minimization"), ("organisation", "organization"),
                 ("authorisation", "authorization"), ("recognise", "recognize")):
        text = text.replace(a, b)
    return text.strip()


class LexicalJudge:
    """
    Offline free-text grading: exact match after normalisation, then a token-overlap
    fallback so "personal protective equipment" scores against "protective equipment
    personal". Conservative by design — it under-credits rather than over-credits,
    because wrongly passing someone on a compliance question is the worse error.
    """

    name = "lexical"

    def judge(self, prompt: str, accepted: List[str], answer: str) -> Tuple[bool, str]:
        given = normalise(answer)
        if not given:
            return False, "empty answer"

        for key in accepted:
            if given == normalise(key):
                return True, "exact match"

        given_tokens = set(given.split())
        for key in accepted:
            key_tokens = set(normalise(key).split())
            if not key_tokens:
                continue
            overlap = len(given_tokens & key_tokens) / len(key_tokens)
            if overlap >= 0.8:
                return True, "token overlap {:.0%}".format(overlap)

        return False, "no match against {} accepted answer(s)".format(len(accepted))
