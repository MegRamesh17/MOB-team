"""
Q Score arithmetic, against the worked examples in docs/q-score.md.

That document is the definition and these are its examples turned into assertions, so a
change to the formula fails here rather than being noticed on a dashboard months later.

The property worth protecting most is the one a stored column cannot have: Q Score falls
when a certificate expires and NOBODY DOES ANYTHING. Every test that matters passes an
explicit `now` rather than relying on wall-clock time, so they mean the same thing in
2027 as today.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quizgen import qscore  # noqa: E402

NOW = datetime(2026, 8, 17, tzinfo=timezone.utc)
FUTURE = (NOW + timedelta(days=200)).isoformat()
PAST = (NOW - timedelta(days=10)).isoformat()


def reqs(n, category="technical"):
    return [{"doc_title": "T{}".format(i), "category": category} for i in range(n)]


def certs(n, score, expires=FUTURE, category="technical", issued="2026-01-01T00:00:00+00:00"):
    return [{"doc_title": "T{}".format(i), "attempt_score": score, "expires_at": expires,
             "issued_at": issued, "category": category} for i in range(n)]


class TestAttemptScore(unittest.TestCase):
    """100 x sum(weight of correct) / sum(weight of all)."""

    def test_all_correct_is_100_whatever_the_difficulties(self):
        self.assertEqual(qscore.attempt_score(
            [{"difficulty": "Easy", "correct": True},
             {"difficulty": "Hard", "correct": True}]), 100.0)

    def test_none_correct_is_zero(self):
        self.assertEqual(qscore.attempt_score(
            [{"difficulty": "Hard", "correct": False}]), 0.0)

    def test_a_hard_question_is_worth_more_than_an_easy_one(self):
        hard_only = qscore.attempt_score(
            [{"difficulty": "Easy", "correct": False}, {"difficulty": "Hard", "correct": True}])
        easy_only = qscore.attempt_score(
            [{"difficulty": "Easy", "correct": True}, {"difficulty": "Hard", "correct": False}])
        self.assertGreater(hard_only, easy_only)

    def test_weighting_applies_to_the_denominator_too(self):
        # The failure mode of the older approach: averaging the multiplier over CORRECT
        # answers only means getting fewer easy questions right raises your multiplier.
        # Weighting both sides cannot do that — missing anything can only lower the score.
        full = qscore.attempt_score([{"difficulty": d, "correct": True}
                                     for d in ("Easy", "Medium", "Hard")])
        dropped_easy = qscore.attempt_score(
            [{"difficulty": "Easy", "correct": False},
             {"difficulty": "Medium", "correct": True},
             {"difficulty": "Hard", "correct": True}])
        self.assertEqual(full, 100.0)
        self.assertLess(dropped_easy, full)

    def test_an_empty_attempt_scores_zero_rather_than_raising(self):
        self.assertEqual(qscore.attempt_score([]), 0.0)

    def test_an_unknown_difficulty_weighs_as_medium(self):
        self.assertEqual(
            qscore.attempt_score([{"difficulty": "Impossible", "correct": True}]), 100.0)


class TestWorkedExamples(unittest.TestCase):
    """The table in docs/q-score.md, with 7 required and an 80 pass mark."""

    def q(self, certificates, requirements=None):
        return qscore.standing(requirements or reqs(7), certificates, now=NOW)["overall"]

    def test_all_seven_at_the_pass_mark_scores_the_pass_mark(self):
        # The case that motivated dropping the 0.75 floor: doing everything asked, at the
        # minimum, must not read as a failing grade.
        self.assertAlmostEqual(self.q(certs(7, 80)).q_score, 80.0, places=1)

    def test_all_seven_averaging_95(self):
        self.assertAlmostEqual(self.q(certs(7, 95)).q_score, 95.0, places=1)

    def test_three_of_seven_averaging_95(self):
        self.assertAlmostEqual(self.q(certs(3, 95)).q_score, 40.7, places=1)

    def test_nothing_done_is_zero_however_good_you_are(self):
        self.assertEqual(self.q([]).q_score, 0.0)

    def test_all_seven_but_three_expired(self):
        held = certs(4, 95) + certs(7, 95, expires=PAST)[4:]
        standing = self.q(held)
        self.assertAlmostEqual(standing.q_score, 54.3, places=1)
        self.assertEqual(len(standing.expired), 3)


class TestExpiryMovesTheNumberOnItsOwn(unittest.TestCase):
    """The property a stored column cannot have."""

    def test_the_same_data_scores_differently_as_time_passes(self):
        held = certs(7, 90, expires=(NOW + timedelta(days=5)).isoformat())
        before = qscore.standing(reqs(7), held, now=NOW)["overall"]
        after = qscore.standing(reqs(7), held, now=NOW + timedelta(days=10))["overall"]
        self.assertAlmostEqual(before.q_score, 90.0, places=1)
        self.assertEqual(after.q_score, 0.0)
        self.assertEqual(len(after.expired), 7)

    def test_expired_and_missing_are_different_things(self):
        # "You let this lapse" and "you never did this" need different words in the UI.
        held = certs(1, 90) + certs(2, 90, expires=PAST)[1:]
        standing = qscore.standing(reqs(3), held, now=NOW)["overall"]
        self.assertEqual(standing.expired, ["T1"])
        self.assertEqual(standing.missing, ["T2"])

    def test_a_certificate_with_no_expiry_does_not_count(self):
        # Not "never expires" — a certificate we cannot vouch for, and Coverage counts
        # only what it can vouch for.
        held = [{"doc_title": "T0", "attempt_score": 99, "expires_at": None,
                 "issued_at": "2026-01-01T00:00:00+00:00", "category": "technical"}]
        self.assertEqual(qscore.standing(reqs(1), held, now=NOW)["overall"].current, 0)


class TestBestScoreOfRecord(unittest.TestCase):
    def test_the_highest_attempt_counts(self):
        held = [
            dict(doc_title="T0", attempt_score=70, expires_at=FUTURE, issued_at="2026-01-01", category="technical"),
            dict(doc_title="T0", attempt_score=95, expires_at=FUTURE, issued_at="2026-02-01", category="technical"),
        ]
        self.assertAlmostEqual(
            qscore.standing(reqs(1), held, now=NOW)["overall"].quality, 95.0, places=1)

    def test_retaking_and_doing_worse_does_not_lower_it(self):
        # PROJECT.md records this as chosen deliberately, with the objection on record.
        held = [
            dict(doc_title="T0", attempt_score=95, expires_at=FUTURE, issued_at="2026-01-01", category="technical"),
            dict(doc_title="T0", attempt_score=60, expires_at=FUTURE, issued_at="2026-06-01", category="technical"),
        ]
        self.assertAlmostEqual(
            qscore.standing(reqs(1), held, now=NOW)["overall"].quality, 95.0, places=1)

    def test_a_tie_keeps_the_newer_one(self):
        # A retake matching an old score should refresh the expiry, not be ignored.
        held = [
            dict(doc_title="T0", attempt_score=90, expires_at=PAST, issued_at="2025-01-01", category="technical"),
            dict(doc_title="T0", attempt_score=90, expires_at=FUTURE, issued_at="2026-06-01", category="technical"),
        ]
        self.assertEqual(qscore.standing(reqs(1), held, now=NOW)["overall"].current, 1)


class TestCategorySplit(unittest.TestCase):
    def test_behavioural_and_technical_are_reported_apart(self):
        requirements = ([{"doc_title": "B0", "category": "behavioural"}]
                        + [{"doc_title": "T0", "category": "technical"}])
        held = [dict(doc_title="T0", attempt_score=100, expires_at=FUTURE,
                     issued_at="2026-01-01", category="technical")]
        out = qscore.standing(requirements, held, now=NOW)
        # "Strong technically, thin on conduct" has to be visible rather than averaged
        # into a single middling number.
        self.assertEqual(out["technical"].q_score, 100.0)
        self.assertEqual(out["behavioural"].q_score, 0.0)
        self.assertAlmostEqual(out["overall"].q_score, 50.0, places=1)


class TestEdges(unittest.TestCase):
    def test_no_requirements_means_nothing_is_owed(self):
        # Coverage 1.0 rather than dividing by zero, and Q Score 0 because there is no
        # evidence either way — not 100, which would read as "fully compliant".
        standing = qscore.standing([], [], now=NOW)["overall"]
        self.assertEqual(standing.coverage, 1.0)
        self.assertEqual(standing.q_score, 0.0)

    def test_extra_certificates_do_not_inflate_coverage(self):
        held = certs(10, 100)
        self.assertEqual(qscore.standing(reqs(3), held, now=NOW)["overall"].coverage, 1.0)

    def test_a_certificate_for_something_not_required_is_ignored_not_counted_missing(self):
        held = [dict(doc_title="Something Else", attempt_score=100, expires_at=FUTURE,
                     issued_at="2026-01-01", category="technical")]
        standing = qscore.standing(reqs(1), held, now=NOW)["overall"]
        self.assertEqual(standing.current, 0)
        self.assertEqual(standing.missing, ["T0"])


class TestRenewal(unittest.TestCase):
    def test_expired_and_soon_to_expire_are_surfaced(self):
        held = (certs(1, 90, expires=PAST)
                + certs(2, 90, expires=(NOW + timedelta(days=5)).isoformat())[1:]
                + certs(3, 90, expires=FUTURE)[2:])
        due = qscore.renewal_candidates(held, within_days=30, now=NOW)
        self.assertEqual([d["doc_title"] for d in due], ["T0", "T1"])
        self.assertTrue(due[0]["expired"])
        self.assertFalse(due[1]["expired"])

    def test_already_expired_sorts_first(self):
        held = (certs(1, 90, expires=(NOW + timedelta(days=2)).isoformat())
                + certs(2, 90, expires=PAST)[1:])
        due = qscore.renewal_candidates(held, within_days=30, now=NOW)
        self.assertTrue(due[0]["expired"], "the most overdue should be first")


if __name__ == "__main__":
    unittest.main()
