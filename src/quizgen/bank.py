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

from . import pet_shop
from .config import CONFIG
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
CREATE TABLE IF NOT EXISTS roles (
    -- The company's role list. Managers own it: the AI maps documents onto these
    -- roles but never invents one the company didn't ask for.
    role_code   TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

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
    role_scope  TEXT NOT NULL DEFAULT 'ALL',
    source_type TEXT NOT NULL DEFAULT 'document',
    source_url  TEXT,
    fetched_at  TEXT,
    company_id  TEXT NOT NULL DEFAULT ''
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
    source_url       TEXT,
    source_fetched_at TEXT,
    generator        TEXT,
    review_status    TEXT NOT NULL DEFAULT 'PendingReview',
    provenance_class TEXT NOT NULL DEFAULT 'Documented',
    role_code        TEXT,
    role_requirement TEXT,
    rubric_json      TEXT,
    fallback_json    TEXT,
    grading_version  TEXT,
    contradiction_notes TEXT,
    times_served     INTEGER NOT NULL DEFAULT 0,
    times_correct    INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    company_id       TEXT NOT NULL DEFAULT ''
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
    passed          INTEGER,
    company_id      TEXT NOT NULL DEFAULT ''
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
    answered_at   TEXT NOT NULL,
    company_id    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_responses_learner ON responses(learner_id, topic);
CREATE INDEX IF NOT EXISTS ix_responses_question ON responses(question_id);

-- Which trainings a role must complete. This is Coverage's DENOMINATOR: without it
-- "7 of 7 done" has no 7, and a Q Score cannot be computed at all. Set by an admin,
-- never inferred from what happens to be in the bank — otherwise uploading a document
-- silently moves everyone's score, and the number stops meaning "you are up to date".
CREATE TABLE IF NOT EXISTS role_requirements (
    role_code   TEXT NOT NULL,
    doc_title   TEXT NOT NULL,
    -- behavioural | technical. Tracked separately so "strong technically, thin on
    -- conduct" stays visible instead of being averaged into one number.
    category    TEXT NOT NULL DEFAULT 'technical',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (role_code, doc_title)
);
CREATE INDEX IF NOT EXISTS ix_role_requirements_role ON role_requirements(role_code);

-- Track D: manager-submitted trusted reference URLs. Mirrors Azure SQL's TrustedLinks
-- (026_create_trusted_links.sql) -- scope='team' targets one role in the caller's own
-- reporting subtree; scope='company_wide' is admin/executive-only and retires the
-- previous active company-wide row (only one active at a time).
CREATE TABLE IF NOT EXISTS trusted_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    added_by    TEXT NOT NULL,
    scope       TEXT NOT NULL,
    role_code   TEXT NOT NULL,
    url         TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    company_id  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_trusted_links_company ON trusted_links(company_id, is_active);

-- Certificates. One row per pass, never edited afterwards.
CREATE TABLE IF NOT EXISTS certificates (
    certificate_id  TEXT PRIMARY KEY,
    learner_id      TEXT NOT NULL,
    doc_title       TEXT NOT NULL,
    attempt_id      TEXT NOT NULL,
    -- The difficulty-weighted score for THIS attempt (docs/q-score.md). Stored because
    -- it is a fact about an event that happened; the Q Score built on top of it is not
    -- stored, because it changes when nobody acts.
    attempt_score   REAL NOT NULL,
    category        TEXT NOT NULL DEFAULT 'technical',
    issued_at       TEXT NOT NULL,
    -- Absolute, computed at issue from validity_months. Storing the date rather than
    -- the interval means changing the default validity later does not silently
    -- re-date every certificate already earned.
    expires_at      TEXT NOT NULL,
    -- Placeholder until the certificate artefact exists. Deliberately nullable rather
    -- than a fake URL: a link that 404s is worse than an honest absence.
    certificate_url TEXT,
    company_id      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_certificates_learner ON certificates(learner_id, doc_title);

-- Pet shop. One row per item a learner owns; `equipped` marks which owned item (at most
-- one per slot, enforced in Bank.pet_equip) is currently worn. Mirrors Azure SQL's
-- dbo.PetPurchases (031_create_pet_purchases.sql).
CREATE TABLE IF NOT EXISTS pet_purchases (
    learner_id    TEXT NOT NULL,
    item_id       TEXT NOT NULL,
    equipped      INTEGER NOT NULL DEFAULT 0,
    purchased_at  TEXT NOT NULL,
    company_id    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (learner_id, item_id)
);
CREATE INDEX IF NOT EXISTS ix_pet_purchases_learner ON pet_purchases(learner_id);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Bank:
    """
    The local question bank, scoped to one company.

    TENANCY IS ENFORCED HERE, not at the call sites. Every read filters on company_id and
    every write stamps it, so a query cannot forget — the alternative is a WHERE clause
    repeated in forty places, where the one that gets missed returns another company's
    rows and looks exactly like a query that worked.

    `company_id` defaults to CONFIG.company_id rather than to "no filtering". A permissive
    default is the bug this exists to prevent; there is no way to open a Bank that sees
    everything by accident. ALL_COMPANIES is the deliberate escape hatch for admin tooling
    and migrations, and it is a distinct, greppable string for exactly that reason.
    """

    #: Opt out of tenant scoping. Deliberately ugly and searchable.
    ALL_COMPANIES = "*all-companies*"

    def __init__(self, db_path: Path, company_id: Optional[str] = None) -> None:
        self.company_id = company_id if company_id is not None else CONFIG.company_id
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # timeout raises the busy wait well above the 5s default. Question generation
        # writes after every chunk while learners are taking quizzes on the same
        # database; measured, a write held past 5s made a learner's quiz submit fail
        # outright with "database is locked" — losing an attempt they had finished.
        self.conn = sqlite3.connect(str(db_path), timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets readers carry on while a writer holds the lock, so a learner can
        # still load a quiz while a manager's upload is generating. Not supported on
        # some network filesystems, where the default journal is the correct fallback.
        try:
            self.conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        self.conn.executescript(SCHEMA)
        self._ensure_columns()
        self.conn.commit()

    def _ensure_columns(self) -> None:
        """
        Add columns that CREATE TABLE IF NOT EXISTS cannot.

        A bank created before a column existed keeps its old shape — the guarded CREATE
        is a no-op on an existing table, so a new column is silently absent and every
        read of it raises. Anyone with a `quizgen.db` from before company_id would have
        hit that on their next quiz.

        Kept deliberately small: name the column, its type, its default. Adding a
        column is the only migration SQLite does cheaply, and the only one this needs.
        """
        # Empty is not a usable value — isolation.validate_company_id rejects it and
        # search_index.upload refuses to index it. It exists so an existing bank can be
        # opened at all; rows still have to be re-tagged before they mean anything.
        tenant_column = ("company_id", "TEXT NOT NULL DEFAULT ''")
        additions = {
            "chunks": [
                tenant_column,
                ("source_type", "TEXT NOT NULL DEFAULT 'document'"),
                ("source_url", "TEXT"),
                ("fetched_at", "TEXT"),
            ],
            "questions": [
                tenant_column,
                ("rubric_json", "TEXT"),
                ("fallback_json", "TEXT"),
                ("grading_version", "TEXT"),
                ("source_url", "TEXT"),
                ("source_fetched_at", "TEXT"),
            ],
            "attempts": [tenant_column],
            "responses": [tenant_column],
            "certificates": [tenant_column],
        }
        for table, columns in additions.items():
            existing = {r["name"] for r in self.conn.execute(
                "PRAGMA table_info({})".format(table))}
            for name, spec in columns:
                if name in existing:
                    continue
                self.conn.execute(
                    "ALTER TABLE {} ADD COLUMN {} {}".format(table, name, spec))

                # Backfill an existing bank to this process's company, EXCEPT chunks.
                #
                # Without this, opening a bank written before the column makes every
                # question, attempt and certificate vanish: the rows get '' and the
                # filter looks for a real company id. An empty app is a worse failure
                # than a wrong one here, because it looks like data loss.
                #
                # chunks are excluded deliberately. They are the one thing that reaches
                # a SHARED search index, where an untagged chunk is retrievable by every
                # company (docs/company-isolation-gap.md). Guessing a tenant for those
                # is the permissive default that whole design rejects, so they stay
                # empty and must be re-ingested — and there is a test for it.
                if name == "company_id" and table != "chunks" and self._scoped:
                    self.conn.execute(
                        "UPDATE {} SET company_id = ? WHERE company_id = ''".format(table),
                        (self.company_id,))

    @property
    def _scoped(self) -> bool:
        return self.company_id != self.ALL_COMPANIES

    def _where(self, alias: str = "") -> str:
        """A leading AND-clause scoping to this tenant, or nothing when opted out."""
        if not self._scoped:
            return ""
        prefix = "{}.".format(alias) if alias else ""
        return " AND {}company_id = :company_id".format(prefix)

    def _params(self, **kw) -> dict:
        if self._scoped:
            kw["company_id"] = self.company_id
        return kw

    def _where_qmark(self, alias: str = "") -> str:
        """
        The same clause in qmark style, for queries that build a positional list.

        sqlite3 refuses to mix ":name" and "?" in one statement, and several callers here
        already accumulate a params list. Two helpers is less error-prone than converting
        those to named parameters and getting one of them wrong.
        """
        if not self._scoped:
            return ""
        prefix = "{}.".format(alias) if alias else ""
        return " AND {}company_id = ?".format(prefix)

    def _scope_params(self, params: list) -> list:
        """Append the tenant value to a positional list, when scoped."""
        return params + [self.company_id] if self._scoped else params

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
             c.page_end, c.text, c.container, c.role_scope, c.source_type,
             c.source_url or None, c.fetched_at or None,
             # The BANK's tenant, not the chunk's. A chunk carrying a
             # different company_id than the bank it is being written to is a
             # bug; taking the bank's value means it cannot silently land in
             # the wrong tenant.
             self.company_id)
            for c in chunks
        ]
        # Columns named explicitly rather than relying on positional VALUES: an
        # existing bank migrated by _ensure_columns has company_id appended last,
        # while a freshly created one gets it from CREATE TABLE. Naming them makes
        # both orders work and stops the next added column breaking this silently.
        self.conn.executemany(
            "INSERT OR REPLACE INTO chunks "
            "(chunk_id, doc_id, doc_title, topic, section, page_start, page_end, "
            " text, container, role_scope, source_type, source_url, fetched_at, company_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
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
                company_id=r["company_id"],
                source_type=r["source_type"] or "document",
                source_url=r["source_url"] or "",
                fetched_at=r["fetched_at"] or "",
            )
            for r in self.conn.execute(
                "SELECT * FROM chunks WHERE 1=1" + self._where()
                + " ORDER BY doc_title, page_start", self._params())
        ]

    # --- questions ------------------------------------------------------------

    def save_questions(
        self,
        questions: Sequence[Question],
        notes: Optional[Dict[str, List[str]]] = None,
    ) -> int:
        """`notes` carries per-question contradiction findings for the reviewer."""
        notes = notes or {}
        # No reviewer capacity on this team, so generated questions go live once the
        # mechanical checks pass. Set QUIZGEN_AUTO_APPROVE=false to reinstate the gate.
        from .config import CONFIG

        auto = CONFIG.auto_approve
        written = 0
        for q in questions:
            # Do not clobber an existing review decision or accumulated statistics on
            # regeneration — a human already made a call on this question.
            existing = self.conn.execute(
                "SELECT review_status FROM questions WHERE question_id = :qid" + self._where(),
                self._params(qid=q.question_id)
            ).fetchone()
            if existing:
                continue

            self.conn.execute(
                """INSERT INTO questions (question_id, topic, question_type, difficulty, prompt,
                                          explanation, points, source_chunk_id, source_doc_title,
                                          source_page, source_quote, generator, review_status,
                                          source_url, source_fetched_at,
                                          provenance_class, role_code, role_requirement,
                                          rubric_json, fallback_json, grading_version,
                                          contradiction_notes,
                                          times_served, times_correct, created_at,
                                          company_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?)""",
                (
                    q.question_id, q.topic, q.question_type.value, q.difficulty.value, q.prompt,
                    q.explanation, q.points, q.source_chunk_id, q.source_doc_title,
                    q.source_page, q.source_quote, q.generator,
                    # Auto-approved when no reviewer is available; the mechanical
                    # checks in validators.py are then the only gate.
                    ReviewStatus.APPROVED.value if auto else q.review_status.value,
                    q.source_url or None, q.source_fetched_at or None,
                    q.provenance_class.value, q.role_code, q.role_requirement,
                    q.rubric_json or None, q.fallback_json or None,
                    q.grading_version or None,
                    "; ".join(notes.get(q.question_id, [])), utcnow(),
                    self.company_id,
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
            rubric_json=row["rubric_json"] or "",
            fallback_json=row["fallback_json"] or "",
            grading_version=row["grading_version"] or "",
            explanation=row["explanation"] or "",
            points=row["points"],
            source_chunk_id=row["source_chunk_id"] or "",
            source_doc_title=row["source_doc_title"] or "",
            source_page=row["source_page"] or 0,
            source_quote=row["source_quote"] or "",
            source_url=row["source_url"] or "",
            source_fetched_at=row["source_fetched_at"] or "",
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
        # Tenant clause first so every branch below inherits it. qmark style, because
        # the optional filters below build a positional list.
        sql = "SELECT * FROM questions WHERE 1=1" + self._where_qmark()
        params: List[object] = self._scope_params([])
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
            "SELECT * FROM questions WHERE question_id = :qid" + self._where(),
            self._params(qid=question_id)
        ).fetchone()
        return self._hydrate(row) if row else None

    # ------------------------------------------------------------------
    # roles
    # ------------------------------------------------------------------

    def roles(self) -> List[Dict[str, str]]:
        rows = self.conn.execute(
            "SELECT role_code, title, description FROM roles ORDER BY title"
        )
        return [dict(r) for r in rows]

    def add_role(self, role_code: str, title: str, description: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO roles (role_code, title, description, created_at) "
            "VALUES (?,?,?,?)",
            (role_code.upper(), title, description, utcnow()),
        )
        self.conn.commit()

    def remove_role(self, role_code: str) -> int:
        cur = self.conn.execute("DELETE FROM roles WHERE role_code = ?", (role_code.upper(),))
        self.conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # trusted links -- mirrors SqlBank.add_trusted_link/trusted_links (026)
    # ------------------------------------------------------------------

    def add_trusted_link(self, added_by: str, scope: str, role_code: str, url: str) -> int:
        """
        Insert a trusted link. For scope='company_wide', retires the previous active
        company-wide link first -- exactly one active at a time, same rule as the Azure
        SQL side. Returns the new row's id.
        """
        if scope == "company_wide":
            self.conn.execute(
                "UPDATE trusted_links SET is_active = 0 "
                "WHERE scope = 'company_wide' AND is_active = 1" + self._where(),
                self._params(),
            )
        cur = self.conn.execute(
            "INSERT INTO trusted_links (added_by, scope, role_code, url, created_at, company_id) "
            "VALUES (?,?,?,?,?,?)",
            (added_by, scope, role_code.upper(), url, utcnow(), self.company_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def trusted_links(self) -> List[Dict[str, object]]:
        rows = self.conn.execute(
            "SELECT id, added_by, scope, role_code, url, is_active, created_at "
            "FROM trusted_links WHERE 1=1" + self._where()
            + " ORDER BY created_at DESC",
            self._params(),
        )
        return [
            {
                "id": r["id"], "scope": r["scope"], "roleCode": r["role_code"],
                "url": r["url"], "isActive": bool(r["is_active"]),
                "createdAt": r["created_at"], "addedBy": r["added_by"],
            }
            for r in rows
        ]

    def set_chunk_roles(self, doc_title: str, mapping: Dict[str, str]) -> int:
        """
        Apply a topic -> role_code mapping to one document's chunks.

        This is where the manager's confirmed role assignment lands. Questions
        generated afterwards inherit role_code from their chunk, which is what the
        serving-side isolation filters on — so a Sales Manager can never be served
        Cloud DevOps material.
        """
        n = 0
        for topic, role in mapping.items():
            cur = self.conn.execute(
                "UPDATE chunks SET role_scope = ? WHERE doc_title = ? AND topic = ?",
                ((role or "ALL").upper(), doc_title, topic),
            )
            n += cur.rowcount
        self.conn.commit()
        return n

    def retire_document_questions(self, doc_title: str) -> int:
        """
        Stop serving a superseded document's questions.

        Used when the AI judges a new upload to be an update of an existing module.
        Attempts and passes already earned are untouched — a certificate holds until
        its normal one-year expiry — but no NEW quiz will contain these questions,
        because serving only ever selects Approved ones.
        """
        cur = self.conn.execute(
            "UPDATE questions SET review_status = ? WHERE source_doc_title = ?",
            (ReviewStatus.REJECTED.value, doc_title),
        )
        self.conn.commit()
        return cur.rowcount

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
                + self._where(), self._params()
            )
        }

    def topics(self) -> List[str]:
        return [
            r["topic"]
            for r in self.conn.execute(
                "SELECT DISTINCT topic FROM questions WHERE review_status = 'Approved'"
                + self._where() + " ORDER BY topic", self._params()
            )
        ]

    # --- attempts and responses -----------------------------------------------

    def save_attempt(self, attempt: Attempt) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO attempts
               (attempt_id, learner_id, started_at, submitted_at, score_percent,
                points_awarded, points_possible, passed, company_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                attempt.attempt_id, attempt.learner_id, attempt.started_at,
                attempt.submitted_at, attempt.score_percent, attempt.points_awarded,
                attempt.points_possible, 1 if attempt.passed else 0,
                self.company_id,
            ),
        )
        for r in attempt.responses:
            self.conn.execute(
                """INSERT OR REPLACE INTO responses
                   (response_id, attempt_id, learner_id, question_id, topic, selected,
                    text_answer, is_correct, points_awarded, answered_at, company_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r.response_id, r.attempt_id, r.learner_id, r.question_id, r.topic,
                    ",".join(r.selected_option_ids), r.text_answer,
                    1 if r.is_correct else 0, r.points_awarded, r.answered_at,
                    self.company_id,
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

    def mastery(self, learner_id: str, grain: str = "") -> Dict[str, TopicMastery]:
        """
        Accuracy per topic for one learner.

        `grain` selects what a "topic" is — see CONFIG.mastery_grain. At subject grain
        the response is attributed to its source document rather than its section
        heading, which is joined from questions because responses only stores the
        fine-grained topic. Responses whose question has since been deleted fall back
        to the stored topic rather than vanishing from the learner's record.
        """
        grain = (grain or CONFIG.mastery_grain).lower()

        if grain == "subject":
            sql = """SELECT COALESCE(NULLIF(q.source_doc_title, ''), r.topic) AS grp,
                            COUNT(*)            AS answered,
                            SUM(r.is_correct)   AS correct
                     FROM responses r
                     LEFT JOIN questions q ON q.question_id = r.question_id
                     WHERE r.learner_id = :learner""" + self._where("r") + """
                     GROUP BY grp"""
        else:
            sql = """SELECT topic AS grp,
                            COUNT(*)          AS answered,
                            SUM(is_correct)   AS correct
                     FROM responses
                     WHERE learner_id = :learner""" + self._where() + """
                     GROUP BY grp"""

        rows = self.conn.execute(sql, self._params(learner=learner_id))
        return {
            r["grp"]: TopicMastery(r["grp"], r["answered"], r["correct"] or 0)
            for r in rows
        }

    def recently_seen(self, learner_id: str, last_n_attempts: int) -> List[str]:
        """Question IDs served in the learner's most recent attempts (cooldown set)."""
        attempt_ids = [
            r["attempt_id"]
            for r in self.conn.execute(
                "SELECT attempt_id FROM attempts WHERE learner_id = :learner" + self._where()
                + " ORDER BY started_at DESC LIMIT :limit",
                self._params(learner=learner_id, limit=last_n_attempts),
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
                ) + self._where_qmark(),
                self._scope_params(attempt_ids),
            )
        ]

    def attempt_count(self, learner_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM attempts WHERE learner_id = :learner "
            "AND submitted_at IS NOT NULL" + self._where(),
            self._params(learner=learner_id),
        ).fetchone()
        return row["n"] if row else 0

    def submitted_attempts(self, learner_id: str) -> List[Dict]:
        """
        Every submitted attempt's date and score, for qscore.training_streak and
        qscore.earned_badges -- attempt_count above only has the total, not each
        attempt's own submitted_at/score_percent.
        """
        return [
            {"submitted_at": r["submitted_at"], "score_percent": r["score_percent"]}
            for r in self.conn.execute(
                "SELECT submitted_at, score_percent FROM attempts "
                "WHERE learner_id = :learner AND submitted_at IS NOT NULL" + self._where(),
                self._params(learner=learner_id),
            )
        ]

    def stats(self) -> Dict[str, int]:
        # Counts are tenant-scoped like everything else. An unscoped total would report
        # another company's volume on this company's dashboard — less severe than leaking
        # their content, still their business and not ours to show.
        def one(sql: str) -> int:
            row = self.conn.execute(sql + self._where(), self._params()).fetchone()
            return row[0] if row else 0

        return {
            "chunks": one("SELECT COUNT(*) FROM chunks WHERE 1=1"),
            "questions": one("SELECT COUNT(*) FROM questions WHERE 1=1"),
            "approved": one("SELECT COUNT(*) FROM questions WHERE review_status='Approved'"),
            "pending": one("SELECT COUNT(*) FROM questions WHERE review_status='PendingReview'"),
            "rejected": one("SELECT COUNT(*) FROM questions WHERE review_status='Rejected'"),
            "attempts": one("SELECT COUNT(*) FROM attempts WHERE 1=1"),
            "responses": one("SELECT COUNT(*) FROM responses WHERE 1=1"),
        }

    # --- role requirements & certificates -------------------------------------

    def set_role_requirements(self, role_code: str, items: Iterable[dict]) -> int:
        """
        Replace the required training list for a role.

        Replace rather than merge: the list is a statement of what a role must complete
        now, and merging would make removing a requirement impossible through the same
        interface that adds one.
        """
        role = (role_code or "").strip().upper()
        rows = [(role, str(i["doc_title"]),
                 (i.get("category") or "technical").strip().lower(), utcnow())
                for i in items if i.get("doc_title")]
        self.conn.execute("DELETE FROM role_requirements WHERE role_code = ?", (role,))
        self.conn.executemany(
            "INSERT INTO role_requirements (role_code, doc_title, category, created_at) "
            "VALUES (?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def add_role_requirement(self, role_code: str, doc_title: str, category: str = "technical") -> bool:
        """
        Add ONE role/doc pair to the required list, without touching whatever else
        that role already requires.

        Called from documents/confirm's "also make this required" step -- the same
        action that assigns a document to a role -- so it must behave nothing like
        set_role_requirements' replace-the-whole-list semantics above, which would
        silently drop everything else that role was already required to complete.

        Idempotent (INSERT OR IGNORE against the role_code+doc_title primary key):
        re-assigning the same document to the same role a second time is a no-op,
        not a duplicate-row error.

        Returns True only when this call actually inserted the row -- devserver.py's
        _confirm_document uses this to send a "new training assigned" email once, on
        the pair's first assignment, not on every re-confirm.
        """
        role = (role_code or "ALL").strip().upper()
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO role_requirements (role_code, doc_title, category, created_at) "
            "VALUES (?,?,?,?)",
            (role, str(doc_title), (category or "technical").strip().lower(), utcnow()),
        )
        inserted = cur.rowcount > 0
        self.conn.commit()
        return inserted

    def role_requirements(self, role_code: str) -> List[dict]:
        """
        What this role must complete.

        Includes the ALL requirements every role carries — company-wide training is
        required of everyone, so it belongs in every role's denominator. Without this a
        role with no specific requirements would score against an empty list and read
        100% compliant while having done nothing.
        """
        role = (role_code or "ALL").strip().upper()
        codes = ["ALL"] if role == "ALL" else [role, "ALL"]
        placeholders = ",".join("?" for _ in codes)
        return [dict(r) for r in self.conn.execute(
            "SELECT DISTINCT doc_title, category FROM role_requirements "
            "WHERE role_code IN ({}) ORDER BY doc_title".format(placeholders), codes)]

    def all_role_requirements(self) -> Dict[str, List[dict]]:
        out: Dict[str, List[dict]] = {}
        for r in self.conn.execute(
                "SELECT role_code, doc_title, category FROM role_requirements "
                "ORDER BY role_code, doc_title"):
            out.setdefault(r["role_code"], []).append(
                {"doc_title": r["doc_title"], "category": r["category"]})
        return out

    def issue_certificate(self, *, certificate_id: str, learner_id: str, doc_title: str,
                          attempt_id: str, attempt_score: float, category: str = "technical",
                          expires_at: str = "") -> dict:
        """
        Record a pass. Never updated afterwards — a retake issues a NEW certificate.

        Keeping every pass rather than overwriting is what makes best-score-of-record
        possible, and leaves an audit trail: "which attempt earned this, and when" is
        the first question anyone asks about a disputed certification.
        """
        row = dict(certificate_id=certificate_id, learner_id=learner_id,
                   doc_title=doc_title, attempt_id=attempt_id,
                   attempt_score=float(attempt_score),
                   category=(category or "technical").lower(),
                   issued_at=utcnow(), expires_at=expires_at, certificate_url=None)
        self.conn.execute(
            "INSERT OR REPLACE INTO certificates (certificate_id, learner_id, doc_title, "
            "attempt_id, attempt_score, category, issued_at, expires_at, certificate_url, "
            "company_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row["certificate_id"], row["learner_id"], row["doc_title"], row["attempt_id"],
             row["attempt_score"], row["category"], row["issued_at"], row["expires_at"], None,
             self.company_id))
        self.conn.commit()
        return row

    def certificates(self, learner_id: str) -> List[dict]:
        """Every certificate this learner holds, expired ones included."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM certificates WHERE learner_id = :learner" + self._where()
            + " ORDER BY issued_at DESC", self._params(learner=learner_id))]

    def trainings_completed_count(self, learner_id: str) -> int:
        """
        Distinct trainings this learner has ever been certified on. The same signal
        list_trainings uses to mark a card "completed" -- so pet points and the
        dashboard's own idea of "done" can never disagree.
        """
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT doc_title) AS n FROM certificates "
            "WHERE learner_id = :learner" + self._where(), self._params(learner=learner_id),
        ).fetchone()
        return int(row["n"]) if row else 0

    def pet_purchases(self, learner_id: str) -> List[dict]:
        """Every item this learner owns, purchase order, oldest first."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM pet_purchases WHERE learner_id = :learner" + self._where()
            + " ORDER BY purchased_at", self._params(learner=learner_id))]

    def pet_purchase(self, learner_id: str, item_id: str) -> bool:
        """
        Record a purchase. Returns False if already owned (INSERT OR IGNORE on the
        (learner_id, item_id) primary key) rather than double-charging on a retried
        request.
        """
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO pet_purchases "
            "(learner_id, item_id, equipped, purchased_at, company_id) "
            "VALUES (?,?,0,?,?)",
            (learner_id, item_id, utcnow(), self.company_id),
        )
        bought = cur.rowcount > 0
        self.conn.commit()
        return bought

    def pet_equip(self, learner_id: str, item_id: str) -> bool:
        """
        Toggle-wear an owned item. Equipping one item in a slot unequips whatever else
        that learner had on in the same slot -- a robot does not wear two hats.
        Returns False if the item is not owned (nothing to equip).
        """
        slot = pet_shop.slot_of(item_id)
        if slot is None:
            return False
        owned_ids = {p["item_id"] for p in self.pet_purchases(learner_id)}
        if item_id not in owned_ids:
            return False
        same_slot = [i for i in owned_ids if pet_shop.slot_of(i) == slot]
        currently_equipped = {
            p["item_id"] for p in self.pet_purchases(learner_id) if p["equipped"]
        }
        if item_id in currently_equipped:
            # already worn -- take it off
            self.conn.execute(
                "UPDATE pet_purchases SET equipped = 0 "
                "WHERE learner_id = :learner AND item_id = :item" + self._where(),
                self._params(learner=learner_id, item=item_id),
            )
        else:
            for other in same_slot:
                self.conn.execute(
                    "UPDATE pet_purchases SET equipped = 0 "
                    "WHERE learner_id = :learner AND item_id = :item" + self._where(),
                    self._params(learner=learner_id, item=other),
                )
            self.conn.execute(
                "UPDATE pet_purchases SET equipped = 1 "
                "WHERE learner_id = :learner AND item_id = :item" + self._where(),
                self._params(learner=learner_id, item=item_id),
            )
        self.conn.commit()
        return True
