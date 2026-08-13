"""
Azure OpenAI provider.

Verified against the shared Foundry resource: gpt-5 for chat, text-embedding-3-large
for embeddings. See _chat_kwargs for the gpt-5 parameter differences, which fail in
non-obvious ways if you assume 4.x behaviour.

Two properties are enforced regardless of what the model returns:

  * **Grounding.** The prompt supplies one passage and forbids outside knowledge. Any
    generated question whose answer is not traceable to that passage is dropped.
  * **Review.** Output is always ReviewStatus.PENDING. There is no code path that
    publishes a model-written question without a human approving it.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..config import CONFIG
from ..models import Chunk, Difficulty, Option, Question, QuestionType, stable_id

_SYSTEM = (
    "You write assessment questions for mandatory workplace compliance training. "
    "You are given ONE passage from a company policy document. "
    "Every question and every answer must be answerable from that passage alone. "
    "Never use outside knowledge. Never invent a policy, a number, or a deadline. "
    "If the passage does not support a good question, return fewer questions or none. "
    "Distractors must be plausible but clearly wrong to someone who has read the passage."
)

_SCHEMA_HINT = """Return ONLY a JSON object of this exact shape:

{
  "questions": [
    {
      "type": "MultipleChoice" | "TrueFalse" | "FillInBlank",
      "difficulty": "Easy" | "Medium" | "Hard",
      "prompt": "the question text",
      "options": [{"text": "...", "is_correct": true}],
      "accepted_answers": ["..."],
      "explanation": "why the answer is right, citing the passage",
      "source_quote": "the exact sentence from the passage that supports the answer"
    }
  ]
}

Rules:
- MultipleChoice: exactly 4 options, exactly 1 correct. Leave accepted_answers empty.
- TrueFalse: exactly 2 options, "True" and "False", exactly 1 correct.
- FillInBlank: options empty; accepted_answers lists every acceptable spelling.
- source_quote must appear verbatim in the passage."""


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
        want = "" if difficulty is None else " Target difficulty: {}.".format(difficulty.value)
        user = (
            "Document: {}\nSection: {}\nTopic: {}\n\n"
            "PASSAGE:\n\"\"\"\n{}\n\"\"\"\n\n"
            "Write {} question(s) from this passage.{}\n\n{}"
        ).format(chunk.doc_title, chunk.section, chunk.topic, chunk.text, count, want, _SCHEMA_HINT)

        response = self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            # Temperature 0.3 where supported: near-deterministic, but enough variation
            # that regenerating a topic gives different questions. Ignored on gpt-5.
            **_chat_kwargs(self._deployment, 4000, 0.3),
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

        # Grounding check. A quote that is not in the passage means the model drifted,
        # and a question we cannot trace to source is one we cannot defend.
        if quote and _normalise(quote) not in _normalise(chunk.text):
            return None

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

        # Structural validation — a question with no correct answer grades everyone to
        # zero, and that is worth catching here rather than in front of a learner.
        if qtype == QuestionType.FILL_IN_BLANK:
            if not accepted:
                return None
        else:
            correct = [o for o in options if o.is_correct]
            if len(options) < 2 or len(correct) != 1:
                return None

        try:
            level = Difficulty(item.get("difficulty", "Medium"))
        except ValueError:
            level = Difficulty.MEDIUM

        return Question(
            question_id=question_id,
            topic=chunk.topic,
            question_type=qtype,
            difficulty=level,
            prompt=prompt,
            options=options,
            accepted_answers=accepted,
            explanation=str(item.get("explanation", "")).strip(),
            source_chunk_id=chunk.chunk_id,
            source_doc_title=chunk.doc_title,
            source_page=chunk.page_start,
            source_quote=quote,
            generator=self.name,
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
