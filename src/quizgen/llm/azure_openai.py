"""
Azure OpenAI provider.

Verified against the shared Foundry resource: gpt-5 for chat, text-embedding-3-large
for embeddings. See _chat_kwargs for the gpt-5 parameter differences, which fail in
non-obvious ways if you assume 4.x behaviour.

Two generation modes, set by QUIZGEN_MODE:

  * **augmented** (default) — the passage is the topic, and the model teaches the
    subject from its own knowledge. Output is RoleKnowledge and may not state any
    company-specific rule.
  * **grounded** — every answer must quote the passage verbatim. Output is Documented
    and may state company policy, because a document says it.

What holds in BOTH modes, and is the only thing standing between a generated question
and a learner now that human review has been dropped:

  * exactly one correct answer, or the question is discarded
  * no fabricated company rule, deadline or threshold — enforced in validators.py
  * conflicts with other documents are flagged on the stored question
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..config import CONFIG
from ..models import Chunk, Difficulty, Option, Question, QuestionType, stable_id

_SYSTEM_GROUNDED = (
    "You write assessment questions for mandatory workplace compliance training. "
    "You are given ONE passage from a company policy document. "
    "Every question and every answer must be answerable from that passage alone. "
    "Never use outside knowledge. Never invent a policy, a number, or a deadline. "
    "If the passage does not support a good question, return fewer questions or none. "
    "Distractors must be plausible but clearly wrong to someone who has read the passage."
)

_SYSTEM_AUGMENTED = (
    "You write assessment questions for professional training at a technology company. "
    "You are given a passage from an internal training outline. Treat it as the TOPIC to "
    "examine, not as the limit of what you may ask. These outlines are bullet-point "
    "syllabi, not teaching material, so relying on them alone produces shallow "
    "recall questions.\n\n"
    "Draw on your own knowledge of the subject to write questions a competent "
    "practitioner should be able to answer. Prefer applied and situational questions "
    "over recall: what someone should DO in a situation, why one approach beats another, "
    "what a symptom indicates.\n\n"
    "ONE HARD RULE: never state, imply or invent a company-specific policy, rule, "
    "deadline, threshold, tool choice or internal procedure. You do not know this "
    "company's internal rules. Write about the profession, not about their handbook. "
    "Say 'a common practice is' or 'generally', never 'the company requires'.\n\n"
    "Distractors must be plausible to someone with partial knowledge and clearly wrong "
    "to someone competent."
)

_SCHEMA_HINT = """Return ONLY a JSON object of this exact shape:

{
  "questions": [
    {
      "type": "MultipleChoice" | "TrueFalse" | "FillInBlank" | "ShortAnswer" |
              "PromptResponse" | "PythonCode",
      "difficulty": "Easy" | "Medium" | "Hard",
      "prompt": "the question text",
      "options": [{"text": "...", "is_correct": true}],
      "accepted_answers": ["..."],
      "rubric": {
        "criteria": [{
          "id": "lowercase_identifier",
          "description": "what must be demonstrated",
          "accepted_evidence": ["concepts or approaches that satisfy this criterion"],
          "weight": 50,
          "required": true,
          "critical": false
        }],
        "correct_threshold": 80,
        "syntax_tolerance": "minor_errors_allowed" | "valid_syntax_required"
      },
      "fallback": {
        "prompt": "an equivalent multiple-choice check of the same objective",
        "options": [{"text": "...", "is_correct": true}]
      },
      "explanation": "why the answer is right, citing the passage",
      "source_quote": "the exact sentence from the passage that supports the answer",
      "learning_point_id": "the supplied lp_ id this assesses",
      "lesson_page_id": "the supplied page_ id that teaches that learning point"
    }
  ]
}

Rules:
- MultipleChoice: exactly 4 options, exactly 1 correct. Leave accepted_answers empty.
- TrueFalse: exactly 2 options, "True" and "False", exactly 1 correct.
- FillInBlank: options empty; accepted_answers lists every acceptable spelling.
- ShortAnswer: a response of one to three sentences. Options and accepted_answers empty.
- PromptResponse: asks the learner to write a compact AI prompt for the stated task.
- PythonCode: asks for a small Python snippet or function; never require code execution.
- Every ShortAnswer, PromptResponse and PythonCode item MUST include a rubric whose
  weights total 100 and a fallback with exactly 4 options and 1 correct answer.
- Keep at least half of each batch choice-based. Use PythonCode or PromptResponse only
  when that format genuinely fits the section; do not force it into policy recall.
- source_quote: in grounded mode it MUST appear verbatim in the passage. In augmented
  mode leave it empty when the answer comes from your own knowledge of the subject.
- When the passage contains tagged LEARNING_POINT and LESSON_PAGE ids, every question
  must include one supplied learning_point_id and its matching lesson_page_id. Spread
  the batch across the supplied learning points."""


def _client():
    from openai import AzureOpenAI

    # gpt-5 spends 20-30s reasoning per call, so the default per-request timeout is
    # marginal and a single hiccup killed a 45-minute run at chunk 67. Generous timeout
    # plus SDK-level retries for the transient 5xx/timeout cases.
    return AzureOpenAI(
        azure_endpoint=CONFIG.azure_openai_endpoint,
        api_key=CONFIG.azure_openai_key,
        api_version=CONFIG.azure_api_version,
        timeout=180.0,
        max_retries=4,
    )


def _is_reasoning_model(deployment: str) -> bool:
    """GPT-5 and the o-series bill and behave differently from the 4.x chat models."""
    name = (deployment or "").lower()
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


def _chat_kwargs(deployment: str, max_output_tokens: int, temperature: float) -> dict:
    """
    Build call kwargs for the deployed model.

    The shared Foundry resource has gpt-5 deployed, which differs from gpt-4.x in two
    ways that both fail confusingly:

      * it takes `max_completion_tokens`, not `max_tokens` (a 400 that reads like a
        malformed request)
      * it only accepts the default temperature; sending 0.3 is rejected outright

    It also spends reasoning tokens before emitting any content, so a budget sized for
    a 4.x model comes back with an empty string rather than an error. A 16-token budget
    produced 192 reasoning tokens and no output at all during setup — hence the floor.
    """
    if _is_reasoning_model(deployment):
        return {"max_completion_tokens": max(max_output_tokens, 2000)}
    return {"max_tokens": max_output_tokens, "temperature": temperature}


class AzureOpenAIGenerator:
    name = "azure-openai"

    def __init__(self) -> None:
        CONFIG.require_azure()
        self._client = _client()
        self._deployment = CONFIG.azure_chat_deployment

    def generate(
        self,
        chunk: Chunk,
        count: int = 2,
        difficulty: Optional[Difficulty] = None,
    ) -> List[Question]:
        balanced_demo = CONFIG.demo_fast and difficulty is None
        want = "" if difficulty is None else " Target difficulty: {}.".format(difficulty.value)
        if balanced_demo:
            want = (
                " Use an even Easy, Medium and Hard distribution, with at least one "
                "MultipleChoice question at every difficulty."
            )
        augmented = (
            CONFIG.generation_mode == "augmented"
            and chunk.container != "generated-lessons"
        )
        system = _SYSTEM_AUGMENTED if augmented else _SYSTEM_GROUNDED
        multiple_choice_minimum = max(1, count - 2)
        instruction = (
            "Write {} question(s) examining this topic. Go beyond what the passage "
            "literally states — test whether someone actually understands the subject.{}"
            if augmented else
            "Write {} question(s) from this passage.{}"
        ).format(count, want)
        instruction += (
            " Include at least {} standard MultipleChoice question(s); reserve no more "
            "than two items for AI-graded ShortAnswer, PromptResponse or PythonCode."
        ).format(multiple_choice_minimum)

        user = (
            "Document: {}\nSection: {}\nTopic: {}\nRole: {}\n\n"
            "PASSAGE:\n\"\"\"\n{}\n\"\"\"\n\n{}\n\n{}"
        ).format(chunk.doc_title, chunk.section, chunk.topic,
                 chunk.role_scope or "ALL", chunk.text, instruction, _SCHEMA_HINT)

        response = self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            # Temperature 0.3 where supported: near-deterministic, but enough variation
            # that regenerating a topic gives different questions. Ignored on gpt-5.
            # Six-question pathway batches may include locked rubrics and equivalent
            # fallbacks, which are substantially larger than the old two-question JSON.
            **_chat_kwargs(self._deployment, 8000, 0.3),
        )

        raw = response.choices[0].message.content or "{}"
        try:
            payload: Dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            return []

        out: List[Question] = []
        for item in payload.get("questions", [])[:count]:
            question = self._to_question(chunk, item)
            if question is not None:
                out.append(question)
        return out

    def _to_question(self, chunk: Chunk, item: Dict[str, Any]) -> Optional[Question]:
        try:
            qtype = QuestionType(item["type"])
            prompt = str(item["prompt"]).strip()
        except (KeyError, ValueError):
            return None
        if not prompt:
            return None

        quote = str(item.get("source_quote", "")).strip()

        # Derive the question id FIRST so option ids can hang off it. Building option
        # ids from a prompt prefix let two questions sharing that prefix collide: their
        # common correct answer produced one id, and INSERT OR REPLACE silently moved
        # the row between questions, leaving one with no correct answer at all.
        question_id = stable_id("q", chunk.chunk_id, prompt)

        augmented = (
            CONFIG.generation_mode == "augmented"
            and chunk.container != "generated-lessons"
        )

        # In grounded mode a quote that is not in the passage means the model drifted,
        # and the question is dropped. In augmented mode the model is expected to go
        # beyond the passage, so an unverifiable quote is simply discarded rather than
        # taking the question with it — a false citation is worse than no citation.
        #
        # Lesson chunks get the same tolerance regardless of mode: this passage is
        # gpt-5's OWN synthesized, Khan-Academy-style lesson prose (see coursegen.py's
        # assessment_chunks), not the original source document, so holding a second
        # gpt-5 call to an exact verbatim substring match against the first call's own
        # paraphrase is an unreasonably strict bar. In practice this was discarding
        # ~90% of generated questions with no rejection ever surfaced to the pipeline
        # (this check runs before validators.py's rejection bookkeeping even sees the
        # item) -- a course would finish "done" with 2-3 approved questions per module
        # against a 20-30 target, and every module would be silently withheld for
        # "assessment bank incomplete" with no visible cause.
        quote_required = not augmented and chunk.container != "generated-lessons"
        if quote and _normalise(quote) not in _normalise(chunk.text):
            if quote_required:
                return None
            quote = ""

        options: List[Option] = []
        for opt in item.get("options", []) or []:
            text = str(opt.get("text", "")).strip()
            if not text:
                continue
            options.append(
                Option(
                    option_id=stable_id("opt", question_id, text),
                    text=text,
                    is_correct=bool(opt.get("is_correct")),
                )
            )

        accepted = [str(a).strip() for a in (item.get("accepted_answers") or []) if str(a).strip()]

        ai_graded = qtype in (
            QuestionType.SHORT_ANSWER,
            QuestionType.PROMPT_RESPONSE,
            QuestionType.PYTHON_CODE,
        )
        rubric_json = ""
        fallback_json = ""
        if ai_graded:
            rubric = item.get("rubric") or {}
            fallback = item.get("fallback") or {}
            criteria = rubric.get("criteria") or []
            fallback_options = fallback.get("options") or []
            try:
                weight_total = sum(int(c.get("weight") or 0) for c in criteria)
            except (AttributeError, TypeError, ValueError):
                return None
            if (
                not criteria
                or weight_total != 100
                or any(not str(c.get("id") or "").strip() for c in criteria)
                or any(not str(c.get("description") or "").strip() for c in criteria)
                or len(fallback_options) != 4
                or sum(1 for option in fallback_options if option.get("is_correct")) != 1
                or not str(fallback.get("prompt") or "").strip()
            ):
                return None
            fallback_id = stable_id("fb", question_id)
            safe_options = []
            for option in fallback_options:
                text = str(option.get("text") or "").strip()
                if not text:
                    return None
                safe_options.append({
                    "optionId": stable_id("fbopt", fallback_id, text),
                    "text": text,
                    "isCorrect": bool(option.get("is_correct")),
                })
            rubric_json = json.dumps(rubric, separators=(",", ":"), sort_keys=True)
            fallback_json = json.dumps({
                "questionId": fallback_id,
                "prompt": str(fallback["prompt"]).strip(),
                "difficulty": item.get("difficulty", "Medium"),
                "options": safe_options,
            }, separators=(",", ":"), sort_keys=True)

        # Structural validation — a question with no correct answer grades everyone to
        # zero, and that is worth catching here rather than in front of a learner.
        if qtype == QuestionType.FILL_IN_BLANK:
            if not accepted:
                return None
        elif ai_graded:
            if options or accepted:
                return None
        else:
            correct = [o for o in options if o.is_correct]
            if len(options) < 2 or len(correct) != 1:
                return None

        try:
            level = Difficulty(item.get("difficulty", "Medium"))
        except ValueError:
            level = Difficulty.MEDIUM

        # Provenance follows the MODE, not whether a quote happened to match.
        #
        # In augmented mode the model was told to teach the subject from its own
        # knowledge, so the substance is its own even when it also quotes a bullet from
        # the passage. Marking such a question Documented would grant it permission to
        # state company policy — observed in testing: a distributed-systems question
        # about message queues and leader-follower replication came back Documented
        # because the model had quoted "Load balancing: distribute traffic across
        # servers" from the outline. The quote was real; the answer was not from it.
        from ..models import ProvenanceClass

        if augmented:
            provenance = ProvenanceClass.ROLE_KNOWLEDGE
        elif chunk.source_type == "web":
            provenance = ProvenanceClass.EXTERNAL
        else:
            provenance = ProvenanceClass.DOCUMENTED

        learning_point_id = str(item.get("learning_point_id") or "").strip()
        valid_point_ids = set(getattr(chunk, "learning_point_ids", []) or [])
        if valid_point_ids and learning_point_id not in valid_point_ids:
            return None
        page_by_point = getattr(chunk, "lesson_page_by_learning_point", {}) or {}
        lesson_page_id = str(item.get("lesson_page_id") or "").strip()
        expected_page_id = page_by_point.get(learning_point_id, "")
        if expected_page_id and lesson_page_id != expected_page_id:
            lesson_page_id = expected_page_id

        return Question(
            provenance_class=provenance,
            role_code=chunk.role_scope or "",
            question_id=question_id,
            topic=chunk.topic,
            question_type=qtype,
            difficulty=level,
            prompt=prompt,
            options=options,
            accepted_answers=accepted,
            rubric_json=rubric_json,
            fallback_json=fallback_json,
            grading_version="rubric-v1" if ai_graded else "",
            explanation=str(item.get("explanation", "")).strip(),
            source_chunk_id=chunk.chunk_id,
            source_doc_title=chunk.doc_title,
            source_page=chunk.page_start,
            source_quote=quote,
            source_url=chunk.source_url,
            source_fetched_at=chunk.fetched_at,
            generator=self.name,
            module_id=str(getattr(chunk, "module_id", "") or ""),
            lesson_page_id=lesson_page_id,
            learning_point_id=learning_point_id,
        )


class AzureOpenAIJudge:
    """
    Semantic grading for free-text answers only.

    Returns a boolean and a reason for ONE answer. It never sees the score, the pass
    bar, or the other questions.
    """

    name = "azure-openai-judge"

    def __init__(self) -> None:
        CONFIG.require_azure()
        self._client = _client()
        self._deployment = CONFIG.azure_chat_deployment

    def judge(self, prompt: str, accepted: List[str], answer: str) -> Tuple[bool, str]:
        if not answer.strip():
            return False, "empty answer"

        # Cheap path first: an exact match needs no model call.
        from .mock import LexicalJudge

        ok, reason = LexicalJudge().judge(prompt, accepted, answer)
        if ok:
            return True, reason

        user = (
            "Question: {}\n"
            "Accepted answers: {}\n"
            "Learner's answer: {}\n\n"
            "Does the learner's answer mean the same thing as any accepted answer? "
            "Ignore spelling, casing and word order. Do not accept an answer that is "
            "merely related but different in meaning.\n"
            'Reply with JSON only: {{"correct": true|false, "reason": "short"}}'
        ).format(prompt, json.dumps(accepted), json.dumps(answer))

        try:
            response = self._client.chat.completions.create(
                model=self._deployment,
                messages=[
                    {"role": "system", "content": "You grade short free-text answers strictly and consistently."},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                # temperature 0 where supported: grading must not vary between runs.
                **_chat_kwargs(self._deployment, 2000, 0.0),
            )
            payload = json.loads(response.choices[0].message.content or "{}")
            return bool(payload.get("correct")), str(payload.get("reason", ""))[:120]
        except Exception as exc:  # noqa: BLE001
            # Fail closed to the lexical verdict rather than crashing a learner's quiz.
            return ok, "judge unavailable ({}); lexical verdict used".format(type(exc).__name__)


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())
