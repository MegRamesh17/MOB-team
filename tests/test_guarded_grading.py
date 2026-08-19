from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from shared import guarded_grading as grading  # noqa: E402


RUBRIC = json.dumps({
    "criteria": [
        {
            "id": "correct_approach",
            "description": "Uses iteration to inspect each value",
            "accepted_evidence": ["for", "iteration"],
            "weight": 60,
            "required": True,
            "critical": False,
        },
        {
            "id": "returns_result",
            "description": "Returns the computed result",
            "accepted_evidence": ["return"],
            "weight": 40,
            "required": True,
            "critical": True,
        },
    ],
    "correct_threshold": 80,
    "syntax_tolerance": "minor_errors_allowed",
})


def decision(first_status="met", second_status="met", confidence=0.95, evidence=True):
    answer_evidence = {
        "correct_approach": "for value in values" if evidence else "invented loop",
        "returns_result": "return total" if evidence else "invented return",
    }
    return grading.ModelDecision.model_validate({
        "criteria": [
            {
                "id": "correct_approach", "status": first_status,
                "confidence": confidence,
                "evidence": answer_evidence["correct_approach"] if first_status == "met" else "",
            },
            {
                "id": "returns_result", "status": second_status,
                "confidence": confidence,
                "evidence": answer_evidence["returns_result"] if second_status == "met" else "",
            },
        ],
        "overall_confidence": confidence,
        "critical_error": False,
        "reason": "rubric comparison",
    })


class TestLockedRubric(unittest.TestCase):
    def test_weights_must_total_one_hundred(self):
        payload = json.loads(RUBRIC)
        payload["criteria"][0]["weight"] = 20
        with self.assertRaises(ValueError):
            grading.parse_rubric(json.dumps(payload))

    def test_backend_computes_correct_from_criteria(self):
        answer = "for value in values:\n    total += value\nreturn total"
        outcome = grading._validated_outcome(
            grading.parse_rubric(RUBRIC), decision(), answer, "gpt-5", {"valid": True})
        self.assertEqual(outcome.verdict, "correct")
        self.assertEqual(outcome.score, 100)

    def test_missing_required_criterion_is_incorrect(self):
        answer = "for value in values:\n    total += value"
        outcome = grading._validated_outcome(
            grading.parse_rubric(RUBRIC), decision(second_status="not_met"),
            answer, "gpt-5", {"valid": True})
        self.assertEqual(outcome.verdict, "incorrect")
        self.assertEqual(outcome.score, 60)

    def test_invented_evidence_invalidates_model_output(self):
        outcome = grading._validated_outcome(
            grading.parse_rubric(RUBRIC), decision(evidence=False),
            "for value in values:\n    return total", "gpt-5", {"valid": True})
        self.assertEqual(outcome.verdict, "system_error")

    def test_low_confidence_requests_fallback(self):
        answer = "for value in values:\n    return total"
        outcome = grading._validated_outcome(
            grading.parse_rubric(RUBRIC), decision(confidence=0.50),
            answer, "gpt-5", {"valid": True})
        self.assertEqual(outcome.verdict, "uncertain")


class TestFallbackDecision(unittest.TestCase):
    def test_two_uncertain_checks_produce_uncertain_outcome(self):
        original = grading._grade_once
        calls = []

        def unresolved(*args, **kwargs):
            calls.append(1)
            return grading.GradeOutcome(
                "uncertain", 60, 0.6, "ambiguous", [], "gpt-5")

        grading._grade_once = unresolved
        self.addCleanup(lambda: setattr(grading, "_grade_once", original))
        outcome = grading.grade_answer("Write Python", RUBRIC, "some answer", "PythonCode")
        self.assertEqual(outcome.verdict, "uncertain")
        self.assertEqual(len(calls), 2)

    def test_clear_incorrect_answer_does_not_get_easier_fallback(self):
        original = grading._grade_once
        calls = []

        def incorrect(*args, **kwargs):
            calls.append(1)
            return grading.GradeOutcome(
                "incorrect", 0, 0.98, "missing requirements", [], "gpt-5")

        grading._grade_once = incorrect
        self.addCleanup(lambda: setattr(grading, "_grade_once", original))
        outcome = grading.grade_answer("Write Python", RUBRIC, "I do not know", "PythonCode")
        self.assertEqual(outcome.verdict, "incorrect")
        self.assertEqual(len(calls), 1)

    def test_empty_answer_is_deterministically_incorrect(self):
        outcome = grading.grade_answer("Write Python", RUBRIC, "", "PythonCode")
        self.assertEqual(outcome.verdict, "incorrect")
        self.assertEqual(outcome.model, "deterministic")


if __name__ == "__main__":
    unittest.main()
