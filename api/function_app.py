"""
HTTP API — Azure Functions (Python v2 programming model).

Serves the endpoints in docs/frontend-spec.md. Reads Azure SQL directly; the quizgen
package remains the authoring side (documents -> questions) and is not imported here.

Two rules this file exists to enforce:

  1. **Quiz delivery never includes the answer key.** GET /quizzes/... reads
     dbo.vw_ServableQuestions, which has no is_correct column and does not touch the
     answer-key table. A careless SELECT * cannot leak the key to a browser.

  2. **Grading is deterministic.** Totals, the pass mark and mastery bands are
     arithmetic here in code. No model call decides a score, because a disputed result
     has to be reproducible and explainable.

Auth is a stub for the demo — see `_caller_id`. Replace with Entra before real use.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import azure.functions as func
from shared.auth import get_current_employee

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

from auth.login import bp as auth_bp
app.register_functions(auth_bp)

PASSING_SCORE = float(os.getenv("QUIZGEN_PASSING_SCORE", "80"))
QUIZ_LENGTH = int(os.getenv("QUIZGEN_QUIZ_LENGTH", "8"))
WEAK_THRESHOLD = float(os.getenv("QUIZGEN_WEAK_THRESHOLD", "0.70"))
MIN_ANSWERS = int(os.getenv("QUIZGEN_MIN_ANSWERS", "3"))


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------

def _odbc_from_ado(value: str) -> str:
    """
    Convert an ADO.NET connection string to ODBC.

    The infra module supplies SQL_CONNECTION_STRING from Key Vault, and whoever wrote
    the secret may have used either format. ADO.NET ("Server=tcp:...;User ID=...") is
    what the Azure portal shows by default and is NOT understood by pyodbc, so it would
    fail with an unhelpful driver error.
    """
    parts = {}
    for piece in value.split(";"):
        if "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        parts[k.strip().lower()] = v.strip()

    server = parts.get("server") or parts.get("data source", "")
    server = server.replace("tcp:", "").split(",")[0]
    database = parts.get("initial catalog") or parts.get("database", "")
    user = parts.get("user id") or parts.get("uid", "")
    password = parts.get("password") or parts.get("pwd", "")

    return (
        "DRIVER={{ODBC Driver 18 for SQL Server}};"
        "SERVER=tcp:{},1433;DATABASE={};UID={};PWD={};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    ).format(server, database, user, password)


def _connection_string() -> str:
    """
    Deployed: SQL_CONNECTION_STRING, supplied by the infra module as a Key Vault
    reference — the secret is resolved by the Function App's managed identity and never
    exists as a file or a plain app setting.

    Local: the four separate parts, so development works without Key Vault access.
    """
    conn = (os.getenv("SQL_CONNECTION_STRING") or "").strip()
    if conn:
        # Already ODBC? Use as-is. Otherwise it is ADO.NET and needs converting.
        return conn if "driver=" in conn.lower() else _odbc_from_ado(conn)

    return (
        "DRIVER={{ODBC Driver 18 for SQL Server}};"
        "SERVER=tcp:{},1433;DATABASE={};UID={};PWD={};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    ).format(
        os.getenv("SQL_SERVER", "mob-sql-server-02.database.windows.net"),
        os.getenv("SQL_DATABASE", "mob-training-db"),
        os.getenv("SQL_USER", "mobsqladmin"),
        os.getenv("SQL_PASSWORD", ""),
    )


def _conn():
    import pyodbc

    return pyodbc.connect(_connection_string())


def _rows(cur) -> List[Dict[str, Any]]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _json(body: Any, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body, default=str), status_code=status, mimetype="application/json"
    )


def _error(status: int, title: str, detail: str = "") -> func.HttpResponse:
    return _json({"title": title, "detail": detail, "status": status}, status)


def _caller_id(req: func.HttpRequest) -> str:
    identity = get_current_employee(req)
    if identity:
        return identity.email
    return req.headers.get("x-learner-id", "demo-learner")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------

@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM dbo.GeneratedQuestions WHERE review_status='Approved'"
            )
            approved = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM dbo.GeneratedQuestions")
            total = cur.fetchone()[0]
        return _json(
            {
                "status": "ok",
                "database": "connected",
                "questionsApproved": approved,
                "questionsTotal": total,
                # A learner cannot be served anything until questions are approved.
                "servable": approved > 0,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _json(
            {"status": "degraded", "database": "unreachable", "error": type(exc).__name__},
            503,
        )


# --------------------------------------------------------------------------
# learner
# --------------------------------------------------------------------------

@app.route(route="me", methods=["GET"])
def get_me(req: func.HttpRequest) -> func.HttpResponse:
    learner = _caller_id(req)
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT topic, answered, correct, accuracy_percent, mastery_level
                   FROM dbo.vw_LearnerTopicMastery WHERE learner_id = ?
                   ORDER BY accuracy_percent""",
                learner,
            )
            mastery = _rows(cur)

            cur.execute(
                """SELECT COUNT(*) AS attempts,
                          SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END) AS passed
                   FROM dbo.GeneratedQuizAttempts
                   WHERE learner_id = ? AND submitted_at IS NOT NULL""",
                learner,
            )
            stats = _rows(cur)[0]

        weak = [
            m for m in mastery
            if m["answered"] >= MIN_ANSWERS
            and float(m["accuracy_percent"]) < WEAK_THRESHOLD * 100
        ]
        return _json(
            {
                "learnerId": learner,
                "attempts": stats.get("attempts") or 0,
                "passed": stats.get("passed") or 0,
                "masteryByTopic": mastery,
                "weakTopics": [m["topic"] for m in weak],
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="topics", methods=["GET"])
def list_topics(req: func.HttpRequest) -> func.HttpResponse:
    """Approved question counts per topic and role — what a catalogue screen needs."""
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT c.role_scope, q.topic, COUNT(*) AS questionCount
                   FROM dbo.GeneratedQuestions q
                   JOIN dbo.SourceChunks c ON c.chunk_id = q.source_chunk_id
                   WHERE q.review_status = 'Approved'
                   GROUP BY c.role_scope, q.topic
                   ORDER BY c.role_scope, q.topic"""
            )
            return _json({"topics": _rows(cur)})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


# --------------------------------------------------------------------------
# quiz
# --------------------------------------------------------------------------

def _weak_topics(cur, learner: str) -> List[str]:
    """
    Topics below the threshold with enough evidence to judge.

    The evidence floor matters: two wrong answers out of two is noise, and drilling
    someone on a topic they actually know is worse than not drilling at all.
    """
    cur.execute(
        """SELECT topic FROM dbo.vw_LearnerTopicMastery
           WHERE learner_id = ? AND answered >= ? AND accuracy_percent < ?
           ORDER BY accuracy_percent""",
        learner, MIN_ANSWERS, WEAK_THRESHOLD * 100,
    )
    return [r["topic"] for r in _rows(cur)]


@app.route(route="quiz/start", methods=["POST"])
def start_quiz(req: func.HttpRequest) -> func.HttpResponse:
    """
    Assemble and serve a quiz. Reads the answer-safe view only.

    No model call happens here — selection is a database query and arithmetic, which is
    why it is fast, free and reproducible.
    """
    learner = _caller_id(req)
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    length = int(body.get("length") or QUIZ_LENGTH)
    role = body.get("role")

    try:
        with _conn() as c:
            cur = c.cursor()
            weak = _weak_topics(cur, learner)

            # Questions the learner has already seen — held back unless the bank is thin.
            cur.execute(
                "SELECT DISTINCT question_id FROM dbo.GeneratedQuizResponses WHERE learner_id = ?",
                learner,
            )
            seen = {r["question_id"] for r in _rows(cur)}

            sql = """SELECT DISTINCT q.question_id, q.topic, q.question_type, q.difficulty,
                            q.prompt, q.points, q.provenance_class, c.role_scope
                     FROM dbo.GeneratedQuestions q
                     JOIN dbo.SourceChunks c ON c.chunk_id = q.source_chunk_id
                     WHERE q.review_status = 'Approved'"""
            params: List[Any] = []
            if role:
                # A role sees its own material plus everything company-wide.
                sql += " AND c.role_scope IN (?, 'ALL')"
                params.append(role)
            cur.execute(sql, *params)
            pool = _rows(cur)

        if not pool:
            return _error(
                503, "No questions available",
                "No approved questions. Generated questions must be reviewed before "
                "they can be served.",
            )

        unseen = [q for q in pool if q["question_id"] not in seen] or pool
        weak_set = set(weak)
        # Weak topics first, then unseen material, then everything else.
        unseen.sort(key=lambda q: (q["topic"] not in weak_set, q["question_id"]))
        chosen = unseen[:length]

        attempt_id = "att_" + uuid.uuid4().hex[:12]
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """INSERT INTO dbo.GeneratedQuizAttempts (attempt_id, learner_id, started_at)
                   VALUES (?, ?, SYSUTCDATETIME())""",
                attempt_id, learner,
            )
            questions = []
            for i, q in enumerate(chosen, 1):
                # Options come from the answer-safe view: no is_correct column exists.
                cur.execute(
                    """SELECT option_id, option_text, sort_order
                       FROM dbo.vw_ServableQuestions
                       WHERE question_id = ? AND option_id IS NOT NULL
                       ORDER BY sort_order""",
                    q["question_id"],
                )
                questions.append(
                    {
                        "questionId": q["question_id"],
                        "order": i,
                        "type": q["question_type"],
                        "topic": q["topic"],
                        "difficulty": q["difficulty"],
                        "points": q["points"],
                        "provenanceClass": q["provenance_class"],
                        "prompt": q["prompt"],
                        "options": [
                            {"optionId": o["option_id"], "text": o["option_text"]}
                            for o in _rows(cur)
                        ],
                    }
                )
            c.commit()

        return _json(
            {
                "attemptId": attempt_id,
                "learnerId": learner,
                "startedAt": _now(),
                "passingScorePercent": PASSING_SCORE,
                "isRemedial": bool(weak),
                "focusMessage": (
                    "Focusing on topics you found difficult: " + ", ".join(weak[:3])
                    if weak else "Baseline quiz — no weakness data yet."
                ),
                "questions": questions,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="quiz/submit", methods=["POST"])
def submit_quiz(req: func.HttpRequest) -> func.HttpResponse:
    """
    Grade an attempt. All arithmetic, no model call.

    MultiSelect is all-or-nothing: partial credit on a compliance question lets someone
    pass while still believing something false.
    """
    learner = _caller_id(req)
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    attempt_id = body.get("attemptId")
    answers = body.get("answers") or []
    if not attempt_id:
        return _error(400, "Bad request", "attemptId is required")

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                "SELECT submitted_at FROM dbo.GeneratedQuizAttempts WHERE attempt_id = ?",
                attempt_id,
            )
            row = cur.fetchone()
            if row is None:
                return _error(404, "Not found", "No such attempt")
            if row[0] is not None:
                return _error(409, "Already submitted", "This attempt is already graded")

            results, awarded, possible = [], 0, 0

            for a in answers:
                qid = a.get("questionId")
                selected = a.get("selectedOptionIds") or []
                text_answer = (a.get("textAnswer") or "").strip()

                cur.execute(
                    """SELECT question_id, question_type, topic, points, explanation,
                              source_doc_title, source_page, source_quote, source_url,
                              provenance_class
                       FROM dbo.GeneratedQuestions WHERE question_id = ?""",
                    qid,
                )
                qrows = _rows(cur)
                if not qrows:
                    continue
                q = qrows[0]
                possible += q["points"]

                cur.execute(
                    "SELECT option_id, option_text, is_correct FROM dbo.GeneratedOptions WHERE question_id = ?",
                    qid,
                )
                options = _rows(cur)
                correct_ids = {o["option_id"] for o in options if o["is_correct"]}

                if q["question_type"] == "FillInBlank":
                    cur.execute(
                        "SELECT accepted_answer FROM dbo.GeneratedAnswerKeys WHERE question_id = ?",
                        qid,
                    )
                    accepted = [r["accepted_answer"] for r in _rows(cur)]
                    norm = " ".join(text_answer.lower().split())
                    is_correct = any(norm == " ".join(a.lower().split()) for a in accepted)
                    correct_display = accepted
                elif q["question_type"] == "MultiSelect":
                    is_correct = set(selected) == correct_ids and bool(correct_ids)
                    correct_display = [o["option_text"] for o in options if o["is_correct"]]
                else:
                    is_correct = len(selected) == 1 and selected[0] in correct_ids
                    correct_display = [o["option_text"] for o in options if o["is_correct"]]

                points = q["points"] if is_correct else 0
                awarded += points

                cur.execute(
                    """INSERT INTO dbo.GeneratedQuizResponses
                       (response_id, attempt_id, learner_id, question_id, topic,
                        selected, text_answer, is_correct, points_awarded)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    "res_" + uuid.uuid4().hex[:12], attempt_id, learner, qid,
                    q["topic"], ",".join(selected), text_answer,
                    1 if is_correct else 0, points,
                )
                # Measured difficulty accumulates here.
                cur.execute(
                    """UPDATE dbo.GeneratedQuestions
                       SET times_served = times_served + 1,
                           times_correct = times_correct + ?
                       WHERE question_id = ?""",
                    1 if is_correct else 0, qid,
                )

                results.append(
                    {
                        "questionId": qid,
                        "topic": q["topic"],
                        "isCorrect": is_correct,
                        "pointsAwarded": points,
                        "yourAnswer": [
                            o["option_text"] for o in options if o["option_id"] in selected
                        ] or ([text_answer] if text_answer else []),
                        "correctAnswer": correct_display,
                        "explanation": q["explanation"],
                        # Only Documented questions cite a company source. External ones
                        # cite a URL; RoleKnowledge cites nothing at all.
                        "source": (
                            {
                                "documentTitle": q["source_doc_title"],
                                "page": q["source_page"],
                                "quote": q["source_quote"],
                                "url": q["source_url"],
                            }
                            if q["provenance_class"] != "RoleKnowledge" and q["source_doc_title"]
                            else None
                        ),
                    }
                )

            percent = round(100.0 * awarded / possible, 2) if possible else 0.0
            passed = percent >= PASSING_SCORE

            cur.execute(
                """UPDATE dbo.GeneratedQuizAttempts
                   SET submitted_at = SYSUTCDATETIME(), score_percent = ?,
                       points_awarded = ?, points_possible = ?, passed = ?
                   WHERE attempt_id = ?""",
                percent, awarded, possible, 1 if passed else 0, attempt_id,
            )
            c.commit()

        return _json(
            {
                "attemptId": attempt_id,
                "scorePercent": percent,
                "pointsAwarded": awarded,
                "pointsPossible": possible,
                "passed": passed,
                "passingScorePercent": PASSING_SCORE,
                "results": results,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


# --------------------------------------------------------------------------
# review — admin
# --------------------------------------------------------------------------

@app.route(route="review/pending", methods=["GET"])
def review_pending(req: func.HttpRequest) -> func.HttpResponse:
    """Unreviewed questions, WITH answers — this endpoint is for reviewers, not learners."""
    limit = int(req.params.get("limit", "20"))
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT TOP (?) question_id, topic, question_type, difficulty, prompt,
                          explanation, source_doc_title, source_page, source_quote,
                          provenance_class, contradiction_notes
                   FROM dbo.GeneratedQuestions
                   WHERE review_status = 'PendingReview'
                   ORDER BY created_at""",
                limit,
            )
            questions = _rows(cur)
            for q in questions:
                cur.execute(
                    "SELECT option_id, option_text, is_correct FROM dbo.GeneratedOptions WHERE question_id = ? ORDER BY sort_order",
                    q["question_id"],
                )
                q["options"] = _rows(cur)
            return _json({"pending": questions})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="review/decide", methods=["POST"])
def review_decide(req: func.HttpRequest) -> func.HttpResponse:
    """Approve or reject. This is the gate before anything reaches a learner."""
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    ids = body.get("questionIds") or []
    decision = body.get("decision")
    reviewer = _caller_id(req)

    if decision not in ("Approved", "Rejected"):
        return _error(400, "Bad request", "decision must be 'Approved' or 'Rejected'")
    if not ids:
        return _error(400, "Bad request", "questionIds is required")

    try:
        with _conn() as c:
            cur = c.cursor()
            for qid in ids:
                cur.execute(
                    """UPDATE dbo.GeneratedQuestions
                       SET review_status = ?, reviewed_by = ?, reviewed_at = SYSUTCDATETIME()
                       WHERE question_id = ?""",
                    decision, reviewer, qid,
                )
            c.commit()
        return _json({"updated": len(ids), "decision": decision, "reviewedBy": reviewer})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)
