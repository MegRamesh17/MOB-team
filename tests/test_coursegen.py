from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quizgen.config import CONFIG  # noqa: E402
from quizgen.coursegen import (  # noqa: E402
    Citation, Evidence, LearningPoint, LessonPage, ModuleDraft, assessment_chunks,
    _needs_web_enrichment, build_instructional_course, validate_module,
)
from quizgen.models import Chunk  # noqa: E402


def source_chunk(topic: str, text: str, index: int = 1) -> Chunk:
    return Chunk(
        chunk_id="chunk_{}_{}".format(topic, index), doc_id="doc_course",
        doc_title="Reliable Prompting", topic=topic, section=topic,
        page_start=index, page_end=index, text=text, container="trusted-site",
        role_scope="SWE", company_id="7", source_type="web",
        source_url="https://docs.example.com/{}".format(topic.lower()),
        fetched_at="2026-08-19T00:00:00+00:00",
    )


class TestCourseGeneration(unittest.TestCase):
    def test_demo_fast_keeps_grounding_and_company_policy_guardrails(self):
        point = LearningPoint("lp_1", 1, "A focused change is easier to review.")
        page = LessonPage(
            page_id="page_1", order=1, title="Focused changes", page_type="concept",
            body="A focused change is easier to review and reverse when an issue appears.",
            learning_point_ids=[point.learning_point_id],
        )
        module = ModuleDraft(
            module_id="mod_1", doc_id="doc_1", doc_title="Course", topic="Delivery",
            heading="Delivery", source_order=1, source_topics=["Delivery"],
            generation_id="gen_1", learning_points=[point], pages=[page],
        )
        with (
            patch.object(CONFIG, "demo_fast", True),
            patch.object(CONFIG, "course_min_pages", 1),
            patch.object(CONFIG, "course_min_learning_points", 1),
            patch.object(CONFIG, "course_min_words", 1),
        ):
            findings = validate_module(module)
            self.assertTrue(any("citation" in finding for finding in findings))

            module.pages[0].body = "Our company requires every change to ship immediately."
            findings = validate_module(module)

        self.assertTrue(any("unsupported company rule" in finding for finding in findings))

    def test_demo_fast_keeps_normal_lesson_quality_floor(self):
        point = LearningPoint("lp_1", 1, "A focused change is easier to review.")
        module = ModuleDraft(
            module_id="mod_1", doc_id="doc_1", doc_title="Course", topic="Delivery",
            heading="Delivery", source_order=1, source_topics=["Delivery"],
            generation_id="gen_1", learning_points=[point], pages=[LessonPage(
                page_id="page_1", order=1, title="Focused changes", page_type="concept",
                body="A focused change is easier to review.",
                learning_point_ids=[point.learning_point_id],
            )],
        )

        with (
            patch.object(CONFIG, "demo_fast", True),
            patch.object(CONFIG, "course_min_pages", 3),
            patch.object(CONFIG, "course_min_learning_points", 5),
            patch.object(CONFIG, "course_min_words", 600),
        ):
            findings = validate_module(module)

        self.assertIn("needs 3-8 lesson pages", findings)
        self.assertTrue(any("600 instructional words" in finding for finding in findings))
        self.assertTrue(any("5 assessable learning points" in finding for finding in findings))

    def test_demo_fast_preserves_topic_coverage_and_uses_bounded_authoring(self):
        sentence = (
            "A safe delivery practice defines the change, verifies an observable result, "
            "and records the evidence needed to review the outcome consistently."
        )
        chunks = [
            source_chunk(topic, "\n\n".join(
                "{} example {}: {}".format(topic, index, sentence)
                for index in range(35)
            ), order)
            for order, topic in enumerate(("Planning", "Testing", "Escalation"), 1)
        ]
        executor_settings = []

        class InlineExecutor:
            def __init__(self, max_workers):
                executor_settings.append(max_workers)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def map(self, function, values):
                return [function(value) for value in values]

        with (
            patch.object(CONFIG, "provider", "azure"),
            patch.object(CONFIG, "demo_fast", True),
            patch.object(CONFIG, "demo_fast_author_workers", 2),
            patch("quizgen.coursegen.ThreadPoolExecutor", InlineExecutor),
            patch("quizgen.coursegen._author_with_azure") as author,
            patch("quizgen.coursegen.validate_module", return_value=[]),
        ):
            course = build_instructional_course(chunks, 7)

        self.assertEqual([module.topic for module in course.modules], [
            "Planning", "Testing", "Escalation",
        ])
        self.assertEqual(executor_settings, [2])
        self.assertEqual(author.call_count, 3)
        self.assertTrue(all(module.status == "ready" for module in course.modules))

    def test_demo_fast_only_researches_sources_too_thin_for_a_full_lesson(self):
        substantial = Evidence(
            "ev_full", "company", "Guide", " ".join("practice" for _ in range(700)))
        thin = Evidence(
            "ev_thin", "company", "Memo", " ".join("practice" for _ in range(80)))

        with (
            patch.object(CONFIG, "demo_fast", True),
            patch.object(CONFIG, "course_min_words", 600),
        ):
            self.assertFalse(_needs_web_enrichment([substantial]))
            self.assertTrue(_needs_web_enrichment([thin]))

        with (
            patch.object(CONFIG, "demo_fast", False),
            patch.object(CONFIG, "course_min_words", 600),
        ):
            self.assertTrue(_needs_web_enrichment([substantial]))

    def test_thin_adjacent_topics_merge_before_module_authoring(self):
        sentence = (
            "A reliable prompt states the task, relevant context, expected constraints, "
            "and an observable output format so the result can be checked consistently."
        )
        first = "\n\n".join("Example {}: {}".format(i, sentence) for i in range(11))
        second = "\n\n".join(
            "Evaluation {}: {}".format(i, sentence.replace("reliable", "testable"))
            for i in range(11))
        third = "\n\n".join(
            "Iteration {}: {}".format(i, sentence.replace("reliable", "repeatable"))
            for i in range(11))
        with (
            patch.object(CONFIG, "provider", "mock"),
            patch.object(CONFIG, "course_min_words", 600),
            patch.object(CONFIG, "course_min_learning_points", 5),
            patch.object(CONFIG, "course_min_pages", 3),
        ):
            course = build_instructional_course([
                source_chunk("Foundations", first, 1),
                source_chunk("Evaluation", second, 2),
                source_chunk("Iteration", third, 3),
            ], 7)

        self.assertEqual(len(course.modules), 1)
        self.assertTrue(all(module.status == "ready" for module in course.modules))
        self.assertTrue(all(module.word_count >= 600 for module in course.modules))
        self.assertTrue(all(len(module.learning_points) >= 5 for module in course.modules))

    def test_two_sentence_source_is_withheld_instead_of_padded(self):
        text = (
            "Prompting gives an AI instructions for a task. "
            "Clear instructions can make the output easier to evaluate."
        )
        with patch.object(CONFIG, "provider", "mock"):
            course = build_instructional_course([source_chunk("Overview", text)], 7)
        self.assertEqual(course.ready_modules, [])
        self.assertEqual(course.modules[0].status, "insufficient")
        self.assertTrue(any("instructional words" in note for note in course.modules[0].quality_notes))

    def test_question_source_is_finalized_lesson_with_normalized_ids(self):
        text = "\n\n".join(
            "Evaluation example {} compares the generated output against a stated success criterion, records the observed result, and explains any meaningful gap before the next attempt."
            .format(i) for i in range(90)
        )
        with patch.object(CONFIG, "provider", "mock"):
            course = build_instructional_course([source_chunk("Evaluation", text)], 7)
        chunks = assessment_chunks(course, {"Evaluation": ["SDE1", "SDE2"]})
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].container, "generated-lessons")
        self.assertEqual(chunks[0].module_id, course.ready_modules[0].module_id)
        self.assertIn("[LEARNING_POINT lp_", chunks[0].text)

    def test_company_claim_requires_company_evidence(self):
        evidence = Evidence("ev_web", "web", "Public guide", "Teams should test outputs.")
        module = ModuleDraft(
            module_id="mod_1", doc_id="doc_1", doc_title="Course", topic="Testing",
            heading="Testing", source_order=1, source_topics=["Testing"],
            generation_id="gen_1", evidence=[evidence],
        )
        module.pages = [LessonPage(
            page_id="page_1", order=1, title="Testing", page_type="concept",
            body="Our company requires every output to be tested.",
            citations=[Citation("ev_web", "Teams should test outputs.")],
        )]
        findings = validate_module(module)
        self.assertTrue(any("unsupported company rule" in finding for finding in findings))

    def test_broad_web_claim_requires_official_or_independent_sources(self):
        quote = "A structured evaluation compares an output with an explicit criterion."
        evidence = Evidence(
            "ev_blog", "web", "Single blog", quote,
            url="https://blog.example.com/evaluation",
        )
        module = ModuleDraft(
            module_id="mod_1", doc_id="doc_1", doc_title="Course", topic="Testing",
            heading="Testing", source_order=1, source_topics=["Testing"],
            generation_id="gen_1", evidence=[evidence],
            learning_points=[LearningPoint(
                learning_point_id="lp_1", order=1, statement=quote,
                citations=[Citation("ev_blog", quote)],
            )],
        )
        findings = validate_module(module)
        self.assertTrue(any(
            "official source or two independent domains" in finding for finding in findings
        ))

    def test_regeneration_uses_new_module_ids_for_safe_staging(self):
        text = "\n\n".join(
            "Evaluation example {} compares an output with an explicit criterion and "
            "records the observed evidence before the next iteration begins."
            .format(i) for i in range(80)
        )
        with patch.object(CONFIG, "provider", "mock"):
            first = build_instructional_course([source_chunk("Evaluation", text)], 7)
            second = build_instructional_course([source_chunk("Evaluation", text)], 7)
        self.assertNotEqual(first.generation_id, second.generation_id)
        self.assertNotEqual(first.modules[0].module_id, second.modules[0].module_id)


if __name__ == "__main__":
    unittest.main()
