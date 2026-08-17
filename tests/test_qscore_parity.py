"""
`api/shared/qscore.py` must stay identical to `src/quizgen/qscore.py`.

The duplication is deliberate: api/ is a flat Azure Functions package that cannot import
from src/, and vendoring the whole quizgen package to get one module of arithmetic would
drag openai, azure-search-documents and pypdf into the Function App for nothing.

What is not acceptable is the duplication drifting. If the deployed Q Score and the local
one disagree, local testing stops predicting the demo — the exact failure PROJECT.md's
"no backend divergence" constraint names. A comment asking people to keep two files in
step is a request; this is the mechanism.

Compared by BEHAVIOUR as well as by text, because a copy could be textually identical and
still differ if one imported something the other did not.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "src" / "quizgen" / "qscore.py"
DEPLOYED = ROOT / "api" / "shared" / "qscore.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


local = _load(LOCAL, "qscore_local")
deployed = _load(DEPLOYED, "qscore_deployed")

NOW = datetime(2026, 8, 17, tzinfo=timezone.utc)
FUTURE = (NOW + timedelta(days=200)).isoformat()
PAST = (NOW - timedelta(days=10)).isoformat()


def _body(path):
    """Everything after the module docstring — the code, without the headers."""
    return path.read_text(encoding="utf-8").split('"""', 2)[2].strip()


class TestTheFilesAreTheSame(unittest.TestCase):
    def test_code_is_byte_identical_below_the_docstring(self):
        # The docstrings differ on purpose: the deployed copy carries a header saying it
        # is a copy and which file is authoritative. Everything below must match.
        self.assertEqual(
            _body(LOCAL), _body(DEPLOYED),
            "api/shared/qscore.py has drifted from src/quizgen/qscore.py. The original "
            "is authoritative — edit src/quizgen/qscore.py and copy it across.")

    def test_the_deployed_copy_says_it_is_one(self):
        # Someone opening only the deployed file must learn immediately that editing it
        # is the wrong move.
        header = DEPLOYED.read_text(encoding="utf-8").split('"""')[1]
        self.assertIn("COPY", header.upper())
        self.assertIn("src/quizgen/qscore.py", header)


class TestTheyBehaveTheSame(unittest.TestCase):
    """Text equality is not behaviour equality. Check the numbers too."""

    def test_same_difficulty_weights(self):
        self.assertEqual(local.DIFFICULTY_WEIGHTS, deployed.DIFFICULTY_WEIGHTS)

    def test_same_validity_and_categories(self):
        self.assertEqual(local.DEFAULT_VALIDITY_MONTHS, deployed.DEFAULT_VALIDITY_MONTHS)
        self.assertEqual(local.CATEGORIES, deployed.CATEGORIES)

    def test_same_attempt_scores(self):
        cases = [
            [],
            [{"difficulty": "Easy", "correct": True}],
            [{"difficulty": "Hard", "correct": False}],
            [{"difficulty": "Easy", "correct": True}, {"difficulty": "Hard", "correct": False}],
            [{"difficulty": "Nonsense", "correct": True}],
        ]
        for graded in cases:
            with self.subTest(graded=graded):
                self.assertEqual(local.attempt_score(graded), deployed.attempt_score(graded))

    def test_same_standing(self):
        reqs = [{"doc_title": "T{}".format(i), "category": "technical"} for i in range(7)]
        held = [{"doc_title": "T{}".format(i), "attempt_score": 90,
                 "expires_at": FUTURE if i < 4 else PAST,
                 "issued_at": "2026-01-01T00:00:00+00:00", "category": "technical"}
                for i in range(7)]
        a = local.standing(reqs, held, now=NOW)
        b = deployed.standing(reqs, held, now=NOW)
        self.assertEqual(sorted(a), sorted(b))
        for key in a:
            with self.subTest(bucket=key):
                self.assertEqual(a[key].to_dict(), b[key].to_dict())

    def test_same_expiry_arithmetic(self):
        issued = "2026-01-15T00:00:00+00:00"
        for months in (1, 6, 12, 24):
            with self.subTest(months=months):
                self.assertEqual(local.expiry_from(issued, months),
                                 deployed.expiry_from(issued, months))

    def test_same_renewal_candidates(self):
        held = [{"doc_title": "A", "attempt_score": 90, "expires_at": PAST,
                 "issued_at": "2025-01-01T00:00:00+00:00", "category": "technical"},
                {"doc_title": "B", "attempt_score": 90, "expires_at": FUTURE,
                 "issued_at": "2026-01-01T00:00:00+00:00", "category": "technical"}]
        self.assertEqual(
            [c["doc_title"] for c in local.renewal_candidates(held, now=NOW)],
            [c["doc_title"] for c in deployed.renewal_candidates(held, now=NOW)])


if __name__ == "__main__":
    unittest.main()
