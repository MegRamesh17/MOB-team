"""
Local question bank (SQLite).

Table names and columns mirror the Azure SQL schema so moving this into the real
database later is a transport change rather than a redesign. Question statistics
(times_served / times_correct) live here rather than being recomputed from responses
each time, because difficulty selection reads them on every quiz assembly.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .models import (
    Attempt,
    ProvenanceClass,
    Chunk,
    Difficulty,
    Option,
    Question,
    QuestionType,
    Response,
    ReviewStatus,
    TopicMastery,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL,
    doc_title   TEXT NOT NULL,
    topic       TEXT NOT NULL,
    section     TEXT NOT NULL,
    page_start  INTEGER NOT NULL,
    page_end    INTEGER NOT NULL,
    text        TEXT NOT NULL,
    container   TEXT NOT NULL DEFAULT '',
    role_scope  TEXT NOT NULL DEFAULT 'ALL'
);
CREATE INDEX IF NOT EXISTS ix_chunks_scope ON chunks(role_scope);

CREATE TABLE IF NOT EXISTS questions (
    question_id      TEXT PRIMARY KEY,
    topic            TEXT NOT NULL,
    question_type    TEXT NOT NULL,
    difficulty       TEXT NOT NULL,
    prompt           TEXT NOT NULL,
    explanation      TEXT,
    points           INTEGER NOT NULL DEFAULT 1,
    source_chunk_id  TEXT,
    source_doc_title TEXT,
    source_page      INTEGER,
    source_quote     TEXT,
    generator        TEXT,
    review_status    TEXT NOT NULL DEFAULT 'PendingReview',
    provenance_class TEXT NOT NULL DEFAULT 'Documented',
    role_code        TEXT,
    role_requirement TEXT,
    contradiction_notes TEXT,
    times_served     INTEGER NOT NULL DEFAULT 0,
    times_correct    INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_questions_topic  ON questions(topic, review_status);
CREATE INDEX IF NOT EXISTS ix_questions_status ON questions(review_status);
CREATE INDEX IF NOT EXISTS ix_questions_class  ON questions(provenance_class, review_status);

CREATE TABLE IF NOT EXISTS options (
    option_id   TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    is_correct  INTEGER NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_options_question ON options(question_id);

CREATE TABLE IF NOT EXISTS answer_keys (
    question_id     TEXT NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
    accepted_answer TEXT NOT NULL,
    PRIMARY KEY (question_id, accepted_answer)
);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id      TEXT PRIMARY KEY,
    learner_id      TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    submitted_at    TEXT,
    score_percent   REAL,
    points_awarded  INTEGER,
    points_possible INTEGER,
    passed          INTEGER
);
CREATE INDEX IF NOT EXISTS ix_attempts_learner ON attempts(learner_id, started_at DESC);

CREATE TABLE IF NOT EXISTS responses (
    response_id   TEXT PRIMARY KEY,
    attempt_id    TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE CASCADE,
    learner_id    TEXT NOT NULL,
    question_id   TEXT NOT NULL,
    topic         TEXT NOT NULL,
    selected      TEXT,
    text_answer   TEXT,
    is_correct    INTEGER NOT NULL,
    points_awarded INTEGER NOT NULL DEFAULT 0,
    answered_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_responses_learner ON responses(learner_id, topic);
CREATE INDEX IF NOT EXISTS ix_responses_question ON responses(question_id);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Bank:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Bank":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- chunks ---------------------------------------------------------------

    def save_chunks(self, chunks: Iterable[Chunk]) -> int:
        rows = [
            (c.chunk_id, c.doc_id, c.doc_title, c.topic, c.section, c.page_start,
             c.page_end, c.text, c.container, c.role_scope)
            for c in chunks
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?)", rows
        )
        self.conn.commit()
        return len(rows)

    def all_chunks(self) -> List[Chunk]:
        return [
            Chunk(
                chunk_id=r["chunk_id"], doc_id=r["doc_id"], doc_title=r["doc_title"],
                topic=r["topic"], section=r["section"], page_start=r["page_start"],
                page_end=r["page_end"], text=r["text"],
                container=r["container"], role_scope=r["role_scope"],
            )
            for r in self.conn.execute("SELECT * FROM chunks ORDER BY doc_title, page_start")
        ]

    # --- questions ------------------------------------------------------------

    def save_questions(
        self,
        questions: Sequence[Question],
        notes: Optional[Dict[str, List[str]]] = None,
    ) -> int:
        """`notes` carries per-question contradiction findings for the reviewer."""
        notes = notes or {}
        written = 0
        for q in questions:
            # Do not clobber an existing review decision or accumulated statistics on
            # regeneration — a human already made a call on this question.
            existing = self.conn.execute(
                "SELECT review_status FROM questions WHERE question_id = ?", (q.question_id,)
            ).fetchone()
            if existing:
                continue

            self.conn.execute(
                """INSERT INTO questions (question_id, topic, question_type, difficulty, prompt,
                                          explanation, points, source_chunk_id, source_doc_title,
                                          source_page, source_quote, generator, review_status,
                                          provenance_class, role_code, role_requirement,
                                          contradiction_notes,
                                          times_served, times_correct, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?)""",
                (
                    q.question_id, q.topic, q.question_type.value, q.difficulty.value, q.prompt,
                    q.explanation, q.points, q.source_chunk_id, q.source_doc_title,
                    q.source_page, q.source_quote, q.generator, q.review_status.value,
                    q.provenance_class.value, q.role_code, q.role_requirement,
                    "; ".join(notes.get(q.question_id, [])), utcnow(),
                ),
            )
            for i, o in enumerate(q.options):
                self.conn.execute(
                    "INSERT INTO options VALUES (?,?,?,?,?)",
                    (o.option_id, q.question_id, o.text, 1 if o.is_correct else 0, i),
                )
            for a in q.accepted_answers:
                self.conn.execute(
                    "INSERT OR REPLACE INTO answer_keys VALUES (?,?)", (q.question_id, a)
                )
            written += 1
        self.conn.commit()
        return written

    def _hydrate(self, row: sqlite3.Row) -> Question:
        qid = row["question_id"]
        options = [
            Option(r["option_id"], r["text"], bool(r["is_correct"]))
            for r in self.conn.execute(
                "SELECT * FROM options WHERE question_id = ? ORDER BY sort_order", (qid,)
            )
        ]
        accepted = [
            r["accepted_answer"]
            for r in self.conn.execute(
                "SELECT accepted_answer FROM answer_keys WHERE question_id = ?", (qid,)
            )
        ]
        return Question(
            question_id=qid,
            topic=row["topic"],
            question_type=QuestionType(row["question_type"]),
            difficulty=Difficulty(row["difficulty"]),
            prompt=row["prompt"],
            options=options,
            accepted_answers=accepted,
            explanation=row["explanation"] or "",
            points=row["points"],
            source_chunk_id=row["source_chunk_id"] or "",
            source_doc_title=row["source_doc_title"] or "",
            source_page=row["source_page"] or 0,
            source_quote=row["source_quote"] or "",
            generator=row["generator"] or "",
            review_status=ReviewStatus(row["review_status"]),
            provenance_class=ProvenanceClass(row["provenance_class"] or "Documented"),
            role_code=row["role_code"] or "",
            role_requirement=row["role_requirement"] or "",
            times_served=row["times_served"],
            times_correct=row["times_correct"],
        )

    def questions(
        self,
        status: Optional[ReviewStatus] = None,
        topic: Optional[str] = None,
    ) -> List[Question]:
        sql = "SELECT * FROM questions WHERE 1=1"
        params: List[object] = []
        if status is not None:
            sql += " AND review_status = ?"
            params.append(status.value)
        if topic is not None:
            sql += " AND topic = ?"
            params.append(topic)
        sql += " ORDER BY topic, question_id"
        return [self._hydrate(r) for r in self.conn.execute(sql, params)]

    def get_question(self, question_id: str) -> Optional[Question]:
        row = self.conn.execute(
            "SELECT * FROM questions WHERE question_id = ?", (question_id,)
        ).fetchone()
        return self._hydrate(row) if row else None

    def set_review_status(self, question_ids: Sequence[str], status: ReviewStatus) -> int:
        self.conn.executemany(
            "UPDATE questions SET review_status = ? WHERE question_id = ?",
            [(status.value, qid) for qid in question_ids],
        )
        self.conn.commit()
        return len(question_ids)

    def chunk_ids_with_questions(self) -> set:
        """Chunks that already produced questions — used to make a resume free."""
        return {
            r["source_chunk_id"]
            for r in self.conn.execute(
                "SELECT DISTINCT source_chunk_id FROM questions WHERE source_chunk_id != ''"
            )
        }

    def topics(self) -> List[str]:
        return [
            r["topic"]
            for r in self.conn.execute(
                "SELECT DISTINCT topic FROM questions WHERE review_status = 'Approved' ORDER BY topic"
            )
        ]

    # --- attempts and responses -----------------------------------------------

    def save_attempt(self, attempt: Attempt) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO attempts
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                attempt.attempt_id, attempt.learner_id, attempt.started_at,
                attempt.submitted_at, attempt.score_percent, attempt.points_awarded,
                attempt.points_possible, 1 if attempt.passed else 0,
            ),
        )
        for r in attempt.responses:
            self.conn.execute(
                """INSERT OR REPLACE INTO responses
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    r.response_id, r.attempt_id, r.learner_id, r.question_id, r.topic,
                    ",".join(r.selected_option_ids), r.text_answer,
                    1 if r.is_correct else 0, r.points_awarded, r.answered_at,
                ),
            )
            # Empirical difficulty accumulates here.
            self.conn.execute(
                """UPDATE questions
                   SET times_served = times_served + 1,
                       times_correct = times_correct + ?
                   WHERE question_id = ?""",
                (1 if r.is_correct else 0, r.question_id),
            )
        self.conn.commit()

    def mastery(self, learner_id: str) -> Dict[str, TopicMastery]:
        rows = self.conn.execute(
            """SELECT topic,
                      COUNT(*)          AS answered,
                      SUM(is_correct)   AS correct
               FROM responses
               WHERE learner_id = ?
               GROUP BY topic""",
            (learner_id,),
        )
        return {
            r["topic"]: TopicMastery(r["topic"], r["answered"], r["correct"] or 0)
            for r in rows
        }

    def recently_seen(self, learner_id: str, last_n_attempts: int) -> List[str]:
        """Question IDs served in the learner's most recent attempts (cooldown set)."""
        attempt_ids = [
            r["attempt_id"]
            for r in self.conn.execute(
                "SELECT attempt_id FROM attempts WHERE learner_id = ? ORDER BY started_at DESC LIMIT ?",
                (learner_id, last_n_attempts),
            )
        ]
        if not attempt_ids:
            return []
        placeholders = ",".join("?" * len(attempt_ids))
        return [
            r["question_id"]
            for r in self.conn.execute(
                "SELECT DISTINCT question_id FROM responses WHERE attempt_id IN ({})".format(
                    placeholders
                ),
                attempt_ids,
            )
        ]

    def attempt_count(self, learner_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM attempts WHERE learner_id = ? AND submitted_at IS NOT NULL",
            (learner_id,),
        ).fetchone()
        return row["n"] if row else 0

    def stats(self) -> Dict[str, int]:
        def one(sql: str, *params) -> int:
            row = self.conn.execute(sql, params).fetchone()
            return row[0] if row else 0

        return {
            "chunks": one("SELECT COUNT(*) FROM chunks"),
            "questions": one("SELECT COUNT(*) FROM questions"),
            "approved": one("SELECT COUNT(*) FROM questions WHERE review_status='Approved'"),
            "pending": one("SELECT COUNT(*) FROM questions WHERE review_status='PendingReview'"),
            "rejected": one("SELECT COUNT(*) FROM questions WHERE review_status='Rejected'"),
            "attempts": one("SELECT COUNT(*) FROM attempts"),
            "responses": one("SELECT COUNT(*) FROM responses"),
        }
