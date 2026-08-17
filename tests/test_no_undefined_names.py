"""
No undefined names anywhere in the Python we ship.

WHY THIS EXISTS
Three NameErrors reached a pushed branch, and every check in place at the time passed:

  * `GET /topics` did `if get_current_employee(req) is None:` without binding the result,
    then used `identity.company_id`. Every authenticated call raised NameError, was
    swallowed by the endpoint's generic `except`, and came back as a 500. The endpoint had
    never worked.
  * `scripts/devserver.py` used `CONFIG` with no module-level import, so an
    unauthenticated hit on `/api/health` — the first thing running-locally.md tells you to
    do — crashed instead of responding.
  * The same file used `Dict` in four annotations without importing it.

`python -m compileall` passes on all of them, because they are syntactically fine.
Importing the module passes too, because the names are only looked up when the function
runs. Nothing short of executing every branch, or a linter, finds them.

DELIBERATELY NOT SKIPPED IF PYFLAKES IS MISSING. A lint that quietly does not run is
worse than no lint: it reports success forever. pyflakes is in requirements.txt so CI has
it, and this fails loudly if it is absent.

Scope is narrow on purpose — undefined names, duplicate dict keys, and f-strings missing
their placeholders. All three are bugs rather than style, so there is nothing here to
argue about and no reason for anyone to start ignoring the output.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Shipped code. Not tests: they legitimately stub and monkeypatch in ways pyflakes
# misreads, and a false positive here would train people to ignore this.
TARGETS = [
    ROOT / "api",
    ROOT / "scripts",
    ROOT / "src" / "quizgen",
]

# Every message pyflakes can emit that is a BUG rather than tidiness. Unused imports are
# deliberately absent: they are real but harmless, the repo has pre-existing ones, and
# failing on them would mean this test spends its life being disabled.
BUGS = (
    "undefined name",
    "repeated with different values",
    "f-string is missing placeholders",
    "local variable defined in enclosing scope referenced before assignment",
)


class TestNoUndefinedNames(unittest.TestCase):
    def _pyflakes(self):
        files = sorted(
            str(p) for target in TARGETS for p in target.rglob("*.py")
            if "__pycache__" not in p.parts)
        self.assertTrue(files, "found no Python to check — the paths above are wrong")

        result = subprocess.run(
            [sys.executable, "-m", "pyflakes", *files],
            capture_output=True, text=True)

        if "No module named pyflakes" in result.stderr:
            self.fail(
                "pyflakes is not installed, so this check did not run. It is in "
                "requirements.txt: pip install -r requirements.txt. This fails rather "
                "than skips on purpose — a lint that silently does not run reports "
                "success forever.")
        return result.stdout.splitlines()

    def test_no_bug_level_findings(self):
        findings = [line for line in self._pyflakes()
                    if any(bug in line for bug in BUGS)]
        self.assertEqual(
            findings, [],
            "pyflakes found name errors that compileall and a successful import both "
            "miss:\n  " + "\n  ".join(findings))

    def test_the_check_is_actually_looking_at_something(self):
        # If the file globbing broke, the test above would pass by examining nothing.
        # A real run produces output — this repo has pre-existing unused imports.
        self.assertTrue(
            self._pyflakes(),
            "pyflakes produced no output at all, which means it did not inspect the "
            "files rather than that the files are perfect")


if __name__ == "__main__":
    unittest.main()
