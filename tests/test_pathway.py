from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from shared.pathway import (  # noqa: E402
    AI_GRADED_TYPES,
    CHOICE_TYPES,
    MAX_FINAL_AI_GRADED,
    choose_adaptive_question,
    diagnostic_pathway,
    diagnostic_questions,
    final_questions,
    next_difficulty,
)


def modules():
    return [
        {"module_id": "m1", "topic": "Access", "source_order": 1},
        {"module_id": "m2", "topic": "Review", "source_order": 2},
        {"module_id": "m3", "topic": "Incidents", "source_order": 3},
    ]


def pool(per_difficulty=12):
    rows = []
    for module in modules():
        for difficulty in ("Easy", "Medium", "Hard"):
            for index in range(per_difficulty):
                rows.append({
                    "question_id": "{}-{}-{}".format(
                        module["module_id"], difficulty.lower(), index),
                    "topic": module["topic"],
                    "difficulty": difficulty,
                    "question_type": "MultipleChoice",
                    "times_served": index,
                })
    return rows


class TestDiagnostic(unittest.TestCase):
    def test_has_easy_medium_and_hard_for_every_module(self):
        selected, missing = diagnostic_questions(pool(), modules(), "learner")
        self.assertEqual(missing, [])
        self.assertEqual(len(selected), 9)
        for module in modules():
            difficulties = {
                question["difficulty"] for question in selected
                if question["module_id"] == module["module_id"]
            }
            self.assertEqual(difficulties, {"Easy", "Medium", "Hard"})
        self.assertTrue(all(q["question_type"] in CHOICE_TYPES for q in selected))

    def test_long_answers_are_never_selected_for_a_diagnostic(self):
        mixed = pool()
        for question in mixed:
            if question["question_id"].endswith("-0"):
                question["question_type"] = "ShortAnswer"
        selected, missing = diagnostic_questions(mixed, modules(), "learner")

        self.assertEqual(missing, [])
        self.assertTrue(all(q["question_type"] in CHOICE_TYPES for q in selected))

    def test_missing_difficulty_is_skipped_rather_than_blocking_the_diagnostic(self):
        thin = [q for q in pool() if not (
            q["topic"] == "Review" and q["difficulty"] == "Hard")]
        chosen, missing = diagnostic_questions(thin, modules(), "learner")
        self.assertEqual(missing, [])
        self.assertFalse(any(
            q["module_id"] == "m2" and q["difficulty"] == "Hard" for q in chosen))
        self.assertTrue(any(
            q["module_id"] == "m2" and q["difficulty"] == "Easy" for q in chosen))

    def test_diagnostic_changes_order_but_never_skips_content(self):
        scores = {
            "m1": {"correct": 3, "possible": 3},
            "m2": {"correct": 0, "possible": 3},
            "m3": {"correct": 2, "possible": 3},
        }
        order = diagnostic_pathway(modules(), scores)
        self.assertEqual(order, ["m2", "m3", "m1"])
        self.assertEqual(set(order), {"m1", "m2", "m3"})


class TestAdaptiveCheckpoint(unittest.TestCase):
    def test_difficulty_moves_one_level_and_stops_at_the_edges(self):
        self.assertEqual(next_difficulty("Medium", True), "Hard")
        self.assertEqual(next_difficulty("Medium", False), "Easy")
        self.assertEqual(next_difficulty("Hard", True), "Hard")
        self.assertEqual(next_difficulty("Easy", False), "Easy")

    def test_review_slot_prefers_a_prior_mistake_at_the_target_level(self):
        questions = [q for q in pool() if q["topic"] == "Access"]
        review = next(q for q in questions if q["difficulty"] == "Hard")
        selected = choose_adaptive_question(
            questions, "Hard", [], [review["question_id"]], [review["question_id"]],
            True, "attempt-3",
        )
        self.assertEqual(selected["question_id"], review["question_id"])

    def test_regular_slot_prefers_unseen_over_a_repeated_question(self):
        questions = [q for q in pool() if q["topic"] == "Access"]
        medium = [q for q in questions if q["difficulty"] == "Medium"]
        selected = choose_adaptive_question(
            questions, "Medium", [], [q["question_id"] for q in medium[:-1]], [],
            False, "attempt-4",
        )
        self.assertEqual(selected["question_id"], medium[-1]["question_id"])


class TestFairFinal(unittest.TestCase):
    def test_anchor_set_is_shared_and_variable_set_can_change(self):
        first, first_blueprint = final_questions(pool(), modules(), "learner-a")
        second, second_blueprint = final_questions(pool(), modules(), "learner-b")
        first_anchors = [q["question_id"] for q in first if q["purpose"] == "anchor"]
        second_anchors = [q["question_id"] for q in second if q["purpose"] == "anchor"]
        first_variable = [q["question_id"] for q in first if q["purpose"] == "variable"]
        second_variable = [q["question_id"] for q in second if q["purpose"] == "variable"]

        self.assertEqual(first_blueprint, second_blueprint)
        self.assertEqual(first_blueprint["total"], 25)
        self.assertEqual(first_anchors, second_anchors)
        self.assertNotEqual(first_variable, second_variable)
        self.assertEqual(len(first), 25)
        self.assertEqual({q["topic"] for q in first}, {"Access", "Review", "Incidents"})

    def test_personalized_module_order_does_not_change_shared_anchors(self):
        first, _ = final_questions(pool(), modules(), "learner-a")
        reordered, _ = final_questions(pool(), list(reversed(modules())), "learner-b")
        first_anchors = [q["question_id"] for q in first if q["purpose"] == "anchor"]
        reordered_anchors = [
            q["question_id"] for q in reordered if q["purpose"] == "anchor"
        ]

        self.assertEqual(first_anchors, reordered_anchors)

    def test_short_training_uses_a_shorter_but_still_substantial_final(self):
        one_module = modules()[:1]
        selected, blueprint = final_questions(pool(), one_module, "learner-a")
        self.assertEqual(blueprint["total"], 15)
        self.assertEqual(len(selected), 15)

    def test_long_final_answers_are_capped_and_placed_last(self):
        mixed = pool()
        for index, question in enumerate(mixed):
            if index % 2:
                question["question_type"] = "ShortAnswer"
        selected, _ = final_questions(mixed, modules(), "learner-a")
        long_flags = [q["question_type"] in AI_GRADED_TYPES for q in selected]

        self.assertLessEqual(sum(long_flags), MAX_FINAL_AI_GRADED)
        self.assertEqual(long_flags, sorted(long_flags))
        self.assertTrue(all(
            q["question_type"] not in AI_GRADED_TYPES
            for q in selected if q["purpose"] == "anchor"
        ))


if __name__ == "__main__":
    unittest.main()
