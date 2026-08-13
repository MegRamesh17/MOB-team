"""
Push the local SQLite bank into Azure SQL.

The tables in migration 011 mirror the SQLite ones, so this is a copy rather than a
translation. Two properties it preserves that a naive dump would lose:

  * **Review status.** A question already Approved or Rejected in Azure keeps that
    decision on re-run. Overwriting a human's review call with a default would silently
    un-reject rejected content.
  * **Answer statistics.** times_served / times_correct accumulate from real learners in
    Azure, not locally. They are never overwritten from the local file.

Idempotent: run it as many times as you like. New questions are inserted, existing ones
have their content refreshed, review decisions and statistics are left alone.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .config import CONFIG


def _require_pyodbc():
    try:
        import pyodbc  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pyodbc is not installed ({}). Install it with:\n"
            "    .venv/bin/pip install pyodbc\n"
            "It also needs the Microsoft ODBC driver:\n"
            "    brew install msodbcsql18".format(exc)
        )
    import pyodbc

    return pyodbc


def connection_string() -> str:
    """
    Build the ODBC connection string from .env.

    Uses SQL auth to match what the team's GitHub Actions workflow already does.
    Managed identity would be better and is a drop-in change later.
    """
    server = CONFIG.sql_server
    database = CONFIG.sql_database
    user = CONFIG.sql_user
    password = CONFIG.sql_password

    missing = [
        name
        for name, value in (
            ("SQL_SERVER", server),
            ("SQL_DATABASE", database),
            ("SQL_USER", user),
            ("SQL_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Cannot connect to Azure SQL — these are unset in .env: {}\n"
            "SQL_PASSWORD is the same value as the SQL_ADMIN_PASSWORD secret used by "
            "the GitHub Actions workflow.".format(", ".join(missing))
        )

    return (
        "DRIVER={{ODBC Driver 18 for SQL Server}};"
        "SERVER=tcp:{},1433;DATABASE={};UID={};PWD={};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    ).format(server, database, user, password)


def _local_rows(table: str) -> List[sqlite3.Row]:
    conn = sqlite3.connect(str(CONFIG.db_path))
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute("SELECT * FROM {}".format(table)))
    finally:
        conn.close()


def load(dry_run: bool = False) -> Dict[str, int]:
    """Copy chunks, questions, options and answer keys into Azure SQL."""
    pyodbc = _require_pyodbc()

    chunks = _local_rows("chunks")
    questions = _local_rows("questions")
    options = _local_rows("options")
    answer_keys = _local_rows("answer_keys")

    counts = {
        "chunks": len(chunks),
        "questions": len(questions),
        "options": len(options),
        "answer_keys": len(answer_keys),
    }

    if dry_run:
        return counts

    conn = pyodbc.connect(connection_string(), autocommit=False)
    cur = conn.cursor()

    try:
        # --- chunks -----------------------------------------------------------
        for c in chunks:
            cur.execute(
                """
                MERGE dbo.SourceChunks AS t
                USING (SELECT ? AS chunk_id) AS s ON t.chunk_id = s.chunk_id
                WHEN MATCHED THEN UPDATE SET
                    doc_id=?, doc_title=?, section=?, topic=?, page_start=?, page_end=?,
                    chunk_text=?, container=?, role_scope=?
                WHEN NOT MATCHED THEN INSERT
                    (chunk_id, doc_id, doc_title, section, topic, page_start, page_end,
                     chunk_text, container, role_scope)
                    VALUES (?,?,?,?,?,?,?,?,?,?);
                """,
                c["chunk_id"],
                c["doc_id"], c["doc_title"], c["section"], c["topic"],
                c["page_start"], c["page_end"], c["text"],
                c["container"], c["role_scope"],
                c["chunk_id"], c["doc_id"], c["doc_title"], c["section"], c["topic"],
                c["page_start"], c["page_end"], c["text"],
                c["container"], c["role_scope"],
            )

        # --- questions --------------------------------------------------------
        # review_status, reviewed_by/at and the answer statistics are deliberately NOT
        # in the UPDATE list: those belong to Azure, where the reviewing and the
        # answering actually happen.
        for q in questions:
            cur.execute(
                """
                MERGE dbo.GeneratedQuestions AS t
                USING (SELECT ? AS question_id) AS s ON t.question_id = s.question_id
                WHEN MATCHED THEN UPDATE SET
                    topic=?, question_type=?, difficulty=?, prompt=?, explanation=?,
                    points=?, source_chunk_id=?, source_doc_title=?, source_page=?,
                    source_quote=?, provenance_class=?, role_code=?, role_requirement=?,
                    contradiction_notes=?, generator=?
                WHEN NOT MATCHED THEN INSERT
                    (question_id, topic, question_type, difficulty, prompt, explanation,
                     points, source_chunk_id, source_doc_title, source_page, source_quote,
                     provenance_class, role_code, role_requirement, contradiction_notes,
                     generator, review_status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
                """,
                q["question_id"],
                q["topic"], q["question_type"], q["difficulty"], q["prompt"],
                q["explanation"], q["points"], q["source_chunk_id"] or None,
                q["source_doc_title"], q["source_page"], q["source_quote"],
                q["provenance_class"], q["role_code"], q["role_requirement"],
                q["contradiction_notes"], q["generator"],
                q["question_id"],
                q["topic"], q["question_type"], q["difficulty"], q["prompt"],
                q["explanation"], q["points"], q["source_chunk_id"] or None,
                q["source_doc_title"], q["source_page"], q["source_quote"],
                q["provenance_class"], q["role_code"], q["role_requirement"],
                q["contradiction_notes"], q["generator"], q["review_status"],
            )

        # --- options ----------------------------------------------------------
        for o in options:
            cur.execute(
                """
                MERGE dbo.GeneratedOptions AS t
                USING (SELECT ? AS option_id) AS s ON t.option_id = s.option_id
                WHEN MATCHED THEN UPDATE SET
                    question_id=?, option_text=?, is_correct=?, sort_order=?
                WHEN NOT MATCHED THEN INSERT
                    (option_id, question_id, option_text, is_correct, sort_order)
                    VALUES (?,?,?,?,?);
                """,
                o["option_id"],
                o["question_id"], o["text"], o["is_correct"], o["sort_order"],
                o["option_id"], o["question_id"], o["text"], o["is_correct"], o["sort_order"],
            )

        # --- answer keys ------------------------------------------------------
        for k in answer_keys:
            cur.execute(
                """
                IF NOT EXISTS (SELECT 1 FROM dbo.GeneratedAnswerKeys
                               WHERE question_id = ? AND accepted_answer = ?)
                    INSERT INTO dbo.GeneratedAnswerKeys (question_id, accepted_answer)
                    VALUES (?, ?);
                """,
                k["question_id"], k["accepted_answer"],
                k["question_id"], k["accepted_answer"],
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return counts


def verify() -> Dict[str, Any]:
    """Read back what actually landed, so a load can be checked rather than assumed."""
    pyodbc = _require_pyodbc()
    conn = pyodbc.connect(connection_string())
    cur = conn.cursor()
    out: Dict[str, Any] = {}
    try:
        for label, sql in (
            ("chunks", "SELECT COUNT(*) FROM dbo.SourceChunks"),
            ("questions", "SELECT COUNT(*) FROM dbo.GeneratedQuestions"),
            ("options", "SELECT COUNT(*) FROM dbo.GeneratedOptions"),
            ("answer_keys", "SELECT COUNT(*) FROM dbo.GeneratedAnswerKeys"),
            ("approved", "SELECT COUNT(*) FROM dbo.GeneratedQuestions WHERE review_status='Approved'"),
            ("pending", "SELECT COUNT(*) FROM dbo.GeneratedQuestions WHERE review_status='PendingReview'"),
        ):
            cur.execute(sql)
            out[label] = cur.fetchone()[0]

        # The check that matters: a question with no single correct answer grades every
        # learner to zero on it.
        cur.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT q.question_id
                FROM dbo.GeneratedQuestions q
                LEFT JOIN dbo.GeneratedOptions o ON o.question_id = q.question_id
                WHERE q.question_type <> 'FillInBlank'
                GROUP BY q.question_id
                HAVING SUM(CAST(o.is_correct AS INT)) <> 1
            ) bad
            """
        )
        out["broken_questions"] = cur.fetchone()[0]
    finally:
        cur.close()
        conn.close()
    return out
