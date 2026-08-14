"""
Tests for the parts that break quietly.

stdlib unittest, no extra dependency:  python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quizgen.adaptive import _allocate, under_sampled_topics, weak_topics  # noqa: E402
from quizgen.bank import Bank  # noqa: E402
from quizgen.grading import grade_one, score_attempt  # noqa: E402
from quizgen.ingest import ingest_directory, looks_like_heading, split_sentences  # noqa: E402
from quizgen.llm.mock import LexicalJudge, MockGenerator  # noqa: E402
from quizgen.models import (  # noqa: E402
    Chunk, Difficulty, Option, ProvenanceClass, Question, QuestionType, ReviewStatus,
    TopicMastery,
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
