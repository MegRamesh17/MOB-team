"""
GET /documents reports a running generation job, so it survives a remount.

The bug this pins: job progress was tracked in plain frontend useState local to
DocumentsScreen. Real generation runs server-side regardless of whether anyone's tab is
open to watch it, but the progress bar vanished the moment that component unmounted
(navigating to another tab and back) -- there was nothing in the document list response
telling a freshly-mounted component that a job existed to resume polling.

Azure SQL is stubbed via test_api_quiz_answer's fragment-matching fake.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from test_api_quiz_answer import _install_azure_stubs, _patch_db, _token  # noqa: E402

_install_azure_stubs()

import function_app  # noqa: E402


class Request:
    def __init__(self, token):
        self.headers = {"Authorization": "Bearer " + token}
        self.params = {}


def response_json(response):
    body = response.get_body() if hasattr(response, "get_body") else response.body
    return json.loads(body)


def _manager_token():
    return _token(role="manager")


class TestActiveJobSurfaced(unittest.TestCase):
    def test_a_running_job_is_attached_to_its_document(self):
        responses = {
            "FROM dbo.SourceChunks AS source": [{
                "doc_id": "doc_zero", "doc_title": "Zero-Trust Security", "chunks": 6,
                "uploaded_by": 3, "uploaded_by_name": "Ethan Brooks",
                "source_kind": "upload", "pending_analysis_json": None,
            }],
            "FROM dbo.GeneratedQuestions": [],
            "FROM dbo.GenerationJobs": [{
                "job_id": "job_abc123", "doc_title": "Zero-Trust Security",
                "total": 6, "done_count": 2, "message": "Reading section 3…",
                "created_at": "2026-08-19T00:00:00",
            }],
        }
        _patch_db(self, responses)

        req = Request(_manager_token())
        res = function_app.list_documents(req)

        self.assertEqual(res.status_code, 200)
        docs = response_json(res)["documents"]
        self.assertEqual(len(docs), 1)
        active = docs[0]["activeJob"]
        self.assertIsNotNone(active, "a running job in GenerationJobs was not surfaced")
        self.assertEqual(active["jobId"], "job_abc123")
        self.assertEqual(active["total"], 6)
        self.assertEqual(active["done"], 2)
        self.assertTrue(docs[0]["canDelete"])
        self.assertEqual(docs[0]["documentId"], "doc_zero")

    def test_a_document_with_no_job_reports_none(self):
        responses = {
            "FROM dbo.SourceChunks AS source": [{
                "doc_id": "doc_onboarding", "doc_title": "Onboarding", "chunks": 3,
                "uploaded_by": 3, "uploaded_by_name": "Ethan Brooks",
                "source_kind": "upload", "pending_analysis_json": None,
            }],
            "FROM dbo.GeneratedQuestions": [],
            "FROM dbo.GenerationJobs": [],
        }
        _patch_db(self, responses)

        req = Request(_manager_token())
        docs = response_json(function_app.list_documents(req))["documents"]
        self.assertIsNone(docs[0]["activeJob"])

    def test_requires_manager(self):
        _patch_db(self, {})
        req = Request(_token(role="employee"))
        res = function_app.list_documents(req)
        self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
