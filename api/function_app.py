"""
HTTP API — Azure Functions (Python v2 programming model).

Serves the endpoints in docs/frontend-spec.md. Reads Azure SQL directly.

/documents, /documents/confirm, /roles and /jobs/{id} import quizgen (vendored into this
package at deploy time by azure-pipelines-backend.yml, from src/quizgen -- see that
file's build step for why a copy rather than a shared install). They do NOT use quizgen's
own Bank, which is sqlite3 end to end; shared.sqlbank.SqlBank implements the same handful
of methods pipeline.py and rolemap.py actually call, against dbo.SourceChunks /
dbo.GeneratedQuestions / dbo.QuizgenRoles directly. No SQLite file exists anywhere in this
request path -- Azure SQL is the only store, matching every other endpoint in this file.

Two rules this file exists to enforce:

  1. **Quiz delivery never includes the answer key.** GET /quizzes/... reads
     dbo.vw_ServableQuestions, which has no is_correct column and does not touch the
     answer-key table. A careless SELECT * cannot leak the key to a browser.

  2. **Grading is deterministic.** Totals, the pass mark and mastery bands are
     arithmetic here in code. No model call decides a score, because a disputed result
     has to be reproducible and explainable.

Auth is real: every endpoint resolves the caller from a signed bearer token via
shared.auth.get_current_employee, and refuses the request when there isn't one.
review/decide additionally requires manager tier or above.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import azure.functions as func

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

from auth.login import bp as auth_bp
from shared.auth import get_current_employee, require_manager
from shared import qscore

app.register_functions(auth_bp)

PASSING_SCORE = float(os.getenv("QUIZGEN_PASSING_SCORE", "80"))
QUIZ_LENGTH = int(os.getenv("QUIZGEN_QUIZ_LENGTH", "8"))
WEAK_THRESHOLD = float(os.getenv("QUIZGEN_WEAK_THRESHOLD", "0.70"))
# How long a certificate stays current. 12 months per docs/q-score.md.
CERT_VALIDITY_MONTHS = int(os.getenv("QUIZGEN_CERT_VALIDITY_MONTHS", "12"))
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


def _learner_key(identity) -> str:
    """
    The string that identifies this learner in the quizgen tables.

    Email, because it is stable, unique (Employees.email is NOT NULL UNIQUE from 001)
    and legible when reading GeneratedQuizAttempts by hand. It comes from a signed
    token, so unlike the header it replaced it cannot be chosen by the caller.
    """
    return identity.email


def _unauthorized() -> func.HttpResponse:
    return _error(401, "Unauthorized",
                  "Sign in at POST /api/login and send the token as "
                  "'Authorization: Bearer <token>'.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------

DIFFICULTY_MULTIPLIERS = {"Easy": 0.95, "Medium": 1.0, "Hard": 1.08}

def _calculate_q_score(percent, results):
    """
    Q Score = raw score % x difficulty weight x consistency factor.
    """
    if not results:
        return round(percent, 2)

    correct = [r for r in results if r["isCorrect"]]
    if correct:
        avg_multiplier = sum(
            DIFFICULTY_MULTIPLIERS.get(r.get("difficulty", "Medium"), 1.0)
            for r in correct
        ) / len(correct)
    else:
        avg_multiplier = 1.0

    topics = {}
    for r in results:
        topics.setdefault(r["topic"], []).append(r["isCorrect"])
    topic_rates = [sum(v) / len(v) for v in topics.values()]
    consistency = 1.0 if not topic_rates or min(topic_rates) >= 0.5 else 0.92

    return round(min(100.0, percent * avg_multiplier * consistency), 2)


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    """
    Liveness, and — only for a signed-in caller — their company's bank size.

    This is the one endpoint that must answer without a token: deploy-backend.yml's health
    check calls it to tell "the app is down" from "the app needs a password", and the
    sign-in screen uses it to tell "API unreachable" from "wrong password".

    That is also why the COUNTS are gated. There is no identity to scope them to when
    nobody is signed in, so an unauthenticated count would sum every company's questions
    — not a content leak, but still one tenant's volume shown to another, and visible to
    anyone who can reach the host at all.
    """
    identity = get_current_employee(req)
    try:
        with _conn() as c:
            cur = c.cursor()
            counts = {}
            if identity is not None:
                cur.execute(
                    """SELECT COUNT(*) FROM dbo.GeneratedQuestions
                        WHERE review_status='Approved' AND company_id = ?""",
                    identity.company_id)
                approved = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM dbo.GeneratedQuestions WHERE company_id = ?",
                    identity.company_id)
                counts = {
                    "questionsApproved": approved,
                    "questionsTotal": cur.fetchone()[0],
                    # A learner cannot be served anything until questions are approved.
                    "servable": approved > 0,
                }
            else:
                # Proves the connection works without reporting anyone's data.
                cur.execute("SELECT 1")
                cur.fetchone()
        return _json({"status": "ok", "database": "connected", **counts})
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
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    learner = _learner_key(identity)
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT topic, answered, correct, accuracy_percent, mastery_level
                   FROM dbo.vw_LearnerTopicMastery
                   WHERE learner_id = ? AND company_id = ?
                   ORDER BY accuracy_percent""",
                learner, identity.company_id,
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
    # No answer keys here, but it does enumerate every role and topic the company
    # trains on, which is not something to hand out unauthenticated.
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT c.role_scope, q.topic, COUNT(*) AS questionCount
                   FROM dbo.GeneratedQuestions q
                   JOIN dbo.SourceChunks c ON c.chunk_id = q.source_chunk_id
                   WHERE q.review_status = 'Approved' AND q.company_id = ?
                   GROUP BY c.role_scope, q.topic
                   ORDER BY c.role_scope, q.topic""",
                identity.company_id,
            )
            return _json({"topics": _rows(cur)})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


# --------------------------------------------------------------------------
# quiz
# --------------------------------------------------------------------------

def _weak_topics(cur, learner: str, company_id: int) -> List[str]:
    """
    Topics below the threshold with enough evidence to judge.

    The evidence floor matters: two wrong answers out of two is noise, and drilling
    someone on a topic they actually know is worse than not drilling at all.
    """
    cur.execute(
        """SELECT topic FROM dbo.vw_LearnerTopicMastery
           WHERE learner_id = ? AND company_id = ?
             AND answered >= ? AND accuracy_percent < ?
           ORDER BY accuracy_percent""",
        learner, company_id, MIN_ANSWERS, WEAK_THRESHOLD * 100,
    )
    return [r["topic"] for r in _rows(cur)]


@app.route(route="quiz/start", methods=["POST"])
def start_quiz(req: func.HttpRequest) -> func.HttpResponse:
    """
    Assemble and serve a quiz. Reads the answer-safe view only.

    No model call happens here — selection is a database query and arithmetic, which is
    why it is fast, free and reproducible.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    learner = _learner_key(identity)
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    length = int(body.get("length") or QUIZ_LENGTH)
    role = body.get("role")

    try:
        with _conn() as c:
            cur = c.cursor()
            weak = _weak_topics(cur, learner, identity.company_id)

            # Questions the learner has already seen — held back unless the bank is thin.
            cur.execute(
                """SELECT DISTINCT question_id FROM dbo.GeneratedQuizResponses
                    WHERE learner_id = ? AND company_id = ?""",
                learner, identity.company_id,
            )
            seen = {r["question_id"] for r in _rows(cur)}

            sql = """SELECT DISTINCT q.question_id, q.topic, q.question_type, q.difficulty,
                            q.prompt, q.points, q.provenance_class, c.role_scope
                     FROM dbo.GeneratedQuestions q
                     JOIN dbo.SourceChunks c ON c.chunk_id = q.source_chunk_id
                     WHERE q.review_status = 'Approved' AND q.company_id = ?"""
            params: List[Any] = [identity.company_id]
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
                """INSERT INTO dbo.GeneratedQuizAttempts
                       (attempt_id, learner_id, started_at, company_id)
                   VALUES (?, ?, SYSUTCDATETIME(), ?)""",
                attempt_id, learner, identity.company_id,
            )
            # Record what was served. POST /quiz/answer reveals the key for one question,
            # so it has to be able to check that the question was actually in this
            # attempt -- otherwise it answers for any question in the bank, which is an
            # oracle. devserver.py keeps this in memory; a multi-instance Function App
            # cannot, so it lives in the database (017_create_attempt_questions.sql).
            cur.executemany(
                """INSERT INTO dbo.GeneratedQuizAttemptQuestions
                       (attempt_id, question_id, sort_order)
                   VALUES (?, ?, ?)""",
                [(attempt_id, q["question_id"], i) for i, q in enumerate(chosen, 1)],
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


def _issue_certificate(cur, identity, attempt_id, results):
    """
    Record a pass as a certificate.

    Certifies the source DOCUMENT, not the topic — the same grain trainings and mastery
    use, so a certificate lines up with the card the learner pressed "start" on. A quiz
    spanning two documents certifies the one most of its questions came from; mixed
    quizzes are an assembly artefact, not something to certify twice.

    attempt_score is the difficulty-weighted score, not the raw percentage the results
    screen shows. Those differ on purpose: the raw score is "how many did you get right",
    the attempt score is what feeds Q Score.

    Returns None rather than raising if there is nothing to certify. A certificate that
    cannot be issued must never lose the learner their quiz result.
    """
    graded = [{"difficulty": r.get("difficulty"), "correct": bool(r.get("isCorrect"))}
              for r in results]
    score = qscore.attempt_score(graded)

    titles = [(r.get("source") or {}).get("documentTitle") or r.get("topic")
              for r in results]
    titles = [t for t in titles if t]
    if not titles:
        return None
    doc_title = max(set(titles), key=titles.count)

    # Category comes from the role's requirement list, where an admin declared it.
    # Defaulting to technical when nothing says otherwise keeps a certificate out of the
    # behavioural bucket rather than guessing it into one.
    category = "technical"
    for req in _role_requirements(cur, identity.role_code, identity.company_id):
        if req["doc_title"] == doc_title:
            category = req.get("category") or "technical"
            break

    expires_at = qscore.expiry_from(
        datetime.now(timezone.utc).isoformat(timespec="seconds"), CERT_VALIDITY_MONTHS)

    cur.execute(
        """INSERT INTO dbo.Certificates
               (employee_id, attempt_id, doc_title, attempt_score, category,
                issued_at, expires_at, status, company_id)
           VALUES (?, ?, ?, ?, ?, SYSUTCDATETIME(), ?, 'Active', ?)""",
        identity.employee_id, attempt_id, doc_title, score, category, expires_at,
        identity.company_id,
    )
    return {
        "docTitle": doc_title,
        "attemptScore": score,
        "category": category,
        "expiresAt": expires_at,
        # No artefact yet. Null beats a link that 404s.
        "certificateUrl": None,
    }


@app.route(route="quiz/submit", methods=["POST"])
def submit_quiz(req: func.HttpRequest) -> func.HttpResponse:
    """
    Grade an attempt. All arithmetic, no model call.

    MultiSelect is all-or-nothing: partial credit on a compliance question lets someone
    pass while still believing something false.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    learner = _learner_key(identity)
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
                              provenance_class, difficulty
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
                        selected, text_answer, is_correct, points_awarded, company_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    "res_" + uuid.uuid4().hex[:12], attempt_id, learner, qid,
                    q["topic"], ",".join(selected), text_answer,
                    1 if is_correct else 0, points, identity.company_id,
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
                        # Carried because shared/qscore.py weights by it. Omitting it
                        # does not error — every question silently weighs 1.0 and the
                        # difficulty weighting becomes a no-op that still looks right.
                        "difficulty": q["difficulty"],
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

            certificate = None
            if passed:
                certificate = _issue_certificate(cur, identity, attempt_id, results)

            c.commit()

        return _json(
            {
                "attemptId": attempt_id,
                "scorePercent": percent,
                "pointsAwarded": awarded,
                "pointsPossible": possible,
                "passed": passed,
                "passingScorePercent": PASSING_SCORE,
                "certificate": certificate,
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
    # This endpoint returns GeneratedOptions.is_correct. Unauthenticated, that published
    # the answer key for the whole bank to anyone who could reach the host -- no sign-in,
    # no role, no rate limit. Reviewing is a manager action in any case, so it takes the
    # same gate as review/decide.
    identity = get_current_employee(req)
    forbidden = require_manager(identity)
    if forbidden:
        return forbidden

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
                   WHERE review_status = 'PendingReview' AND company_id = ?
                   ORDER BY created_at""",
                limit, identity.company_id,
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
    identity = get_current_employee(req)
    forbidden = require_manager(identity)
    if forbidden:
        return forbidden
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    ids = body.get("questionIds") or []
    decision = body.get("decision")
    reviewer = _learner_key(identity)

    if decision not in ("Approved", "Rejected"):
        return _error(400, "Bad request", "decision must be 'Approved' or 'Rejected'")
    if not ids:
        return _error(400, "Bad request", "questionIds is required")

    try:
        updated = 0
        with _conn() as c:
            cur = c.cursor()
            for qid in ids:
                cur.execute(
                    """UPDATE dbo.GeneratedQuestions
                       SET review_status = ?, reviewed_by = ?, reviewed_at = SYSUTCDATETIME()
                       WHERE question_id = ? AND company_id = ?""",
                    decision, reviewer, qid, identity.company_id,
                )
                updated += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            c.commit()
        # rowcount, not len(ids): a question id belonging to another company now matches
        # nothing, and reporting it as updated would hide exactly the case the filter is
        # there to catch.
        return _json({"updated": updated, "requested": len(ids),
                      "decision": decision, "reviewedBy": reviewer})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


# --------------------------------------------------------------------------
# product surface — what the web app actually renders
# --------------------------------------------------------------------------
# These existed only in scripts/devserver.py, which reads the SQLite bank. Ported
# against Azure SQL rather than copied: the queries differ, the answer-safety rules do
# not.


@app.route(route="quiz/answer", methods=["POST"])
def answer_question(req: func.HttpRequest) -> func.HttpResponse:
    """
    Grade ONE question mid-quiz, so the UI can give immediate feedback.

    This endpoint exists because the alternative is worse. The React app shows a verdict
    as each question is answered, and it used to do that against a correct-answer index
    held in the browser — which puts every answer one devtools panel away. Here the
    browser sends what was picked and gets back a verdict, so the key is revealed for
    that one question, only after an answer was committed.

    That makes the two checks below load-bearing rather than defensive. The endpoint
    reveals an answer, so it must be impossible to ask it about a question you were not
    served, or about somebody else's attempt. Without either check it is a way to read
    the whole answer key without taking a quiz.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    learner = _learner_key(identity)

    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    attempt_id = (body.get("attemptId") or "").strip()
    question_id = (body.get("questionId") or "").strip()
    if not attempt_id or not question_id:
        return _error(400, "Bad request", "attemptId and questionId are required")

    try:
        with _conn() as c:
            cur = c.cursor()

            # Check 1: the attempt is this caller's. Same 404 for "no such attempt" and
            # "someone else's" — distinguishing them confirms an attempt id exists.
            cur.execute(
                """SELECT learner_id FROM dbo.GeneratedQuizAttempts
                    WHERE attempt_id = ? AND company_id = ?""",
                attempt_id, identity.company_id,
            )
            attempt = cur.fetchone()
            if attempt is None or attempt.learner_id != learner:
                return _error(404, "Unknown attempt", "Start a quiz first.")

            # Check 2: the question was served in it.
            cur.execute(
                """SELECT 1 FROM dbo.GeneratedQuizAttemptQuestions
                   WHERE attempt_id = ? AND question_id = ?""",
                attempt_id, question_id,
            )
            if cur.fetchone() is None:
                return _error(403, "Not part of this attempt", question_id)

            cur.execute(
                """SELECT question_id, question_type, explanation, source_doc_title,
                          source_page, source_quote, source_url, provenance_class
                     FROM dbo.GeneratedQuestions
                    WHERE question_id = ? AND company_id = ?""",
                question_id, identity.company_id,
            )
            question = cur.fetchone()
            if question is None:
                return _error(404, "Unknown question", question_id)

            cur.execute(
                """SELECT option_id, is_correct FROM dbo.GeneratedOptions
                   WHERE question_id = ?""",
                question_id,
            )
            options = _rows(cur)

            cur.execute(
                "SELECT accepted_answer FROM dbo.GeneratedAnswerKeys WHERE question_id = ?",
                question_id,
            )
            accepted = [r["accepted_answer"] for r in _rows(cur)]

        if (question.question_type or "").lower() in ("fill_in_blank", "fillintheblank"):
            typed = str(body.get("textAnswer") or "").strip().lower()
            correct = bool(typed) and typed in {a.strip().lower() for a in accepted}
        else:
            # Exact set match, not overlap. Partial credit on a compliance question lets
            # someone pass while still believing something false — the same rule
            # submit_quiz grades by.
            selected = set(body.get("selectedOptionIds") or [])
            key = {o["option_id"] for o in options if o["is_correct"]}
            correct = bool(key) and selected == key

        return _json({
            "questionId": question_id,
            "correct": correct,
            "correctOptionIds": [o["option_id"] for o in options if o["is_correct"]],
            "acceptedAnswers": accepted,
            "explanation": question.explanation,
            "sourceTitle": question.source_doc_title,
            "sourcePage": question.source_page,
            "sourceQuote": question.source_quote,
            "sourceUrl": question.source_url,
            "provenance": question.provenance_class,
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="trainings", methods=["GET"])
def list_trainings(req: func.HttpRequest) -> func.HttpResponse:
    """
    Source documents, presented as the UI's "trainings".

        training = source document        (Behavioral Compliance for Employees)
        module   = section heading in it  (Recognising Phishing Attempts)

    Deliberately the same grain mastery is measured on, so a training card's progress
    ring and the quiz engine's weak-topic targeting agree. Deriving it any other way lets
    the UI show 90% on a subject the engine considers weak.

    Role isolation happens here, on the serving side: a Sales Manager should not see that
    Cloud DevOps modules exist, never mind be able to take them.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    learner = _learner_key(identity)
    role = (identity.role_code or "ALL").upper()

    try:
        with _conn() as c:
            cur = c.cursor()

            # Approved questions in scope, grouped by the document they came from.
            # role_scope 'ALL' is company-wide material everyone takes.
            cur.execute(
                """SELECT COALESCE(q.source_doc_title, q.topic) AS doc,
                          COUNT(*) AS question_count
                     FROM dbo.GeneratedQuestions q
                     LEFT JOIN dbo.SourceChunks c ON c.chunk_id = q.source_chunk_id
                    WHERE q.review_status = 'Approved'
                      AND q.company_id = ?
                      AND (COALESCE(c.role_scope, 'ALL') IN ('ALL', ?))
                    GROUP BY COALESCE(q.source_doc_title, q.topic)
                    ORDER BY doc""",
                identity.company_id, role,
            )
            docs = _rows(cur)
            if not docs:
                return _json({"trainings": []})

            visible = {d["doc"] for d in docs}

            cur.execute(
                """SELECT DISTINCT doc_title, topic FROM dbo.SourceChunks
                    WHERE company_id = ?
                      AND COALESCE(role_scope, 'ALL') IN ('ALL', ?)
                    ORDER BY doc_title, topic""",
                identity.company_id, role,
            )
            modules: Dict[str, List[str]] = {}
            for row in _rows(cur):
                if row["doc_title"] in visible:
                    modules.setdefault(row["doc_title"], []).append(row["topic"])

            # Mastery is per topic; a training is a document. Roll the topics up.
            cur.execute(
                """SELECT m.topic, m.answered, m.correct
                     FROM dbo.vw_LearnerTopicMastery m
                    WHERE m.learner_id = ? AND m.company_id = ?""",
                learner, identity.company_id,
            )
            by_topic = {r["topic"]: r for r in _rows(cur)}

            cur.execute(
                """SELECT DISTINCT doc_title, topic FROM dbo.SourceChunks
                    WHERE company_id = ?""", identity.company_id)
            topic_to_doc = {r["topic"]: r["doc_title"] for r in _rows(cur)}

        rolled: Dict[str, Dict[str, int]] = {}
        for topic, row in by_topic.items():
            doc = topic_to_doc.get(topic, topic)
            acc = rolled.setdefault(doc, {"answered": 0, "correct": 0})
            acc["answered"] += int(row["answered"] or 0)
            acc["correct"] += int(row["correct"] or 0)

        out = []
        for d in docs:
            doc = d["doc"]
            acc = rolled.get(doc, {"answered": 0, "correct": 0})
            answered = acc["answered"]
            accuracy = round(100.0 * acc["correct"] / answered, 1) if answered else 0.0
            # Status comes from evidence, not a stored flag: nothing answered means not
            # started, and at or above the pass mark means done.
            if answered == 0:
                status = "not-started"
            elif accuracy >= PASSING_SCORE:
                status = "completed"
            else:
                status = "in-progress"
            out.append({
                "id": doc,
                "title": doc,
                "status": status,
                "mastery": int(accuracy),
                "answered": answered,
                "questionCount": d["question_count"],
                "modules": modules.get(doc, []),
            })
        return _json({"trainings": out})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="lesson", methods=["GET"])
def get_lesson(req: func.HttpRequest) -> func.HttpResponse:
    """
    The reading material for one training, before its quiz.

    Source passages, in document order — not model-generated prose. The whole provenance
    model rests on a learner being tested against text somebody approved, so the lesson
    is that same text rather than a summary of it.

    Scoped to the caller's role for the same reason the training list is: a document out
    of scope should not be readable by guessing its title.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    role = (identity.role_code or "ALL").upper()

    training = (req.params.get("training") or "").strip()
    if not training:
        return _error(400, "Bad request", "training is required")

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT chunk_id, topic, section, page_start, page_end, chunk_text
                     FROM dbo.SourceChunks
                    WHERE doc_title = ?
                      AND company_id = ?
                      AND COALESCE(role_scope, 'ALL') IN ('ALL', ?)
                    ORDER BY page_start, section""",
                training, identity.company_id, role,
            )
            sections = _rows(cur)

        if not sections:
            # Same response whether the document does not exist or is out of scope.
            # Different ones would let a learner enumerate other roles' material by
            # title, which is what role scoping exists to prevent.
            return _error(404, "No lesson available",
                          "No readable sections for this training.")

        return _json({
            "training": training,
            "sections": [
                {
                    "id": s["chunk_id"],
                    "topic": s["topic"],
                    "heading": s["section"],
                    "pageStart": s["page_start"],
                    "pageEnd": s["page_end"],
                    "text": s["chunk_text"],
                }
                for s in sections
            ],
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="team", methods=["GET"])
def get_team(req: func.HttpRequest) -> func.HttpResponse:
    """
    Who reports to me, and which roles I may upload for.

    Two questions, answered separately:

      people         everyone below me in the Employees.manager_id chain, however deep,
                     so a director sees their managers' reports too
      uploadTargets  the roles those people hold, with roles held by my DIRECT reports
                     marked. The UI defaults to those; the whole subtree is permitted,
                     because a director may want to push something to every engineer
                     beneath them rather than only to their own managers.

    An employee with nobody under them gets empty lists and a 200, not a 403. "You manage
    no one" is a fact about the org chart, not a permissions failure, and the UI renders
    it as an empty state. Gating this on manager tier would also be wrong: the tier and
    the reporting line are different things, and it is the reporting line that decides
    whose training you are responsible for.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()

    try:
        with _conn() as c:
            cur = c.cursor()
            # Recursive CTE rather than repeated round trips. MAXRECURSION 32 is a
            # deliberate cap: manager_id is a self-referencing FK with nothing stopping a
            # cycle, and a malformed org chart should error rather than spin.
            cur.execute(
                """WITH subtree AS (
                       SELECT e.id, e.name, e.email, e.role_id, e.manager_id, 1 AS depth
                         FROM dbo.Employees e
                        WHERE e.manager_id = ?
                       UNION ALL
                       SELECT e.id, e.name, e.email, e.role_id, e.manager_id, s.depth + 1
                         FROM dbo.Employees e
                         JOIN subtree s ON e.manager_id = s.id
                   )
                   SELECT s.id, s.name, s.email, s.depth, s.manager_id,
                          r.title, r.access_role, r.role_code
                     FROM subtree s
                     LEFT JOIN dbo.Roles r ON r.id = s.role_id
                    ORDER BY s.depth, s.name
                   OPTION (MAXRECURSION 32)""",
                identity.employee_id,
            )
            people = _rows(cur)

        # ALL is company-wide, not a role anyone "controls", so it is not an upload
        # target for a manager — only for admin and executive. A subtree member whose
        # role_code is unmapped surfaces as ALL and must not smuggle it in.
        company_wide_allowed = (identity.access_role or "") in ("admin", "executive")

        targets: Dict[str, Dict[str, Any]] = {}
        for person in people:
            code = (person.get("role_code") or "ALL").upper()
            if code == "ALL" and not company_wide_allowed:
                continue
            entry = targets.setdefault(code, {
                "roleCode": code,
                "title": person.get("title") or code,
                "direct": False,
                "headcount": 0,
            })
            entry["headcount"] += 1
            # A role held by both a direct report and someone deeper counts as direct:
            # the closer relationship decides whether it is a default target.
            if person["depth"] == 1:
                entry["direct"] = True

        return _json({
            "manages": bool(people),
            "people": [
                {
                    "employeeId": p["id"],
                    "name": p["name"],
                    "email": p["email"],
                    "title": p.get("title"),
                    "roleCode": (p.get("role_code") or "ALL").upper(),
                    "accessRole": p.get("access_role"),
                    "managerId": p.get("manager_id"),
                    "direct": p["depth"] == 1,
                }
                for p in people
            ],
            "uploadTargets": sorted(
                targets.values(), key=lambda t: (not t["direct"], t["title"])),
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


# --------------------------------------------------------------------------
# certificates, requirements and Q Score
# --------------------------------------------------------------------------
# Mirrors what scripts/devserver.py serves, against Azure SQL. The arithmetic is shared
# via shared/qscore.py; only the queries differ.


def _role_requirements(cur, role_code: str, company_id: int) -> List[Dict[str, Any]]:
    """
    What this role must complete.

    Always includes the ALL requirements. Company-wide training is required of everyone,
    so it belongs in every role's denominator — without it a role with no specific
    requirements scores against an empty list and reads 100% compliant having done
    nothing.
    """
    role = (role_code or "ALL").upper()
    if role == "ALL":
        cur.execute(
            """SELECT DISTINCT doc_title, category FROM dbo.RoleRequirements
                WHERE company_id = ? AND role_code = 'ALL' ORDER BY doc_title""",
            company_id)
    else:
        cur.execute(
            """SELECT DISTINCT doc_title, category FROM dbo.RoleRequirements
                WHERE company_id = ? AND role_code IN (?, 'ALL') ORDER BY doc_title""",
            company_id, role)
    return _rows(cur)


def _certificates_for(cur, employee_id: int, company_id: int) -> List[Dict[str, Any]]:
    cur.execute(
        """SELECT id, doc_title, attempt_id, attempt_score, category,
                  issued_at, expires_at, certificate_url, status
             FROM dbo.Certificates
            WHERE employee_id = ? AND company_id = ? AND doc_title IS NOT NULL
            ORDER BY issued_at DESC""",
        employee_id, company_id)
    return [
        {
            "certificate_id": str(r["id"]),
            "doc_title": r["doc_title"],
            "attempt_id": r["attempt_id"],
            "attempt_score": float(r["attempt_score"] or 0),
            "category": r["category"] or "technical",
            "issued_at": str(r["issued_at"]),
            "expires_at": str(r["expires_at"]),
            "certificate_url": r["certificate_url"],
            "status": r["status"],
        }
        for r in _rows(cur)
    ]


@app.route(route="certificates", methods=["GET"])
def list_certificates(req: func.HttpRequest) -> func.HttpResponse:
    """Certificates this learner holds, expired ones included and marked."""
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()

    try:
        with _conn() as c:
            held = _certificates_for(c.cursor(), identity.employee_id, identity.company_id)

        best = qscore.best_certificates(held)
        return _json({
            "certificates": [
                {
                    "certificateId": cert["certificate_id"],
                    "title": cert["doc_title"],
                    "date": (cert["issued_at"] or "")[:10],
                    "score": round(cert["attempt_score"], 1),
                    "category": cert["category"],
                    "expiresAt": (cert["expires_at"] or "")[:10],
                    "expired": qscore.is_expired(cert["expires_at"]),
                    "daysUntilExpiry": qscore.days_until_expiry(cert["expires_at"]),
                    "ofRecord": best.get(cert["doc_title"], {}).get(
                        "certificate_id") == cert["certificate_id"],
                    "certificateUrl": cert["certificate_url"],
                }
                for cert in held
            ],
            "renewalsDue": qscore.renewal_candidates(held),
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="qscore", methods=["GET"])
def get_qscore(req: func.HttpRequest) -> func.HttpResponse:
    """
    Q Score for the caller, or for someone they manage.

    ?employee=<email> reads a report's score. Permitted only inside the caller's
    reporting subtree — QSCORE-08's "everyone above you in the chain", read from the
    other end. Anyone else is a 404 rather than a 403: whether a given person exists is
    not something to confirm to someone with no business asking.

    Computed on read, never stored. It has to fall when a certificate expires and nobody
    has done anything, and a stored copy cannot do that — it would go stale silently,
    and a stale compliance number reads as "you are fine" while you are not.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()

    wanted = (req.params.get("employee") or "").strip().lower()
    try:
        with _conn() as c:
            cur = c.cursor()

            target_id, target_email = identity.employee_id, identity.email
            target_role, target_name = identity.role_code, identity.name

            if wanted and wanted != identity.email.lower():
                # Recursive walk down the reporting chain, same shape as /team.
                cur.execute(
                    """WITH subtree AS (
                           SELECT e.id, e.email, e.name, e.role_id
                             FROM dbo.Employees e WHERE e.manager_id = ?
                           UNION ALL
                           SELECT e.id, e.email, e.name, e.role_id
                             FROM dbo.Employees e
                             JOIN subtree s ON e.manager_id = s.id
                       )
                       SELECT TOP 1 s.id, s.email, s.name, r.role_code
                         FROM subtree s
                         LEFT JOIN dbo.Roles r ON r.id = s.role_id
                        WHERE LOWER(s.email) = ?
                       OPTION (MAXRECURSION 32)""",
                    identity.employee_id, wanted)
                person = cur.fetchone()
                if person is None:
                    return _error(404, "Not found", "No such employee in your team.")
                target_id, target_email = person.id, person.email
                target_name = person.name
                target_role = (person.role_code or "ALL").upper()

            requirements = _role_requirements(cur, target_role, identity.company_id)
            held = _certificates_for(cur, target_id, identity.company_id)

        standing = qscore.standing(requirements, held)
        return _json({
            "employee": {"email": target_email, "name": target_name,
                         "roleCode": target_role},
            "overall": standing["overall"].to_dict(),
            "behavioural": standing["behavioural"].to_dict(),
            "technical": standing["technical"].to_dict(),
            # Stated rather than implied: a Q Score of 0 against an empty required list
            # is a missing configuration, not a judgement on the person.
            "requirementsConfigured": bool(requirements),
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="requirements", methods=["GET"])
def list_requirements(req: func.HttpRequest) -> func.HttpResponse:
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT role_code, doc_title, category FROM dbo.RoleRequirements
                    WHERE company_id = ? ORDER BY role_code, doc_title""",
                identity.company_id)
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for row in _rows(cur):
                grouped.setdefault(row["role_code"], []).append(
                    {"doc_title": row["doc_title"], "category": row["category"]})
            mine = _role_requirements(cur, identity.role_code, identity.company_id)
        return _json({"requirements": grouped, "mine": mine})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="requirements", methods=["POST"])
def set_requirements(req: func.HttpRequest) -> func.HttpResponse:
    """
    Replace the required list for one role. Admin or executive only.

    Not a manager action, deliberately: this is Coverage's denominator, so editing it
    moves the Q Score of everyone in that role. That is a compliance decision, not a
    team one.

    Replace rather than merge, so removing a requirement is possible through the same
    interface that adds one.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    if (identity.access_role or "") not in ("admin", "executive"):
        return _error(403, "Forbidden",
                      "Setting required training changes the Q Score of everyone in that "
                      "role. Requires admin access or above.")

    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    role = str(body.get("roleCode") or "").strip().upper()
    items = body.get("requirements")
    if not role or not isinstance(items, list):
        return _error(400, "Bad request", "roleCode and a requirements list are required.")

    rows = [(identity.company_id, role, str(i["doc_title"]),
             (i.get("category") or "technical").strip().lower())
            for i in items if i.get("doc_title")]
    bad = [r[3] for r in rows if r[3] not in qscore.CATEGORIES]
    if bad:
        return _error(422, "Unknown category",
                      "category must be behavioural or technical; got {}.".format(
                          ", ".join(sorted(set(bad)))))

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                "DELETE FROM dbo.RoleRequirements WHERE company_id = ? AND role_code = ?",
                identity.company_id, role)
            if rows:
                cur.executemany(
                    """INSERT INTO dbo.RoleRequirements
                           (company_id, role_code, doc_title, category)
                       VALUES (?, ?, ?, ?)""",
                    rows)
            c.commit()
        return _json({"roleCode": role, "count": len(rows)})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


# --------------------------------------------------------------------------
# documents / roles / generation
# --------------------------------------------------------------------------
#
# Uploading material and editing the role catalog change what an entire role is taught
# and certified against, so every route below requires manager tier or above, same as
# scripts/devserver.py's local equivalents -- the UI hides these controls too, but a
# hidden button is not a permission check.
#
# Generation runs INLINE, in the same request that confirms the upload, rather than on
# a background thread the way the local dev server does it. A thread survives for the
# life of one Python process; an Azure Functions instance can recycle or scale to a
# second one between the request that starts generation and the next /jobs/{id} poll,
# and an in-memory job record on the first instance is invisible to the second. Running
# a few dozen chunks against gpt-5 inline is a matter of minutes, and mob-functions-dev
# is provisioned on a Basic (B1) Dedicated plan (infra/modules/functions/main.tf), which
# has no Consumption-plan-style 5/10 minute HTTP timeout -- see host.json's
# functionTimeout for the explicit ceiling this relies on. The job row this writes to
# dbo.GenerationJobs is what actually makes /jobs/{id} correct across instances; running
# inline just means it is written as "done" already by the time confirm() returns,
# rather than updated incrementally by a worker only that request's instance can see.


def _permitted_upload_roles(cur, identity) -> set:
    """
    Role codes this manager may publish to: everyone in their reporting subtree, plus
    ALL if and only if they are admin or executive.

    Same rule as GET /team's uploadTargets, computed separately rather than shared with
    it -- get_team's query already ships and is tested; changing it to serve two callers
    risks the working one for the sake of not repeating four lines. Matches
    scripts/devauth.py's permitted_upload_roles(), which every local endpoint enforces
    the same way.
    """
    cur.execute(
        """WITH subtree AS (
               SELECT e.id, e.role_id FROM dbo.Employees e WHERE e.manager_id = ?
               UNION ALL
               SELECT e.id, e.role_id FROM dbo.Employees e
               JOIN subtree s ON e.manager_id = s.id
           )
           SELECT r.role_code FROM subtree s
           LEFT JOIN dbo.Roles r ON r.id = s.role_id
          OPTION (MAXRECURSION 32)""",
        identity.employee_id,
    )
    allowed = {(r[0] or "ALL").upper() for r in cur.fetchall()}
    allowed.discard("ALL")
    if (identity.access_role or "") in ("admin", "executive"):
        allowed.add("ALL")
    return allowed


@app.route(route="documents", methods=["GET"])
def list_documents(req: func.HttpRequest) -> func.HttpResponse:
    """What has been uploaded, and how far each one got."""
    identity = get_current_employee(req)
    forbidden = require_manager(identity)
    if forbidden:
        return forbidden
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                "SELECT doc_title, COUNT(*) AS chunks FROM dbo.SourceChunks "
                "WHERE company_id = ? GROUP BY doc_title",
                identity.company_id,
            )
            chunk_counts = {r.doc_title: r.chunks for r in cur.fetchall()}
            cur.execute(
                "SELECT source_doc_title, COUNT(*) AS n FROM dbo.GeneratedQuestions "
                "WHERE company_id = ? AND review_status = 'Approved' "
                "GROUP BY source_doc_title",
                identity.company_id,
            )
            q_counts = {(r.source_doc_title or ""): r.n for r in cur.fetchall()}

        docs = [
            {
                "title": title,
                "chunks": count,
                "questions": q_counts.get(title, 0),
                # Read but not yet generated from -- the UI offers to generate for these.
                "ready": q_counts.get(title, 0) > 0,
            }
            for title, count in sorted(chunk_counts.items())
        ]
        from quizgen.pipeline import generator_name
        return _json({"documents": docs, "files": [], "generator": generator_name(),
                      "uploadDir": ""})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:300]))


@app.route(route="documents", methods=["POST"])
def upload_document(req: func.HttpRequest) -> func.HttpResponse:
    """
    Accept an uploaded document and extract it. Generation is a separate call
    (/documents/confirm), after the manager confirms the AI's proposed section->role
    mapping -- the AI proposes, the manager decides, same as the local dev server.
    """
    identity = get_current_employee(req)
    forbidden = require_manager(identity)
    if forbidden:
        return forbidden

    upload = req.files.get("file") if req.files else None
    if upload is None or not upload.filename:
        return _error(400, "No file received", "Send multipart/form-data with a 'file' part.")

    from pathlib import Path
    safe = Path(upload.filename).name  # basename only -- no path traversal via filename
    suffix = Path(safe).suffix.lower()
    if suffix not in (".pdf", ".txt", ".md"):
        return _error(415, "Unsupported file type",
                      "Only .pdf, .txt and .md can be read. Got {!r}.".format(suffix or "none"))

    content = upload.stream.read()
    if not content:
        return _error(400, "Empty upload", "No content was sent.")

    # /tmp is writable on Azure Functions Linux and is per-invocation scratch space for
    # the extraction library, which needs a real file path (pypdf and Document
    # Intelligence both read from disk) -- not a database. Nothing here is data of
    # record; the extracted chunks are what get written to Azure SQL below, and this
    # file is gone the moment the request ends.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / safe
        target.write_bytes(content)

        from quizgen.ingest import ingest_document
        try:
            chunks = ingest_document(target)
        except Exception as exc:  # noqa: BLE001
            return _error(422, "Could not read this document",
                          "{}: {}".format(type(exc).__name__, str(exc)[:200]))

    if not chunks:
        return _error(422, "No teachable content found",
                      "Text was extracted but nothing usable was found in it.")

    doc_title = chunks[0].doc_title
    try:
        with _conn() as c:
            cur = c.cursor()
            # Two different documents must never share a title -- they would merge
            # into one training, mixing roles and letting set_chunk_roles tag the
            # wrong sections.
            cur.execute(
                "SELECT DISTINCT doc_id FROM dbo.SourceChunks WHERE doc_title = ? AND company_id = ?",
                doc_title, identity.company_id,
            )
            existing_ids = {r.doc_id for r in cur.fetchall()}
            if existing_ids and chunks[0].doc_id not in existing_ids:
                doc_title = "{} ({})".format(doc_title, Path(safe).stem.replace("_", " "))
                for ch in chunks:
                    ch.doc_title = doc_title

            from shared.sqlbank import SqlBank
            bank = SqlBank(c, identity.company_id)
            bank.save_chunks(chunks)

            from quizgen.rolemap import seed_roles
            seed_roles(bank)
            known_roles = bank.roles()

            permitted = _permitted_upload_roles(cur, identity)

            topics = sorted({ch.topic for ch in chunks})
            sections = {}
            for ch in chunks:
                sections.setdefault(ch.topic, ch.text)

            from quizgen.rolemap import analyze_document
            try:
                mapping = analyze_document(doc_title, sections, known_roles)
            except RuntimeError as exc:
                # No model credentials. The chunks are already saved (committed by
                # save_chunks/seed_roles above), so nothing is lost -- confirm can
                # still be called once credentials exist.
                return _error(503, "Role mapping needs the real model", str(exc)[:300])
            except Exception as exc:  # noqa: BLE001
                return _error(502, "Role analysis failed",
                              "{}: {}".format(type(exc).__name__, str(exc)[:250]))

        from quizgen.pipeline import generator_name
        return _json({
            "file": safe, "title": doc_title, "chunks": len(chunks), "topics": topics,
            "summary": mapping.summary,
            "proposedRoles": mapping.assignments,
            "permittedRoles": sorted(permitted),
            "unknownRoles": mapping.unknown_roles,
            "thinTopics": mapping.thin_topics,
            "knownRoles": known_roles,
            "generator": generator_name(),
            "needsConfirmation": True,
        }, 201)
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:300]))


@app.route(route="documents/confirm", methods=["POST"])
def confirm_document(req: func.HttpRequest) -> func.HttpResponse:
    """
    The manager's confirmed section->role mapping. Tags chunks, creates any new roles
    they chose to add, retires a superseded document if named, then generates.
    """
    identity = get_current_employee(req)
    forbidden = require_manager(identity)
    if forbidden:
        return forbidden
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    doc_title = str(body.get("title", "")).strip()
    assignments = body.get("assignments") or {}
    new_roles = body.get("newRoles") or []
    supersede = str(body.get("supersede", "")).strip()
    if not doc_title or not isinstance(assignments, dict):
        return _error(400, "Bad request", "title and assignments are required")

    from shared.sqlbank import SqlBank, create_job, new_job_id, update_job

    try:
        with _conn() as c:
            cur = c.cursor()
            bank = SqlBank(c, identity.company_id)

            permitted = _permitted_upload_roles(cur, identity)
            for code in set(assignments.values()):
                up = (code or "ALL").upper()
                if up != "ALL" and up not in permitted:
                    return _error(
                        403, "Not your role to publish to",
                        "{} is outside your reporting chain.".format(up))

            for role in new_roles:
                code = str(role.get("roleCode", "")).strip().upper().replace(" ", "_")
                title = str(role.get("title", "")).strip()
                if code and title:
                    bank.add_role(code, title, str(role.get("description", "")))

            tagged = bank.set_chunk_roles(doc_title, {
                topic: str(code) for topic, code in assignments.items()
            })

            retired = 0
            if supersede and supersede != doc_title:
                retired = bank.retire_document_questions(supersede)

            job_id = new_job_id()
            create_job(c, job_id, identity.company_id, doc_title)

        from quizgen.pipeline import select_chunks, generate_questions
        with _conn() as c2:
            bank2 = SqlBank(c2, identity.company_id)
            to_generate, skipped = select_chunks(bank2, doc_title=doc_title)
            update_job(c2, job_id, identity.company_id, total=len(to_generate),
                       message="Reading {} section(s)…".format(len(to_generate))
                       if to_generate else "Already generated for this document.")

            if not to_generate:
                update_job(c2, job_id, identity.company_id, state="done",
                           message="Already generated.")
            else:
                try:
                    result = generate_questions(bank2, to_generate)
                    update_job(
                        c2, job_id, identity.company_id, state="done",
                        done_count=len(to_generate), kept=len(result.kept),
                        written=result.written, rejected=len(result.rejected),
                        message="Generated {} question(s).".format(result.written),
                    )
                except Exception as exc:  # noqa: BLE001
                    update_job(c2, job_id, identity.company_id, state="error",
                               message="{}: {}".format(type(exc).__name__, str(exc)[:200]))

        return _json({
            "title": doc_title, "taggedChunks": tagged, "retired": retired,
            "jobId": job_id,
        }, 201)
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:300]))


@app.route(route="jobs/{jobId}", methods=["GET"])
def job_status(req: func.HttpRequest) -> func.HttpResponse:
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    from shared.sqlbank import get_job
    job_id = req.route_params.get("jobId", "")
    with _conn() as c:
        job = get_job(c, job_id, identity.company_id)
    if job is None:
        return _error(404, "Unknown job", job_id)
    return _json(job)


@app.route(route="roles", methods=["GET"])
def list_roles(req: func.HttpRequest) -> func.HttpResponse:
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    try:
        with _conn() as c:
            cur = c.cursor()
            from shared.sqlbank import SqlBank
            bank = SqlBank(c, identity.company_id)
            roles = bank.roles()

            cur.execute(
                "SELECT role_code, COUNT(*) AS n FROM dbo.GeneratedQuestions "
                "WHERE company_id = ? AND review_status = 'Approved' "
                "GROUP BY role_code",
                identity.company_id,
            )
            counts: Dict[str, int] = {}
            for r in cur.fetchall():
                counts[(r.role_code or "ALL").upper()] = r.n

        return _json({
            "roles": [
                {**r, "questionCount": counts.get(r["role_code"], 0) + counts.get("ALL", 0)}
                for r in roles
            ],
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:300]))


@app.route(route="roles", methods=["POST"])
def add_role(req: func.HttpRequest) -> func.HttpResponse:
    identity = get_current_employee(req)
    forbidden = require_manager(identity)
    if forbidden:
        return forbidden
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    code = str(body.get("roleCode", "")).strip().upper().replace(" ", "_")
    title = str(body.get("title", "")).strip()
    if not code or not title:
        return _error(400, "Bad request", "roleCode and title are required")

    from shared.sqlbank import SqlBank
    with _conn() as c:
        SqlBank(c, identity.company_id).add_role(code, title, str(body.get("description", "")))
    return _json({"roleCode": code, "title": title}, 201)


@app.route(route="roles/{roleCode}/delete", methods=["POST"])
def remove_role(req: func.HttpRequest) -> func.HttpResponse:
    identity = get_current_employee(req)
    forbidden = require_manager(identity)
    if forbidden:
        return forbidden
    code = req.route_params.get("roleCode", "")
    from shared.sqlbank import SqlBank
    with _conn() as c:
        removed = SqlBank(c, identity.company_id).remove_role(code)
    if not removed:
        return _error(404, "No such role", code)
    return _json({"removed": code})

