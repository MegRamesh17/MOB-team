"""
POST /documents/{documentId}/delete -- permanent removal, and doubling as cancel.

Deliberately does NOT block while a job is running (that's the whole point: deleting
IS how a running job is told to stop, since _run_generation_job's only way to notice
is that its own GenerationJobs row disappeared). See test_generation_cancellation.py
for the worker side of that contract; this file only covers the delete endpoint.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from test_api_quiz_answer import _install_azure_stubs  # noqa: E402

_install_azure_stubs()

import function_app  # noqa: E402


class Request:
    route_params = {"documentId": "doc_owned"}


class Cursor:
    def __init__(self, uploaded_by=7):
        self.uploaded_by = uploaded_by
        self.current = None
        self.executed = []
        self.rowcount = 0

    def execute(self, sql, *params):
        normalized = " ".join(sql.split())
        self.executed.append(normalized)
        self.rowcount = 1 if normalized.startswith(("DELETE", "UPDATE")) else 0
        if "SELECT TOP 1 source.doc_id" in normalized:
            self.current = SimpleNamespace(
                doc_id="doc_owned", doc_title="Intern Onboarding",
                uploaded_by=self.uploaded_by, trusted_link_id=None,
            )
        else:
            self.current = None
        return self

    def fetchone(self):
        return self.current


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.committed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def identity(employee_id=7, access_role="manager"):
    return SimpleNamespace(employee_id=employee_id, company_id=1, access_role=access_role)


class TestDocumentDeletion(unittest.TestCase):
    def call(self, caller, cursor):
        connection = Connection(cursor)
        with (
            patch.object(function_app, "get_current_employee", return_value=caller),
            patch.object(function_app, "_conn", return_value=connection),
        ):
            response = function_app.delete_document(Request())
        return response, connection

    def test_owner_permanently_removes_score_and_course_records(self):
        cursor = Cursor(uploaded_by=7)
        response, connection = self.call(identity(), cursor)

        self.assertEqual(response.status_code, 200)
        body = response.get_body() if hasattr(response, "get_body") else response.body
        self.assertTrue(json.loads(body)["deleted"])
        self.assertTrue(connection.committed)
        sql = "\n".join(cursor.executed)
        for table in (
            "dbo.Certificates", "dbo.GeneratedGradingEvents",
            "dbo.GeneratedQuizResponses", "dbo.GeneratedQuizAttempts",
            "dbo.EmployeeModuleProgress", "dbo.EmployeeTrainingProgress",
            "dbo.GeneratedQuestions", "dbo.TrainingModules",
            "dbo.RoleRequirements", "dbo.EmployeeSkillInterest",
            "dbo.GenerationJobs", "dbo.SourceChunks", "dbo.TrainingDocuments",
        ):
            self.assertIn("DELETE FROM " + table, sql)

    def test_non_owner_manager_is_forbidden(self):
        cursor = Cursor(uploaded_by=99)
        response, connection = self.call(identity(), cursor)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(connection.committed)
        self.assertFalse(any(sql.startswith("DELETE") for sql in cursor.executed))

    def test_admin_can_remove_a_legacy_document(self):
        response, connection = self.call(
            identity(access_role="admin"), Cursor(uploaded_by=None))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(connection.committed)

    def test_deleting_a_running_generation_is_allowed_and_cancels_it(self):
        # No "running" state modeled here on purpose -- the endpoint no longer checks
        # GenerationJobs.state before deleting at all. Its own DELETE FROM
        # dbo.GenerationJobs (asserted above) is the cancellation signal; there is
        # nothing left for this endpoint itself to gate on.
        cursor = Cursor(uploaded_by=7)
        response, connection = self.call(identity(), cursor)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(connection.committed)


if __name__ == "__main__":
    unittest.main()
