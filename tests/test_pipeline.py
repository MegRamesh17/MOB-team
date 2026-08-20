"""
Tests for the parts that break quietly.

stdlib unittest, no extra dependency:  python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quizgen.adaptive import _allocate, under_sampled_topics, weak_topics  # noqa: E402
from quizgen.bank import Bank  # noqa: E402
from quizgen.grading import grade_one, score_attempt  # noqa: E402
from quizgen.ingest import ingest_directory, looks_like_heading, split_sentences  # noqa: E402
from quizgen.llm.mock import LexicalJudge, MockGenerator  # noqa: E402
from quizgen.models import (  # noqa: E402
    Attempt, Chunk, Difficulty, Option, ProvenanceClass, Question, QuestionType,
    Response, ReviewStatus, TopicMastery,
)
from quizgen.validators import check_role_knowledge_voice, validate  # noqa: E402

PDF_DIR = Path(__file__).resolve().parents[1] / "data" / "documents"


def sample_chunk(topic="Fire Safety", text=None):
    return Chunk(
        chunk_id="chunk_test1", doc_id="doc_1", doc_title="Test Doc", topic=topic,
        section=topic, page_start=1, page_end=1,
        text=text or ("PPE stands for personal protective equipment. "
                      "Employees must never use a lift during a fire evacuation. "
                      "Reports must be filed within 24 hours of the incident."),
    )


class TestIngest(unittest.TestCase):
    def test_heading_detection(self):
        self.assertTrue(looks_like_heading("Fire Safety and Evacuation"))
        self.assertTrue(looks_like_heading("INCIDENT REPORTING"))
        self.assertFalse(looks_like_heading(
            "Employees must report all hazards through the safety system on the same day."))
        self.assertFalse(looks_like_heading("Purpose and Scope:"))

    def test_abbreviations_do_not_split_sentences(self):
        # Regression: the abbreviation sentinel was once written as a literal NUL byte,
        # which made the module unimportable.
        out = split_sentences("Use PPE, e.g. gloves and goggles. Then report it.")
        self.assertEqual(len(out), 2)
        self.assertIn("e.g.", out[0])

    @unittest.skipUnless(
        any(PDF_DIR.glob("*.pdf")) or any(PDF_DIR.glob("*.txt")),
        "local-source test: put a document in data/documents to run it",
    )
    def test_local_documents_produce_topic_tagged_chunks(self):
        chunks = ingest_directory(PDF_DIR)
        self.assertGreater(len(chunks), 5)
        # Regression: collapsing newlines during cleaning put every chunk in one
        # nameless section, which silently destroyed all topic targeting.
        topics = {c.topic for c in chunks}
        self.assertGreater(len(topics), 3, "section headings were not detected")
        self.assertNotIn("Introduction", topics)
        for c in chunks:
            self.assertTrue(c.text.strip())
            self.assertGreaterEqual(c.page_start, 1)


class TestMockGenerator(unittest.TestCase):
    def setUp(self):
        self.chunks = [sample_chunk(), sample_chunk("Data Privacy",
            "Personal data means any information relating to an identifiable person. "
            "A breach must be reported within 72 hours of discovery.")]
        self.gen = MockGenerator(self.chunks, seed=1)

    def test_every_question_is_answerable(self):
        """A question with no correct answer grades everyone to zero."""
        for chunk in self.chunks:
            for q in self.gen.generate(chunk, count=4):
                if q.question_type == QuestionType.FILL_IN_BLANK:
                    self.assertTrue(q.accepted_answers, q.prompt)
                else:
                    self.assertGreaterEqual(len(q.options), 2, q.prompt)
                    self.assertEqual(len(q.correct_option_ids()), 1, q.prompt)

    def test_questions_carry_provenance(self):
        for q in self.gen.generate(self.chunks[0], count=4):
            self.assertTrue(q.source_chunk_id)
            self.assertTrue(q.source_quote)

    def test_generation_is_deterministic(self):
        a = [q.question_id for q in MockGenerator(self.chunks, seed=5).generate(self.chunks[0], 4)]
        b = [q.question_id for q in MockGenerator(self.chunks, seed=5).generate(self.chunks[0], 4)]
        self.assertEqual(a, b)


class TestGrading(unittest.TestCase):
    def mcq(self):
        return Question(
            question_id="q1", topic="T", question_type=QuestionType.MULTIPLE_CHOICE,
            difficulty=Difficulty.MEDIUM, prompt="?",
            options=[Option("o1", "right", True), Option("o2", "wrong", False)],
        )

    def test_mcq(self):
        q = self.mcq()
        self.assertTrue(grade_one(q, ["o1"], "").is_correct)
        self.assertFalse(grade_one(q, ["o2"], "").is_correct)
        self.assertFalse(grade_one(q, [], "").is_correct)

    def test_multi_select_is_all_or_nothing(self):
        q = Question(
            question_id="q2", topic="T", question_type=QuestionType.MULTI_SELECT,
            difficulty=Difficulty.MEDIUM, prompt="?",
            options=[Option("a", "1", True), Option("b", "2", True), Option("c", "3", False)],
        )
        self.assertTrue(grade_one(q, ["a", "b"], "").is_correct)
        self.assertFalse(grade_one(q, ["a"], "").is_correct, "partial credit would let a learner pass while still wrong")

    def test_fill_in_blank_tolerates_wording(self):
        judge = LexicalJudge()
        accepted = ["personal protective equipment"]
        self.assertTrue(judge.judge("?", accepted, "Personal Protective Equipment")[0])
        self.assertTrue(judge.judge("?", accepted, "protective equipment personal")[0])
        self.assertFalse(judge.judge("?", accepted, "a hard hat")[0])
        self.assertFalse(judge.judge("?", accepted, "")[0])

    def test_scoring_is_deterministic_and_uses_the_pass_bar(self):
        qs = [self.mcq() for _ in range(10)]
        for i, q in enumerate(qs):
            q.question_id = "q{}".format(i)
        responses = [grade_one(q, ["o1"], "") for q in qs[:8]] + \
                    [grade_one(q, ["o2"], "") for q in qs[8:]]
        a = score_attempt("L", "att1", qs, responses, "2026-01-01T00:00:00+00:00")
        self.assertEqual(a.score_percent, 80.0)
        self.assertTrue(a.passed, "80% must pass at an 80% bar")


class TestAdaptive(unittest.TestCase):
    def test_weakness_needs_evidence(self):
        """One wrong answer is noise, not a weakness."""
        mastery = {
            "Thin": TopicMastery("Thin", answered=2, correct=0),
            "Solid": TopicMastery("Solid", answered=10, correct=4),
        }
        weak = [m.topic for m in weak_topics(mastery)]
        self.assertIn("Solid", weak)
        self.assertNotIn("Thin", weak)

    def test_under_sampled_topics_are_surfaced(self):
        """Regression: a failing but under-sampled topic used to be crowded out forever."""
        mastery = {"Thin": TopicMastery("Thin", answered=2, correct=1)}
        under = under_sampled_topics(mastery, ["Thin", "Unseen", "Solid"])
        self.assertIn("Thin", under)
        self.assertIn("Unseen", under)

    def test_never_seen_topics_outrank_partially_seen_ones(self):
        """
        Regression: unseen topics defaulted to accuracy 1.0 and sorted last, so with
        more topics than quiz slots some were never served even once.
        """
        mastery = {
            "Seen2": TopicMastery("Seen2", answered=2, correct=1),
            "Seen1": TopicMastery("Seen1", answered=1, correct=1),
        }
        under = under_sampled_topics(mastery, ["Seen2", "Seen1", "NeverSeen"])
        self.assertEqual(under[0], "NeverSeen", "a total blind spot must be sampled first")
        self.assertEqual(under[-1], "Seen2")

    def test_baseline_rotates_coverage_when_topics_exceed_slots(self):
        """
        Regression: the baseline path allocated one slot each in alphabetical order and
        took the first N, so with 14 topics and 8 slots the tail of the alphabet was
        never served. 'Phishing And Social Engineering' got zero questions in six
        rounds. Ordering by evidence need rotates coverage instead.
        """
        topics = ["A-topic", "B-topic", "C-topic", "Z-topic"]
        # A and B already sampled; C and Z never seen.
        under = ["Z-topic", "C-topic"]
        plans = _allocate([], under, topics, total=2, weak_share=0.7)
        chosen = {p.topic for p in plans}
        self.assertEqual(chosen, {"Z-topic", "C-topic"},
                         "never-seen topics must be served before already-sampled ones")

    def test_under_sampled_topics_get_slots_alongside_weak_ones(self):
        weak = [TopicMastery("Weak", answered=10, correct=3)]
        plans = _allocate(weak, ["Thin"], ["Weak", "Thin", "Other"], total=8, weak_share=0.7)
        by_topic = {p.topic: p.slots for p in plans}
        self.assertGreater(by_topic.get("Weak", 0), 0)
        self.assertGreater(by_topic.get("Thin", 0), 0, "evidence gathering must not be starved")
        self.assertLessEqual(sum(by_topic.values()), 9)


class TestConservativeGuard(unittest.TestCase):
    """
    The rule: a company-specific claim needs a source; general professional knowledge
    does not, but must not be dressed up as company policy.
    """

    def role_q(self, prompt, explanation=""):
        return Question(
            question_id="q", topic="Incident Response",
            question_type=QuestionType.MULTIPLE_CHOICE, difficulty=Difficulty.MEDIUM,
            prompt=prompt, explanation=explanation,
            options=[Option("a", "yes", True), Option("b", "no", False)],
            provenance_class=ProvenanceClass.ROLE_KNOWLEDGE, role_code="SEC_ANALYST",
        )

    def test_invented_company_policy_is_blocked(self):
        for prompt in (
            "According to company policy, incidents must be escalated within 4 hours.",
            "The handbook requires dual approval for evidence release.",
            "Our procedure mandates immediate isolation of the host.",
        ):
            self.assertIsNotNone(check_role_knowledge_voice(self.role_q(prompt)), prompt)

    def test_invented_numeric_obligation_is_blocked(self):
        q = self.role_q("An analyst must preserve evidence for 90 days before disposal.")
        reason = check_role_knowledge_voice(q)
        self.assertIsNotNone(reason)
        self.assertIn("numeric obligation", reason)

    def test_genuine_professional_judgment_is_allowed(self):
        """The whole point: role knowledge the documents never state must get through."""
        for prompt in (
            "An analyst sees a colleague's account sending traffic at 2am. What is the "
            "appropriate first action?",
            "Why is preserving volatile memory a priority during incident triage?",
        ):
            self.assertIsNone(check_role_knowledge_voice(self.role_q(prompt)), prompt)

    def test_documented_questions_may_state_company_rules(self):
        q = self.role_q("According to the policy, incidents must be reported within 1 hour.")
        q.provenance_class = ProvenanceClass.DOCUMENTED
        q.source_quote = "Suspected incidents must be reported within one hour of discovery."
        src = "Suspected incidents must be reported within one hour of discovery."
        ok, _ = validate(q, src)
        self.assertTrue(ok, "a sourced claim is exactly what Documented is for")

    def test_documented_question_must_actually_quote_its_source(self):
        q = self.role_q("Anything")
        q.provenance_class = ProvenanceClass.DOCUMENTED
        q.source_quote = "A sentence that does not appear in the passage."
        ok, notes = validate(q, "Completely unrelated passage text.")
        self.assertFalse(ok)
        self.assertIn("verbatim", notes[0])


class TestContradictionCheck(unittest.TestCase):
    def q_with(self, prompt):
        return Question(
            question_id="q", topic="Breach", question_type=QuestionType.MULTIPLE_CHOICE,
            difficulty=Difficulty.MEDIUM, prompt=prompt,
            options=[Option("a", "yes", True), Option("b", "no", False)],
            source_quote=prompt, provenance_class=ProvenanceClass.DOCUMENTED,
        )

    def chunk_with(self, text):
        return Chunk("c2", "d2", "Other Policy", "S", "S", 2, 2, text)

    def test_conflict_across_different_units_is_caught(self):
        """Regression: matching on unit alone missed 30 days vs 72 hours entirely."""
        q = self.q_with("A breach must be reported to the authority within 30 days.")
        other = [self.chunk_with(
            "It must be reported to the supervisory authority within 72 hours.")]
        _, notes = validate(q, q.prompt, other)
        self.assertTrue(notes, "a deadline restated in another unit is the common conflict")

    def test_unrelated_durations_are_not_flagged(self):
        """
        Regression: same-dimension-different-magnitude flagged nearly everything.
        Training renewal and breach reporting are both durations and unrelated.
        """
        q = self.q_with("A breach must be reported within 72 hours.")
        other = [self.chunk_with("Training must be renewed every 12 months.")]
        _, notes = validate(q, q.prompt, other)
        self.assertEqual(notes, [], "unrelated rules must not generate review noise")


class TestBank(unittest.TestCase):
    def test_review_decisions_survive_regeneration(self):
        """Re-running generate must not silently un-reject a question a human rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            bank = Bank(Path(tmp) / "t.db")
            q = Question(
                question_id="q1", topic="T", question_type=QuestionType.TRUE_FALSE,
                difficulty=Difficulty.EASY, prompt="?",
                options=[Option("a", "True", True), Option("b", "False", False)],
            )
            bank.save_questions([q])
            bank.set_review_status(["q1"], ReviewStatus.REJECTED)
            bank.save_questions([q])  # regenerate
            self.assertEqual(bank.get_question("q1").review_status, ReviewStatus.REJECTED)
            bank.close()

    def test_auto_approve_makes_questions_servable(self):
        """
        The team has no reviewer, so QUIZGEN_AUTO_APPROVE defaults to true and generated
        questions go straight to Approved. The mechanical checks in validators.py are
        then the only thing between a generated question and a learner.
        """
        from quizgen import config

        with tempfile.TemporaryDirectory() as tmp:
            bank = Bank(Path(tmp) / "t.db")
            gen = MockGenerator([sample_chunk()], seed=2)
            config.CONFIG.auto_approve = True
            bank.save_questions(gen.generate(sample_chunk(), count=3))
            self.assertGreater(len(bank.questions(status=ReviewStatus.APPROVED)), 0)
            self.assertEqual(bank.questions(status=ReviewStatus.PENDING), [])
            bank.close()

    def test_review_gate_can_be_reinstated(self):
        """Setting auto_approve false restores the hold-for-review behaviour."""
        from quizgen import config

        with tempfile.TemporaryDirectory() as tmp:
            bank = Bank(Path(tmp) / "t.db")
            gen = MockGenerator([sample_chunk()], seed=2)
            config.CONFIG.auto_approve = False
            try:
                bank.save_questions(gen.generate(sample_chunk(), count=3))
                self.assertGreater(len(bank.questions(status=ReviewStatus.PENDING)), 0)
                self.assertEqual(bank.questions(status=ReviewStatus.APPROVED), [])
            finally:
                config.CONFIG.auto_approve = True
            bank.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestRoleScoping(unittest.TestCase):
    """
    A source approved for a subset of roles must not reach another role.

    This was enforced at the search index but nowhere else: build_quiz had no role
    parameter at all, so the bank happily served SDE-only questions to a Director.
    scope_matches existed for exactly this and was never called.
    """

    def _bank_with_roles(self, tmp):
        from quizgen import config

        config.CONFIG.auto_approve = True
        bank = Bank(Path(tmp) / "roles.db")
        bank.save_questions([
            Question(
                question_id="q_sde", topic="Git", question_type=QuestionType.TRUE_FALSE,
                difficulty=Difficulty.EASY, prompt="SDE only?",
                options=[Option("a", "True", True), Option("b", "False", False)],
                role_code="SDE1,SDE2,SDE3", source_doc_title="Git Workflows",
            ),
            Question(
                question_id="q_all", topic="Security", question_type=QuestionType.TRUE_FALSE,
                difficulty=Difficulty.EASY, prompt="Everyone?",
                options=[Option("c", "True", True), Option("d", "False", False)],
                role_code="", source_doc_title="Security Basics",
            ),
        ])
        return bank

    def test_scope_matches_honours_multi_role_scopes(self):
        from quizgen.adaptive import scope_matches

        self.assertTrue(scope_matches("SDE1,SDE2,SDE3", "SDE2"))
        self.assertFalse(scope_matches("SDE1,SDE2,SDE3", "SWE_DIRECTOR"))
        self.assertTrue(scope_matches("ALL", "SWE_DIRECTOR"))
        # No scope recorded means unrestricted, not restricted.
        self.assertTrue(scope_matches("", "SWE_DIRECTOR"))

    def test_director_never_receives_sde_only_questions(self):
        from quizgen.adaptive import build_quiz

        with tempfile.TemporaryDirectory() as tmp:
            bank = self._bank_with_roles(tmp)
            plan = build_quiz(bank, "d1", length=5, role="SWE_DIRECTOR")
            served = {q.question_id for q in plan.questions}
            self.assertNotIn("q_sde", served)
            self.assertIn("q_all", served)
            bank.close()

    def test_sde_receives_both_scoped_and_universal(self):
        from quizgen.adaptive import build_quiz

        with tempfile.TemporaryDirectory() as tmp:
            bank = self._bank_with_roles(tmp)
            plan = build_quiz(bank, "s1", length=5, role="SDE2")
            served = {q.question_id for q in plan.questions}
            self.assertEqual(served, {"q_sde", "q_all"})
            bank.close()

    def test_empty_scope_refuses_rather_than_widening(self):
        """
        Falling back to the whole bank when a role has no questions of its own would
        serve exactly the material the scope exists to withhold — and would look like
        it worked. It must fail loudly instead.
        """
        from quizgen.adaptive import build_quiz

        with tempfile.TemporaryDirectory() as tmp:
            from quizgen import config

            config.CONFIG.auto_approve = True
            bank = Bank(Path(tmp) / "narrow.db")
            bank.save_questions([Question(
                question_id="q_sde", topic="Git", question_type=QuestionType.TRUE_FALSE,
                difficulty=Difficulty.EASY, prompt="SDE only?",
                options=[Option("a", "True", True), Option("b", "False", False)],
                role_code="SDE1", source_doc_title="Git",
            )])
            with self.assertRaises(RuntimeError):
                build_quiz(bank, "d1", length=5, role="SWE_DIRECTOR")
            bank.close()


class TestMasteryGrain(unittest.TestCase):
    """
    Mastery has to be measured on a grain coarse enough to accumulate evidence.

    Measured on the real bank: 235 questions across 112 section-level topics is 2.1
    questions per topic against an evidence floor of 3 answers, so most topics could
    never be judged weak and adaptive targeting never engaged over six rounds. Grouping
    by source document gives 32-44 questions per subject.
    """

    def _bank(self, tmp):
        from quizgen import config

        config.CONFIG.auto_approve = True
        bank = Bank(Path(tmp) / "grain.db")
        questions = []
        for i in range(4):
            questions.append(Question(
                question_id="q{}".format(i),
                topic="Section {}".format(i),          # a different topic each
                source_doc_title="Handbook",           # but the same document
                question_type=QuestionType.TRUE_FALSE,
                difficulty=Difficulty.EASY, prompt="q{}?".format(i),
                options=[Option("a{}".format(i), "True", True),
                         Option("b{}".format(i), "False", False)],
            ))
        bank.save_questions(questions)

        responses = [Response(
            response_id="r{}".format(i), attempt_id="a1", learner_id="L",
            question_id="q{}".format(i), topic="Section {}".format(i),
            is_correct=False, points_awarded=0,
        ) for i in range(4)]
        bank.save_attempt(Attempt(
            attempt_id="a1", learner_id="L", started_at="2026-01-01T00:00:00+00:00",
            submitted_at="2026-01-01T00:10:00+00:00", score_percent=0.0,
            points_awarded=0, points_possible=4, passed=False, responses=responses,
        ))
        return bank

    def test_topic_grain_cannot_reach_the_evidence_floor(self):
        """Four answers spread over four sections is one answer each — never judgeable."""
        with tempfile.TemporaryDirectory() as tmp:
            bank = self._bank(tmp)
            mastery = bank.mastery("L", grain="topic")
            self.assertEqual(len(mastery), 4)
            self.assertTrue(all(m.answered == 1 for m in mastery.values()))
            self.assertEqual(weak_topics(mastery), [])
            bank.close()

    def test_subject_grain_accumulates_enough_evidence(self):
        """The same four answers, grouped by document, are judgeable immediately."""
        with tempfile.TemporaryDirectory() as tmp:
            bank = self._bank(tmp)
            mastery = bank.mastery("L", grain="subject")
            self.assertEqual(set(mastery), {"Handbook"})
            self.assertEqual(mastery["Handbook"].answered, 4)
            self.assertEqual([m.topic for m in weak_topics(mastery)], ["Handbook"])
            bank.close()


class TestMultipartParsing(unittest.TestCase):
    """
    The upload parser is hand-rolled — the stdlib cgi module was removed in 3.13 —
    so it carries its own tests. Binary integrity and the filename are the two things
    that must not be got wrong: a corrupted byte range makes a valid PDF unreadable,
    and an unsanitised filename is a path traversal.
    """

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import importlib
        self.dev = importlib.import_module("devserver")

    def _body(self, filename, content, boundary="----test"):
        return (
            "--{}\r\n"
            'Content-Disposition: form-data; name="file"; filename="{}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).format(boundary, filename).encode() + content + "\r\n--{}--\r\n".format(boundary).encode()

    def test_extracts_filename_and_exact_bytes(self):
        payload = b"%PDF-1.4\x00\x01\x02binary\xff\xfe content"
        parts = self.dev._parse_multipart(self._body("policy.pdf", payload), "----test")
        self.assertEqual(parts["filename"], "policy.pdf")
        # Exact equality, not "starts with": an off-by-one on the trailing CRLF
        # corrupts every uploaded PDF in a way that only shows up at extraction.
        self.assertEqual(parts["content"], payload)

    def test_binary_containing_the_boundary_text_is_not_truncated(self):
        """A file whose bytes happen to contain the boundary string must survive."""
        payload = b"before ----test not-a-real-delimiter after"
        parts = self.dev._parse_multipart(self._body("x.pdf", payload, "unique-boundary-9271"),
                                          "unique-boundary-9271")
        self.assertEqual(parts["content"], payload)

    def test_missing_file_part_yields_nothing(self):
        body = (b"--b\r\nContent-Disposition: form-data; name=\"note\"\r\n\r\nhello\r\n--b--\r\n")
        self.assertEqual(self.dev._parse_multipart(body, "b"), {})

    def test_traversal_filename_is_reduced_to_a_basename(self):
        """
        The parser returns the name as sent; the handler is what must sanitise it.
        This pins the behaviour the handler relies on: Path(...).name strips the
        traversal, so an upload cannot write outside the documents directory.
        """
        parts = self.dev._parse_multipart(
            self._body("../../../../etc/passwd", b"data"), "----test")
        self.assertEqual(parts["filename"], "../../../../etc/passwd")
        self.assertEqual(Path(parts["filename"]).name, "passwd")


class TestGenerationPipeline(unittest.TestCase):
    """The loop shared by the CLI and the upload endpoint."""

    def test_generates_and_stores_with_progress(self):
        from quizgen import config
        from quizgen.pipeline import generate_questions

        config.CONFIG.auto_approve = True
        with tempfile.TemporaryDirectory() as tmp:
            bank = Bank(Path(tmp) / "p.db")
            chunk = sample_chunk()
            bank.save_chunks([chunk])

            seen = []
            result = generate_questions(bank, [chunk], per_chunk=2,
                                        on_progress=lambda p: seen.append(p))
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0].total, 1)
            self.assertEqual(result.written, len(bank.questions()))
            bank.close()

    def test_demo_fast_requests_one_balanced_difficulty_batch(self):
        from quizgen import config
        from quizgen.pipeline import _generate_candidate_batch

        class BalancedGenerator:
            def __init__(self):
                self.calls = []

            def generate(self, chunk, count=2, difficulty=None):
                self.calls.append((count, difficulty))
                return [
                    SimpleNamespace(
                        difficulty=value,
                        question_type=QuestionType.MULTIPLE_CHOICE,
                    )
                    for value in (Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD)
                ]

        generator = BalancedGenerator()
        with patch.object(config.CONFIG, "demo_fast", True):
            produced = _generate_candidate_batch(
                generator, sample_chunk(), per_chunk=6, difficulty_ladder=True
            )

        self.assertEqual(generator.calls, [(18, None)])
        self.assertEqual(len(produced), 3)

    def test_already_generated_chunks_are_skipped(self):
        """Re-running must not re-pay for work already done."""
        from quizgen import config
        from quizgen.pipeline import generate_questions, select_chunks

        config.CONFIG.auto_approve = True
        with tempfile.TemporaryDirectory() as tmp:
            bank = Bank(Path(tmp) / "p.db")
            chunk = sample_chunk()
            bank.save_chunks([chunk])
            generate_questions(bank, [chunk], per_chunk=2)

            remaining, skipped = select_chunks(bank)
            self.assertEqual(remaining, [])
            self.assertEqual(skipped, 1)

            forced, _ = select_chunks(bank, regenerate=True)
            self.assertEqual(len(forced), 1)
            bank.close()

    def test_a_failing_chunk_does_not_abort_the_run(self):
        """One bad call must not lose the whole batch — the tokens are already spent."""
        from quizgen import config, pipeline

        config.CONFIG.auto_approve = True
        with tempfile.TemporaryDirectory() as tmp:
            bank = Bank(Path(tmp) / "p.db")
            chunks = [sample_chunk(), sample_chunk("chunk_two")]
            bank.save_chunks(chunks)

            class Flaky:
                name = "flaky"
                calls = 0

                def generate(self, chunk, count=2, difficulty=None):
                    Flaky.calls += 1
                    if Flaky.calls == 1:
                        raise RuntimeError("transient")
                    return MockGenerator(chunks, seed=1).generate(chunk, count=count)

            original = pipeline.get_generator
            pipeline.get_generator = lambda corpus: Flaky()
            try:
                result = pipeline.generate_questions(bank, chunks, per_chunk=2)
            finally:
                pipeline.get_generator = original

            self.assertEqual(len(result.failed), 1)
            # The second chunk still ran and was stored.
            self.assertGreater(result.written, 0)
            bank.close()


class TestRoleManagement(unittest.TestCase):
    """Roles are the manager's list; chunks and questions inherit from it."""

    def test_roles_crud_and_chunk_tagging(self):
        with tempfile.TemporaryDirectory() as tmp:
            bank = Bank(Path(tmp) / "r.db")
            bank.add_role("sales_manager", "Sales Manager", "desc")
            self.assertEqual(bank.roles()[0]["role_code"], "SALES_MANAGER")

            chunk = sample_chunk()
            chunk.doc_title = "Mixed Doc"
            bank.save_chunks([chunk])
            n = bank.set_chunk_roles("Mixed Doc", {chunk.topic: "sales_manager"})
            self.assertEqual(n, 1)
            self.assertEqual(bank.all_chunks()[0].role_scope, "SALES_MANAGER")

            self.assertEqual(bank.remove_role("SALES_MANAGER"), 1)
            self.assertEqual(bank.roles(), [])
            bank.close()

    def test_superseded_document_stops_serving_but_keeps_history(self):
        """An update retires the old module's questions without touching attempts."""
        from quizgen import config

        config.CONFIG.auto_approve = True
        with tempfile.TemporaryDirectory() as tmp:
            bank = Bank(Path(tmp) / "s.db")
            q = Question(
                question_id="old1", topic="T", question_type=QuestionType.TRUE_FALSE,
                difficulty=Difficulty.EASY, prompt="?", source_doc_title="Policy v1",
                options=[Option("a", "True", True), Option("b", "False", False)],
            )
            bank.save_questions([q])
            self.assertEqual(len(bank.questions(status=ReviewStatus.APPROVED)), 1)

            bank.retire_document_questions("Policy v1")
            self.assertEqual(bank.questions(status=ReviewStatus.APPROVED), [],
                             "retired questions must never be served again")
            # The question row still exists — history and citations survive.
            self.assertIsNotNone(bank.get_question("old1"))
            bank.close()

    def test_mock_generator_propagates_role_scope(self):
        """
        Regression: the mock generator dropped chunk.role_scope, producing questions
        with role_code='' — which scope_matches treats as unrestricted. Every question
        it made leaked to every role.
        """
        chunk = sample_chunk()
        chunk.role_scope = "SALES_MANAGER"
        for q in MockGenerator([chunk], seed=3).generate(chunk, count=4):
            self.assertEqual(q.role_code, "SALES_MANAGER", q.prompt)


class TestRenewalWindow(unittest.TestCase):
    def test_plus_one_year(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import importlib
        dev = importlib.import_module("devserver")
        self.assertTrue(dev._plus_one_year("2026-08-14T10:00:00+00:00")
                        .startswith("2027-08-14"))
        # Feb 29 has no anniversary in a non-leap year; clamp, don't crash.
        self.assertTrue(dev._plus_one_year("2024-02-29T10:00:00+00:00")
                        .startswith("2025-02-28"))
        self.assertEqual(dev._plus_one_year("garbage"), "")


class TestLetterheadTitles(unittest.TestCase):
    """
    Document titles must distinguish documents.

    A real pack of 16 role briefs each began "LatticePeak Systems | Internal Use
    Only", so every one derived the SAME title and merged into a single training —
    sixteen roles in one module with sections from different roles side by side, and
    role tagging writing to the wrong rows.
    """

    def test_letterhead_lines_are_not_titles(self):
        from quizgen.ingest import _looks_like_letterhead

        self.assertTrue(_looks_like_letterhead("LatticePeak Systems | Internal Use Only"))
        self.assertTrue(_looks_like_letterhead("Role Operations Brief | August 2026"))
        self.assertTrue(_looks_like_letterhead("CONFIDENTIAL"))
        self.assertTrue(_looks_like_letterhead("Document"))
        self.assertFalse(_looks_like_letterhead("VP of Revenue Operations"))
        self.assertFalse(_looks_like_letterhead("Refund Authority Limits"))

    def test_title_skips_the_letterhead_and_finds_the_real_title(self):
        from quizgen.ingest import _title_from

        pages = ["LatticePeak Systems | Internal Use Only\n"
                 "Role Operations Brief | August 2026\n"
                 "VP of Revenue Operations\n"
                 "Focused operating area: Pipeline Coverage\n"]
        self.assertEqual(_title_from(Path("VP_of_Revenue_Operations.pdf"), pages),
                         "VP of Revenue Operations")

    def test_two_briefs_with_the_same_letterhead_get_different_titles(self):
        from quizgen.ingest import _title_from

        head = "LatticePeak Systems | Internal Use Only\nRole Brief | August 2026\n"
        a = _title_from(Path("a.pdf"), [head + "Sales Manager\nOwns the pipeline.\n"])
        b = _title_from(Path("b.pdf"), [head + "Cloud DevOps Engineer\nOwns infra.\n"])
        self.assertNotEqual(a, b, "same-letterhead documents must not merge")

    def test_falls_back_to_the_filename_when_everything_is_letterhead(self):
        from quizgen.ingest import _title_from

        title = _title_from(Path("Sales_Manager_Role_Brief.pdf"),
                            ["ACME Corp | Confidential\nCONFIDENTIAL\n"])
        self.assertEqual(title, "Sales Manager Role Brief")


class TestConcurrentAccess(unittest.TestCase):
    def test_a_long_write_does_not_fail_a_concurrent_write(self):
        """
        Regression: generation writes after every chunk while learners take quizzes on
        the same database. Measured, a write held past the 5s default busy timeout made
        a learner's quiz submit fail with "database is locked" — losing a finished
        attempt. WAL plus a 30s busy timeout fixes it.
        """
        import threading
        import time

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.db"
            Bank(path).close()
            errors = []

            def long_writer():
                b = Bank(path)
                b.conn.execute("BEGIN IMMEDIATE")
                b.conn.execute("INSERT OR REPLACE INTO roles VALUES ('X','X','','now')")
                time.sleep(6)
                b.conn.commit()
                b.close()

            def concurrent_writer():
                time.sleep(0.3)
                try:
                    b = Bank(path)
                    b.conn.execute("INSERT OR REPLACE INTO roles VALUES ('Y','Y','','now')")
                    b.conn.commit()
                    b.close()
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=long_writer),
                       threading.Thread(target=concurrent_writer)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [], "a concurrent write must wait, not fail")
