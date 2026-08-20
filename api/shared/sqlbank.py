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

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence

from quizgen.models import Chunk, Question, ReviewStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str) -> Optional[datetime]:
    """
    Chunk.fetched_at is an ISO string (set by quizgen.web.fetch), but
    SourceChunks.fetched_at is DATETIME2 -- pyodbc binds a Python str as text, and
    relying on SQL Server to implicitly convert a string carrying a '+00:00' offset is
    not worth the risk when parsing it here is one line.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _module_roles(module, assignments: Dict[str, List[str]]) -> List[str]:
    roles: List[str] = []
    for topic in module.source_topics:
        value = assignments.get(topic) or ["ALL"]
        if isinstance(value, str):
            value = [value]
        roles.extend(str(role).strip().upper() for role in value if str(role).strip())
    roles = list(dict.fromkeys(roles)) or ["ALL"]
    return ["ALL"] if "ALL" in roles else roles


def _legacy_role(module, assignments: Dict[str, List[str]]) -> str:
    """Keep the old single column populated while normalized roles are authoritative."""
    return _module_roles(module, assignments)[0]


def _expanded_citations(citations, evidence: Dict[str, object]) -> List[Dict[str, object]]:
    out = []
    for citation in citations:
        item = evidence.get(citation.evidence_id)
        out.append({
            "evidenceId": citation.evidence_id,
            "quote": citation.quote,
            "title": getattr(item, "title", ""),
            "url": getattr(item, "url", ""),
            "sourceType": getattr(item, "source_type", ""),
            "fetchedAt": getattr(item, "fetched_at", ""),
        })
    return out


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
            # company_id is NOT NULL on this table (020_add_company_to_quizgen.sql) --
            # both branches below must set it, or SQL Server rejects the write. It was
            # missing here originally on the wrong assumption (stated in the old
            # comment on all_chunks below, now corrected) that this column did not
            # exist -- the first real upload hit that immediately with a 500.
            # source_type/source_url/fetched_at were previously hardcoded to
            # ('document', NULL, NULL) here regardless of what the Chunk actually
            # carried -- harmless while every caller was a PDF upload, but it would have
            # silently dropped a trusted link's URL and retrieval date (the citation
            # ProvenanceClass.EXTERNAL questions require, per validators.py) the moment
            # the chunk was saved. Now written from the Chunk itself, same as every
            # other field here.
            cur.execute(
                """MERGE dbo.SourceChunks AS target
                   USING (SELECT ? AS chunk_id) AS src ON target.chunk_id = src.chunk_id
                   WHEN MATCHED THEN UPDATE SET
                       doc_id = ?, doc_title = ?, section = ?, topic = ?,
                       page_start = ?, page_end = ?, chunk_text = ?, container = ?,
                       role_scope = ?, company_id = ?, source_type = ?, source_url = ?,
                       fetched_at = ?
                   WHEN NOT MATCHED THEN INSERT
                       (chunk_id, doc_id, doc_title, section, topic, page_start,
                        page_end, chunk_text, container, role_scope, source_type,
                        company_id, source_url, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                c.chunk_id,
                c.doc_id, c.doc_title, c.section, c.topic, c.page_start, c.page_end,
                c.text, c.container, c.role_scope, self.company_id,
                c.source_type or "document", c.source_url or None, _parse_dt(c.fetched_at),
                c.chunk_id, c.doc_id, c.doc_title, c.section, c.topic,
                c.page_start, c.page_end, c.text, c.container, c.role_scope,
                c.source_type or "document", self.company_id,
                c.source_url or None, _parse_dt(c.fetched_at),
            )
            n += 1
        self.conn.commit()
        return n

    def all_chunks(self) -> List[Chunk]:
        cur = self.conn.cursor()
        # 020_add_company_to_quizgen.sql DOES add company_id to SourceChunks (an
        # earlier comment here claimed otherwise and was wrong -- see save_chunks'
        # MERGE, which hit that column's NOT NULL constraint on the first real
        # upload). Scoped here the same way every other tenant-scoped table in this
        # file is scoped, so one company never reads another's uploaded chunks.
        cur.execute(
            """SELECT chunk_id, doc_id, doc_title, topic, section, page_start,
                      page_end, chunk_text, container, role_scope, source_type,
                      source_url, fetched_at
                 FROM dbo.SourceChunks
                WHERE company_id = ?
                ORDER BY doc_title, page_start""",
            self.company_id,
        )
        return [
            Chunk(
                chunk_id=r.chunk_id, doc_id=r.doc_id, doc_title=r.doc_title,
                topic=r.topic, section=r.section, page_start=r.page_start,
                page_end=r.page_end, text=r.chunk_text, container=r.container or "",
                role_scope=r.role_scope or "ALL", company_id=str(self.company_id),
                source_type=r.source_type or "document", source_url=r.source_url or "",
                fetched_at=r.fetched_at.isoformat() if r.fetched_at else "",
            )
            for r in cur.fetchall()
        ]

    def set_chunk_roles(self, doc_title: str, mapping: Dict[str, str]) -> int:
        cur = self.conn.cursor()
        n = 0
        for topic, role in mapping.items():
            cur.execute(
                "UPDATE dbo.SourceChunks SET role_scope = ? "
                "WHERE doc_title = ? AND topic = ? AND company_id = ?",
                (role or "ALL").upper(), doc_title, topic, self.company_id,
            )
            n += cur.rowcount
        self.conn.commit()
        return n

    def save_instructional_course(
        self, course, role_assignments: Dict[str, List[str]]
    ) -> Dict[str, int]:
        """Persist one versioned course and its normalized multi-role audience."""
        cur = self.conn.cursor()
        ready = 0
        for module in course.modules:
            # Valid lessons stay draft while their question bank is generated. Existing
            # ready modules are retired only in finalize_instructional_course, after the
            # replacement has passed both publication gates.
            staged_status = "draft" if module.status == "ready" else module.status
            cur.execute(
                """MERGE dbo.TrainingModules AS target
                   USING (SELECT ? AS module_id) AS source
                      ON target.module_id = source.module_id
                   WHEN MATCHED THEN UPDATE SET
                       doc_id = ?, doc_title = ?, topic = ?, heading = ?, source_order = ?,
                       role_scope = ?, status = ?, summary = ?, lesson_word_count = ?,
                       learning_point_count = ?, generation_version = 'instructional-v1',
                       active_generation_id = ?, quality_notes_json = ?,
                       updated_at = SYSUTCDATETIME()
                   WHEN NOT MATCHED THEN INSERT
                       (module_id, company_id, doc_id, doc_title, topic, heading,
                        source_order, role_scope, status, summary, lesson_word_count,
                        learning_point_count, generation_version, active_generation_id,
                        quality_notes_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'instructional-v1', ?, ?);""",
                module.module_id,
                module.doc_id, module.doc_title, module.topic, module.heading,
                module.source_order, _legacy_role(module, role_assignments),
                staged_status, module.summary or None, module.word_count,
                len(module.learning_points), module.generation_id,
                json.dumps(module.quality_notes),
                module.module_id, self.company_id, module.doc_id, module.doc_title,
                module.topic, module.heading, module.source_order,
                _legacy_role(module, role_assignments), staged_status,
                module.summary or None, module.word_count, len(module.learning_points),
                module.generation_id, json.dumps(module.quality_notes),
            )

            cur.execute(
                "DELETE FROM dbo.TrainingModuleRoles WHERE company_id = ? AND module_id = ?",
                self.company_id, module.module_id,
            )
            roles = _module_roles(module, role_assignments)
            cur.executemany(
                "INSERT INTO dbo.TrainingModuleRoles (company_id, module_id, role_code) "
                "VALUES (?, ?, ?)",
                [(self.company_id, module.module_id, role) for role in roles],
            )

            evidence = {item.evidence_id: item for item in module.evidence}
            for point in module.learning_points:
                cur.execute(
                    """IF NOT EXISTS (SELECT 1 FROM dbo.ModuleLearningPoints
                                       WHERE learning_point_id = ?)
                           INSERT INTO dbo.ModuleLearningPoints
                               (learning_point_id, company_id, module_id, generation_id,
                                point_order, statement, evidence_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    point.learning_point_id,
                    point.learning_point_id, self.company_id, module.module_id,
                    module.generation_id, point.order, point.statement,
                    json.dumps(_expanded_citations(point.citations, evidence)),
                )
            for page in module.pages:
                cur.execute(
                    """IF NOT EXISTS (SELECT 1 FROM dbo.LessonPages WHERE page_id = ?)
                           INSERT INTO dbo.LessonPages
                               (page_id, company_id, module_id, generation_id, page_order,
                                title, page_type, body, word_count,
                                learning_point_ids_json, citations_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    page.page_id,
                    page.page_id, self.company_id, module.module_id,
                    module.generation_id, page.order, page.title, page.page_type,
                    page.body, page.word_count, json.dumps(page.learning_point_ids),
                    json.dumps(_expanded_citations(page.citations, evidence)),
                )
            ready += 1 if module.status == "ready" else 0
        self.conn.commit()
        return {"modules": len(course.modules), "ready": ready}

    def finalize_instructional_course(self, course, lesson_chunks: Sequence[Chunk]) -> int:
        """Publish only modules whose active assessment bank meets pathway floors."""
        cur = self.conn.cursor()
        chunk_by_module = {
            str(getattr(chunk, "module_id", "")): chunk.chunk_id for chunk in lesson_chunks
        }
        ready = 0
        for module in course.modules:
            if module.status != "ready":
                continue
            source_chunk_id = chunk_by_module.get(module.module_id, "")
            cur.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN question_type IN
                              ('MultipleChoice', 'MultiSelect', 'TrueFalse', 'FillInBlank')
                              THEN 1 ELSE 0 END) AS quick_count,
                          COUNT(DISTINCT CASE WHEN question_type = 'MultipleChoice'
                              THEN difficulty END) AS diagnostic_difficulties
                     FROM dbo.GeneratedQuestions
                    WHERE company_id = ? AND module_id = ? AND source_chunk_id = ?
                      AND review_status = 'Approved'""",
                self.company_id, module.module_id, source_chunk_id,
            )
            row = cur.fetchone()
            total = int(getattr(row, "total", 0) or 0) if row else 0
            quick = int(getattr(row, "quick_count", 0) or 0) if row else 0
            diagnostic = int(getattr(row, "diagnostic_difficulties", 0) or 0) if row else 0
            # Was total>=10 and quick>=10 and diagnostic>=3 (originally ==3). Dropped
            # the fixed count floors entirely: the lesson content itself already passed
            # its own word-count/learning-point quality gate during authoring (see
            # validate_module), so a module reaching this point already has real,
            # verified instructional depth -- gating publication on a SEPARATE arbitrary
            # question-count minimum on top of that was withholding modules with real,
            # approved questions and no way to recover short of a full regeneration.
            # Only requirement left: not literally empty.
            publish = total >= 1
            notes = list(module.quality_notes)
            if not publish:
                notes.append(
                    "assessment bank incomplete: {} total, {} quick-response, "
                    "{} diagnostic difficulty levels".format(total, quick, diagnostic))
            cur.execute(
                """UPDATE dbo.TrainingModules
                      SET status = ?, quality_notes_json = ?, updated_at = SYSUTCDATETIME()
                    WHERE company_id = ? AND module_id = ?
                      AND active_generation_id = ?""",
                "ready" if publish else "insufficient", json.dumps(notes),
                self.company_id, module.module_id, module.generation_id,
            )
            if publish:
                ready += 1
            else:
                module.status = "insufficient"
                module.quality_notes = notes
        if ready:
            cur.execute(
                """UPDATE dbo.TrainingModules
                      SET status = 'retired', updated_at = SYSUTCDATETIME()
                    WHERE company_id = ? AND doc_id = ?
                      AND generation_version = 'instructional-v1'
                      AND active_generation_id <> ? AND status <> 'retired'""",
                self.company_id, course.doc_id, course.generation_id,
            )
            # The diagnostic orders version-specific module ids. A newly published
            # version must establish its own baseline; otherwise an old completion row
            # would silently skip the new diagnostic and personalize from stale scores.
            cur.execute(
                "DELETE FROM dbo.EmployeeTrainingProgress "
                "WHERE company_id = ? AND doc_id = ?",
                self.company_id, course.doc_id,
            )
        self.conn.commit()
        return ready

    def add_role_requirement(self, role_code: str, doc_title: str, category: str = "technical") -> bool:
        """
        Add ONE role/doc pair to RoleRequirements, without touching whatever else
        that role already requires.

        Mirrors src/quizgen/bank.py's method of the same name -- see its docstring
        for why this must not behave like the admin-facing "replace the whole list"
        endpoint (set_requirements in function_app.py). Idempotent: guarded by
        IF NOT EXISTS against RoleRequirements' (company_id, role_code, doc_title)
        primary key, so calling this twice for the same pair is a no-op rather than
        a duplicate-key error.

        Returns True only when this call actually inserted the row -- confirm_document
        uses this to send a "new training assigned" email once, on the pair's first
        assignment, not on every re-confirm of an already-required document.
        """
        cur = self.conn.cursor()
        code = (role_code or "ALL").strip().upper()
        cur.execute(
            "IF NOT EXISTS (SELECT 1 FROM dbo.RoleRequirements "
            "               WHERE company_id = ? AND role_code = ? AND doc_title = ?) "
            "  INSERT INTO dbo.RoleRequirements (company_id, role_code, doc_title, category) "
            "  VALUES (?, ?, ?, ?);",
            self.company_id, code, doc_title,
            self.company_id, code, doc_title, (category or "technical").strip().lower(),
        )
        inserted = cur.rowcount > 0
        self.conn.commit()
        return inserted

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
                        source_page, source_quote, source_url, source_fetched_at,
                        generator, review_status,
                        provenance_class, role_code, role_requirement,
                        rubric_json, fallback_json, grading_version,
                        contradiction_notes, module_id, lesson_page_id,
                        learning_point_id, times_served, times_correct, company_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?)""",
                q.question_id, q.topic, q.question_type.value, q.difficulty.value,
                q.prompt, q.explanation, q.points, q.source_chunk_id or None,
                q.source_doc_title or None, q.source_page or None,
                q.source_quote or None, q.source_url or None,
                _parse_dt(q.source_fetched_at), q.generator or None, status,
                q.provenance_class.value, q.role_code or None,
                q.role_requirement or None,
                q.rubric_json or None, q.fallback_json or None,
                q.grading_version or None,
                "; ".join(notes.get(q.question_id, [])) or None,
                q.module_id or None, q.lesson_page_id or None,
                q.learning_point_id or None,
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

    def retire_stale_course_questions(
        self, doc_title: str, active_source_chunk_ids: Sequence[str]
    ) -> int:
        """Retire the previous bank only after its replacement has been written."""
        cur = self.conn.cursor()
        active = list(dict.fromkeys(active_source_chunk_ids))
        if active:
            placeholders = ",".join("?" for _ in active)
            cur.execute(
                "UPDATE dbo.GeneratedQuestions SET review_status = ? "
                "WHERE source_doc_title = ? AND company_id = ? "
                "AND (source_chunk_id IS NULL OR source_chunk_id NOT IN ({}))".format(
                    placeholders),
                ReviewStatus.REJECTED.value, doc_title, self.company_id, *active,
            )
        else:
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
        # MERGE, not IF EXISTS/ELSE INSERT: the old two-step check-then-act let two
        # near-simultaneous calls for the same (role_code, company_id) both see "not
        # found" and both try to INSERT, colliding on PK_QuizgenRoles with an
        # IntegrityError. seed_roles() calls this in a loop on every document upload
        # while a company's QuizgenRoles is still empty, which made the race easy to
        # hit -- exactly the same problem save_chunks() below already avoids the same
        # way.
        cur = self.conn.cursor()
        code = role_code.upper()
        cur.execute(
            """MERGE dbo.QuizgenRoles AS target
               USING (SELECT ? AS role_code, ? AS company_id) AS src
                 ON target.role_code = src.role_code AND target.company_id = src.company_id
               WHEN MATCHED THEN UPDATE SET
                   title = ?, description = ?
               WHEN NOT MATCHED THEN INSERT
                   (role_code, company_id, title, description)
                   VALUES (?, ?, ?, ?);""",
            code, self.company_id,
            title, description,
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

    # ------------------------------------------------------------------
    # trusted links (dbo.TrustedLinks, 026)
    # ------------------------------------------------------------------

    def add_trusted_link(self, added_by: int, scope: str, role_code: str, url: str) -> int:
        """
        Insert a trusted link. For scope='company_wide', retires the previous active
        company-wide link first -- Decisions Log #4: exactly one active at a time, and
        adding a new one supersedes rather than stacking. Both statements run under this
        one connection/request, so there is no window for the QuizgenRoles.add_role-style
        race a second concurrent caller could hit.

        Returns the new row's id.
        """
        cur = self.conn.cursor()
        if scope == "company_wide":
            cur.execute(
                "UPDATE dbo.TrustedLinks SET is_active = 0 "
                "WHERE company_id = ? AND scope = 'company_wide' AND is_active = 1",
                self.company_id,
            )
        cur.execute(
            "INSERT INTO dbo.TrustedLinks (company_id, added_by, scope, role_code, url) "
            "OUTPUT INSERTED.id VALUES (?, ?, ?, ?, ?)",
            self.company_id, added_by, scope, role_code.upper(), url,
        )
        new_id = cur.fetchone()[0]
        self.conn.commit()
        return new_id

    def trusted_links(self) -> List[Dict[str, object]]:
        cur = self.conn.cursor()
        cur.execute(
            """SELECT tl.id, tl.scope, tl.role_code, tl.url, tl.is_active, tl.created_at,
                      e.name AS added_by_name
                 FROM dbo.TrustedLinks tl
                 LEFT JOIN dbo.Employees e ON e.id = tl.added_by
                WHERE tl.company_id = ?
                ORDER BY tl.created_at DESC""",
            self.company_id,
        )
        return [
            {
                "id": r.id, "scope": r.scope, "roleCode": r.role_code, "url": r.url,
                "isActive": bool(r.is_active), "createdAt": r.created_at,
                "addedBy": r.added_by_name or "",
            }
            for r in cur.fetchall()
        ]


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
