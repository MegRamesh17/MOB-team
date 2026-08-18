"""
Azure SQL-backed storage for the upload/generate pipeline. No SQLite, anywhere.

WHY THIS EXISTS
The upload -> extract -> generate pipeline (src/quizgen/pipeline.py, rolemap.py,
ingest.py) is written against `Bank`, which is `sqlite3` from top to bottom -- it opens a
file, owns the schema, and every read and write goes through that one connection. That is
correct for local development and the offline `QUIZGEN_PROVIDER=mock` workflow, which
CLAUDE.md names as a hard constraint. It is wrong for the deployed Function, where Azure
SQL is the database and nothing else should hold data of record -- a SQLite scratch file
copied to Azure SQL afterward, even one that only lives in a Function's /tmp for one
request, is a second store with its own durability and deduplication problems for no
benefit once you are willing to write this class.

pipeline.py and rolemap.py take a `bank` parameter and call exactly nine methods on it
(save_chunks, all_chunks, roles, add_role, set_chunk_roles, chunk_ids_with_questions,
save_questions, retire_document_questions -- checked directly against both files rather
than assumed). Neither file imports Bank or does anything sqlite3-specific with it beyond
that. Python does not enforce the type hint, so an object that implements the same nine
methods against Azure SQL is a drop-in substitute with no change to either file.

SqlBank is that object. Every method mirrors the semantics of the corresponding Bank
method in src/quizgen/bank.py -- same INSERT-if-new / skip-if-exists behaviour for
questions, same "does not clobber an existing review decision" rule, same auto-approve
read from CONFIG -- against SourceChunks / GeneratedQuestions / GeneratedOptions /
GeneratedAnswerKeys (011) and QuizgenRoles (025) instead of chunks / questions / options /
answer_keys / roles. Every query is scoped to one company_id, always, matching the tenant
isolation the rest of api/function_app.py already enforces.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence

from quizgen.models import Chunk, Question, ReviewStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqlBank:
    """Azure SQL storage for one company, matching the subset of Bank's interface
    the generation pipeline actually calls."""

    def __init__(self, conn, company_id: int) -> None:
        self.conn = conn
        self.company_id = company_id

    # ------------------------------------------------------------------
    # chunks
    # ------------------------------------------------------------------

    def save_chunks(self, chunks: Iterable[Chunk]) -> int:
        cur = self.conn.cursor()
        n = 0
        for c in chunks:
            # MERGE rather than a blind INSERT: re-uploading the same document (a
            # manager fixing a typo and re-saving) must update the chunk in place, not
            # duplicate it or fail on the primary key.
            cur.execute(
                """MERGE dbo.SourceChunks AS target
                   USING (SELECT ? AS chunk_id) AS src ON target.chunk_id = src.chunk_id
                   WHEN MATCHED THEN UPDATE SET
                       doc_id = ?, doc_title = ?, section = ?, topic = ?,
                       page_start = ?, page_end = ?, chunk_text = ?, container = ?,
                       role_scope = ?
                   WHEN NOT MATCHED THEN INSERT
                       (chunk_id, doc_id, doc_title, section, topic, page_start,
                        page_end, chunk_text, container, role_scope, source_type)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'document');""",
                c.chunk_id,
                c.doc_id, c.doc_title, c.section, c.topic, c.page_start, c.page_end,
                c.text, c.container, c.role_scope,
                c.chunk_id, c.doc_id, c.doc_title, c.section, c.topic,
                c.page_start, c.page_end, c.text, c.container, c.role_scope,
            )
            n += 1
        self.conn.commit()
        return n

    def all_chunks(self) -> List[Chunk]:
        cur = self.conn.cursor()
        cur.execute(
            """SELECT chunk_id, doc_id, doc_title, topic, section, page_start,
                      page_end, chunk_text, container, role_scope
                 FROM dbo.SourceChunks
                -- company_id lives on GeneratedQuestions/GenerationJobs, not on
                -- SourceChunks directly (020 added it to the tables that needed
                -- tenant-scoped serving; a chunk is scoped by which container/role it
                -- was filed under, not a separate column). All chunks currently belong
                -- to one company in practice; if a second tenant starts uploading,
                -- SourceChunks needs its own company_id column the same way the other
                -- 020 tables got one -- filed as a known gap, not silently assumed safe.
                ORDER BY doc_title, page_start"""
        )
        return [
            Chunk(
                chunk_id=r.chunk_id, doc_id=r.doc_id, doc_title=r.doc_title,
                topic=r.topic, section=r.section, page_start=r.page_start,
                page_end=r.page_end, text=r.chunk_text, container=r.container or "",
                role_scope=r.role_scope or "ALL", company_id=str(self.company_id),
            )
            for r in cur.fetchall()
        ]

    def set_chunk_roles(self, doc_title: str, mapping: Dict[str, str]) -> int:
        cur = self.conn.cursor()
        n = 0
        for topic, role in mapping.items():
            cur.execute(
                "UPDATE dbo.SourceChunks SET role_scope = ? "
                "WHERE doc_title = ? AND topic = ?",
                (role or "ALL").upper(), doc_title, topic,
            )
            n += cur.rowcount
        self.conn.commit()
        return n

    # ------------------------------------------------------------------
    # questions
    # ------------------------------------------------------------------

    def chunk_ids_with_questions(self) -> set:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT DISTINCT source_chunk_id FROM dbo.GeneratedQuestions "
            "WHERE company_id = ? AND source_chunk_id IS NOT NULL",
            self.company_id,
        )
        return {r[0] for r in cur.fetchall()}

    def save_questions(
        self,
        questions: Sequence[Question],
        notes: Optional[Dict[str, List[str]]] = None,
    ) -> int:
        from quizgen.config import CONFIG

        notes = notes or {}
        auto = CONFIG.auto_approve
        cur = self.conn.cursor()
        written = 0
        for q in questions:
            # Same rule as Bank.save_questions: never overwrite an existing row. A
            # human (or a prior run's auto-approve) already made a call on this
            # question_id; regenerating must not silently discard that.
            cur.execute(
                "SELECT 1 FROM dbo.GeneratedQuestions WHERE question_id = ? AND company_id = ?",
                q.question_id, self.company_id,
            )
            if cur.fetchone():
                continue

            status = ReviewStatus.APPROVED.value if auto else q.review_status.value
            cur.execute(
                """INSERT INTO dbo.GeneratedQuestions
                       (question_id, topic, question_type, difficulty, prompt,
                        explanation, points, source_chunk_id, source_doc_title,
                        source_page, source_quote, generator, review_status,
                        provenance_class, role_code, role_requirement,
                        contradiction_notes, times_served, times_correct, company_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?)""",
                q.question_id, q.topic, q.question_type.value, q.difficulty.value,
                q.prompt, q.explanation, q.points, q.source_chunk_id or None,
                q.source_doc_title or None, q.source_page or None,
                q.source_quote or None, q.generator or None, status,
                q.provenance_class.value, q.role_code or None,
                q.role_requirement or None,
                "; ".join(notes.get(q.question_id, [])) or None,
                self.company_id,
            )
            for i, o in enumerate(q.options):
                cur.execute(
                    "INSERT INTO dbo.GeneratedOptions "
                    "(option_id, question_id, option_text, is_correct, sort_order) "
                    "VALUES (?,?,?,?,?)",
                    o.option_id, q.question_id, o.text, 1 if o.is_correct else 0, i,
                )
            for a in q.accepted_answers:
                cur.execute(
                    "IF NOT EXISTS (SELECT 1 FROM dbo.GeneratedAnswerKeys "
                    "               WHERE question_id = ? AND accepted_answer = ?) "
                    "INSERT INTO dbo.GeneratedAnswerKeys (question_id, accepted_answer) "
                    "VALUES (?, ?)",
                    q.question_id, a, q.question_id, a,
                )
            written += 1
        self.conn.commit()
        return written

    def retire_document_questions(self, doc_title: str) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE dbo.GeneratedQuestions SET review_status = ? "
            "WHERE source_doc_title = ? AND company_id = ?",
            ReviewStatus.REJECTED.value, doc_title, self.company_id,
        )
        n = cur.rowcount
        self.conn.commit()
        return n

    # ------------------------------------------------------------------
    # role catalog (dbo.QuizgenRoles, 025)
    # ------------------------------------------------------------------

    def roles(self) -> List[Dict[str, str]]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT role_code, title, description FROM dbo.QuizgenRoles "
            "WHERE company_id = ? ORDER BY title",
            self.company_id,
        )
        return [
            {"role_code": r.role_code, "title": r.title, "description": r.description or ""}
            for r in cur.fetchall()
        ]

    def add_role(self, role_code: str, title: str, description: str = "") -> None:
        cur = self.conn.cursor()
        code = role_code.upper()
        cur.execute(
            "IF EXISTS (SELECT 1 FROM dbo.QuizgenRoles WHERE role_code = ? AND company_id = ?) "
            "  UPDATE dbo.QuizgenRoles SET title = ?, description = ? "
            "  WHERE role_code = ? AND company_id = ?; "
            "ELSE "
            "  INSERT INTO dbo.QuizgenRoles (role_code, company_id, title, description) "
            "  VALUES (?, ?, ?, ?);",
            code, self.company_id, title, description, code, self.company_id,
            code, self.company_id, title, description,
        )
        self.conn.commit()

    def remove_role(self, role_code: str) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "DELETE FROM dbo.QuizgenRoles WHERE role_code = ? AND company_id = ?",
            role_code.upper(), self.company_id,
        )
        n = cur.rowcount
        self.conn.commit()
        return n


# ------------------------------------------------------------------------------
# GenerationJobs (025) -- a durable job record, since an in-memory dict does not
# survive across Azure Functions invocations that may land on a different instance.
# ------------------------------------------------------------------------------

def new_job_id() -> str:
    return "job_" + uuid.uuid4().hex[:12]


def create_job(conn, job_id: str, company_id: int, doc_title: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.GenerationJobs (job_id, company_id, doc_title, state) "
        "VALUES (?, ?, ?, 'running')",
        job_id, company_id, doc_title,
    )
    conn.commit()


def update_job(conn, job_id: str, company_id: int, **fields) -> None:
    """fields may include: state, total, done_count, kept, written, rejected, message.
    finished_at is set automatically when state is 'done' or 'error'."""
    sets, params = [], []
    for key, value in fields.items():
        sets.append("{} = ?".format(key))
        params.append(value)
    if fields.get("state") in ("done", "error"):
        sets.append("finished_at = SYSUTCDATETIME()")
    params.extend([job_id, company_id])
    cur = conn.cursor()
    cur.execute(
        "UPDATE dbo.GenerationJobs SET " + ", ".join(sets)
        + " WHERE job_id = ? AND company_id = ?",
        params,
    )
    conn.commit()


def get_job(conn, job_id: str, company_id: int) -> Optional[Dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT job_id, doc_title, state, total, done_count, kept, written,
                  rejected, message, finished_at
             FROM dbo.GenerationJobs WHERE job_id = ? AND company_id = ?""",
        job_id, company_id,
    )
    r = cur.fetchone()
    if r is None:
        return None
    return {
        "jobId": r.job_id, "title": r.doc_title, "state": r.state,
        "total": r.total, "done": r.done_count, "kept": r.kept,
        "written": r.written, "rejected": r.rejected, "message": r.message,
        "finishedAt": r.finished_at,
    }
