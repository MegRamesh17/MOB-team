"""
GET /lesson returns the field names the frontend actually reads.

Every lesson on the deployed app rendered an empty reading pane until the first real
uploaded document reached this endpoint end to end -- the backend sent "text", the
frontend reads .body, and nothing caught the mismatch because this endpoint had no test
at all. This pins the exact keys web-app/src/App.jsx's LessonReading component consumes
(heading, body, readTime), so a renamed field fails here instead of silently in the
browser.

Azure SQL is stubbed -- what's under test is the response shape, not pyodbc.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

# Reused rather than re-implemented: this helper is idempotent
# (`if "azure.functions" in sys.modules: return`) specifically so that whichever test
# file runs first stubs it and every later one reuses that stub -- an earlier,
# independent stub in this same test suite once replaced the real `azure` namespace
# package outright and broke azure.search.documents imports for unrelated tests four
# files away. Writing a second, differently-shaped stub here would reintroduce exactly
# that risk.
from test_api_quiz_answer import _install_azure_stubs  # noqa: E402

_install_azure_stubs()

import unittest  # noqa: E402
import function_app  # noqa: E402
import shared.auth as shared_auth  # noqa: E402
from azure.functions import HttpRequest  # noqa: E402


_TEST_SIGNING_KEY = "x" * 64


def _token():
    import os
    os.environ.setdefault("JWT_SIGNING_SECRET", _TEST_SIGNING_KEY)
    return shared_auth.create_token(
        employee_id=3, email="ethan.brooks@demo.com", company_id=1,
        access_role="employee", role_code="SDE2", manager_id=1, name="Ethan Brooks")


class Row(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __iter__(self):
        return iter(self.values())


class FakeCursor:
    def __init__(self, rows):
        self._rows = [Row(r) for r in rows]

    def execute(self, sql, *params):
        return self

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    @property
    def description(self):
        return [(k,) for k in self._rows[0].keys()] if self._rows else []


class FakeConn:
    def __init__(self, rows):
        self._cur = FakeCursor(rows)

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestLessonResponseShape(unittest.TestCase):
    def setUp(self):
        rows = [{
            "chunk_id": "c1", "topic": "Intro", "section": "Getting Started",
            "page_start": 1, "page_end": 1,
            "chunk_text": "This is the actual passage a learner reads.",
        }]
        conn = FakeConn(rows)
        self._orig_conn, self._orig_rows = function_app._conn, function_app._rows
        function_app._conn = lambda: conn
        function_app._rows = lambda cur: [dict(r) for r in cur.fetchall()]
        self.addCleanup(lambda: setattr(function_app, "_conn", self._orig_conn))
        self.addCleanup(lambda: setattr(function_app, "_rows", self._orig_rows))

    def test_section_uses_body_not_text(self):
        # The actual bug: the frontend reads section.body. A field named "text" here
        # renders as a silently empty reading pane, not an error anywhere visible.
        req = HttpRequest(
            headers={"Authorization": "Bearer " + _token()},
            params={"training": "Getting Started Doc"})
        res = function_app.get_lesson(req)
        self.assertEqual(res.status_code, 200)
        import json
        payload = json.loads(res.body)
        section = payload["sections"][0]
        self.assertIn("body", section, "section is missing 'body' -- frontend renders nothing")
        self.assertEqual(section["body"], "This is the actual passage a learner reads.")
        self.assertNotIn("text", section, "old field name left in place alongside the fix")

    def test_response_includes_a_read_time(self):
        req = HttpRequest(
            headers={"Authorization": "Bearer " + _token()},
            params={"training": "Getting Started Doc"})
        res = function_app.get_lesson(req)
        import json
        payload = json.loads(res.body)
        self.assertIn("readTime", payload, "frontend renders {data.readTime} unconditionally")
        self.assertTrue(payload["readTime"])


if __name__ == "__main__":
    unittest.main()
