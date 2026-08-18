"""
Every .sql file parses as T-SQL.

WHAT THIS CATCHES, AND WHAT IT DOES NOT — read this before trusting it.

There is no SQL Server in this repo's test environment, so none of db/ has ever been
executed by anything except the deploy pipeline against real Azure. Three separate bugs
reached that pipeline as a result:

  * a PRINT with a subquery inside CAST (invalid T-SQL, in the seed)
  * 009 failing mid-batch while the runner recorded it as applied
  * migrations referencing dbo.Companies, which did not exist

This test would have caught NONE OF THEM. It is a parser, not SQL Server: it checks the
text is grammatically T-SQL, not that the statements are legal, that the objects exist, or
that they do what they say. The PRINT bug parses perfectly and fails at execution.

So it is a cheap net for typos and malformed statements across 21 migrations, two seeds
and a verification script — a real but narrow thing. The gap it does not close is that
this project cannot execute SQL until it reaches Azure, and closing that needs either a
SQL Server container in CI or an ephemeral Azure SQL database. Worth doing; not done.

Claiming more for this than it does would be worse than not having it.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_DIRS = [ROOT / "db"]


def sql_files():
    return sorted(p for d in SQL_DIRS for p in d.rglob("*.sql"))


class TestSqlParses(unittest.TestCase):
    def test_every_sql_file_parses_as_tsql(self):
        files = sql_files()
        self.assertTrue(files, "found no .sql files — the paths above are wrong")

        result = subprocess.run(
            [sys.executable, "-m", "sqlfluff", "parse", "--dialect", "tsql",
             *[str(f) for f in files]],
            capture_output=True, text=True, cwd=str(ROOT))

        if "No module named sqlfluff" in result.stderr:
            self.fail(
                "sqlfluff is not installed, so this did not run. It is in "
                "requirements.txt: pip install -r requirements.txt. Failing rather than "
                "skipping, because a check that silently does not run reports success "
                "forever.")

        unparsable = [line for line in (result.stdout + result.stderr).splitlines()
                      if "unparsable section" in line.lower()]
        self.assertEqual(
            unparsable, [],
            "T-SQL that does not parse:\n  " + "\n  ".join(unparsable))

    def test_it_is_actually_reading_the_files(self):
        # A broken glob would make the test above pass by checking nothing.
        self.assertGreater(
            len(sql_files()), 15,
            "expected the migrations, seeds and verify script; if this drops, the glob "
            "has broken rather than the SQL having been deleted")


if __name__ == "__main__":
    unittest.main()
