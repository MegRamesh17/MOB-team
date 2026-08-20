from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from set_passwords import _demo_intern_password  # noqa: E402


class TestAudienceInternSeed(unittest.TestCase):
    def test_ten_low_privilege_intern_accounts_are_seeded(self):
        seed = (ROOT / "db" / "seed" / "seed_data.sql").read_text()
        for index in range(1, 11):
            self.assertIn("intern{:02d}@quizrant.com".format(index), seed)

    def test_seeded_demo_hashes_match_the_numbered_passwords(self):
        seed = (ROOT / "db" / "seed" / "seed_data.sql").read_text()
        credentials = dict(re.findall(
            r"\('(intern\d{2}@quizrant\.com)', '(\$2b\$12\$[^']+)'\)", seed
        ))
        self.assertEqual(len(credentials), 10)
        verifier = subprocess.run(
            [sys.executable, "-c", (
                "import bcrypt,json,sys; c=json.loads(sys.stdin.read()); "
                "sys.exit(0 if all(bcrypt.checkpw(('password'+str(i)).encode(), "
                "c[('intern%02d@quizrant.com' % i)].encode()) for i in range(1,11)) else 1)"
            )],
            input=json.dumps(credentials), text=True, capture_output=True, check=False,
        )
        self.assertEqual(verifier.returncode, 0, verifier.stderr)
        self.assertIn("WHERE role.role_code = 'INTERN'", seed)

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
