from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
sys.path.insert(0, str(API))
sys.path.insert(0, str(ROOT / "src"))

import function_app  # noqa: E402
import shared.sqlbank as sqlbank  # noqa: E402
import quizgen.pipeline as pipeline  # noqa: E402
import quizgen.coursegen as coursegen  # noqa: E402


class FakeConnection:
    def cursor(self):
        return Mock()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeBank:
    chunks = []
    published_override = None
    retire_stale_calls = 0

    def __init__(self, conn, company_id):
        self.conn = conn
        self.company_id = company_id

    def add_role(self, *args):
        pass

    def set_chunk_roles(self, title, assignments):
        return len(assignments)

    def add_role_requirement(self, *args):
        pass

    def retire_document_questions(self, *args):
        return 0

    def all_chunks(self):
        return list(self.chunks)

    def save_instructional_course(self, course, assignments):
        self.course = course
        self.assignments = assignments
        return {"modules": len(course.modules), "ready": len(course.ready_modules)}

    def save_chunks(self, chunks):
        self.saved_chunks = list(chunks)
        return len(self.saved_chunks)

    def retire_stale_course_questions(self, *args):
        type(self).retire_stale_calls += 1
        return 0

    def finalize_instructional_course(self, course, chunks):
        if type(self).published_override is not None:
            return type(self).published_override
        return len(course.ready_modules)


class FakeRequest:
    def get_json(self):
        return {
            "title": "Capacity Planning",
            "assignments": {"Role Overview": "SWE_DIRECTOR"},
            "newRoles": [],
            "makeRequired": True,
        }


class FakeOutput:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


def response_json(response):
    raw = response.get_body() if hasattr(response, "get_body") else response.body
    return json.loads(raw)


class TestGenerationQueueHandoff(unittest.TestCase):
    def setUp(self):
        FakeBank.published_override = None
        FakeBank.retire_stale_calls = 0

    def test_confirm_returns_before_generation_and_queues_the_job(self):
        identity = SimpleNamespace(company_id=7)
        output = FakeOutput()
        generate = Mock(side_effect=AssertionError("generation must not run in HTTP request"))

        with (
            patch.object(function_app, "get_current_employee", return_value=identity),
            patch.object(function_app, "require_manager", return_value=None),
            patch.object(function_app, "_permitted_upload_roles", return_value={"SWE_DIRECTOR"}),
            patch.object(function_app, "_conn", return_value=FakeConnection()),
            patch.object(sqlbank, "SqlBank", FakeBank),
            patch.object(sqlbank, "new_job_id", return_value="job_queued"),
            patch.object(sqlbank, "create_job") as create_job,
            patch.object(pipeline, "generate_questions", generate),
        ):
            response = function_app.confirm_document(FakeRequest(), output)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response_json(response)["jobId"], "job_queued")
        self.assertEqual(json.loads(output.value), {
            "jobId": "job_queued",
            "companyId": 7,
            "docTitle": "Capacity Planning",
            "assignments": {"Role Overview": ["SWE_DIRECTOR"]},
        })
        create_job.assert_called_once()
        generate.assert_not_called()

    def test_worker_updates_progress_and_marks_the_job_done(self):
        connection = FakeConnection()
        chunk = SimpleNamespace(topic="Capacity", chunk_id="chunk_1", doc_title="Capacity Planning",
                                container="source", role_scope="SWE_DIRECTOR")
        lesson = SimpleNamespace(topic="Capacity", chunk_id="lesson_1", module_id="mod_1")
        module = SimpleNamespace(module_id="mod_1", heading="Capacity",
                                 learning_points=list(range(5)))
        course = SimpleNamespace(modules=[module], ready_modules=[module])
        result = SimpleNamespace(kept=[1, 2], written=2, rejected=[])
        updates = []

        def update_job(conn, job_id, company_id, **fields):
            updates.append(fields)

        def generate(bank, chunks, **kwargs):
            self.assertEqual(kwargs["per_chunk"], 7)
            self.assertTrue(kwargs["difficulty_ladder"])
            return result

        FakeBank.chunks = [chunk]

        with (
            patch.object(function_app, "_conn", return_value=connection),
            patch.object(sqlbank, "SqlBank", FakeBank),
            patch.object(sqlbank, "get_job", return_value={"state": "running"}),
            patch.object(sqlbank, "update_job", side_effect=update_job),
            patch.object(coursegen, "build_instructional_course", return_value=course),
            patch.object(coursegen, "assessment_chunks", return_value=[lesson]),
            patch.object(pipeline, "generate_questions", side_effect=generate),
        ):
            function_app._run_generation_job("job_queued", 7, "Capacity Planning")

        self.assertEqual(updates[0]["total"], 1)
        self.assertTrue(any(update.get("done_count") == 1 for update in updates))
        self.assertEqual(updates[-1]["state"], "done")
        self.assertEqual(updates[-1]["written"], 2)
        self.assertEqual(FakeBank.retire_stale_calls, 1)

    def test_failed_replacement_keeps_previous_question_bank(self):
        connection = FakeConnection()
        chunk = SimpleNamespace(topic="Capacity", chunk_id="chunk_1",
                                doc_title="Capacity Planning", container="source",
                                role_scope="SWE_DIRECTOR")
        lesson = SimpleNamespace(topic="Capacity", chunk_id="lesson_1", module_id="mod_1")
        module = SimpleNamespace(module_id="mod_1", heading="Capacity",
                                 learning_points=list(range(5)))
        course = SimpleNamespace(modules=[module], ready_modules=[module])
        FakeBank.chunks = [chunk]
        FakeBank.published_override = 0

        with (
            patch.object(function_app, "_conn", return_value=connection),
            patch.object(sqlbank, "SqlBank", FakeBank),
            patch.object(sqlbank, "get_job", return_value={"state": "running"}),
            patch.object(sqlbank, "update_job"),
            patch.object(coursegen, "build_instructional_course", return_value=course),
            patch.object(coursegen, "assessment_chunks", return_value=[lesson]),
            patch.object(pipeline, "generate_questions", return_value=SimpleNamespace(
                kept=[], written=0, rejected=[])),
        ):
            function_app._run_generation_job("site_job", 7, "Capacity Planning")

        self.assertEqual(FakeBank.retire_stale_calls, 0)

    def test_website_worker_bounds_generation_per_module(self):
        connection = FakeConnection()
        chunks = [SimpleNamespace(
            topic="Prompting Techniques", chunk_id="chunk_{}".format(i),
            container="trusted-site", source_url="https://docs.example.com/{}".format(i),
            page_start=1, doc_title="Prompt Guide", role_scope="SWE_DIRECTOR",
        ) for i in range(9)]
        lesson = SimpleNamespace(topic="Prompting Techniques", chunk_id="lesson_1",
                                 module_id="mod_prompting")
        module = SimpleNamespace(module_id="mod_prompting", heading="Prompting Techniques",
                                 learning_points=list(range(8)))
        course = SimpleNamespace(modules=[module], ready_modules=[module])
        result = SimpleNamespace(kept=list(range(24)), written=24, rejected=[])
        updates = []

        def generate(bank, selected, **kwargs):
            self.assertEqual(selected, [lesson])
            self.assertEqual(kwargs["per_chunk"], 8)
            self.assertTrue(kwargs["difficulty_ladder"])
            return result

        FakeBank.chunks = chunks

        with (
            patch.object(function_app, "_conn", return_value=connection),
            patch.object(sqlbank, "SqlBank", FakeBank),
            patch.object(sqlbank, "get_job", return_value={"state": "running"}),
            patch.object(sqlbank, "update_job", side_effect=lambda *args, **fields: updates.append(fields)),
            patch.object(coursegen, "build_instructional_course", return_value=course),
            patch.object(coursegen, "assessment_chunks", return_value=[lesson]),
            patch.object(pipeline, "generate_questions", side_effect=generate),
        ):
            function_app._run_generation_job("site_job", 7, "Prompt Guide")

        self.assertEqual(updates[0]["total"], 9)
        self.assertEqual(updates[-1]["state"], "done")


if __name__ == "__main__":
    unittest.main()
