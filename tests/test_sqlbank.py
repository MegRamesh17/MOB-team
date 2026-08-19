"""
SqlBank against a stateful fake connection — not real Azure SQL, but real Python logic.

WHAT THIS CATCHES, AND WHAT IT DOES NOT
There is no SQL Server in this test environment (same limitation test_sql_parses.py
states up front), so this cannot prove a query is valid T-SQL, that a constraint holds,
or that MERGE behaves the way SQL Server's MERGE behaves. What it CAN catch, because the
fake actually tracks rows and mutates them: a placeholder count that does not match the
params passed (the single most common bug in hand-written parameterized SQL), a WHERE
clause that forgets to scope by company_id, an upsert that inserts a duplicate instead of
updating, and a method that returns the wrong shape to the pipeline code calling it.

The fake is deliberately narrow -- it understands exactly the query shapes SqlBank issues,
matched by fragment, not a general SQL engine. That is a real limitation: it would not
notice SqlBank starting to issue a DIFFERENT query for the same operation as long as this
fake's patterns still happen to match. It is still worth having, because every method
below is exercised through the same public interface pipeline.py and rolemap.py actually
call, with the same placeholder-count assertion applied to every statement -- not
hand-checked once and trusted forever.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from quizgen.models import (  # noqa: E402
    Chunk, Difficulty, Option, ProvenanceClass, Question, QuestionType, ReviewStatus,
)
from shared.sqlbank import (  # noqa: E402
    SqlBank, create_job, get_job, new_job_id, update_job,
)


class Row:
    """Attribute AND index access, matching a pyodbc row."""

    def __init__(self, values, cols):
        self._d = dict(zip(cols, values))

    def __getattr__(self, name):
        try:
            return self._d[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __getitem__(self, i):
        return list(self._d.values())[i]


class FakeCursor:
    """
    Understands exactly the query shapes SqlBank issues, by fragment. Mutates the tables
    dict it is given, so a MERGE really upserts and a later SELECT really sees it.
    """

    def __init__(self, tables):
        self.tables = tables  # {table_name: [dict, ...]}
        self._result = []
        self.rowcount = 0
        self.executed = []  # (sql, params) for placeholder-count assertions

    def _bind(self, sql, params):
        flat = list(params[0]) if len(params) == 1 and isinstance(params[0], (list, tuple)) else list(params)
        placeholders = sql.count("?")
        assert placeholders == len(flat), (
            "placeholder/param mismatch: {} '?' in SQL, {} params given\n{}"
            .format(placeholders, len(flat), sql)
        )
        self.executed.append((" ".join(sql.split()), flat))
        return flat

    def execute(self, sql, *params):
        p = self._bind(sql, params)
        s = " ".join(sql.split())
        t = self.tables

        if "MERGE dbo.SourceChunks" in s:
            # company_id is NOT NULL on the real table (020_add_company_to_quizgen.sql)
            # -- enforced here too, since the original version of this fake didn't
            # model that constraint at all and let a real bug (company_id missing
            # from this exact MERGE) pass every test before it hit production.
            chunk_id = p[0]
            existing = next((r for r in t["SourceChunks"] if r["chunk_id"] == chunk_id), None)
            (doc_id, doc_title, section, topic, page_start, page_end,
             chunk_text, container, role_scope, company_id) = p[1:11]
            if company_id is None:
                raise AssertionError(
                    "IntegrityError: NULL company_id on SourceChunks -- MERGE must "
                    "set it in both the UPDATE SET and INSERT VALUES branches")
            if existing:
                existing.update(doc_id=doc_id, doc_title=doc_title, section=section,
                                 topic=topic, page_start=page_start, page_end=page_end,
                                 chunk_text=chunk_text, container=container,
                                 role_scope=role_scope, company_id=company_id)
            else:
                t["SourceChunks"].append(dict(
                    chunk_id=chunk_id, doc_id=doc_id, doc_title=doc_title, section=section,
                    topic=topic, page_start=page_start, page_end=page_end,
                    chunk_text=chunk_text, container=container, role_scope=role_scope,
                    company_id=company_id))
            self.rowcount = 1

        elif "SELECT chunk_id, doc_id, doc_title, topic, section, page_start" in s:
            (company_id,) = p
            self._result = sorted(
                (r for r in t["SourceChunks"] if r["company_id"] == company_id),
                key=lambda r: (r["doc_title"], r["page_start"]))

        elif "UPDATE dbo.SourceChunks SET role_scope" in s:
            role, doc_title, topic, company_id = p
            n = 0
            for r in t["SourceChunks"]:
                if r["doc_title"] == doc_title and r["topic"] == topic and r["company_id"] == company_id:
                    r["role_scope"] = role
                    n += 1
            self.rowcount = n

        elif "SELECT DISTINCT doc_id FROM dbo.SourceChunks" in s:
            doc_title, company_id = p
            self._result = [{"doc_id": r["doc_id"]} for r in t["SourceChunks"]
                             if r["doc_title"] == doc_title and r["company_id"] == company_id]

        elif "SELECT DISTINCT source_chunk_id FROM dbo.GeneratedQuestions" in s:
            (company_id,) = p
            self._result = [{"source_chunk_id": r["source_chunk_id"]}
                             for r in t["GeneratedQuestions"]
                             if r["company_id"] == company_id and r["source_chunk_id"]]

        elif "SELECT 1 FROM dbo.GeneratedQuestions WHERE question_id" in s:
            qid, company_id = p
            self._result = [{"1": 1} for r in t["GeneratedQuestions"]
                             if r["question_id"] == qid and r["company_id"] == company_id]

        elif "INSERT INTO dbo.GeneratedQuestions" in s:
            cols = ("question_id", "topic", "question_type", "difficulty", "prompt",
                    "explanation", "points", "source_chunk_id", "source_doc_title",
                    "source_page", "source_quote", "generator", "review_status",
                    "provenance_class", "role_code", "role_requirement",
                    "rubric_json", "fallback_json", "grading_version",
                    "contradiction_notes", "company_id")
            row = dict(zip(cols, p))
            row["times_served"], row["times_correct"] = 0, 0
            t["GeneratedQuestions"].append(row)
            self.rowcount = 1

        elif "INSERT INTO dbo.GeneratedOptions" in s:
            cols = ("option_id", "question_id", "option_text", "is_correct", "sort_order")
            t["GeneratedOptions"].append(dict(zip(cols, p)))
            self.rowcount = 1

        elif "SELECT 1 FROM dbo.GeneratedAnswerKeys" in s or "IF NOT EXISTS (SELECT 1 FROM dbo.GeneratedAnswerKeys" in s:
            # The real statement is IF NOT EXISTS (...) INSERT ... in one batch. The fake
            # just performs the upsert-if-absent directly against the same four params.
            qid, ans, qid2, ans2 = p
            exists = any(r["question_id"] == qid and r["accepted_answer"] == ans
                         for r in t["GeneratedAnswerKeys"])
            if not exists:
                t["GeneratedAnswerKeys"].append({"question_id": qid2, "accepted_answer": ans2})
            self.rowcount = 1

        elif "UPDATE dbo.GeneratedQuestions SET review_status" in s:
            status, doc_title, company_id = p
            n = 0
            for r in t["GeneratedQuestions"]:
                if r["source_doc_title"] == doc_title and r["company_id"] == company_id:
                    r["review_status"] = status
                    n += 1
            self.rowcount = n

        elif "SELECT role_code, title, description FROM dbo.QuizgenRoles" in s:
            (company_id,) = p
            self._result = [r for r in t["QuizgenRoles"] if r["company_id"] == company_id]

        elif "IF EXISTS (SELECT 1 FROM dbo.QuizgenRoles" in s:
            code, cid1, title, desc, code2, cid2, code3, cid3, title2, desc2 = p
            existing = next((r for r in t["QuizgenRoles"]
                              if r["role_code"] == code and r["company_id"] == cid1), None)
            if existing:
                existing.update(title=title, description=desc)
            else:
                t["QuizgenRoles"].append({"role_code": code3, "company_id": cid3,
                                           "title": title2, "description": desc2})
            self.rowcount = 1

        elif "DELETE FROM dbo.QuizgenRoles" in s:
            code, company_id = p
            before = len(t["QuizgenRoles"])
            t["QuizgenRoles"][:] = [r for r in t["QuizgenRoles"]
                                     if not (r["role_code"] == code and r["company_id"] == company_id)]
            self.rowcount = before - len(t["QuizgenRoles"])

        elif "INSERT INTO dbo.GenerationJobs" in s:
            job_id, company_id, doc_title = p
            t["GenerationJobs"].append({
                "job_id": job_id, "company_id": company_id, "doc_title": doc_title,
                "state": "running", "total": 0, "done_count": 0, "kept": 0,
                "written": 0, "rejected": 0, "message": "", "finished_at": None,
            })
            self.rowcount = 1

        elif "UPDATE dbo.GenerationJobs SET" in s:
            *values, job_id, company_id = p
            job = next((r for r in t["GenerationJobs"]
                        if r["job_id"] == job_id and r["company_id"] == company_id), None)
            if job:
                # The SET clause order matches the field iteration order update_job used;
                # reconstruct it the same way by re-reading the SQL's column names.
                set_cols = [c.split("=")[0].strip()
                            for c in s.split("SET", 1)[1].split("WHERE", 1)[0].split(",")
                            if "SYSUTCDATETIME" not in c]
                for col, val in zip(set_cols, values):
                    job[col] = val
                if "finished_at = SYSUTCDATETIME" in s:
                    job["finished_at"] = "2026-01-01T00:00:00"
            self.rowcount = 1 if job else 0

        elif "SELECT job_id, doc_title, state, total" in s:
            job_id, company_id = p
            self._result = [r for r in t["GenerationJobs"]
                             if r["job_id"] == job_id and r["company_id"] == company_id]

        else:
            raise AssertionError("FakeCursor does not recognise this query:\n" + s)

        return self

    def fetchall(self):
        cols = list(self._result[0].keys()) if self._result else []
        return [Row(list(r.values()), cols) for r in self._result]

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None


class FakeConn:
    def __init__(self):
        self.tables = {
            "SourceChunks": [], "GeneratedQuestions": [], "GeneratedOptions": [],
            "GeneratedAnswerKeys": [], "QuizgenRoles": [], "GenerationJobs": [],
        }

    def cursor(self):
        return FakeCursor(self.tables)

    def commit(self):
        pass


def make_chunk(chunk_id="c1", topic="Intro", doc_title="Doc"):
    return Chunk(chunk_id=chunk_id, doc_id="d1", doc_title=doc_title, topic=topic,
                 section="Sec", page_start=1, page_end=1, text="some passage text",
                 container="company-docs", role_scope="ALL", company_id="1")


def make_question(qid="q1", topic="Intro"):
    return Question(
        question_id=qid, topic=topic, question_type=QuestionType.MULTIPLE_CHOICE,
        difficulty=Difficulty.MEDIUM, prompt="What?",
        options=[Option("o1", "A", True), Option("o2", "B", False)],
        source_chunk_id="c1", source_doc_title="Doc", generator="mock",
        review_status=ReviewStatus.PENDING, provenance_class=ProvenanceClass.DOCUMENTED,
    )


class TestChunks(unittest.TestCase):
    def test_save_then_all_chunks_round_trips(self):
        # Every field checked, not just two -- the MERGE statement binds 20 parameters
        # across a USING clause, an UPDATE SET and an INSERT VALUES, and a single swapped
        # pair would only show up in whichever field it landed in.
        conn = FakeConn()
        bank = SqlBank(conn, company_id=1)
        chunk = Chunk(chunk_id="c1", doc_id="d1", doc_title="Doc", topic="Intro",
                      section="Section One", page_start=3, page_end=5,
                      text="the actual passage", container="company-docs",
                      role_scope="ALL", company_id="1")
        bank.save_chunks([chunk])
        result = bank.all_chunks()
        self.assertEqual(len(result), 1)
        got = result[0]
        self.assertEqual(got.chunk_id, "c1")
        self.assertEqual(got.doc_id, "d1")
        self.assertEqual(got.doc_title, "Doc")
        self.assertEqual(got.topic, "Intro")
        self.assertEqual(got.section, "Section One")
        self.assertEqual(got.page_start, 3)
        self.assertEqual(got.page_end, 5)
        self.assertEqual(got.text, "the actual passage")
        self.assertEqual(got.container, "company-docs")
        self.assertEqual(got.role_scope, "ALL")

    def test_save_chunks_actually_writes_company_id(self):
        # The real bug: SourceChunks.company_id is NOT NULL, the MERGE didn't set it,
        # and the first real upload hit a live IntegrityError. all_chunks() always
        # fills company_id=str(self.company_id) on the Chunk it returns regardless of
        # what got written -- so a round-trip through bank.all_chunks() cannot catch
        # this class of bug. Only checking the stored row directly can.
        conn = FakeConn()
        SqlBank(conn, company_id=7).save_chunks([make_chunk()])
        self.assertEqual(conn.tables["SourceChunks"][0]["company_id"], 7)

    def test_save_chunks_upserts_not_duplicates(self):
        conn = FakeConn()
        bank = SqlBank(conn, company_id=1)
        bank.save_chunks([make_chunk()])
        bank.save_chunks([make_chunk()])  # same chunk_id again
        self.assertEqual(len(bank.all_chunks()), 1, "re-saving the same chunk_id duplicated it")

    def test_set_chunk_roles_only_touches_matching_topic(self):
        conn = FakeConn()
        bank = SqlBank(conn, company_id=1)
        bank.save_chunks([make_chunk(chunk_id="c1", topic="Intro"),
                           make_chunk(chunk_id="c2", topic="Other")])
        tagged = bank.set_chunk_roles("Doc", {"Intro": "SDE2"})
        self.assertEqual(tagged, 1)
        by_id = {c.chunk_id: c for c in bank.all_chunks()}
        self.assertEqual(by_id["c1"].role_scope, "SDE2")
        self.assertEqual(by_id["c2"].role_scope, "ALL", "untouched topic changed role_scope")


class TestQuestions(unittest.TestCase):
    def test_save_questions_writes_options_and_marks_written(self):
        conn = FakeConn()
        bank = SqlBank(conn, company_id=1)
        written = bank.save_questions([make_question()])
        self.assertEqual(written, 1)
        self.assertEqual(len(conn.tables["GeneratedQuestions"]), 1)
        self.assertEqual(len(conn.tables["GeneratedOptions"]), 2)
        self.assertEqual(conn.tables["GeneratedQuestions"][0]["company_id"], 1)

    def test_save_questions_never_overwrites_existing_review_decision(self):
        conn = FakeConn()
        bank = SqlBank(conn, company_id=1)
        bank.save_questions([make_question()])
        conn.tables["GeneratedQuestions"][0]["review_status"] = "Approved"  # a human decided
        bank.save_questions([make_question()])  # regenerate, same question_id
        self.assertEqual(conn.tables["GeneratedQuestions"][0]["review_status"], "Approved",
                          "regenerating clobbered a human review decision")
        self.assertEqual(len(conn.tables["GeneratedQuestions"]), 1, "duplicated the question")

    def test_chunk_ids_with_questions_scopes_by_company(self):
        conn = FakeConn()
        SqlBank(conn, company_id=1).save_questions([make_question()])
        self.assertEqual(SqlBank(conn, company_id=1).chunk_ids_with_questions(), {"c1"})
        self.assertEqual(SqlBank(conn, company_id=2).chunk_ids_with_questions(), set(),
                          "a different company saw another company's generated question")

    def test_retire_document_questions_scopes_by_company(self):
        conn = FakeConn()
        SqlBank(conn, company_id=1).save_questions([make_question()])
        retired = SqlBank(conn, company_id=2).retire_document_questions("Doc")
        self.assertEqual(retired, 0, "retired another company's questions")
        retired = SqlBank(conn, company_id=1).retire_document_questions("Doc")
        self.assertEqual(retired, 1)
        self.assertEqual(conn.tables["GeneratedQuestions"][0]["review_status"], "Rejected")


class TestRoles(unittest.TestCase):
    def test_add_then_list_roles(self):
        conn = FakeConn()
        bank = SqlBank(conn, company_id=1)
        bank.add_role("sde2", "SDE 2", "Mid-level engineer")
        roles = bank.roles()
        self.assertEqual(roles, [{"role_code": "SDE2", "title": "SDE 2",
                                   "description": "Mid-level engineer"}])

    def test_add_role_upserts_on_re_add(self):
        conn = FakeConn()
        bank = SqlBank(conn, company_id=1)
        bank.add_role("SDE2", "SDE 2", "first")
        bank.add_role("SDE2", "SDE 2", "second")
        roles = bank.roles()
        self.assertEqual(len(roles), 1, "re-adding the same role_code duplicated it")
        self.assertEqual(roles[0]["description"], "second")

    def test_roles_scoped_by_company(self):
        conn = FakeConn()
        SqlBank(conn, company_id=1).add_role("SDE2", "SDE 2")
        self.assertEqual(SqlBank(conn, company_id=2).roles(), [],
                          "a different company saw another company's role catalog")

    def test_remove_role(self):
        conn = FakeConn()
        bank = SqlBank(conn, company_id=1)
        bank.add_role("SDE2", "SDE 2")
        self.assertEqual(bank.remove_role("SDE2"), 1)
        self.assertEqual(bank.roles(), [])
        self.assertEqual(bank.remove_role("SDE2"), 0, "removing twice should be a no-op, not an error")


class TestGenerationJobs(unittest.TestCase):
    def test_create_update_get_round_trips(self):
        conn = FakeConn()
        job_id = new_job_id()
        create_job(conn, job_id, 1, "Doc")
        self.assertEqual(get_job(conn, job_id, 1)["state"], "running")

        update_job(conn, job_id, 1, total=5, message="Reading 5 section(s)…")
        job = get_job(conn, job_id, 1)
        self.assertEqual(job["total"], 5)
        self.assertEqual(job["state"], "running", "unrelated field update changed state")

        update_job(conn, job_id, 1, state="done", written=3)
        job = get_job(conn, job_id, 1)
        self.assertEqual(job["state"], "done")
        self.assertEqual(job["written"], 3)
        self.assertIsNotNone(job["finishedAt"], "state=done did not stamp finished_at")

    def test_get_job_scoped_by_company(self):
        conn = FakeConn()
        job_id = new_job_id()
        create_job(conn, job_id, 1, "Doc")
        self.assertIsNone(get_job(conn, job_id, 2),
                           "a different company could read another company's job")

    def test_get_unknown_job_is_none_not_an_error(self):
        conn = FakeConn()
        self.assertIsNone(get_job(conn, "job_doesnotexist", 1))


if __name__ == "__main__":
    unittest.main()
