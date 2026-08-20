"""
GET /skills/options and POST /skills/interest -- the skill-interest popup's backend.

Three things this has to prove, not just assert:

  1. GET only ever offers trainings already visible to the caller's role, not already
     required for it, and not already accepted in an earlier round -- never a title
     outside what that person could already see, never something re-offering what they
     already owe or already picked.
  2. Recurring, not one-time: a prompt from more than SKILL_PROMPT_COOLDOWN_DAYS ago is
     treated as stale and the popup can show again -- but ONLY if there is something
     left to offer. A fully-picked or fully-declined list does not nag on a timer with
     nothing new to say.
  3. POST never trusts the client's list of skills at face value -- it re-derives the
     same allowed set server-side and silently drops anything outside it, the same
     posture every role-tagging endpoint in this file already takes toward client input.

Azure SQL is stubbed via test_api_quiz_answer's fragment-matching fake -- reused rather
than reimplemented, same reasoning as test_lesson.py.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from test_api_quiz_answer import (  # noqa: E402
    Row, FakeCursor, FakeConn, _install_azure_stubs, _patch_db, _token,
)

_install_azure_stubs()

import function_app  # noqa: E402
from azure.functions import HttpRequest  # noqa: E402


REQUIRED = {
    "FROM dbo.RoleRequirements": [
        {"doc_title": "Code of Conduct", "category": "behavioural"},
    ],
}

# pyodbc returns a real (naive, UTC-valued) datetime.datetime for a DATETIME2 column --
# never a string. An earlier version of this fixture used an ISO string, which happened
# to work only because nothing read the value; skill_options now does arithmetic on it
# (last_prompted.replace(tzinfo=...)), which a string does not support. Matching the real
# type here is what makes that a fixture bug this suite would have caught, not a surprise
# in production.
LONG_AGO = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
    days=function_app.SKILL_PROMPT_COOLDOWN_DAYS + 1)
RECENTLY = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)


class TestSkillOptions(unittest.TestCase):
    def test_offers_visible_not_required_trainings_only(self):
        responses = dict(REQUIRED)
        responses["FROM dbo.Employees WHERE id"] = [{"skills_prompted_at": None}]
        responses["source_doc_title AS doc_title"] = [
            {"doc_title": "Python Fundamentals"},
            {"doc_title": "Code of Conduct"},  # already required -- must be excluded
        ]
        _patch_db(self, responses)

        req = HttpRequest(headers={"Authorization": "Bearer " + _token()}, params={})
        res = function_app.skill_options(req)

        self.assertEqual(res.status_code, 200)
        payload = json.loads(res.body)
        self.assertFalse(payload["prompted"])
        self.assertEqual(payload["options"], ["Python Fundamentals"],
                          "a required training leaked into the optional-skills offer")

    def test_already_accepted_skills_are_not_reoffered(self):
        responses = dict(REQUIRED)
        responses["FROM dbo.Employees WHERE id"] = [{"skills_prompted_at": None}]
        responses["source_doc_title AS doc_title"] = [
            {"doc_title": "Python Fundamentals"}, {"doc_title": "Rust Basics"}]
        responses["FROM dbo.EmployeeSkillInterest WHERE employee_id"] = [
            {"doc_title": "Python Fundamentals"}]
        _patch_db(self, responses)

        req = HttpRequest(headers={"Authorization": "Bearer " + _token()}, params={})
        payload = json.loads(function_app.skill_options(req).body)
        self.assertEqual(payload["options"], ["Rust Basics"],
                          "re-offered a skill this employee already picked")

    def test_recently_prompted_with_nothing_new_stays_quiet(self):
        # This is the exact "asked once, don't ask again immediately" case -- the
        # cooldown has not elapsed AND there's nothing uncovered to offer anyway.
        responses = dict(REQUIRED)
        responses["FROM dbo.Employees WHERE id"] = [{"skills_prompted_at": RECENTLY}]
        responses["source_doc_title AS doc_title"] = [{"doc_title": "Python Fundamentals"}]
        responses["FROM dbo.EmployeeSkillInterest WHERE employee_id"] = [
            {"doc_title": "Python Fundamentals"}]
        _patch_db(self, responses)

        req = HttpRequest(headers={"Authorization": "Bearer " + _token()}, params={})
        payload = json.loads(function_app.skill_options(req).body)
        self.assertTrue(payload["prompted"])
        self.assertEqual(payload["options"], [])

    def test_cooldown_elapsed_but_nothing_left_to_offer_stays_quiet(self):
        # The recurring case this feature exists for is "something NEW appeared" --
        # time alone, with an unchanged fully-picked list, must not nag.
        responses = dict(REQUIRED)
        responses["FROM dbo.Employees WHERE id"] = [{"skills_prompted_at": LONG_AGO}]
        responses["source_doc_title AS doc_title"] = [{"doc_title": "Python Fundamentals"}]
        responses["FROM dbo.EmployeeSkillInterest WHERE employee_id"] = [
            {"doc_title": "Python Fundamentals"}]
        _patch_db(self, responses)

        req = HttpRequest(headers={"Authorization": "Bearer " + _token()}, params={})
        payload = json.loads(function_app.skill_options(req).body)
        self.assertTrue(payload["prompted"],
                         "re-prompted on a timer with nothing new to offer")

    def test_cooldown_elapsed_with_something_new_prompts_again(self):
        # The actual point of this feature: a document uploaded after the last prompt
        # is new content this person hasn't been asked about, and enough time has
        # passed that asking again is a recommendation, not a nag.
        responses = dict(REQUIRED)
        responses["FROM dbo.Employees WHERE id"] = [{"skills_prompted_at": LONG_AGO}]
        responses["source_doc_title AS doc_title"] = [
            {"doc_title": "Python Fundamentals"}, {"doc_title": "New Terraform Module"}]
        responses["FROM dbo.EmployeeSkillInterest WHERE employee_id"] = [
            {"doc_title": "Python Fundamentals"}]
        _patch_db(self, responses)

        req = HttpRequest(headers={"Authorization": "Bearer " + _token()}, params={})
        payload = json.loads(function_app.skill_options(req).body)
        self.assertFalse(payload["prompted"])
        self.assertEqual(payload["options"], ["New Terraform Module"])

    def test_requires_auth(self):
        _patch_db(self, REQUIRED)
        res = function_app.skill_options(HttpRequest(headers={}, params={}))
        self.assertEqual(res.status_code, 401)


class TestSkillInterest(unittest.TestCase):
    def _responses(self, allowed_docs):
        responses = dict(REQUIRED)
        responses["source_doc_title AS doc_title"] = [
            {"doc_title": d} for d in allowed_docs]
        return responses

    def test_drops_titles_outside_the_allowed_set(self):
        # Client claims interest in something never actually offered -- a tampered
        # request, or just stale client state. Must not be trusted or recorded.
        conn = _patch_db(self, self._responses(["Python Fundamentals"]))
        req = HttpRequest(
            headers={"Authorization": "Bearer " + _token()},
            body={"skills": ["Python Fundamentals", "Secret Executive Compensation Doc"]})
        res = function_app.set_skill_interest(req)
        self.assertEqual(res.status_code, 200)
        payload = json.loads(res.body)
        self.assertEqual(payload["recorded"], ["Python Fundamentals"])

    def test_drops_a_title_that_is_already_required(self):
        conn = _patch_db(self, self._responses(["Code of Conduct"]))
        req = HttpRequest(
            headers={"Authorization": "Bearer " + _token()},
            body={"skills": ["Code of Conduct"]})
        payload = json.loads(function_app.set_skill_interest(req).body)
        self.assertEqual(payload["recorded"], [],
                          "recorded interest in something already required")

    def test_empty_list_is_a_valid_dismissal(self):
        conn = _patch_db(self, self._responses([]))
        req = HttpRequest(
            headers={"Authorization": "Bearer " + _token()}, body={"skills": []})
        res = function_app.set_skill_interest(req)
        self.assertEqual(res.status_code, 200)
        # Marks skills_prompted_at regardless -- the UPDATE ran without error is the
        # observable proxy here since the fake doesn't track column-level state.
        executed = " ".join(conn.cursor_obj.executed)
        self.assertIn("skills_prompted_at = SYSUTCDATETIME()", executed)

    def test_rejects_a_non_list_body(self):
        _patch_db(self, self._responses([]))
        req = HttpRequest(
            headers={"Authorization": "Bearer " + _token()},
            body={"skills": "Python Fundamentals"})
        res = function_app.set_skill_interest(req)
        self.assertEqual(res.status_code, 400)

    def test_requires_auth(self):
        _patch_db(self, self._responses([]))
        res = function_app.set_skill_interest(
            HttpRequest(headers={}, body={"skills": []}))
        self.assertEqual(res.status_code, 401)


if __name__ == "__main__":
    unittest.main()
