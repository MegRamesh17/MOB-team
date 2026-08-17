"""
Tests for POST /quiz/answer — the endpoint that reveals an answer.

It has to reveal one: the UI shows a verdict as each question is answered, and the only
alternative is shipping the answer key to the browser, where it is one devtools panel
away. So the endpoint gives back the key for a single question, after an answer was
committed.

That makes two checks load-bearing rather than defensive:

  1. the attempt belongs to the caller
  2. the question was actually served in that attempt

Without either, this is an oracle: loop over question ids, collect the whole answer key,
then take the quiz. `scripts/devserver.py` enforces it with an in-process dict, which a
multi-instance Function App cannot do — hence GeneratedQuizAttemptQuestions
(017_create_attempt_questions.sql) and these tests.

Azure SQL is stubbed. What is under test is the authorisation logic and the grading
arithmetic, not pyodbc.
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "api"


def _install_azure_stubs():
    """
    Stand in for the packages that only exist in the Functions runtime.

    ADDITIVE ON PURPOSE. An earlier version put a bare module at sys.modules["azure"],
    which replaced the real azure namespace package — and since unittest discovery imports
    this file before test_isolation, search_index.py could then no longer find
    azure.core.credentials or azure.search.documents. Four unrelated tests failed with
    import errors that pointed nowhere near the cause.

    So: only attach `functions` to whatever `azure` already is, and create the parent
    only when it genuinely is not installed.
    """
    if "azure.functions" in sys.modules:
        return

    try:
        import azure as az  # the real namespace package, when azure-* is installed
    except ImportError:
        az = types.ModuleType("azure")
        sys.modules["azure"] = az

    fn = types.ModuleType("azure.functions")

    class Blueprint:
        def route(self, **kw):
            def deco(f):
                return f
            return deco

    class FunctionApp(Blueprint):
        def __init__(self, **kw):
            pass

        def register_functions(self, bp):
            pass

    class AuthLevel:
        ANONYMOUS = "anonymous"

    class HttpRequest:
        def __init__(self, headers=None, body=None, params=None):
            self.headers = headers or {}
            self._body = body
            self.params = params or {}

        def get_json(self):
            if self._body is None:
                raise ValueError("no body")
            return self._body

    class HttpResponse:
        def __init__(self, body="", status_code=200, mimetype=""):
            self.body = body
            self.status_code = status_code

        def json(self):
            return json.loads(self.body)

    fn.Blueprint = Blueprint
    fn.FunctionApp = FunctionApp
    fn.AuthLevel = AuthLevel
    fn.HttpRequest = HttpRequest
    fn.HttpResponse = HttpResponse
    # Both, so `import azure.functions` and `azure.functions` attribute access work.
    sys.modules["azure.functions"] = fn
    az.functions = fn

    sys.modules.setdefault("pyodbc", types.ModuleType("pyodbc"))
    bcrypt = types.ModuleType("bcrypt")
    bcrypt.checkpw = lambda a, b: False
    bcrypt.hashpw = lambda a, b: b"$2b$12$stub"
    bcrypt.gensalt = lambda: b""
    sys.modules.setdefault("bcrypt", bcrypt)


_install_azure_stubs()
sys.path.insert(0, str(API))

import function_app  # noqa: E402
import shared.auth as shared_auth  # noqa: E402
from azure.functions import HttpRequest  # noqa: E402


class Row(dict):
    """pyodbc rows support both attribute and key access; so does this."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class FakeCursor:
    """
    Answers queries by matching a fragment of the SQL.

    Deliberately dumb: the point is to control what each lookup returns so the branches
    in the endpoint can be driven, not to emulate a database.
    """

    def __init__(self, responses):
        self.responses = responses
        self._rows = []
        self.executed = []

    def execute(self, sql, *params):
        self.executed.append(" ".join(sql.split()))
        for fragment, rows in self.responses.items():
            if fragment in " ".join(sql.split()):
                self._rows = [Row(r) for r in rows]
                return self
        self._rows = []
        return self

    def executemany(self, sql, seq):
        self.executed.append(" ".join(sql.split()))
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    @property
    def description(self):
        if not self._rows:
            return []
        return [(k,) for k in self._rows[0].keys()]


class FakeConn:
    def __init__(self, responses):
        self.cursor_obj = FakeCursor(responses)

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_db(test, responses):
    """Point function_app._conn and _rows at the fake, for the duration of one test."""
    conn = FakeConn(responses)
    original_conn, original_rows = function_app._conn, function_app._rows
    function_app._conn = lambda: conn
    function_app._rows = lambda cur: [dict(r) for r in cur.fetchall()]
    test.addCleanup(lambda: setattr(function_app, "_conn", original_conn))
    test.addCleanup(lambda: setattr(function_app, "_rows", original_rows))
    return conn


# 64 chars, matching what random_password generates in infra/main.tf. Shorter keys make
# PyJWT warn about HMAC key length, and a test suite that prints warnings trains people to
# ignore them. setdefault, not assignment, so a real environment is never overwritten.
_TEST_SIGNING_KEY = "test-only-key-" + ("x" * 50)


def _token(employee_id=3, email="ethan.brooks@demo.com", role="employee"):
    import os
    os.environ.setdefault("JWT_SIGNING_SECRET", _TEST_SIGNING_KEY)
    return shared_auth.create_token(employee_id, email, 1, role, "SDE2", 1)


def _request(body, email="ethan.brooks@demo.com"):
    return HttpRequest(headers={"Authorization": "Bearer " + _token(email=email)}, body=body)


# The happy-path fixture: attempt belongs to Ethan, question was served in it.
SERVED = {
    "FROM dbo.GeneratedQuizAttempts WHERE attempt_id": [
        {"learner_id": "ethan.brooks@demo.com"}],
    "FROM dbo.GeneratedQuizAttemptQuestions": [{"": 1}],
    "FROM dbo.GeneratedQuestions WHERE question_id": [{
        "question_id": "q1", "question_type": "MultipleChoice", "explanation": "Because.",
        "source_doc_title": "Security Policy", "source_page": 3,
        "source_quote": "Passwords must be 14 characters.", "source_url": None,
        "provenance_class": "Documented"}],
    "FROM dbo.GeneratedOptions": [
        {"option_id": "o1", "is_correct": True},
        {"option_id": "o2", "is_correct": False},
        {"option_id": "o3", "is_correct": False}],
    "FROM dbo.GeneratedAnswerKeys": [],
}


class TestAuthorisation(unittest.TestCase):
    """The two checks that stop this being an answer oracle."""

    def test_requires_a_token(self):
        _patch_db(self, SERVED)
        res = function_app.answer_question(
            HttpRequest(headers={}, body={"attemptId": "a1", "questionId": "q1"}))
        self.assertEqual(res.status_code, 401)

    def test_another_learners_attempt_is_refused(self):
        # The attempt exists, but belongs to Dana. Ethan must not be able to grade
        # against it — nor learn that it exists.
        responses = dict(SERVED)
        responses["FROM dbo.GeneratedQuizAttempts WHERE attempt_id"] = [
            {"learner_id": "dana.whitfield@demo.com"}]
        _patch_db(self, responses)

        res = function_app.answer_question(
            _request({"attemptId": "a1", "questionId": "q1"}))
        self.assertEqual(res.status_code, 404)
        # Same status and wording as a nonexistent attempt: a different response here
        # would confirm which attempt ids are real.
        self.assertIn("Unknown attempt", res.json()["title"])

    def test_unknown_attempt_is_refused_identically(self):
        responses = dict(SERVED)
        responses["FROM dbo.GeneratedQuizAttempts WHERE attempt_id"] = []
        _patch_db(self, responses)

        res = function_app.answer_question(
            _request({"attemptId": "nope", "questionId": "q1"}))
        self.assertEqual(res.status_code, 404)
        self.assertIn("Unknown attempt", res.json()["title"])

    def test_question_not_served_in_this_attempt_is_refused(self):
        # THE oracle test. Without this check, a caller with one valid attempt could
        # walk every question_id in the bank and collect the whole answer key.
        responses = dict(SERVED)
        responses["FROM dbo.GeneratedQuizAttemptQuestions"] = []
        _patch_db(self, responses)

        res = function_app.answer_question(
            _request({"attemptId": "a1", "questionId": "some-other-question"}))
        self.assertEqual(res.status_code, 403)
        body = res.json()
        self.assertIn("Not part of this attempt", body["title"])
        # And no key leaks in the refusal.
        self.assertNotIn("correctOptionIds", body)
        self.assertNotIn("acceptedAnswers", body)

    def test_missing_ids_are_rejected_before_any_lookup(self):
        conn = _patch_db(self, SERVED)
        res = function_app.answer_question(_request({"attemptId": "", "questionId": ""}))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(conn.cursor_obj.executed, [],
                         "should not touch the database for a malformed request")


class TestGrading(unittest.TestCase):
    """Same arithmetic submit_quiz uses — partial credit is not a pass."""

    def test_exact_selection_is_correct(self):
        _patch_db(self, SERVED)
        res = function_app.answer_question(
            _request({"attemptId": "a1", "questionId": "q1", "selectedOptionIds": ["o1"]}))
        body = res.json()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["correct"])
        self.assertEqual(body["correctOptionIds"], ["o1"])
        self.assertEqual(body["explanation"], "Because.")

    def test_wrong_selection_is_incorrect(self):
        _patch_db(self, SERVED)
        res = function_app.answer_question(
            _request({"attemptId": "a1", "questionId": "q1", "selectedOptionIds": ["o2"]}))
        self.assertFalse(res.json()["correct"])

    def test_empty_selection_is_incorrect(self):
        _patch_db(self, SERVED)
        res = function_app.answer_question(
            _request({"attemptId": "a1", "questionId": "q1", "selectedOptionIds": []}))
        self.assertFalse(res.json()["correct"])

    def test_superset_selection_is_incorrect(self):
        # Selecting everything must not count as selecting the right one.
        _patch_db(self, SERVED)
        res = function_app.answer_question(
            _request({"attemptId": "a1", "questionId": "q1",
                      "selectedOptionIds": ["o1", "o2", "o3"]}))
        self.assertFalse(res.json()["correct"])

    def test_multi_select_needs_every_correct_option(self):
        responses = dict(SERVED)
        responses["FROM dbo.GeneratedOptions"] = [
            {"option_id": "o1", "is_correct": True},
            {"option_id": "o2", "is_correct": True},
            {"option_id": "o3", "is_correct": False}]
        _patch_db(self, responses)

        partial = function_app.answer_question(
            _request({"attemptId": "a1", "questionId": "q1", "selectedOptionIds": ["o1"]}))
        self.assertFalse(partial.json()["correct"], "partial credit must not pass")

        both = function_app.answer_question(
            _request({"attemptId": "a1", "questionId": "q1",
                      "selectedOptionIds": ["o2", "o1"]}))
        self.assertTrue(both.json()["correct"], "order must not matter")


class TestFillInBlank(unittest.TestCase):
    def _responses(self):
        responses = dict(SERVED)
        responses["FROM dbo.GeneratedQuestions WHERE question_id"] = [{
            "question_id": "q2", "question_type": "fill_in_blank", "explanation": "",
            "source_doc_title": "Safety", "source_page": 1, "source_quote": "",
            "source_url": None, "provenance_class": "Documented"}]
        responses["FROM dbo.GeneratedOptions"] = []
        responses["FROM dbo.GeneratedAnswerKeys"] = [
            {"accepted_answer": "elevator"}, {"accepted_answer": "lift"}]
        return responses

    def test_accepted_answer_matches_case_insensitively(self):
        _patch_db(self, self._responses())
        res = function_app.answer_question(
            _request({"attemptId": "a1", "questionId": "q2", "textAnswer": "  ELEVATOR "}))
        self.assertTrue(res.json()["correct"])

    def test_any_accepted_synonym_counts(self):
        _patch_db(self, self._responses())
        res = function_app.answer_question(
            _request({"attemptId": "a1", "questionId": "q2", "textAnswer": "lift"}))
        self.assertTrue(res.json()["correct"])

    def test_blank_answer_is_not_correct(self):
        # An empty string must not match by falling through set membership.
        _patch_db(self, self._responses())
        for value in ("", "   "):
            with self.subTest(value=value):
                res = function_app.answer_question(
                    _request({"attemptId": "a1", "questionId": "q2", "textAnswer": value}))
                self.assertFalse(res.json()["correct"])

    def test_wrong_word_is_not_correct(self):
        _patch_db(self, self._responses())
        res = function_app.answer_question(
            _request({"attemptId": "a1", "questionId": "q2", "textAnswer": "stairs"}))
        self.assertFalse(res.json()["correct"])


if __name__ == "__main__":
    unittest.main()
