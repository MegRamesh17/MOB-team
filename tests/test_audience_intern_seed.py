from __future__ import annotations

import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from set_passwords import _demo_intern_password  # noqa: E402


class TestAudienceInternSeed(unittest.TestCase):
    def test_ten_low_privilege_intern_accounts_are_seeded_without_passwords(self):
        seed = (ROOT / "db" / "seed" / "seed_data.sql").read_text()
        for index in range(1, 11):
            self.assertIn("intern{:02d}@quizrant.com".format(index), seed)
        self.assertNotIn("password_hash", seed)

    def test_numbered_demo_password_setup_is_explicit_and_intern_only(self):
        script = (ROOT / "scripts" / "set_passwords.py").read_text()
        self.assertIn("--demo-intern-passwords requires --role-code INTERN", script)
        for index in range(1, 11):
            self.assertEqual(
                _demo_intern_password("intern{:02d}@quizrant.com".format(index)),
                "password{}".format(index),
            )
        with self.assertRaises(ValueError):
            _demo_intern_password("ethan.brooks@quizrant.com")

    def test_intern_role_is_targetable_and_employee_only(self):
        org = (ROOT / "db" / "seed" / "org_seed.sql").read_text()
        mappings = (ROOT / "db" / "seed" / "role_codes.sql").read_text()
        self.assertIn("'Engineering Intern',              6, 'employee'", org)
        self.assertIn("('Engineering Intern',                 'INTERN')", mappings)
        self.assertIn("MERGE dbo.QuizgenRoles", mappings)


if __name__ == "__main__":
    unittest.main()
