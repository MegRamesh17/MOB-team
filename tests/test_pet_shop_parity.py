"""
`api/shared/pet_shop.py` must stay identical to `src/quizgen/pet_shop.py`.

Same reasoning as tests/test_qscore_parity.py: api/ is a flat Azure Functions package
that cannot import from src/, so the catalog and points arithmetic are duplicated on
purpose. What is not acceptable is the duplication drifting — a learner who can afford
an item locally but not in the deployed app (or vice versa) is exactly the "works
locally, fails in the demo" failure PROJECT.md's "no backend divergence" constraint
names.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "src" / "quizgen" / "pet_shop.py"
DEPLOYED = ROOT / "api" / "shared" / "pet_shop.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


local = _load(LOCAL, "pet_shop_local")
deployed = _load(DEPLOYED, "pet_shop_deployed")


def _body(path):
    """Everything after the module docstring — the code, without the headers."""
    return path.read_text(encoding="utf-8").split('"""', 2)[2].strip()


class TestTheFilesAreTheSame(unittest.TestCase):
    def test_code_is_byte_identical_below_the_docstring(self):
        self.assertEqual(
            _body(LOCAL), _body(DEPLOYED),
            "api/shared/pet_shop.py has drifted from src/quizgen/pet_shop.py. The "
            "original is authoritative — edit src/quizgen/pet_shop.py and copy it across.")

    def test_the_deployed_copy_says_it_is_one(self):
        header = DEPLOYED.read_text(encoding="utf-8").split('"""')[1]
        self.assertIn("COPY", header.upper())
        self.assertIn("src/quizgen/pet_shop.py", header)


class TestTheyBehaveTheSame(unittest.TestCase):
    def test_same_catalog(self):
        self.assertEqual(local.CATALOG, deployed.CATALOG)
        self.assertEqual(local.POINTS_PER_TRAINING, deployed.POINTS_PER_TRAINING)

    def test_same_points_arithmetic(self):
        cases = [(0, []), (1, []), (3, ["antenna_bow"]), (2, ["scarf", "bowtie", "bogus"])]
        for trainings, owned in cases:
            with self.subTest(trainings=trainings, owned=owned):
                self.assertEqual(
                    local.points_balance(trainings, owned),
                    deployed.points_balance(trainings, owned))

    def test_same_affordability(self):
        for item in local.CATALOG:
            with self.subTest(item=item["id"]):
                self.assertEqual(
                    local.can_afford(1, [], item["id"]),
                    deployed.can_afford(1, [], item["id"]))


if __name__ == "__main__":
    unittest.main()
