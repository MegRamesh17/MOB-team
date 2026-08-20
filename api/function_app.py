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
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import azure.functions as func

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

from auth.login import bp as auth_bp
from shared.auth import get_current_employee, require_manager
from shared import qscore
from shared import pet_shop

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

            # For the streak and the two one-time badges, which need each attempt's
            # own date and score rather than just the totals above.
            cur.execute(
                """SELECT submitted_at, score_percent
                   FROM dbo.GeneratedQuizAttempts
                   WHERE learner_id = ? AND submitted_at IS NOT NULL""",
                learner,
            )
            # str()'d here, same as _certificates_for's issued_at/expires_at: pyodbc
            # returns a datetime, and qscore.py's _parse expects an ISO string like
            # every other caller gives it.
            submitted_attempts = [
                {"submitted_at": str(r["submitted_at"]), "score_percent": r["score_percent"]}
                for r in _rows(cur)
            ]

            requirements = _role_requirements(cur, identity.role_code, identity.company_id)
            held = _certificates_for(cur, identity.employee_id, identity.company_id)

        weak = [
            m for m in mastery
            if m["answered"] >= MIN_ANSWERS
            and float(m["accuracy_percent"]) < WEAK_THRESHOLD * 100
        ]

        streak = qscore.training_streak(
            [a["submitted_at"] for a in submitted_attempts])
        overall_q_score = qscore.standing(requirements, held)["overall"].q_score
        badges = qscore.earned_badges(
            attempts=submitted_attempts, streak=streak, q_score=overall_q_score)

        return _json(
            {
                "learnerId": learner,
                "attempts": stats.get("attempts") or 0,
                "passed": stats.get("passed") or 0,
                "streak": streak,
                "badges": badges,
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
                     WHERE q.review_status = 'Approved' AND q.company_id = ?
                       AND q.question_type IN
                           ('MultipleChoice', 'MultiSelect', 'TrueFalse', 'FillInBlank')"""
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


def _store_certificate_artifact(
        identity, attempt_id, doc_title, score, issued_at, expires_at):
    from shared.certificates import render_certificate, store_certificate

    certificate_ref = "cert_{}".format(attempt_id.removeprefix("att_"))
    pdf = render_certificate(
        identity.name or identity.email, doc_title, score, issued_at,
        expires_at, certificate_ref,
    )
    return store_certificate(
        pdf,
        "{}/{}/{}.pdf".format(
            identity.company_id, identity.employee_id, certificate_ref),
    )


def _issue_certificate(cur, identity, attempt_id, results, artifact_writer=None):
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

    blob_name = None
    if artifact_writer is not None:
        try:
            blob_name = artifact_writer(
                identity, attempt_id, doc_title, score, _now(), expires_at)
        except Exception:  # noqa: BLE001
            # The pass and certificate row are facts even if Blob Storage has a bad minute.
            # Keeping the URL null makes the missing artefact visible and retryable without
            # taking away the learner's result.
            blob_name = None

    cur.execute(
        """INSERT INTO dbo.Certificates
               (employee_id, attempt_id, doc_title, attempt_score, category,
                issued_at, expires_at, certificate_url, status, company_id)
           VALUES (?, ?, ?, ?, ?, SYSUTCDATETIME(), ?, ?, 'Active', ?)""",
        identity.employee_id, attempt_id, doc_title, score, category, expires_at,
        blob_name, identity.company_id,
    )
    return {
        "docTitle": doc_title,
        "attemptScore": score,
        "category": category,
        "expiresAt": expires_at,
        "certificateUrl": blob_name,
        "artifactReady": bool(blob_name),
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
                certificate = _issue_certificate(
                    cur, identity, attempt_id, results, _store_certificate_artifact)

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


# Demo-only: a fixed role -> course-title list, shown under Recommended regardless of
# whether the employee ever opened the skill-interest popup. Real recommendation is
# meant to come from EmployeeSkillInterest (an employee opting in); this hardcoded list
# exists so the tab has real, clickable content to show right now without waiting on
# that opt-in flow. Add more titles here as more courses get generated for a role --
# nothing else needs to change, a title just has to exist as a real training for it to
# show up.
HARDCODED_RECOMMENDATIONS: Dict[str, List[str]] = {
    "SDE1": ["Prompt Engineering"],
    "SDE2": ["Prompt Engineering"],
    "SDE3": ["Prompt Engineering"],
}


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

            # Which visible documents this role is actually required to pass.
            required_titles = {
                r["doc_title"]
                for r in _role_requirements(cur, role, identity.company_id)
            }

            # TrainingModuleRoles is authoritative for new multi-role courses.
            # Migration 030 backfills legacy modules.
            cur.execute(
                """SELECT COALESCE(q.source_doc_title, q.topic) AS doc,
                          MAX(m.doc_id) AS doc_id, COUNT(*) AS question_count
                     FROM dbo.GeneratedQuestions q
                     JOIN dbo.TrainingModules m
                       ON m.company_id = q.company_id AND m.module_id = q.module_id
                    WHERE q.review_status = 'Approved'
                      AND q.company_id = ?
                      AND m.status = 'ready'
                      AND EXISTS (
                          SELECT 1 FROM dbo.TrainingModuleRoles audience
                           WHERE audience.company_id = m.company_id
                             AND audience.module_id = m.module_id
                             AND audience.role_code IN ('ALL', ?))
                    GROUP BY COALESCE(q.source_doc_title, q.topic)
                    ORDER BY doc""",
                identity.company_id, role,
            )
            docs = _rows(cur)
            if not docs:
                return _json({"trainings": []})

            visible = {d["doc"] for d in docs}

            cur.execute(
                """SELECT m.doc_title, m.heading AS topic, m.source_order
                     FROM dbo.TrainingModules m
                    WHERE m.company_id = ? AND m.status = 'ready'
                      AND EXISTS (
                          SELECT 1 FROM dbo.TrainingModuleRoles audience
                           WHERE audience.company_id = m.company_id
                             AND audience.module_id = m.module_id
                             AND audience.role_code IN ('ALL', ?))
                    ORDER BY m.doc_title, m.source_order""",
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
                """SELECT DISTINCT doc_title, topic FROM dbo.TrainingModules
                    WHERE company_id = ? AND status = 'ready'""", identity.company_id)
            topic_to_doc = {r["topic"]: r["doc_title"] for r in _rows(cur)}

            cur.execute(
                """SELECT DISTINCT training_doc_id
                     FROM dbo.GeneratedQuizAttempts
                    WHERE company_id = ? AND learner_id = ?
                      AND training_doc_id IS NOT NULL""",
                identity.company_id, learner,
            )
            started_docs = {row["training_doc_id"] for row in _rows(cur)}

            cur.execute(
                """SELECT doc_title, MAX(expires_at) AS expires_at
                     FROM dbo.Certificates
                    WHERE company_id = ? AND employee_id = ? AND status = 'Active'
                    GROUP BY doc_title""",
                identity.company_id, identity.employee_id,
            )
            certificates_by_doc = {row["doc_title"]: row for row in _rows(cur)}

            # Reuses the same helper /qscore already reads its denominator from, so
            # "required" here can never drift from what actually counts toward
            # compliance -- one definition of required, not two that happen to agree
            # today and quietly diverge later.
            required_docs = {r["doc_title"] for r in _role_requirements(cur, role, identity.company_id)}

            cur.execute(
                "SELECT doc_title FROM dbo.EmployeeSkillInterest WHERE employee_id = ? AND company_id = ?",
                identity.employee_id, identity.company_id,
            )
            interested_docs = {r["doc_title"] for r in _rows(cur)}
            interested_docs |= set(HARDCODED_RECOMMENDATIONS.get(role, []))

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
            # Certification completion is intentionally separate from topic accuracy.
            # Diagnostic and module responses contribute mastery evidence, but neither
            # is allowed to mark the training complete or issue a certificate.
            certificate = certificates_by_doc.get(doc)
            if certificate:
                status = "completed"
            elif d.get("doc_id") in started_docs:
                status = "in-progress"
            else:
                status = "not-started"
            out.append({
                "id": doc,
                "title": doc,
                "status": status,
                "mastery": int(accuracy),
                "answered": answered,
                "questionCount": d["question_count"],
                "modules": modules.get(doc, []),
                "compliant": bool(certificate),
                "expiresAt": certificate.get("expires_at") if certificate else None,
                "required": doc in required_docs,
                # A required training is never ALSO "recommended" -- it's not optional
                # enrichment for that person, it's already owed. Interest expressed in
                # something that later became required (a manager made it mandatory
                # after the fact) should read as Required, not both.
                "recommended": doc in interested_docs and doc not in required_docs,
            })
        return _json({"trainings": out})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


# How long a "no new options" answer is honored before asking again. Not one-time: the
# bank grows as managers upload more documents, and something uploaded last month is
# worth surfacing to someone who declined before it existed. Not constant either --
# re-asking every session about the same unchanged list is nagging, not recommending.
# 21 days is a starting guess, easy to change; nothing else depends on this exact number.
SKILL_PROMPT_COOLDOWN_DAYS = 21


@app.route(route="skills/options", methods=["GET"])
def skill_options(req: func.HttpRequest) -> func.HttpResponse:
    """
    Whether to show the "anything you'd like to learn" popup, and what it can offer.

    Deliberately offers ONLY trainings that already exist and are already visible to
    this employee's role -- same role_scope filter list_trainings uses. Nothing here
    can recommend content the employee couldn't otherwise see, and nothing here can
    trigger new generation: this is a pointer into the existing bank, not a request for
    more of it. A document already required for their role, OR already accepted by this
    employee in an earlier round, is excluded -- the first because owing it isn't a
    choice, the second because it's already sitting under Recommended and re-offering an
    already-accepted pick is confusing, not helpful.

    Recurring, not one-time: skills_prompted_at is "last asked", not "ever asked". This
    re-prompts after SKILL_PROMPT_COOLDOWN_DAYS, but ONLY if there is at least one option
    left to offer -- a declined or fully-picked list does not nag on a timer with nothing
    new to say. A newly-uploaded document becoming visible is what actually brings the
    popup back, the cooldown just rate-limits how often that check happens.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    role = (identity.role_code or "ALL").upper()

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                "SELECT skills_prompted_at FROM dbo.Employees WHERE id = ? AND company_id = ?",
                identity.employee_id, identity.company_id,
            )
            row = cur.fetchone()
            last_prompted = row.skills_prompted_at if row else None
            cooldown_elapsed = (
                last_prompted is None
                or (datetime.now(timezone.utc) - last_prompted.replace(tzinfo=timezone.utc))
                   >= timedelta(days=SKILL_PROMPT_COOLDOWN_DAYS)
            )

            cur.execute(
                """SELECT DISTINCT source_doc_title AS doc_title
                     FROM dbo.GeneratedQuestions q
                     LEFT JOIN dbo.SourceChunks c ON c.chunk_id = q.source_chunk_id
                    WHERE q.review_status = 'Approved'
                      AND q.company_id = ?
                      AND COALESCE(c.role_scope, 'ALL') IN ('ALL', ?)
                      AND source_doc_title IS NOT NULL
                    ORDER BY source_doc_title""",
                identity.company_id, role,
            )
            visible_docs = [r["doc_title"] for r in _rows(cur)]

            required_docs = {r["doc_title"] for r in _role_requirements(cur, role, identity.company_id)}

            cur.execute(
                "SELECT doc_title FROM dbo.EmployeeSkillInterest WHERE employee_id = ? AND company_id = ?",
                identity.employee_id, identity.company_id,
            )
            already_picked = {r["doc_title"] for r in _rows(cur)}

        options = [d for d in visible_docs if d not in required_docs and d not in already_picked]
        show_prompt = cooldown_elapsed and len(options) > 0
        return _json({"prompted": not show_prompt, "options": options if show_prompt else []})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="skills/interest", methods=["POST"])
def set_skill_interest(req: func.HttpRequest) -> func.HttpResponse:
    """
    Records the popup's answer -- a chosen list of existing trainings, or an empty one
    if the employee closed it without picking anything. Either way this is a ONE-TIME
    answer: skills_prompted_at is set regardless of whether anything was picked, so the
    popup never asks again. There is deliberately no "ask me later" -- respecting a
    dismissal means respecting it, not nagging again next session.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    skills = body.get("skills")
    if not isinstance(skills, list):
        return _error(400, "Bad request", "skills must be a list (empty if none chosen)")
    chosen = sorted({str(s).strip() for s in skills if str(s).strip()})

    try:
        with _conn() as c:
            cur = c.cursor()
            role = (identity.role_code or "ALL").upper()
            # Only ever record interest in something this employee could actually be
            # shown -- a title submitted from a stale/tampered client request that
            # isn't in their own visible+non-required set is silently dropped rather
            # than trusted, same posture as the role-tagging checks elsewhere in this
            # file that never take a client's word for what it's allowed to touch.
            cur.execute(
                """SELECT DISTINCT source_doc_title AS doc_title
                     FROM dbo.GeneratedQuestions q
                     LEFT JOIN dbo.SourceChunks c ON c.chunk_id = q.source_chunk_id
                    WHERE q.review_status = 'Approved'
                      AND q.company_id = ?
                      AND COALESCE(c.role_scope, 'ALL') IN ('ALL', ?)
                      AND source_doc_title IS NOT NULL""",
                identity.company_id, role,
            )
            allowed = {r["doc_title"] for r in _rows(cur)}
            required_docs = {r["doc_title"] for r in _role_requirements(cur, role, identity.company_id)}
            valid = [s for s in chosen if s in allowed and s not in required_docs]

            for doc_title in valid:
                cur.execute(
                    "IF NOT EXISTS (SELECT 1 FROM dbo.EmployeeSkillInterest "
                    "               WHERE employee_id = ? AND doc_title = ?) "
                    "INSERT INTO dbo.EmployeeSkillInterest (employee_id, company_id, doc_title) "
                    "VALUES (?, ?, ?)",
                    identity.employee_id, doc_title,
                    identity.employee_id, identity.company_id, doc_title,
                )

            cur.execute(
                "UPDATE dbo.Employees SET skills_prompted_at = SYSUTCDATETIME() "
                "WHERE id = ? AND company_id = ?",
                identity.employee_id, identity.company_id,
            )
            c.commit()
        return _json({"recorded": valid})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:300]))


def _sync_training_modules(cur, company_id: int, training: str) -> None:
    """Mirror source topics into stable module rows without rewriting source content."""
    from shared.pathway import stable_module_id

    cur.execute(
        "SELECT COUNT(*) AS n FROM dbo.TrainingModules WHERE company_id = ? "
        "AND doc_title = ? AND generation_version = 'instructional-v1' "
        "AND status <> 'retired'",
        company_id, training,
    )
    if int(_rows(cur)[0]["n"] or 0):
        return

    cur.execute(
        """SELECT doc_id, doc_title, topic, MIN(section) AS heading,
                  MIN(page_start) AS source_order, MIN(role_scope) AS role_scope
             FROM dbo.SourceChunks
            WHERE company_id = ? AND doc_title = ?
            GROUP BY doc_id, doc_title, topic""",
        company_id, training,
    )
    for source in _rows(cur):
        module_id = stable_module_id(company_id, source["doc_id"], source["topic"])
        cur.execute(
            """MERGE dbo.TrainingModules AS target
               USING (SELECT ? AS module_id) AS source
                  ON target.module_id = source.module_id
               WHEN MATCHED THEN UPDATE SET
                    doc_title = ?, heading = ?, source_order = ?, role_scope = ?
               WHEN NOT MATCHED THEN INSERT
                    (module_id, company_id, doc_id, doc_title, topic, heading,
                     source_order, role_scope)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
            module_id,
            source["doc_title"], source["heading"], source["source_order"],
            source["role_scope"] or "ALL",
            module_id, company_id, source["doc_id"], source["doc_title"],
            source["topic"], source["heading"], source["source_order"],
            source["role_scope"] or "ALL",
        )
        cur.execute(
            """IF NOT EXISTS (SELECT 1 FROM dbo.TrainingModuleRoles
                               WHERE company_id = ? AND module_id = ?)
                   INSERT INTO dbo.TrainingModuleRoles (company_id, module_id, role_code)
                   VALUES (?, ?, ?)""",
            company_id, module_id, company_id, module_id,
            source["role_scope"] or "ALL",
        )


def _pathway_state(cur, identity, learner: str, training: str) -> Optional[Dict[str, Any]]:
    """Load the ordered modules and derive locks from completed checkpoint evidence."""
    from shared.pathway import DIFFICULTIES, final_assessment_size

    _sync_training_modules(cur, identity.company_id, training)
    role = (identity.role_code or "ALL").upper()
    cur.execute(
        """SELECT m.module_id, m.doc_id, m.doc_title, m.topic, m.heading,
                  m.source_order, m.summary, m.lesson_word_count,
                  m.learning_point_count, m.active_generation_id
             FROM dbo.TrainingModules m
            WHERE m.company_id = ? AND m.doc_title = ? AND m.status = 'ready'
              AND (
                  EXISTS (SELECT 1 FROM dbo.TrainingModuleRoles audience
                           WHERE audience.company_id = m.company_id
                             AND audience.module_id = m.module_id
                             AND audience.role_code IN ('ALL', ?))
                  OR (NOT EXISTS (SELECT 1 FROM dbo.TrainingModuleRoles audience
                                  WHERE audience.company_id = m.company_id
                                    AND audience.module_id = m.module_id)
                      AND m.role_scope IN ('ALL', ?))
              )
            ORDER BY m.source_order, m.module_id""",
        identity.company_id, training, role, role,
    )
    modules = _rows(cur)
    if not modules:
        return None

    doc_id = modules[0]["doc_id"]
    cur.execute(
        """SELECT diagnostic_attempt_id, diagnostic_completed_at,
                  diagnostic_scores_json, pathway_json
             FROM dbo.EmployeeTrainingProgress
            WHERE company_id = ? AND learner_id = ? AND doc_id = ?""",
        identity.company_id, learner, doc_id,
    )
    progress_rows = _rows(cur)
    progress = progress_rows[0] if progress_rows else {}

    def decoded(name: str, fallback):
        try:
            return json.loads(progress.get(name) or "")
        except (TypeError, ValueError):
            return fallback

    diagnostic_done = bool(progress.get("diagnostic_completed_at"))
    scores = decoded("diagnostic_scores_json", {})
    saved_order = decoded("pathway_json", []) if diagnostic_done else []
    by_id = {module["module_id"]: module for module in modules}
    ordered_ids = [module_id for module_id in saved_order if module_id in by_id]
    ordered_ids.extend(module["module_id"] for module in modules if module["module_id"] not in ordered_ids)
    modules = [by_id[module_id] for module_id in ordered_ids]

    cur.execute(
        """SELECT module_id, status, best_score, attempt_count,
                  weak_sections_json, completed_at
             FROM dbo.EmployeeModuleProgress
            WHERE company_id = ? AND learner_id = ?""",
        identity.company_id, learner,
    )
    module_progress = {row["module_id"]: row for row in _rows(cur)}

    cur.execute(
        """SELECT m.module_id, q.difficulty,
                  COUNT(DISTINCT q.question_id) AS n,
                  COUNT(DISTINCT CASE
                      WHEN q.question_type = 'MultipleChoice'
                      THEN q.question_id END) AS choice_n
             FROM dbo.TrainingModules m
             JOIN dbo.GeneratedQuestions q
               ON q.company_id = m.company_id AND q.module_id = m.module_id
            WHERE m.company_id = ? AND m.doc_id = ? AND q.review_status = 'Approved'
            GROUP BY m.module_id, q.difficulty""",
        identity.company_id, doc_id,
    )
    counts: Dict[str, Dict[str, int]] = {}
    choice_counts: Dict[str, Dict[str, int]] = {}
    for row in _rows(cur):
        counts.setdefault(row["module_id"], {})[row["difficulty"]] = int(row["n"] or 0)
        choice_counts.setdefault(row["module_id"], {})[row["difficulty"]] = int(
            row["choice_n"] or 0)

    cur.execute(
        """SELECT m.module_id, COUNT(p.page_id) AS page_count,
                  COUNT(done.page_id) AS completed_pages
             FROM dbo.TrainingModules m
             LEFT JOIN dbo.LessonPages p
               ON p.company_id = m.company_id AND p.module_id = m.module_id
              AND p.generation_id = m.active_generation_id
             LEFT JOIN dbo.EmployeeLessonPageProgress done
               ON done.company_id = p.company_id AND done.page_id = p.page_id
              AND done.learner_id = ?
            WHERE m.company_id = ? AND m.doc_id = ?
            GROUP BY m.module_id""",
        learner, identity.company_id, doc_id,
    )
    page_progress = {row["module_id"]: row for row in _rows(cur)}

    first_incomplete = next(
        (module["module_id"] for module in modules
         if module_progress.get(module["module_id"], {}).get("status") != "passed"),
        None,
    )
    output_modules = []
    for pathway_order, module in enumerate(modules, 1):
        stored = module_progress.get(module["module_id"], {})
        if stored.get("status") == "passed":
            status = "passed"
        elif not diagnostic_done or module["module_id"] != first_incomplete:
            status = "locked"
        else:
            status = stored.get("status") or "available"
        try:
            weak_sections = json.loads(stored.get("weak_sections_json") or "[]")
        except (TypeError, ValueError):
            weak_sections = []
        difficulty_counts = counts.get(module["module_id"], {})
        choice_difficulty_counts = choice_counts.get(module["module_id"], {})
        pages = page_progress.get(module["module_id"], {})
        page_count = int(pages.get("page_count") or 0)
        completed_pages = int(pages.get("completed_pages") or 0)
        output_modules.append({
            "moduleId": module["module_id"],
            "title": module["heading"] or module["topic"],
            "topic": module["topic"],
            "sourceOrder": module["source_order"],
            "pathwayOrder": pathway_order,
            "status": status,
            "bestScore": float(stored.get("best_score") or 0),
            "attemptCount": int(stored.get("attempt_count") or 0),
            "weakSections": weak_sections,
            "questionCount": sum(difficulty_counts.values()),
            "difficultyCounts": {
                difficulty: difficulty_counts.get(difficulty, 0)
                for difficulty in DIFFICULTIES
            },
            "choiceDifficultyCounts": {
                difficulty: choice_difficulty_counts.get(difficulty, 0)
                for difficulty in DIFFICULTIES
            },
            "diagnosticScore": scores.get(module["module_id"]),
            "summary": module.get("summary") or "",
            "lessonWordCount": int(module.get("lesson_word_count") or 0),
            "learningPointCount": int(module.get("learning_point_count") or 0),
            "pageCount": page_count,
            "completedPages": completed_pages,
            "lessonCompleted": page_count == 0 or completed_pages >= page_count,
        })

    all_passed = diagnostic_done and all(module["status"] == "passed" for module in output_modules)
    # Was requiring every module to have all three of Easy/Medium/Hard before the
    # diagnostic could start at all -- one module short a single tier blocked every
    # other module's diagnostic too. Now only requires each module to have SOME
    # choice question, at any difficulty; diagnostic_questions picks whatever tiers
    # actually exist per module (see pathway.py).
    diagnostic_ready = bool(output_modules) and all(
        any(module["choiceDifficultyCounts"].get(difficulty, 0) > 0
            for difficulty in DIFFICULTIES)
        for module in output_modules
    )
    diagnostic_question_count = sum(
        sum(1 for difficulty in DIFFICULTIES
            if module["choiceDifficultyCounts"].get(difficulty, 0) > 0)
        for module in output_modules
    )
    return {
        "id": doc_id,
        "title": training,
        "diagnostic": {
            "completed": diagnostic_done,
            "ready": diagnostic_ready,
            "questionCount": diagnostic_question_count,
            "attemptId": progress.get("diagnostic_attempt_id"),
        },
        "modules": output_modules,
        "finalAssessment": {
            "locked": not all_passed,
            "questionCount": final_assessment_size(len(output_modules)),
            "passingScore": 80,
        },
    }


@app.route(route="pathway", methods=["GET"])
def get_pathway(req: func.HttpRequest) -> func.HttpResponse:
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    training = (req.params.get("training") or "").strip()
    if not training:
        return _error(400, "Bad request", "training is required")
    try:
        with _conn() as c:
            cur = c.cursor()
            state = _pathway_state(cur, identity, _learner_key(identity), training)
            c.commit()
        if state is None:
            return _error(404, "No training pathway", "No readable modules were found.")
        return _json({"training": state})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:240]))


def _pathway_question_pool(cur, identity, doc_id: str, topic: str = "") -> List[Dict[str, Any]]:
    role = (identity.role_code or "ALL").upper()
    sql = """SELECT DISTINCT q.question_id, q.topic, q.question_type, q.difficulty,
                    q.prompt, q.points, q.provenance_class, q.times_served,
                    q.times_correct, q.module_id
               FROM dbo.GeneratedQuestions q
               JOIN dbo.TrainingModules m
                 ON m.company_id = q.company_id AND m.module_id = q.module_id
              WHERE q.review_status = 'Approved' AND q.company_id = ?
                AND m.doc_id = ? AND m.status = 'ready'
                AND EXISTS (SELECT 1 FROM dbo.TrainingModuleRoles audience
                             WHERE audience.company_id = m.company_id
                               AND audience.module_id = m.module_id
                               AND audience.role_code IN ('ALL', ?))"""
    params: List[Any] = [identity.company_id, doc_id, role]
    if topic:
        sql += " AND m.topic = ?"
        params.append(topic)
    cur.execute(sql, *params)
    return _rows(cur)


def _pathway_question_payload(cur, identity, question_id: str) -> Optional[Dict[str, Any]]:
    cur.execute(
        """SELECT DISTINCT question_id, topic, question_type, difficulty, prompt,
                  points, provenance_class
             FROM dbo.vw_ServableQuestions
            WHERE question_id = ? AND company_id = ?""",
        question_id, identity.company_id,
    )
    rows = _rows(cur)
    if not rows:
        return None
    question = rows[0]
    cur.execute(
        """SELECT option_id, option_text, sort_order
             FROM dbo.vw_ServableQuestions
            WHERE question_id = ? AND company_id = ? AND option_id IS NOT NULL
            ORDER BY sort_order""",
        question_id, identity.company_id,
    )
    return {
        "questionId": question_id,
        "type": question["question_type"],
        "topic": question["topic"],
        "difficulty": question["difficulty"],
        "points": question["points"],
        "provenanceClass": question["provenance_class"],
        "prompt": question["prompt"],
        "options": [
            {"optionId": option["option_id"], "text": option["option_text"]}
            for option in _rows(cur)
        ],
    }


@app.route(route="pathway/start", methods=["POST"])
def start_pathway_assessment(req: func.HttpRequest) -> func.HttpResponse:
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    learner = _learner_key(identity)
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")
    training = str(body.get("training") or "").strip()
    kind = str(body.get("kind") or "").strip().lower()
    module_id = str(body.get("moduleId") or "").strip()
    if not training or kind not in ("diagnostic", "module", "final"):
        return _error(400, "Bad request", "training and a valid kind are required")

    from shared import pathway

    try:
        with _conn() as c:
            cur = c.cursor()
            state = _pathway_state(cur, identity, learner, training)
            if state is None:
                return _error(404, "No training pathway", training)
            modules = [{
                "module_id": module["moduleId"], "topic": module["topic"],
                "source_order": module["sourceOrder"],
            } for module in state["modules"]]
            doc_id = state["id"]
            pool = _pathway_question_pool(cur, identity, doc_id)
            attempt_id = "att_" + uuid.uuid4().hex[:12]
            blueprint: Dict[str, Any] = {}

            if kind == "diagnostic":
                if state["diagnostic"]["completed"]:
                    return _error(409, "Diagnostic already completed",
                                  "Continue with the first available module.")
                selected, missing = pathway.diagnostic_questions(pool, modules, learner + doc_id)
                if missing:
                    labels = ["{} {}".format(mid, difficulty) for mid, difficulty in missing[:6]]
                    return _error(
                        409, "Diagnostic question bank is incomplete",
                        "Generate a MultipleChoice Easy, Medium and Hard question for every module. Missing: "
                        + ", ".join(labels),
                    )
                target, pass_mark = len(selected), None
            elif kind == "module":
                if not state["diagnostic"]["completed"]:
                    return _error(409, "Diagnostic required", "Complete the diagnostic first.")
                module = next((item for item in state["modules"] if item["moduleId"] == module_id), None)
                if module is None:
                    return _error(404, "Unknown module", module_id)
                if module["status"] not in ("available", "needs-review", "in-progress"):
                    return _error(409, "Module is locked", "Complete the previous module first.")
                if module.get("pageCount", 0) and not module.get("lessonCompleted"):
                    return _error(
                        409, "Finish the lesson first",
                        "Complete every lesson page before starting the checkpoint.",
                    )
                module_pool = [
                    q for q in pool
                    if q.get("module_id") == module_id
                    and q["question_type"] in pathway.FAST_MODULE_TYPES
                ]
                # Was a hard `< 10` block on starting the checkpoint at all. 10 was an
                # arbitrary target, not a real minimum -- a module that generated fewer
                # (a realistic outcome now that the publish gate only requires total>=1)
                # could have a real, approved question bank and still never be
                # startable. Scale the target down to what actually exists instead of
                # blocking; only an empty bank is a real problem.
                if not module_pool:
                    return _error(409, "No module questions available", module["title"])
                diagnostic = module.get("diagnosticScore") or {}
                wanted = pathway.initial_module_difficulty(
                    int(diagnostic.get("correct") or 0), int(diagnostic.get("possible") or 3))
                cur.execute(
                    """SELECT DISTINCT aq.question_id
                         FROM dbo.GeneratedQuizAttemptQuestions aq
                         JOIN dbo.GeneratedQuizAttempts a ON a.attempt_id = aq.attempt_id
                        WHERE a.company_id = ? AND a.learner_id = ? AND a.module_id = ?""",
                    identity.company_id, learner, module_id,
                )
                historical = [row["question_id"] for row in _rows(cur)]
                first = pathway.choose_adaptive_question(
                    module_pool, wanted, [], historical, [], False, attempt_id)
                if first is None:
                    return _error(409, "No module questions available", module["title"])
                selected = [{**first, "purpose": "adaptive"}]
                target, pass_mark = min(10, len(module_pool)), 90
                blueprint = {"initialDifficulty": wanted}
            else:
                if state["finalAssessment"]["locked"]:
                    return _error(409, "Final assessment is locked",
                                  "Pass every module checkpoint first.")
                selected, blueprint = pathway.final_questions(pool, modules, learner + attempt_id)
                # Was a hard block whenever the pool came up short of the blueprint's
                # target (25-35, from final_assessment_size) -- final_questions() already
                # degrades gracefully when a topic/difficulty slot has no candidates (see
                # its own shortages fallback), so len(selected) coming in under target is
                # an expected outcome for a thinner bank, not a broken one. Score against
                # what was actually selected instead of refusing to start at all.
                if not selected:
                    return _error(409, "Final question bank is incomplete",
                                  "No final assessment questions are available yet.")
                target, pass_mark = len(selected), 80

            cur.execute(
                """INSERT INTO dbo.GeneratedQuizAttempts
                       (attempt_id, learner_id, started_at, company_id, training_doc_id,
                        training_title, module_id, attempt_kind, question_target,
                        passing_score, current_difficulty, blueprint_json)
                   VALUES (?, ?, SYSUTCDATETIME(), ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                attempt_id, learner, identity.company_id, doc_id, training,
                module_id or None, kind, target, pass_mark,
                blueprint.get("initialDifficulty"), json.dumps(blueprint),
            )
            cur.executemany(
                """INSERT INTO dbo.GeneratedQuizAttemptQuestions
                       (attempt_id, question_id, sort_order, purpose)
                   VALUES (?, ?, ?, ?)""",
                [
                    (attempt_id, question["question_id"], index, question.get("purpose"))
                    for index, question in enumerate(selected, 1)
                ],
            )
            if kind == "module":
                cur.execute(
                    """MERGE dbo.EmployeeModuleProgress AS target
                       USING (SELECT ? AS company_id, ? AS learner_id, ? AS module_id) AS source
                          ON target.company_id = source.company_id
                         AND target.learner_id = source.learner_id
                         AND target.module_id = source.module_id
                       WHEN MATCHED THEN UPDATE SET status = 'in-progress', updated_at = SYSUTCDATETIME()
                       WHEN NOT MATCHED THEN INSERT
                           (company_id, learner_id, module_id, status)
                           VALUES (source.company_id, source.learner_id, source.module_id, 'in-progress');""",
                    identity.company_id, learner, module_id,
                )
            current = _pathway_question_payload(cur, identity, selected[0]["question_id"])
            c.commit()

        return _json({
            "attemptId": attempt_id,
            "kind": kind,
            "training": training,
            "moduleId": module_id or None,
            "questionTarget": target,
            "passingScore": pass_mark,
            "answeredCount": 0,
            "currentQuestion": current,
            "blueprint": blueprint if kind == "final" else None,
        }, 201)
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:240]))


def _grade_pathway_question(
    cur, question_id: str, selected: List[str], text_answer: str,
    fallback_active: bool = False,
):
    cur.execute(
        """SELECT question_type, explanation, source_doc_title, source_page,
                  source_quote, source_url, provenance_class, difficulty, prompt, topic,
                  rubric_json, fallback_json, grading_version
             FROM dbo.GeneratedQuestions WHERE question_id = ?""",
        question_id,
    )
    rows = _rows(cur)
    if not rows:
        return None
    question = rows[0]
    cur.execute(
        "SELECT option_id, option_text, is_correct FROM dbo.GeneratedOptions WHERE question_id = ?",
        question_id,
    )
    options = _rows(cur)
    cur.execute(
        "SELECT accepted_answer FROM dbo.GeneratedAnswerKeys WHERE question_id = ?",
        question_id,
    )
    accepted = [row["accepted_answer"] for row in _rows(cur)]
    if fallback_active:
        try:
            fallback = json.loads(question["fallback_json"] or "{}")
            fallback_options = fallback.get("options") or []
        except (TypeError, ValueError):
            fallback_options = []
        key = {
            option["optionId"] for option in fallback_options if option.get("isCorrect")
        }
        correct = bool(key) and set(selected) == key
        display_options = [{
            "option_id": option["optionId"],
            "option_text": option["text"],
            "is_correct": bool(option.get("isCorrect")),
        } for option in fallback_options]
        return question, display_options, [], correct, None

    key = {option["option_id"] for option in options if option["is_correct"]}
    qtype = (question["question_type"] or "").lower()
    if qtype in ("fill_in_blank", "fillintheblank", "fillinblank"):
        normalized = " ".join(text_answer.lower().split())
        correct = bool(normalized) and any(
            normalized == " ".join(answer.lower().split()) for answer in accepted)
        guard = None
    elif qtype in ("shortanswer", "promptresponse", "pythoncode"):
        from shared.guarded_grading import grade_answer

        guard = grade_answer(
            question["prompt"], question["rubric_json"] or "", text_answer,
            question["question_type"],
        )
        correct = True if guard.verdict == "correct" else (
            False if guard.verdict == "incorrect" else None)
    else:
        correct = bool(key) and set(selected) == key
        guard = None
    return question, options, accepted, correct, guard


@app.route(route="pathway/answer", methods=["POST"])
def answer_pathway_question(req: func.HttpRequest) -> func.HttpResponse:
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    learner = _learner_key(identity)
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")
    attempt_id = str(body.get("attemptId") or "").strip()
    question_id = str(body.get("questionId") or "").strip()
    selected = body.get("selectedOptionIds") or []
    text_answer = str(body.get("textAnswer") or "").strip()
    if not attempt_id or not question_id:
        return _error(400, "Bad request", "attemptId and questionId are required")

    from shared import pathway

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT attempt_kind, training_doc_id, module_id, question_target,
                          current_difficulty, submitted_at
                     FROM dbo.GeneratedQuizAttempts
                    WHERE attempt_id = ? AND learner_id = ? AND company_id = ?""",
                attempt_id, learner, identity.company_id,
            )
            attempts = _rows(cur)
            if not attempts:
                return _error(404, "Unknown attempt", "Start an assessment first.")
            attempt = attempts[0]
            if attempt["submitted_at"] is not None:
                return _error(409, "Already completed", attempt_id)

            cur.execute(
                """SELECT sort_order, answered_at, fallback_active
                     FROM dbo.GeneratedQuizAttemptQuestions
                    WHERE attempt_id = ? AND question_id = ?""",
                attempt_id, question_id,
            )
            served = _rows(cur)
            if not served:
                return _error(403, "Not part of this attempt", question_id)
            if served[0]["answered_at"] is not None:
                return _error(409, "Question already answered", question_id)

            fallback_active = bool(served[0].get("fallback_active"))
            graded = _grade_pathway_question(
                cur, question_id, selected, text_answer, fallback_active)
            if graded is None:
                return _error(404, "Unknown question", question_id)
            question, options, accepted, correct, guard = graded

            if guard is not None:
                fallback_used = guard.verdict in ("uncertain", "system_error")
                cur.execute(
                    """INSERT INTO dbo.GeneratedGradingEvents
                           (grading_event_id, company_id, attempt_id, question_id,
                            verdict, rubric_score, confidence, reason, criteria_json,
                            grader_model, grading_version, fallback_used)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    "grade_" + uuid.uuid4().hex[:12], identity.company_id,
                    attempt_id, question_id, guard.verdict, guard.score,
                    guard.confidence, guard.reason[:500], json.dumps(guard.criteria),
                    guard.model, guard.grading_version, 1 if fallback_used else 0,
                )
                if fallback_used:
                    try:
                        fallback = json.loads(question["fallback_json"] or "{}")
                    except (TypeError, ValueError):
                        fallback = {}
                    fallback_options = fallback.get("options") or []
                    if not fallback.get("prompt") or not fallback_options:
                        return _error(
                            409, "Fallback unavailable",
                            "This question cannot be graded safely. Start a new attempt.",
                        )
                    cur.execute(
                        """UPDATE dbo.GeneratedQuizAttemptQuestions
                              SET text_answer = ?, fallback_active = 1
                            WHERE attempt_id = ? AND question_id = ?""",
                        text_answer, attempt_id, question_id,
                    )
                    cur.execute(
                        """SELECT COUNT(*) AS n FROM dbo.GeneratedQuizAttemptQuestions
                            WHERE attempt_id = ? AND answered_at IS NOT NULL""",
                        attempt_id,
                    )
                    answered_before_fallback = int(_rows(cur)[0]["n"])
                    c.commit()
                    return _json({
                        "questionId": question_id,
                        "requiresFallback": True,
                        "answeredCount": answered_before_fallback,
                        "questionTarget": int(attempt["question_target"] or 0),
                        "fallbackQuestion": {
                            "questionId": question_id,
                            "type": "MultipleChoice",
                            "topic": question.get("topic") or "Clarification",
                            "difficulty": fallback.get("difficulty") or question["difficulty"],
                            "prompt": fallback["prompt"],
                            "options": [{
                                "optionId": option["optionId"], "text": option["text"]
                            } for option in fallback_options],
                            "isFallback": True,
                        },
                    })

            cur.execute(
                """UPDATE dbo.GeneratedQuizAttemptQuestions
                      SET selected = ?,
                          text_answer = CASE WHEN ? = 1 THEN text_answer ELSE ? END,
                          is_correct = ?,
                          answered_at = SYSUTCDATETIME()
                    WHERE attempt_id = ? AND question_id = ?""",
                ",".join(selected), 1 if fallback_active else 0, text_answer,
                1 if correct else 0,
                attempt_id, question_id,
            )
            next_question = None
            next_difficulty = None

            cur.execute(
                """SELECT question_id, sort_order
                     FROM dbo.GeneratedQuizAttemptQuestions
                    WHERE attempt_id = ? AND answered_at IS NULL
                    ORDER BY sort_order""",
                attempt_id,
            )
            pending = _rows(cur)

            if attempt["attempt_kind"] == "module" and not pending:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM dbo.GeneratedQuizAttemptQuestions WHERE attempt_id = ?",
                    attempt_id,
                )
                count = int(_rows(cur)[0]["n"])
                if count < int(attempt["question_target"] or 10):
                    next_difficulty = pathway.next_difficulty(
                        attempt["current_difficulty"] or question["difficulty"], correct)
                    cur.execute(
                        "SELECT topic FROM dbo.TrainingModules WHERE module_id = ? AND company_id = ?",
                        attempt["module_id"], identity.company_id,
                    )
                    module_rows = _rows(cur)
                    if not module_rows:
                        return _error(409, "Module is unavailable", attempt["module_id"])
                    pool = [
                        q for q in _pathway_question_pool(
                            cur, identity, attempt["training_doc_id"], module_rows[0]["topic"])
                        if q["question_type"] in pathway.FAST_MODULE_TYPES
                    ]
                    cur.execute(
                        "SELECT question_id FROM dbo.GeneratedQuizAttemptQuestions WHERE attempt_id = ?",
                        attempt_id,
                    )
                    current_ids = [row["question_id"] for row in _rows(cur)]
                    cur.execute(
                        """SELECT DISTINCT aq.question_id
                             FROM dbo.GeneratedQuizAttemptQuestions aq
                             JOIN dbo.GeneratedQuizAttempts a ON a.attempt_id = aq.attempt_id
                            WHERE a.company_id = ? AND a.learner_id = ? AND a.module_id = ?
                              AND a.attempt_id <> ?""",
                        identity.company_id, learner, attempt["module_id"], attempt_id,
                    )
                    historical = [row["question_id"] for row in _rows(cur)]
                    cur.execute(
                        """SELECT DISTINCT aq.question_id
                             FROM dbo.GeneratedQuizAttemptQuestions aq
                             JOIN dbo.GeneratedQuizAttempts a ON a.attempt_id = aq.attempt_id
                            WHERE a.company_id = ? AND a.learner_id = ? AND a.module_id = ?
                              AND aq.is_correct = 0 AND a.attempt_id <> ?""",
                        identity.company_id, learner, attempt["module_id"], attempt_id,
                    )
                    review_ids = [row["question_id"] for row in _rows(cur)]
                    # Positions 3, 6 and 9 are review slots on a retake: seven fresh
                    # selections plus three focused checks of prior mistakes.
                    position = count + 1
                    pick = pathway.choose_adaptive_question(
                        pool, next_difficulty, current_ids, historical, review_ids,
                        bool(historical) and position in (3, 6, 9), attempt_id + str(position),
                    )
                    if pick is None:
                        return _error(409, "Question bank exhausted",
                                      "This module needs more approved questions for an adaptive quiz.")
                    cur.execute(
                        """INSERT INTO dbo.GeneratedQuizAttemptQuestions
                               (attempt_id, question_id, sort_order, purpose)
                           VALUES (?, ?, ?, ?)""",
                        attempt_id, pick["question_id"], position,
                        "review" if position in (3, 6, 9) and pick["question_id"] in review_ids
                        else "adaptive",
                    )
                    cur.execute(
                        "UPDATE dbo.GeneratedQuizAttempts SET current_difficulty = ? WHERE attempt_id = ?",
                        next_difficulty, attempt_id,
                    )
                    pending = [{"question_id": pick["question_id"], "sort_order": position}]

            if pending:
                next_question = _pathway_question_payload(
                    cur, identity, pending[0]["question_id"])
            cur.execute(
                """SELECT COUNT(*) AS n FROM dbo.GeneratedQuizAttemptQuestions
                    WHERE attempt_id = ? AND answered_at IS NOT NULL""",
                attempt_id,
            )
            answered_count = int(_rows(cur)[0]["n"])
            c.commit()

        return _json({
            "questionId": question_id,
            "correct": correct,
            "correctOptionIds": [option["option_id"] for option in options if option["is_correct"]],
            "acceptedAnswers": accepted,
            "explanation": question["explanation"],
            "sourceTitle": question["source_doc_title"],
            "sourcePage": question["source_page"],
            "sourceQuote": question["source_quote"],
            "sourceUrl": question["source_url"],
            "provenance": question["provenance_class"],
            "answeredCount": answered_count,
            "questionTarget": int(attempt["question_target"] or 0),
            "nextDifficulty": next_difficulty,
            "nextQuestion": next_question,
            "readyToComplete": next_question is None,
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:240]))


@app.route(route="pathway/complete", methods=["POST"])
def complete_pathway_assessment(req: func.HttpRequest) -> func.HttpResponse:
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    learner = _learner_key(identity)
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")
    attempt_id = str(body.get("attemptId") or "").strip()
    if not attempt_id:
        return _error(400, "Bad request", "attemptId is required")

    from shared import pathway

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT attempt_kind, training_doc_id, training_title, module_id,
                          question_target, passing_score, submitted_at
                     FROM dbo.GeneratedQuizAttempts
                    WHERE attempt_id = ? AND learner_id = ? AND company_id = ?""",
                attempt_id, learner, identity.company_id,
            )
            attempt_rows = _rows(cur)
            if not attempt_rows:
                return _error(404, "Unknown attempt", attempt_id)
            attempt = attempt_rows[0]
            if attempt["submitted_at"] is not None:
                return _error(409, "Already completed", attempt_id)

            cur.execute(
                """SELECT aq.question_id, aq.selected, aq.text_answer, aq.is_correct,
                          aq.purpose, q.topic, q.prompt, q.points, q.difficulty,
                          q.explanation, q.source_doc_title, q.source_page,
                          q.source_quote, q.source_url, q.provenance_class,
                          COALESCE(p.title, c.section) AS section
                     FROM dbo.GeneratedQuizAttemptQuestions aq
                     JOIN dbo.GeneratedQuestions q ON q.question_id = aq.question_id
                     LEFT JOIN dbo.SourceChunks c ON c.chunk_id = q.source_chunk_id
                     LEFT JOIN dbo.LessonPages p ON p.page_id = q.lesson_page_id
                    WHERE aq.attempt_id = ? AND aq.answered_at IS NOT NULL
                    ORDER BY aq.sort_order""",
                attempt_id,
            )
            answered = _rows(cur)
            target = int(attempt["question_target"] or 0)
            if len(answered) < target:
                return _error(409, "Assessment is not finished",
                              "Answer all {} questions first.".format(target))

            awarded = sum(int(row["points"] or 0) for row in answered if row["is_correct"])
            possible = sum(int(row["points"] or 0) for row in answered)
            percent = round(100.0 * awarded / possible, 2) if possible else 0.0
            pass_mark = float(attempt["passing_score"] or 0)
            passed = True if attempt["attempt_kind"] == "diagnostic" else percent >= pass_mark

            for row in answered:
                cur.execute(
                    """INSERT INTO dbo.GeneratedQuizResponses
                           (response_id, attempt_id, learner_id, question_id, topic,
                            selected, text_answer, is_correct, points_awarded, company_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    "res_" + uuid.uuid4().hex[:12], attempt_id, learner,
                    row["question_id"], row["topic"], row["selected"] or "",
                    row["text_answer"] or "", 1 if row["is_correct"] else 0,
                    int(row["points"] or 0) if row["is_correct"] else 0,
                    identity.company_id,
                )
                cur.execute(
                    """UPDATE dbo.GeneratedQuestions
                          SET times_served = times_served + 1,
                              times_correct = times_correct + ?
                        WHERE question_id = ? AND company_id = ?""",
                    1 if row["is_correct"] else 0, row["question_id"], identity.company_id,
                )

            cur.execute(
                """UPDATE dbo.GeneratedQuizAttempts
                      SET submitted_at = SYSUTCDATETIME(), score_percent = ?,
                          points_awarded = ?, points_possible = ?, passed = ?
                    WHERE attempt_id = ?""",
                percent, awarded, possible, 1 if passed else 0, attempt_id,
            )

            weak_sections = sorted({
                row["section"] for row in answered
                if not row["is_correct"] and row.get("section")
            })
            certificate = None

            if attempt["attempt_kind"] == "diagnostic":
                cur.execute(
                    """SELECT module_id, topic, source_order
                         FROM dbo.TrainingModules
                        WHERE company_id = ? AND doc_id = ? AND status = 'ready'
                        ORDER BY source_order""",
                    identity.company_id, attempt["training_doc_id"],
                )
                modules = _rows(cur)
                by_topic = {module["topic"]: module["module_id"] for module in modules}
                scores: Dict[str, Dict[str, int]] = {
                    module["module_id"]: {"correct": 0, "possible": 0}
                    for module in modules
                }
                for row in answered:
                    mid = by_topic.get(row["topic"])
                    if mid:
                        scores[mid]["possible"] += 1
                        scores[mid]["correct"] += 1 if row["is_correct"] else 0
                order = pathway.diagnostic_pathway(modules, scores)
                cur.execute(
                    """MERGE dbo.EmployeeTrainingProgress AS target
                       USING (SELECT ? AS company_id, ? AS learner_id, ? AS doc_id) AS source
                          ON target.company_id = source.company_id
                         AND target.learner_id = source.learner_id
                         AND target.doc_id = source.doc_id
                       WHEN MATCHED THEN UPDATE SET
                            doc_title = ?, diagnostic_attempt_id = ?,
                            diagnostic_completed_at = SYSUTCDATETIME(),
                            diagnostic_scores_json = ?, pathway_json = ?,
                            updated_at = SYSUTCDATETIME()
                       WHEN NOT MATCHED THEN INSERT
                            (company_id, learner_id, doc_id, doc_title,
                             diagnostic_attempt_id, diagnostic_completed_at,
                             diagnostic_scores_json, pathway_json)
                            VALUES (source.company_id, source.learner_id, source.doc_id,
                                    ?, ?, SYSUTCDATETIME(), ?, ?);""",
                    identity.company_id, learner, attempt["training_doc_id"],
                    attempt["training_title"], attempt_id, json.dumps(scores), json.dumps(order),
                    attempt["training_title"], attempt_id, json.dumps(scores), json.dumps(order),
                )
            elif attempt["attempt_kind"] == "module":
                status = "passed" if passed else "needs-review"
                cur.execute(
                    """MERGE dbo.EmployeeModuleProgress AS target
                       USING (SELECT ? AS company_id, ? AS learner_id, ? AS module_id) AS source
                          ON target.company_id = source.company_id
                         AND target.learner_id = source.learner_id
                         AND target.module_id = source.module_id
                       WHEN MATCHED THEN UPDATE SET
                            status = ?, best_score = CASE WHEN best_score > ? THEN best_score ELSE ? END,
                            attempt_count = attempt_count + 1,
                            weak_sections_json = ?,
                            completed_at = CASE WHEN ? = 'passed' THEN SYSUTCDATETIME() ELSE completed_at END,
                            updated_at = SYSUTCDATETIME()
                       WHEN NOT MATCHED THEN INSERT
                            (company_id, learner_id, module_id, status, best_score,
                             attempt_count, weak_sections_json, completed_at)
                            VALUES (source.company_id, source.learner_id, source.module_id,
                                    ?, ?, 1, ?, CASE WHEN ? = 'passed' THEN SYSUTCDATETIME() END);""",
                    identity.company_id, learner, attempt["module_id"],
                    status, percent, percent, json.dumps(weak_sections), status,
                    status, percent, json.dumps(weak_sections), status,
                )
            elif attempt["attempt_kind"] == "final" and passed:
                cert_results = [{
                    "difficulty": row["difficulty"],
                    "isCorrect": bool(row["is_correct"]),
                    "topic": row["topic"],
                    "source": {"documentTitle": row["source_doc_title"]},
                } for row in answered]
                certificate = _issue_certificate(
                    cur, identity, attempt_id, cert_results,
                    _store_certificate_artifact)

            c.commit()

        results = [{
            "questionId": row["question_id"],
            "topic": row["topic"],
            "prompt": row["prompt"],
            "difficulty": row["difficulty"],
            "correct": bool(row["is_correct"]),
            "explanation": row["explanation"],
            "sourceTitle": row["source_doc_title"],
            "sourcePage": row["source_page"],
            "sourceQuote": row["source_quote"],
            "sourceUrl": row["source_url"],
            "provenance": row["provenance_class"],
        } for row in answered]
        return _json({
            "attemptId": attempt_id,
            "kind": attempt["attempt_kind"],
            "scorePercent": percent,
            "pointsAwarded": awarded,
            "pointsPossible": possible,
            "passed": passed,
            "passingScore": pass_mark,
            "certificate": certificate,
            "weakSections": weak_sections,
            "results": results,
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:240]))


@app.route(route="lesson", methods=["GET"])
def get_lesson(req: func.HttpRequest) -> func.HttpResponse:
    """Ordered finalized lesson pages, with a source-chunk fallback for old courses."""
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    role = (identity.role_code or "ALL").upper()

    training = (req.params.get("training") or "").strip()
    module_id = (req.params.get("moduleId") or "").strip()
    if not training:
        return _error(400, "Bad request", "training is required")

    try:
        with _conn() as c:
            cur = c.cursor()
            learner = _learner_key(identity)
            if module_id:
                cur.execute(
                    """SELECT m.module_id, m.heading, m.summary, m.active_generation_id
                         FROM dbo.TrainingModules m
                        WHERE m.company_id = ? AND m.doc_title = ? AND m.module_id = ?
                          AND m.status = 'ready'
                          AND EXISTS (
                              SELECT 1 FROM dbo.TrainingModuleRoles audience
                               WHERE audience.company_id = m.company_id
                                 AND audience.module_id = m.module_id
                                 AND audience.role_code IN ('ALL', ?))""",
                    identity.company_id, training, module_id, role,
                )
                module_rows = _rows(cur)
                if not module_rows:
                    return _error(404, "No lesson available",
                                  "No readable sections for this training.")
                selected_module = module_rows[0]
                if selected_module.get("active_generation_id"):
                    cur.execute(
                        """SELECT p.page_id, p.page_order, p.title, p.page_type, p.body,
                                  p.word_count, p.learning_point_ids_json,
                                  p.citations_json, done.completed_at
                             FROM dbo.LessonPages p
                             LEFT JOIN dbo.EmployeeLessonPageProgress done
                               ON done.company_id = p.company_id
                              AND done.learner_id = ? AND done.page_id = p.page_id
                            WHERE p.company_id = ? AND p.module_id = ?
                              AND p.generation_id = ?
                            ORDER BY p.page_order""",
                        learner, identity.company_id, module_id,
                        selected_module["active_generation_id"],
                    )
                    pages = _rows(cur)
                    if pages:
                        def decode(value, fallback):
                            try:
                                return json.loads(value or "")
                            except (TypeError, ValueError):
                                return fallback

                        total_words = sum(int(page["word_count"] or 0) for page in pages)
                        payload_pages = [{
                            "id": page["page_id"],
                            "order": int(page["page_order"]),
                            "title": page["title"],
                            "heading": page["title"],
                            "type": page["page_type"],
                            "body": page["body"],
                            "learningPointIds": decode(
                                page.get("learning_point_ids_json"), []),
                            "citations": decode(page.get("citations_json"), []),
                            "completed": page.get("completed_at") is not None,
                        } for page in pages]
                        return _json({
                            "training": training,
                            "moduleId": module_id,
                            "title": selected_module["heading"],
                            "summary": selected_module.get("summary") or "",
                            "readTime": "{} min read".format(
                                max(1, round(total_words / 200))),
                            "pageCount": len(payload_pages),
                            "completedPages": sum(
                                1 for page in payload_pages if page["completed"]),
                            "pages": payload_pages,
                            # Temporary compatibility for older deployed frontends.
                            "sections": payload_pages,
                        })

            sql = """SELECT c.chunk_id, c.topic, c.section, c.page_start,
                            c.page_end, c.chunk_text, c.source_url
                       FROM dbo.SourceChunks c"""
            params: List[Any] = []
            if module_id:
                sql += " JOIN dbo.TrainingModules m ON m.company_id = c.company_id AND m.doc_id = c.doc_id AND m.topic = c.topic"
            sql += """ WHERE c.doc_title = ? AND c.company_id = ?
                        AND COALESCE(c.role_scope, 'ALL') IN ('ALL', ?)"""
            params.extend([training, identity.company_id, role])
            if module_id:
                sql += " AND m.module_id = ?"
                params.append(module_id)
            sql += " ORDER BY c.page_start, c.section"
            cur.execute(sql, *params)
            sections = _rows(cur)

        if not sections:
            # Same response whether the document does not exist or is out of scope.
            # Different ones would let a learner enumerate other roles' material by
            # title, which is what role scoping exists to prevent.
            return _error(404, "No lesson available",
                          "No readable sections for this training.")

        # Frontend reads .body, not .text -- key mismatch, not a naming preference.
        # Every lesson has been rendering with an empty reading pane since this
        # endpoint was written, invisible until the first real document actually
        # reached it end to end.
        total_words = sum(len((s["chunk_text"] or "").split()) for s in sections)
        return _json({
            "training": training,
            "readTime": "{} min read".format(max(1, round(total_words / 200))),
            "sections": [
                {
                    "id": s["chunk_id"],
                    "topic": s["topic"],
                    "heading": s["section"],
                    "pageStart": s["page_start"],
                    "pageEnd": s["page_end"],
                    "body": s["chunk_text"],
                    "sourceUrl": s.get("source_url") or "",
                    "completed": True,
                }
                for s in sections
            ],
            "pages": [],
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="lesson/page/complete", methods=["POST"])
def complete_lesson_page(req: func.HttpRequest) -> func.HttpResponse:
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")
    module_id = str(body.get("moduleId") or "").strip()
    page_id = str(body.get("pageId") or "").strip()
    if not module_id or not page_id:
        return _error(400, "Bad request", "moduleId and pageId are required")
    role = (identity.role_code or "ALL").upper()
    learner = _learner_key(identity)
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT p.page_id
                     FROM dbo.LessonPages p
                     JOIN dbo.TrainingModules m
                       ON m.company_id = p.company_id AND m.module_id = p.module_id
                      AND m.active_generation_id = p.generation_id
                    WHERE p.company_id = ? AND p.module_id = ? AND p.page_id = ?
                      AND m.status = 'ready'
                      AND EXISTS (
                          SELECT 1 FROM dbo.TrainingModuleRoles audience
                           WHERE audience.company_id = m.company_id
                             AND audience.module_id = m.module_id
                             AND audience.role_code IN ('ALL', ?))""",
                identity.company_id, module_id, page_id, role,
            )
            if not _rows(cur):
                return _error(404, "Page unavailable", "This lesson page is not available.")
            cur.execute(
                """IF NOT EXISTS (
                       SELECT 1 FROM dbo.EmployeeLessonPageProgress
                        WHERE company_id = ? AND learner_id = ? AND page_id = ?)
                       INSERT INTO dbo.EmployeeLessonPageProgress
                           (company_id, learner_id, page_id)
                       VALUES (?, ?, ?)""",
                identity.company_id, learner, page_id,
                identity.company_id, learner, page_id,
            )
            cur.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN done.page_id IS NOT NULL THEN 1 ELSE 0 END) AS completed
                     FROM dbo.LessonPages p
                     JOIN dbo.TrainingModules m
                       ON m.module_id = p.module_id AND m.company_id = p.company_id
                      AND m.active_generation_id = p.generation_id
                     LEFT JOIN dbo.EmployeeLessonPageProgress done
                       ON done.company_id = p.company_id AND done.learner_id = ?
                      AND done.page_id = p.page_id
                    WHERE p.company_id = ? AND p.module_id = ?""",
                learner, identity.company_id, module_id,
            )
            progress = _rows(cur)[0]
            c.commit()
        total = int(progress["total"] or 0)
        completed = int(progress["completed"] or 0)
        return _json({"moduleId": module_id, "completedPages": completed,
                      "pageCount": total, "lessonCompleted": total > 0 and completed >= total})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:240]))


@app.route(route="courses/preview", methods=["GET"])
def preview_course(req: func.HttpRequest) -> func.HttpResponse:
    identity = get_current_employee(req)
    forbidden = require_manager(identity)
    if forbidden:
        return forbidden
    training = (req.params.get("training") or "").strip()
    if not training:
        return _error(400, "Bad request", "training is required")
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT module_id, heading, summary, status, source_order,
                          lesson_word_count, learning_point_count,
                          active_generation_id, quality_notes_json
                     FROM dbo.TrainingModules
                    WHERE company_id = ? AND doc_title = ?
                      AND generation_version = 'instructional-v1'
                      AND status <> 'retired'
                    ORDER BY source_order""",
                identity.company_id, training,
            )
            modules = _rows(cur)
            output = []
            for module in modules:
                cur.execute(
                    """SELECT page_id, page_order, title, page_type, body, citations_json
                         FROM dbo.LessonPages
                        WHERE company_id = ? AND module_id = ? AND generation_id = ?
                        ORDER BY page_order""",
                    identity.company_id, module["module_id"],
                    module.get("active_generation_id"),
                )
                pages = _rows(cur)
                output.append({
                    "moduleId": module["module_id"],
                    "title": module["heading"],
                    "summary": module.get("summary") or "",
                    "status": module["status"],
                    "wordCount": int(module.get("lesson_word_count") or 0),
                    "learningPointCount": int(module.get("learning_point_count") or 0),
                    "qualityNotes": json.loads(module.get("quality_notes_json") or "[]"),
                    "pages": [{
                        "id": page["page_id"], "order": int(page["page_order"]),
                        "title": page["title"], "type": page["page_type"],
                        "body": page["body"],
                        "citations": json.loads(page.get("citations_json") or "[]"),
                    } for page in pages],
                })
        return _json({"training": training, "modules": output})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:240]))


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

            # Peers: everyone else who shares my manager. Available to everyone, not
            # only people who manage someone -- an SDE1 has SDE2/SDE3 as teammates
            # because they share a manager, even though nobody reports to the SDE1
            # themselves. ISNULL sentinel rather than a plain "=" comparison: NULL =
            # NULL is never true in SQL, so someone at the top of the chain with no
            # manager_id at all would otherwise match nobody, including other people
            # also at the top with no manager.
            cur.execute(
                """SELECT e.id, e.name, e.email, r.title, r.role_code
                     FROM dbo.Employees e
                     LEFT JOIN dbo.Roles r ON r.id = e.role_id
                    WHERE e.company_id = ?
                      AND e.id <> ?
                      AND ISNULL(e.manager_id, -1) = ISNULL(?, -1)
                    ORDER BY e.name""",
                identity.company_id, identity.employee_id, identity.manager_id,
            )
            peers = _rows(cur)

            # What each peer's robot is wearing -- cosmetic only, never points or
            # trainings completed (peers' own progress is theirs, not something this
            # screen exposes). One batched query rather than one per peer.
            peer_equipped: Dict[int, List[str]] = {}
            peer_ids = [p["id"] for p in peers]
            if peer_ids:
                placeholders = ",".join("?" for _ in peer_ids)
                cur.execute(
                    "SELECT employee_id, item_id FROM dbo.PetPurchases "
                    "WHERE company_id = ? AND equipped = 1 "
                    "AND employee_id IN ({})".format(placeholders),
                    identity.company_id, *peer_ids,
                )
                for r in _rows(cur):
                    peer_equipped.setdefault(r["employee_id"], []).append(r["item_id"])

            # Who I report to -- shown alongside "people below you" on My Team, so a
            # manager sees both directions of the chain, not just downward.
            manager = None
            if identity.manager_id:
                cur.execute(
                    """SELECT e.id, e.name, e.email, r.title, r.role_code
                         FROM dbo.Employees e
                         LEFT JOIN dbo.Roles r ON r.id = e.role_id
                        WHERE e.id = ? AND e.company_id = ?""",
                    identity.manager_id, identity.company_id,
                )
                row = cur.fetchone()
                if row:
                    manager = {
                        "employeeId": row.id, "name": row.name, "email": row.email,
                        "title": row.title,
                        "roleCode": (row.role_code or "ALL").upper(),
                    }

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
            "peers": [
                {
                    "employeeId": p["id"],
                    "name": p["name"],
                    "email": p["email"],
                    "title": p.get("title"),
                    "roleCode": (p.get("role_code") or "ALL").upper(),
                    "equippedItemIds": peer_equipped.get(p["id"], []),
                }
                for p in peers
            ],
            "manager": manager,
            "uploadTargets": sorted(
                targets.values(), key=lambda t: (not t["direct"], t["title"])),
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="team/leaderboard", methods=["GET"])
def get_team_leaderboard(req: func.HttpRequest) -> func.HttpResponse:
    """
    Everyone in the caller's department, ranked by points earned. Deliberately
    department-wide rather than just the peers GET /team returns -- a leaderboard of
    two or three people sharing one manager isn't much of a competition.

    Ranked by pointsEarned (100 per training actually completed), not pointsBalance --
    balance falls when someone spends it in the shop, and standing should reward
    finishing training, not hoarding points.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT e.id, e.name, r.title
                     FROM dbo.Employees e
                     JOIN dbo.Roles r ON r.id = e.role_id
                     JOIN dbo.Teams t ON t.id = r.team_id
                     JOIN dbo.Departments d ON d.id = t.department_id
                    WHERE e.company_id = ? AND d.name = ?
                    ORDER BY e.name""",
                identity.company_id, identity.department,
            )
            members = _rows(cur)
            member_ids = [m["id"] for m in members]

            completed_by_employee: Dict[int, int] = {}
            equipped_by_employee: Dict[int, List[str]] = {}
            if member_ids:
                placeholders = ",".join("?" for _ in member_ids)
                cur.execute(
                    "SELECT employee_id, COUNT(DISTINCT doc_title) AS n FROM dbo.Certificates "
                    "WHERE company_id = ? AND employee_id IN ({}) "
                    "GROUP BY employee_id".format(placeholders),
                    identity.company_id, *member_ids,
                )
                for r in _rows(cur):
                    completed_by_employee[r["employee_id"]] = r["n"]

                cur.execute(
                    "SELECT employee_id, item_id FROM dbo.PetPurchases "
                    "WHERE company_id = ? AND equipped = 1 AND employee_id IN ({})".format(
                        placeholders),
                    identity.company_id, *member_ids,
                )
                for r in _rows(cur):
                    equipped_by_employee.setdefault(r["employee_id"], []).append(r["item_id"])

        board = []
        for m in members:
            completed = completed_by_employee.get(m["id"], 0)
            board.append({
                "employeeId": m["id"],
                "name": m["name"],
                "title": m.get("title"),
                "isYou": m["id"] == identity.employee_id,
                "trainingsCompleted": completed,
                "pointsEarned": pet_shop.points_earned(completed),
                "equippedItemIds": equipped_by_employee.get(m["id"], []),
            })
        board.sort(key=lambda x: (-x["pointsEarned"], x["name"]))

        return _json({"departmentName": identity.department, "leaderboard": board})
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


@app.route(route="team/completion", methods=["GET"])
def get_team_completion(req: func.HttpRequest) -> func.HttpResponse:
    """
    Real coverage numbers for everyone in the caller's reporting subtree, batched.

    Powers the My Team roster: team size, how many people are missing required
    training, how many have something expiring soon, and completion -- computed the
    same way GET /qscore computes one person's (same qscore.standing call, same
    definition of "missing" and "expired"), just for the whole subtree in one request
    instead of one per row. No number here is invented; a person with nothing required
    counts as fully covered, the same rule qscore.standing already applies.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """WITH subtree AS (
                       SELECT e.id, e.role_id
                         FROM dbo.Employees e WHERE e.manager_id = ?
                       UNION ALL
                       SELECT e.id, e.role_id
                         FROM dbo.Employees e
                         JOIN subtree s ON e.manager_id = s.id
                   )
                   SELECT s.id, r.role_code
                     FROM subtree s
                     LEFT JOIN dbo.Roles r ON r.id = s.role_id
                   OPTION (MAXRECURSION 32)""",
                identity.employee_id,
            )
            subtree = _rows(cur)

            req_cache: Dict[str, List[Dict[str, Any]]] = {}
            rows = []
            for person in subtree:
                role = (person.get("role_code") or "ALL").upper()
                if role not in req_cache:
                    req_cache[role] = _role_requirements(cur, role, identity.company_id)
                held = _certificates_for(cur, person["id"], identity.company_id)
                overall = qscore.standing(req_cache[role], held)["overall"]
                renewal_due = [r for r in qscore.renewal_candidates(held) if not r["expired"]]
                rows.append({
                    "employeeId": person["id"],
                    **overall.to_dict(),
                    "renewalDueCount": len(renewal_due),
                })

        return _json({"people": rows})
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="team/remind", methods=["POST"])
def send_team_reminder(req: func.HttpRequest) -> func.HttpResponse:
    """
    Manager-triggered nudge for one person in the caller's reporting subtree.

    What the email says is computed here from that person's actual missing/expired
    requirements -- never passed by the client -- so this cannot be used to send an
    arbitrary message, and the target must resolve inside the caller's own subtree, the
    same check GET /qscore?employee= makes. A 404 rather than 403 for someone outside
    it: whether a given employee exists is not something to confirm to someone with no
    business asking.

    Sending is real (shared.comms, the same module the daily expiry-reminder job uses),
    but depends on RESEND_API_KEY being configured for this environment. Where it is
    not, this still reports honestly what would have been sent rather than pretending
    delivery succeeded.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()

    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    target_id = body.get("employeeId")
    if not isinstance(target_id, int):
        return _error(400, "Bad request", "employeeId is required.")

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """WITH subtree AS (
                       SELECT e.id, e.name, e.email, e.role_id
                         FROM dbo.Employees e WHERE e.manager_id = ?
                       UNION ALL
                       SELECT e.id, e.name, e.email, e.role_id
                         FROM dbo.Employees e
                         JOIN subtree s ON e.manager_id = s.id
                   )
                   SELECT TOP 1 s.id, s.name, s.email, r.role_code
                     FROM subtree s
                     LEFT JOIN dbo.Roles r ON r.id = s.role_id
                    WHERE s.id = ?
                   OPTION (MAXRECURSION 32)""",
                identity.employee_id, target_id,
            )
            person = cur.fetchone()
            if person is None:
                return _error(404, "Not found", "No such employee in your team.")

            role = (person.role_code or "ALL").upper()
            requirements = _role_requirements(cur, role, identity.company_id)
            held = _certificates_for(cur, person.id, identity.company_id)
            company_name = _company_name(identity.company_id)

        overall = qscore.standing(requirements, held)["overall"]
        if not overall.missing and not overall.expired:
            return _json({
                "sent": False, "reason": "Already compliant -- nothing outstanding.",
                "missing": [], "expired": [],
            })

        from shared.comms import CommsNotConfigured, send_manager_reminder_email
        try:
            send_manager_reminder_email(
                person.email, person.name, overall.missing, overall.expired, company_name,
            )
            return _json({"sent": True, "missing": overall.missing, "expired": overall.expired})
        except CommsNotConfigured as exc:
            return _json({
                "sent": False, "reason": str(exc),
                "missing": overall.missing, "expired": overall.expired,
            })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="settings", methods=["GET"])
def get_settings(req: func.HttpRequest) -> func.HttpResponse:
    """This employee's own preferences: whether they want the reminder/notification
    email shared.comms already knows how to send, and whether the floating desk pet
    shows up on their screen at all."""
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                "SELECT notifications_enabled, pet_visible FROM dbo.Employees "
                "WHERE id = ? AND company_id = ?",
                identity.employee_id, identity.company_id,
            )
            row = cur.fetchone()
        return _json({
            "notificationsEnabled": bool(row.notifications_enabled) if row else True,
            "petVisible": bool(row.pet_visible) if row else True,
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="settings", methods=["POST"])
def set_settings(req: func.HttpRequest) -> func.HttpResponse:
    """
    Update the caller's own preferences -- never someone else's: there is no
    employeeId in the body, only what the token says about who is asking, the same
    rule every other write in this file follows.

    Each preference is independently optional in the body -- the caller sends only
    the one it changed, and whichever it omits is left exactly as stored rather than
    reset to a default. The response always reflects both, current, regardless of
    which one (if either) the request actually touched.
    """
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    updates = []
    params = []
    if "notificationsEnabled" in body:
        enabled = body.get("notificationsEnabled")
        if not isinstance(enabled, bool):
            return _error(400, "Bad request", "notificationsEnabled must be a boolean.")
        updates.append("notifications_enabled = ?")
        params.append(enabled)
    if "petVisible" in body:
        visible = body.get("petVisible")
        if not isinstance(visible, bool):
            return _error(400, "Bad request", "petVisible must be a boolean.")
        updates.append("pet_visible = ?")
        params.append(visible)
    if not updates:
        return _error(400, "Bad request",
                       "Provide notificationsEnabled and/or petVisible (booleans).")

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                "UPDATE dbo.Employees SET {} WHERE id = ? AND company_id = ?".format(
                    ", ".join(updates)),
                *params, identity.employee_id, identity.company_id,
            )
            c.commit()
            cur.execute(
                "SELECT notifications_enabled, pet_visible FROM dbo.Employees "
                "WHERE id = ? AND company_id = ?",
                identity.employee_id, identity.company_id,
            )
            row = cur.fetchone()
        return _json({
            "notificationsEnabled": bool(row.notifications_enabled),
            "petVisible": bool(row.pet_visible),
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


def _pet_state(cur, identity) -> Dict[str, Any]:
    """Shared by GET /pet and both mutating routes below, so a purchase or an equip
    responds with the same freshly-computed numbers rather than the caller's stale copy."""
    cur.execute(
        "SELECT COUNT(DISTINCT doc_title) AS n FROM dbo.Certificates "
        "WHERE employee_id = ? AND company_id = ?",
        identity.employee_id, identity.company_id,
    )
    completed = int(cur.fetchone()[0] or 0)

    cur.execute(
        "SELECT item_id, equipped FROM dbo.PetPurchases WHERE employee_id = ? AND company_id = ?",
        identity.employee_id, identity.company_id,
    )
    owned = _rows(cur)
    owned_ids = [r["item_id"] for r in owned]
    equipped_ids = [r["item_id"] for r in owned if r["equipped"]]

    return {
        "trainingsCompleted": completed,
        "pointsEarned": pet_shop.points_earned(completed),
        "pointsBalance": pet_shop.points_balance(completed, owned_ids),
        "ownedItemIds": owned_ids,
        "equippedItemIds": equipped_ids,
        "catalog": pet_shop.catalog_public(),
    }


@app.route(route="pet", methods=["GET"])
def get_pet(req: func.HttpRequest) -> func.HttpResponse:
    """This employee's pet: points derived from certificates earned, items owned and
    worn. Nothing here is stored as a mutable balance -- see api/shared/pet_shop.py."""
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    try:
        with _conn() as c:
            return _json(_pet_state(c.cursor(), identity))
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="pet/purchase", methods=["POST"])
def purchase_pet_item(req: func.HttpRequest) -> func.HttpResponse:
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    item_id = (body.get("itemId") or "").strip()

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                "SELECT COUNT(DISTINCT doc_title) AS n FROM dbo.Certificates "
                "WHERE employee_id = ? AND company_id = ?",
                identity.employee_id, identity.company_id,
            )
            completed = int(cur.fetchone()[0] or 0)
            cur.execute(
                "SELECT item_id FROM dbo.PetPurchases WHERE employee_id = ? AND company_id = ?",
                identity.employee_id, identity.company_id,
            )
            owned_ids = [r["item_id"] for r in _rows(cur)]

            if not pet_shop.can_afford(completed, owned_ids, item_id):
                return _error(400, "Bad request",
                              "Cannot buy {!r} -- not enough points, already owned, or "
                              "not a real item.".format(item_id))

            cur.execute(
                "INSERT INTO dbo.PetPurchases (employee_id, company_id, item_id, equipped) "
                "VALUES (?, ?, ?, 0)",
                identity.employee_id, identity.company_id, item_id,
            )
            c.commit()
            return _json(_pet_state(cur, identity))
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="pet/equip", methods=["POST"])
def equip_pet_item(req: func.HttpRequest) -> func.HttpResponse:
    """Toggle-wear an owned item. Equipping one item in a slot unequips whatever else
    was worn in that slot -- a robot does not wear two hats."""
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    item_id = (body.get("itemId") or "").strip()
    slot = pet_shop.slot_of(item_id)
    if slot is None:
        return _error(400, "Bad request", "{!r} is not a real item.".format(item_id))

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                "SELECT item_id, equipped FROM dbo.PetPurchases "
                "WHERE employee_id = ? AND company_id = ?",
                identity.employee_id, identity.company_id,
            )
            owned = _rows(cur)
            owned_ids = {r["item_id"] for r in owned}
            if item_id not in owned_ids:
                return _error(400, "Bad request", "{!r} is not owned.".format(item_id))

            currently_equipped = {r["item_id"] for r in owned if r["equipped"]}
            if item_id in currently_equipped:
                cur.execute(
                    "UPDATE dbo.PetPurchases SET equipped = 0 "
                    "WHERE employee_id = ? AND company_id = ? AND item_id = ?",
                    identity.employee_id, identity.company_id, item_id,
                )
            else:
                same_slot = [i for i in owned_ids if pet_shop.slot_of(i) == slot]
                for other in same_slot:
                    cur.execute(
                        "UPDATE dbo.PetPurchases SET equipped = 0 "
                        "WHERE employee_id = ? AND company_id = ? AND item_id = ?",
                        identity.employee_id, identity.company_id, other,
                    )
                cur.execute(
                    "UPDATE dbo.PetPurchases SET equipped = 1 "
                    "WHERE employee_id = ? AND company_id = ? AND item_id = ?",
                    identity.employee_id, identity.company_id, item_id,
                )
            c.commit()
            return _json(_pet_state(cur, identity))
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


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
                    "certificateUrl": (
                        "/api/certificates/{}/download".format(cert["certificate_id"])
                        if cert["certificate_url"] else None
                    ),
                }
                for cert in held
            ],
            "renewalsDue": qscore.renewal_candidates(held),
        })
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", type(exc).__name__)


@app.route(route="certificates/{certificateId}/download", methods=["GET"])
def download_certificate_pdf(req: func.HttpRequest) -> func.HttpResponse:
    """Authenticated proxy for a PDF in the private Blob container."""
    identity = get_current_employee(req)
    if identity is None:
        return _unauthorized()
    certificate_id = str(req.route_params.get("certificateId") or "").strip()
    if not certificate_id.isdigit():
        return _error(404, "Certificate not found", certificate_id)

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT certificate_url, doc_title
                     FROM dbo.Certificates
                    WHERE id = ? AND employee_id = ? AND company_id = ?""",
                int(certificate_id), identity.employee_id, identity.company_id,
            )
            rows = _rows(cur)
        if not rows or not rows[0]["certificate_url"]:
            return _error(404, "Certificate PDF not available", certificate_id)

        from shared.certificates import download_certificate

        content = download_certificate(rows[0]["certificate_url"])
        filename = "quizrant-certificate-{}.pdf".format(certificate_id)
        return func.HttpResponse(
            body=content,
            status_code=200,
            mimetype="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="{}"'.format(filename)},
        )
    except Exception as exc:  # noqa: BLE001
        return _error(503, "Certificate download unavailable", type(exc).__name__)


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
# Generation is handed to an Azure Storage Queue trigger. A Python background thread is
# not durable across Function instance recycling, while doing the model calls inline can
# exceed Azure's HTTP front-end timeout even on a Dedicated plan. GenerationJobs remains
# the durable status record the browser polls; the queue is only the work handoff.


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


def _employees_for_role(cur, company_id: int, role_code: str) -> List[tuple]:
    """
    (email, name) for everyone in this company holding role_code -- or the whole
    company, when role_code is 'ALL' (a company-wide requirement, per the same
    'ALL means company-wide, not a role anyone controls' rule _permitted_upload_roles
    already enforces on the write side).

    Used by confirm_document to find who to notify when a document becomes newly
    required for a role. Read-only, so it doesn't need SqlBank -- a plain query is
    clearer than routing a two-column SELECT through a class built for chunks/questions.
    """
    if role_code == "ALL":
        cur.execute(
            "SELECT email, name, notifications_enabled FROM dbo.Employees WHERE company_id = ?",
            company_id,
        )
    else:
        cur.execute(
            """SELECT e.email, e.name, e.notifications_enabled FROM dbo.Employees e
                 JOIN dbo.Roles r ON r.id = e.role_id
                WHERE e.company_id = ? AND r.role_code = ?""",
            company_id, role_code,
        )
    return [(r.email, r.name) for r in cur.fetchall() if r.notifications_enabled]


def _company_name(company_id: int) -> str:
    """For the email's greeting/sign-off. Falls back to a generic label rather than
    failing the whole notification over a display string."""
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute("SELECT name FROM dbo.Companies WHERE id = ?", company_id)
            row = cur.fetchone()
            return row.name if row else "Your company"
    except Exception:  # noqa: BLE001
        return "Your company"


def _ingest_and_propose(chunks, identity, label: str, retitle_suffix: str,
                        source_kind: str = "upload",
                        trusted_link_id: Optional[int] = None,
                        extra_fields: Optional[dict] = None) -> func.HttpResponse:
    """
    Save already-extracted chunks, seed the role catalog if empty, and ask the model to
    propose a section->role mapping -- the part upload_document and add_trusted_link
    share in full. Generation itself happens later, in /documents/confirm, once a
    manager approves the mapping; nothing here writes a GeneratedQuestions row.

    label is what the response's "file" field shows (a filename or a URL). retitle_suffix
    is the parenthetical used only if doc_title collides with a different document
    (Path(safe).stem for an upload, the URL's host for a link) -- kept separate from
    label because a suffix built from a full URL would be unreadable.

    source_kind/trusted_link_id record ownership in TrainingDocuments, at document grain
    rather than repeated on every chunk -- see 033_create_training_documents.sql. That
    registry is also where the proposed mapping itself gets saved (pending_analysis_json),
    so a manager who navigates away from the mapping-review screen before confirming can
    come back to the same proposal instead of it vanishing with the React state that held
    it -- the chunks were already durable the moment this function ran; the proposal
    describing what to do with them was not, until now.
    """
    if not chunks:
        return _error(422, "No teachable content found",
                      "Text was extracted but nothing usable was found in it.")

    doc_title = chunks[0].doc_title
    try:
        with _conn() as c:
            cur = c.cursor()

            from shared.sqlbank import SqlBank
            bank = SqlBank(c, identity.company_id)

            from quizgen.rolemap import seed_roles
            seed_roles(bank)
            known_roles = bank.roles()

            permitted = _permitted_upload_roles(cur, identity)

            topics = sorted({ch.topic for ch in chunks})
            sections = {}
            for ch in chunks:
                sections.setdefault(ch.topic, ch.text)

            # Asked for BEFORE anything is written, so a corrected title lands in the
            # same save_chunks call below rather than needing a second UPDATE pass over
            # rows already on disk. doc_title here is still whatever the mechanical
            # extractor guessed (first line of a PDF page, a page's <title> tag) --
            # exactly the guess analyze_document is being asked to correct.
            from quizgen.rolemap import analyze_document
            try:
                mapping = analyze_document(doc_title, sections, known_roles)
            except RuntimeError as exc:
                # No model credentials. Nothing was written yet, so nothing is lost --
                # confirm still can't be called until this succeeds, unchanged from
                # before this reorder.
                return _error(503, "Role mapping needs the real model", str(exc)[:300])
            except Exception as exc:  # noqa: BLE001
                return _error(502, "Role analysis failed",
                              "{}: {}".format(type(exc).__name__, str(exc)[:250]))

            # Only overrides the mechanical guess when the model actually proposed
            # something -- an empty suggested_title (the model declining) must fall
            # back to the heuristic, never to an empty doc_title.
            if mapping.suggested_title:
                doc_title = mapping.suggested_title
                for ch in chunks:
                    ch.doc_title = doc_title

            # Two different documents must never share a title -- they would merge
            # into one training, mixing roles and letting set_chunk_roles tag the
            # wrong sections. Checked against the FINAL title (post AI-correction),
            # since that is what would actually collide.
            cur.execute(
                "SELECT DISTINCT doc_id FROM dbo.SourceChunks WHERE doc_title = ? AND company_id = ?",
                doc_title, identity.company_id,
            )
            existing_ids = {r.doc_id for r in cur.fetchall()}
            if existing_ids and chunks[0].doc_id not in existing_ids:
                doc_title = "{} ({})".format(doc_title, retitle_suffix)
                for ch in chunks:
                    ch.doc_title = doc_title

            bank.save_chunks(chunks)

            from quizgen.pipeline import generator_name
            payload = {
                "file": label, "title": doc_title, "documentId": chunks[0].doc_id,
                "chunks": len(chunks), "topics": topics,
                "summary": mapping.summary,
                "proposedRoles": mapping.assignments,
                "permittedRoles": sorted(permitted),
                "unknownRoles": mapping.unknown_roles,
                "thinTopics": mapping.thin_topics,
                "knownRoles": known_roles,
                "generator": generator_name(),
                "needsConfirmation": True,
            }
            payload.update(extra_fields or {})

            # SourceChunks is passage-level evidence. Keep document ownership, and the
            # AI's not-yet-confirmed proposal, once at document grain -- see the
            # docstring above and 033_create_training_documents.sql.
            analysis_json = json.dumps(payload, separators=(",", ":"))
            cur.execute(
                """MERGE dbo.TrainingDocuments AS target
                   USING (SELECT ? AS company_id, ? AS document_id) AS source
                      ON target.company_id = source.company_id
                     AND target.document_id = source.document_id
                   WHEN MATCHED THEN UPDATE SET
                       doc_title = ?, uploaded_by = COALESCE(target.uploaded_by, ?),
                       source_kind = ?, source_label = ?,
                       trusted_link_id = COALESCE(target.trusted_link_id, ?),
                       pending_analysis_json = ?
                   WHEN NOT MATCHED THEN INSERT
                       (company_id, document_id, doc_title, uploaded_by, source_kind,
                        source_label, trusted_link_id, pending_analysis_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
                identity.company_id, chunks[0].doc_id,
                doc_title, identity.employee_id, source_kind, label, trusted_link_id,
                analysis_json,
                identity.company_id, chunks[0].doc_id, doc_title, identity.employee_id,
                source_kind, label, trusted_link_id, analysis_json,
            )
            c.commit()

        return _json(payload, 201)
    except Exception as exc:  # noqa: BLE001
        logging.exception("ingest/propose failed for %s", label)
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:300]))


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
                """SELECT source.doc_id, source.doc_title, COUNT(*) AS chunks,
                          registry.uploaded_by, employee.name AS uploaded_by_name,
                          registry.source_kind,
                          MAX(registry.pending_analysis_json) AS pending_analysis_json
                     FROM dbo.SourceChunks AS source
                     LEFT JOIN dbo.TrainingDocuments AS registry
                       ON registry.company_id = source.company_id
                      AND registry.document_id = source.doc_id
                     LEFT JOIN dbo.Employees AS employee
                       ON employee.id = registry.uploaded_by
                    WHERE source.company_id = ?
                      AND COALESCE(source.container, '') <> 'generated-lessons'
                    GROUP BY source.doc_id, source.doc_title, registry.uploaded_by,
                             employee.name, registry.source_kind""",
                identity.company_id,
            )
            source_docs = list(cur.fetchall())
            cur.execute(
                "SELECT source_doc_title, COUNT(*) AS n FROM dbo.GeneratedQuestions "
                "WHERE company_id = ? AND review_status = 'Approved' "
                "GROUP BY source_doc_title",
                identity.company_id,
            )
            q_counts = {(r.source_doc_title or ""): r.n for r in cur.fetchall()}

            # A running job survives here across a page reload or a tab switch, because
            # it lives in the database, not in the browser tab that started it. Without
            # this the frontend's job state was plain useState in DocumentsScreen --
            # real, still-running generation on the server, but the progress bar
            # vanished the moment that component unmounted, because nothing told a
            # freshly-remounted one that a job existed to resume polling.
            cur.execute(
                """SELECT job_id, doc_title, total, done_count, message, created_at
                     FROM dbo.GenerationJobs
                    WHERE company_id = ? AND state = 'running'
                    ORDER BY created_at DESC""",
                identity.company_id,
            )
            active_jobs: Dict[str, Dict[str, Any]] = {}
            for r in cur.fetchall():
                # ORDER BY created_at DESC means the first row seen per doc_title is
                # the most recent -- later rows for the same title (should not
                # normally happen; nothing stops two jobs queuing for one document) are
                # skipped rather than overwriting it.
                active_jobs.setdefault(r.doc_title, {
                    "jobId": r.job_id, "total": r.total, "done": r.done_count,
                    "message": r.message,
                })

        may_delete_any = (identity.access_role or "") in ("admin", "executive")
        docs = []
        for row in sorted(source_docs, key=lambda item: item.doc_title.lower()):
            pending = None
            if row.pending_analysis_json:
                try:
                    pending = json.loads(row.pending_analysis_json)
                except (TypeError, ValueError):
                    # Corrupt or truncated JSON must not break the whole list -- the
                    # document itself is still real and still listable, it just can't
                    # offer to reopen a mapping review that no longer parses.
                    pending = None
            docs.append({
                "documentId": row.doc_id,
                "title": row.doc_title,
                "chunks": row.chunks,
                "questions": q_counts.get(row.doc_title, 0),
                # Read but not yet generated from -- the UI offers to generate for these.
                "ready": q_counts.get(row.doc_title, 0) > 0,
                "activeJob": active_jobs.get(row.doc_title),
                # Set only while the AI's proposed mapping hasn't been confirmed yet --
                # lets DocumentsScreen reopen MappingReview after a remount instead of
                # the proposal just vanishing with whatever tab held it in memory.
                "pendingAnalysis": pending,
                "uploadedBy": row.uploaded_by_name or "Unknown (legacy)",
                "sourceKind": row.source_kind or "legacy",
                "canDelete": may_delete_any or row.uploaded_by == identity.employee_id,
            })
        from quizgen.pipeline import generator_name
        return _json({"documents": docs, "files": [], "generator": generator_name(),
                      "uploadDir": ""})
    except Exception as exc:  # noqa: BLE001
        logging.exception("GET /documents failed")
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:300]))


def _delete_training_document(cur, company_id: int, document_id: str,
                              doc_title: str, trusted_link_id: Optional[int]) -> Dict[str, int]:
    """Remove one source and every learner-facing or score-bearing record derived from it."""
    cur.execute("CREATE TABLE #DeleteModules (module_id NVARCHAR(64) PRIMARY KEY)")
    cur.execute(
        "INSERT INTO #DeleteModules SELECT module_id FROM dbo.TrainingModules "
        "WHERE company_id = ? AND doc_id = ?",
        company_id, document_id,
    )
    cur.execute("CREATE TABLE #DeleteQuestions (question_id NVARCHAR(64) PRIMARY KEY)")
    cur.execute(
        """INSERT INTO #DeleteQuestions
           SELECT DISTINCT question.question_id
             FROM dbo.GeneratedQuestions AS question
             LEFT JOIN dbo.SourceChunks AS source
               ON source.chunk_id = question.source_chunk_id
            WHERE question.company_id = ?
              AND (question.source_doc_title = ?
                   OR question.module_id IN (SELECT module_id FROM #DeleteModules)
                   OR source.doc_id = ?)""",
        company_id, doc_title, document_id,
    )
    cur.execute("CREATE TABLE #DeleteAttempts (attempt_id NVARCHAR(64) PRIMARY KEY)")
    cur.execute(
        """INSERT INTO #DeleteAttempts
           SELECT DISTINCT attempt.attempt_id
             FROM dbo.GeneratedQuizAttempts AS attempt
            WHERE attempt.company_id = ?
              AND (attempt.training_doc_id = ? OR attempt.training_title = ?
                   OR attempt.module_id IN (SELECT module_id FROM #DeleteModules)
                   OR EXISTS
                      (SELECT 1 FROM dbo.GeneratedQuizAttemptQuestions AS served
                        WHERE served.attempt_id = attempt.attempt_id
                          AND served.question_id IN
                              (SELECT question_id FROM #DeleteQuestions)))""",
        company_id, document_id, doc_title,
    )

    counts: Dict[str, int] = {}

    def remove(key: str, sql: str, *params) -> None:
        cur.execute(sql, *params)
        counts[key] = max(0, cur.rowcount)

    # Attempts and questions are audit history, but the product action explicitly says
    # permanent deletion. Remove dependent audit rows before their parents so SQL Server
    # cannot leave a half-deleted course behind.
    remove(
        "certificates",
        "DELETE FROM dbo.Certificates WHERE company_id = ? "
        "AND (doc_title = ? OR attempt_id IN (SELECT attempt_id FROM #DeleteAttempts))",
        company_id, doc_title,
    )
    remove(
        "gradingEvents",
        "DELETE FROM dbo.GeneratedGradingEvents WHERE company_id = ? AND "
        "(attempt_id IN (SELECT attempt_id FROM #DeleteAttempts) "
        "OR question_id IN (SELECT question_id FROM #DeleteQuestions))",
        company_id,
    )
    remove(
        "responses",
        "DELETE FROM dbo.GeneratedQuizResponses WHERE company_id = ? AND "
        "(attempt_id IN (SELECT attempt_id FROM #DeleteAttempts) "
        "OR question_id IN (SELECT question_id FROM #DeleteQuestions))",
        company_id,
    )
    remove(
        "servedQuestions",
        "DELETE FROM dbo.GeneratedQuizAttemptQuestions WHERE "
        "attempt_id IN (SELECT attempt_id FROM #DeleteAttempts) "
        "OR question_id IN (SELECT question_id FROM #DeleteQuestions)",
    )
    remove(
        "attempts",
        "DELETE FROM dbo.GeneratedQuizAttempts WHERE attempt_id IN "
        "(SELECT attempt_id FROM #DeleteAttempts)",
    )
    remove(
        "moduleProgress",
        "DELETE FROM dbo.EmployeeModuleProgress WHERE company_id = ? AND module_id IN "
        "(SELECT module_id FROM #DeleteModules)",
        company_id,
    )
    remove(
        "trainingProgress",
        "DELETE FROM dbo.EmployeeTrainingProgress WHERE company_id = ? "
        "AND (doc_id = ? OR doc_title = ?)",
        company_id, document_id, doc_title,
    )
    remove(
        "questions",
        "DELETE FROM dbo.GeneratedQuestions WHERE question_id IN "
        "(SELECT question_id FROM #DeleteQuestions)",
    )
    remove(
        "modules",
        "DELETE FROM dbo.TrainingModules WHERE module_id IN "
        "(SELECT module_id FROM #DeleteModules)",
    )
    remove(
        "requirements",
        "DELETE FROM dbo.RoleRequirements WHERE company_id = ? AND doc_title = ?",
        company_id, doc_title,
    )
    remove(
        "interests",
        "DELETE FROM dbo.EmployeeSkillInterest WHERE company_id = ? AND doc_title = ?",
        company_id, doc_title,
    )
    remove(
        # Deleting this row is also how a job that's still running finds out it should
        # stop -- _run_generation_job's per-chunk loop checks whether its own job row
        # still exists. There's no separate "cancel" endpoint: cancelling a running
        # generation and deleting a finished document are the same action from here,
        # just with different timing. A best-effort stop, not an instant kill -- the
        # background worker notices between chunks (one chunk's worth of gpt-5 latency,
        # ~20-90s), not mid-call.
        "jobs",
        "DELETE FROM dbo.GenerationJobs WHERE company_id = ? AND doc_title = ?",
        company_id, doc_title,
    )
    remove(
        "sourceChunks",
        "DELETE FROM dbo.SourceChunks WHERE company_id = ? AND doc_id = ?",
        company_id, document_id,
    )
    if trusted_link_id is not None:
        remove(
            "trustedLinksRetired",
            "UPDATE dbo.TrustedLinks SET is_active = 0 WHERE id = ? AND company_id = ?",
            trusted_link_id, company_id,
        )
    remove(
        "documents",
        "DELETE FROM dbo.TrainingDocuments WHERE company_id = ? AND document_id = ?",
        company_id, document_id,
    )
    return counts


@app.route(route="documents/{documentId}/delete", methods=["POST"])
def delete_document(req: func.HttpRequest) -> func.HttpResponse:
    """
    Permanently delete a course owned by the caller, or any course for admin/executive.

    Works regardless of whether generation is running -- deleting the GenerationJobs
    row IS how a running job is told to stop (see _delete_training_document). There is
    deliberately no separate cancel endpoint: "cancel this upload" and "delete this
    document" are the same request whether or not a job happens to be mid-run.
    """
    identity = get_current_employee(req)
    forbidden = require_manager(identity)
    if forbidden:
        return forbidden

    document_id = str(req.route_params.get("documentId", "")).strip()
    if not document_id:
        return _error(400, "Bad request", "documentId is required.")

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT TOP 1 source.doc_id, source.doc_title, registry.uploaded_by,
                                  registry.trusted_link_id
                     FROM dbo.SourceChunks AS source
                     LEFT JOIN dbo.TrainingDocuments AS registry
                       ON registry.company_id = source.company_id
                      AND registry.document_id = source.doc_id
                    WHERE source.company_id = ? AND source.doc_id = ?
                      AND COALESCE(source.container, '') <> 'generated-lessons'""",
                identity.company_id, document_id,
            )
            row = cur.fetchone()
            if row is None:
                return _error(404, "Document not found", document_id)

            may_delete_any = (identity.access_role or "") in ("admin", "executive")
            if not may_delete_any and row.uploaded_by != identity.employee_id:
                return _error(
                    403, "Forbidden",
                    "Only the person who added this document, or an admin/executive, may delete it.",
                )

            counts = _delete_training_document(
                cur, identity.company_id, row.doc_id, row.doc_title, row.trusted_link_id)
            c.commit()
            return _json({
                "deleted": True,
                "documentId": row.doc_id,
                "title": row.doc_title,
                "removed": counts,
            })
    except Exception as exc:  # noqa: BLE001
        logging.exception("DELETE document failed for %s", document_id)
        return _error(500, "Delete failed", "{}: {}".format(type(exc).__name__, str(exc)[:300]))


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

    return _ingest_and_propose(
        chunks, identity, label=safe, retitle_suffix=Path(safe).stem.replace("_", " "),
        source_kind="upload")


@app.route(route="documents/confirm", methods=["POST"])
@app.queue_output(
    arg_name="generation_message", queue_name="generation-jobs",
    connection="AzureWebJobsStorage")
def confirm_document(req: func.HttpRequest, generation_message) -> func.HttpResponse:
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
    raw_assignments = body.get("assignments") or {}
    new_roles = body.get("newRoles") or []
    supersede = str(body.get("supersede", "")).strip()
    # Default true: assigning a document to a role and having it count toward that
    # role's Q Score are the same decision in the manager's head, and making them
    # tick a second box to get the obvious outcome is friction with no upside. Still
    # opt-out, and still a per-request choice a human made -- not inferred silently
    # from role_scope after the fact, which is exactly what RoleRequirements' own
    # migration comment (019_create_role_requirements.sql) warns against: a Q Score
    # that moves because a document was uploaded or retired, not because anyone did
    # any training.
    make_required = bool(body.get("makeRequired", True))
    if not doc_title or not isinstance(raw_assignments, dict):
        return _error(400, "Bad request", "title and assignments are required")

    assignments: Dict[str, List[str]] = {}
    for topic, raw_roles in raw_assignments.items():
        values = raw_roles if isinstance(raw_roles, list) else [raw_roles]
        roles = list(dict.fromkeys(
            str(code or "ALL").strip().upper() for code in values
            if str(code or "").strip()
        ))
        if not roles:
            return _error(400, "Bad request", "Every section needs at least one role.")
        assignments[str(topic)] = ["ALL"] if "ALL" in roles else roles

    from shared.sqlbank import SqlBank, create_job, new_job_id

    try:
        with _conn() as c:
            cur = c.cursor()
            bank = SqlBank(c, identity.company_id)

            permitted = _permitted_upload_roles(cur, identity)
            for code in {role for roles in assignments.values() for role in roles}:
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

            # SourceChunks keeps one legacy role for compatibility. The generated
            # module audience is normalized in TrainingModuleRoles by the worker.
            tagged = bank.set_chunk_roles(doc_title, {
                topic: ("ALL" if "ALL" in roles else roles[0])
                for topic, roles in assignments.items()
            })

            # Deliberately gated by the same require_manager(...) check at the top of
            # this endpoint, not restricted to admin/executive like set_requirements
            # below -- and that is safe rather than a loosening of that endpoint's
            # "compliance decision, not a team one" rule, because the permitted-roles
            # check just above already confines this to roles in the caller's own
            # reporting subtree. A manager can only make required the exact thing
            # they were already trusted to assign; they still cannot touch
            # requirements for any role outside their own chain.
            required_for: List[str] = []
            # (email, name) pairs to notify once this transaction is committed --
            # collected here, while cur is open, but the actual sends happen after the
            # `with` block closes, so a slow or failing Resend call never holds the DB
            # connection open.
            to_notify: List[tuple] = []
            if make_required:
                for code in {
                    str(role or "ALL").upper()
                    for roles in assignments.values()
                    for role in roles
                }:
                    newly_required = bank.add_role_requirement(code, doc_title)
                    required_for.append(code)
                    if newly_required:
                        to_notify.extend(_employees_for_role(cur, identity.company_id, code))

            retired = 0
            if supersede and supersede != doc_title:
                retired = bank.retire_document_questions(supersede)

            job_id = new_job_id()
            create_job(c, job_id, identity.company_id, doc_title)

            # The proposal this confirms is no longer pending -- clear it so the
            # mapping-review screen doesn't reopen for a document that's already
            # generating. Matched on title, not document_id: this handler only ever
            # receives the title, and doc_title is already unique per company by the
            # collision check in _ingest_and_propose.
            cur.execute(
                "UPDATE dbo.TrainingDocuments SET pending_analysis_json = NULL "
                "WHERE company_id = ? AND doc_title = ?",
                identity.company_id, doc_title,
            )

        generation_message.set(json.dumps({
            "jobId": job_id,
            "companyId": identity.company_id,
            "docTitle": doc_title,
            "assignments": assignments,
        }))

        if to_notify:
            from shared.comms import send_new_training_email
            company_name = _company_name(identity.company_id)
            # Same email can appear twice if, say, a role holder's role_code happens
            # to match two different assignments in this same confirm -- dict.fromkeys
            # on the pair de-dupes without needing the pairs to be hashable-sorted.
            for email, name in dict.fromkeys(to_notify):
                try:
                    send_new_training_email(email, name, doc_title, company_name)
                except Exception:  # noqa: BLE001
                    # A notification failure must never fail the confirm itself -- the
                    # document is already saved and generating either way.
                    logging.exception(
                        "confirm_document: failed to notify %s of new training %s",
                        email, doc_title)

        return _json({
            "title": doc_title, "taggedChunks": tagged, "retired": retired,
            "requiredFor": sorted(required_for), "jobId": job_id,
        }, 202)
    except Exception as exc:  # noqa: BLE001
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:300]))


def _run_generation_job(
    job_id: str, company_id: int, doc_title: str,
    assignments: Optional[Dict[str, List[str]]] = None,
) -> None:
    """Author, validate and assess a course outside the request that queued it."""
    from shared.sqlbank import SqlBank, get_job, update_job
    from quizgen.config import CONFIG
    from quizgen.coursegen import assessment_chunks, build_instructional_course
    from quizgen.pipeline import generate_questions

    try:
        with _conn() as c:
            existing = get_job(c, job_id, company_id)
            if existing is None or existing["state"] == "done":
                return

            bank = SqlBank(c, company_id)
            source_chunks = [
                chunk for chunk in bank.all_chunks()
                if chunk.doc_title == doc_title and chunk.container != "generated-lessons"
            ]
            update_job(
                c, job_id, company_id, total=max(1, len(source_chunks)),
                message="Building lessons from {} source section(s)...".format(len(source_chunks))
                if source_chunks else "No source sections were found.",
            )
            if not source_chunks:
                update_job(c, job_id, company_id, state="error",
                           message="No source sections were found for this course.")
                return

            normalized = assignments or {}
            if not normalized:
                for chunk in source_chunks:
                    normalized.setdefault(chunk.topic, [chunk.role_scope or "ALL"])
            course = build_instructional_course(source_chunks, company_id)
            bank.save_instructional_course(course, normalized)
            lesson_chunks = assessment_chunks(course, normalized)
            if not lesson_chunks:
                notes = "; ".join(
                    "{}: {}".format(module.heading, ", ".join(module.quality_notes[:2]))
                    for module in course.modules
                )
                update_job(
                    c, job_id, company_id, state="done", total=len(course.modules),
                    done_count=len(course.modules), rejected=len(course.modules),
                    message="No course was published. {}".format(notes[:500]),
                )
                return

            bank.save_chunks(lesson_chunks)
            total_written = 0
            total_kept = 0
            total_rejected = 0
            completed = 0
            update_job(
                c, job_id, company_id, total=len(lesson_chunks), done_count=0,
                message="Writing assessments for {} module(s)...".format(len(lesson_chunks)),
            )
            for chunk in lesson_chunks:
                # Cancellation IS deletion: delete_document removes this job's own row
                # as part of its cascade, and that's the only signal a still-running
                # invocation has to know it should stop -- there is no separate cancel
                # call. Checked once per lesson chunk (each iteration is a full
                # difficulty-ladder pass, ~60-90s of gpt-5 calls), not more finely,
                # since a mid-chunk kill would still leave that chunk's own writes to
                # land after the check either way.
                if get_job(c, job_id, company_id) is None:
                    return
                module = next(item for item in course.ready_modules
                              if item.module_id == getattr(chunk, "module_id", ""))
                target = (
                    CONFIG.demo_fast_question_count if CONFIG.demo_fast
                    else max(20, min(30, len(module.learning_points) * 3))
                )
                per_difficulty = int((target + 2) // 3)
                result = generate_questions(
                    bank, [chunk], per_chunk=per_difficulty,
                    difficulty_ladder=True,
                )
                completed += 1
                total_written += result.written
                total_kept += len(result.kept)
                total_rejected += len(result.rejected)
                update_job(
                    c, job_id, company_id, done_count=completed,
                    kept=total_kept, written=total_written,
                    rejected=total_rejected,
                    message="Generated {} ({}/{})".format(
                        module.heading[:80], completed, len(lesson_chunks)),
                )

            published = bank.finalize_instructional_course(course, lesson_chunks)
            if published:
                bank.retire_stale_course_questions(
                    doc_title, [chunk.chunk_id for chunk in lesson_chunks])
            update_job(
                c, job_id, company_id, state="done",
                total=len(lesson_chunks), done_count=len(lesson_chunks),
                kept=total_kept, written=total_written, rejected=total_rejected,
                message=("Published {} module(s) and {} question(s).".format(
                    published, total_written) if published else
                    "Lessons were withheld because the assessment bank was incomplete."),
            )
    except Exception as exc:  # noqa: BLE001
        logging.exception("Generation job %s failed", job_id)
        try:
            with _conn() as failed:
                update_job(
                    failed, job_id, company_id, state="error",
                    message="{}: {}".format(type(exc).__name__, str(exc)[:200]),
                )
        except Exception:  # noqa: BLE001
            logging.exception("Could not record failure for generation job %s", job_id)


@app.queue_trigger(
    arg_name="message", queue_name="generation-jobs",
    connection="AzureWebJobsStorage")
def generate_document_questions(message: func.QueueMessage) -> None:
    """Durable worker for the generation message emitted by /documents/confirm."""
    try:
        payload = json.loads(message.get_body().decode("utf-8"))
        job_id = str(payload["jobId"])
        company_id = int(payload["companyId"])
        doc_title = str(payload["docTitle"])
        assignments = payload.get("assignments") or {}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        logging.exception("Discarding malformed generation queue message")
        return
    _run_generation_job(job_id, company_id, doc_title, assignments)


@app.route(route="links/add", methods=["POST"])
def add_trusted_link(req: func.HttpRequest) -> func.HttpResponse:
    """
    Manager submits a trusted reference URL. Same targeting rule as an upload (own
    reporting subtree, or company-wide for admin/executive only), and the fetched page
    goes through the exact same extraction/grounding/confirm-before-generate pipeline as
    an uploaded PDF -- see _ingest_and_propose. The only thing that differs from
    upload_document is where the text comes from.
    """
    identity = get_current_employee(req)
    forbidden = require_manager(identity)
    if forbidden:
        return forbidden

    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad request", "Body must be JSON")

    url = str(body.get("url", "")).strip()
    scope = str(body.get("scope", "")).strip().lower()
    role_code = str(body.get("roleCode", "")).strip().upper()
    crawl_value = body.get("crawl", True)
    if not isinstance(crawl_value, bool):
        return _error(400, "Bad request", "crawl must be true or false.")
    crawl_subpages = crawl_value
    try:
        max_pages = int(body.get("maxPages", 25))
    except (TypeError, ValueError):
        return _error(400, "Bad request", "maxPages must be 10, 25, or 50.")

    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return _error(400, "Bad request", "A valid http(s) url is required.")
    if scope not in ("team", "company_wide"):
        return _error(400, "Bad request", "scope must be 'team' or 'company_wide'.")
    if max_pages not in (10, 25, 50):
        return _error(400, "Bad request", "maxPages must be 10, 25, or 50.")

    if scope == "company_wide":
        # Same tier _permitted_upload_roles grants "ALL" to for uploads -- a company-wide
        # link reaches every role the same way an ALL-scoped upload does.
        if (identity.access_role or "") not in ("admin", "executive"):
            return _error(403, "Forbidden",
                          "Only admin/executive may add a company-wide trusted link.")
        role_code = "ALL"
    else:
        if not role_code:
            return _error(400, "Bad request", "roleCode is required for a team-scoped link.")
        with _conn() as c:
            permitted = _permitted_upload_roles(c.cursor(), identity)
        if role_code not in permitted:
            return _error(403, "Forbidden",
                          "You may only target roles within your own reporting chain.")

    from urllib.parse import urlparse
    host = urlparse(url).netloc or url[:40]
    crawl_info = None

    try:
        if crawl_subpages:
            from quizgen.web import chunks_from_crawl, crawl_site
            crawl = crawl_site(url, max_pages=max_pages)
            chunks = chunks_from_crawl(crawl, role_code)
            crawl_info = {
                "enabled": True,
                "pageLimit": max_pages,
                "pageCount": len(crawl.pages),
                "totalChars": crawl.total_chars,
                "skipped": crawl.skipped,
                "truncated": crawl.truncated,
                "pages": [{"url": page.url, "title": page.title} for page in crawl.pages],
            }
        else:
            from quizgen.web import fetch
            title, text, fetched_at = fetch(url)
            if not text or not text.strip():
                return _error(422, "No teachable content found",
                              "The page was reachable but had no readable text.")

            from quizgen.ingest import chunks_from_text
            display_title = title or host
            chunks = chunks_from_text(
                text, source_name=url, doc_title=display_title)
            for ch in chunks:
                ch.source_type = "web"
                ch.source_url = url
                ch.fetched_at = fetched_at
                ch.role_scope = role_code
            crawl_info = {
                "enabled": False,
                "pageLimit": 1,
                "pageCount": 1,
                "totalChars": len(text),
                "skipped": 0,
                "truncated": False,
                "pages": [{"url": url, "title": display_title}],
            }
    except Exception as exc:  # noqa: BLE001
        return _error(422, "Could not crawl this website" if crawl_subpages
                      else "Could not fetch this URL",
                      "{}: {}".format(type(exc).__name__, str(exc)[:200]))

    if not chunks:
        return _error(422, "No teachable content found",
                      "The website was reachable but had no readable training content.")

    # Recorded once the page has actually yielded something teachable, same point
    # upload_document's chunks are considered "saved" -- before the AI role-mapping step,
    # so a manager can still see and re-confirm this link even if that step fails for
    # lack of model credentials.
    trusted_link_id = None
    try:
        with _conn() as c:
            from shared.sqlbank import SqlBank
            bank = SqlBank(c, identity.company_id)
            trusted_link_id = bank.add_trusted_link(
                identity.employee_id, scope, role_code, url)
    except Exception as exc:  # noqa: BLE001
        logging.exception("Failed to record trusted link row for %s", url)
        return _error(500, "Internal error", "{}: {}".format(type(exc).__name__, str(exc)[:300]))

    return _ingest_and_propose(
        chunks, identity, label=url, retitle_suffix=host,
        source_kind="trusted_link", trusted_link_id=trusted_link_id,
        extra_fields={"crawl": crawl_info},
    )


@app.route(route="links", methods=["GET"])
def list_trusted_links(req: func.HttpRequest) -> func.HttpResponse:
    """A manager's company's trusted links, active and retired."""
    identity = get_current_employee(req)
    forbidden = require_manager(identity)
    if forbidden:
        return forbidden
    try:
        with _conn() as c:
            from shared.sqlbank import SqlBank
            bank = SqlBank(c, identity.company_id)
            return _json({"links": bank.trusted_links()})
    except Exception as exc:  # noqa: BLE001
        logging.exception("GET /links failed")
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

            # For grouping the upload screen's role picker by org-chart team, so
            # someone whose reporting subtree spans several teams (a CTO over
            # Cybersecurity, Software Engineering and DevOps, say) sees three groups
            # instead of one flat list of every role_code mixed together. Best-effort:
            # QuizgenRoles.role_code is a training track, not an org-chart foreign key,
            # so a code with no matching org-chart Roles row (a manually-added role
            # that was never mapped by role_codes.sql) just gets no team, and the
            # picker falls back to showing it ungrouped rather than erroring.
            cur.execute(
                """SELECT r.role_code, t.name AS team_name
                     FROM dbo.Roles r
                     JOIN dbo.Teams t ON t.id = r.team_id
                     JOIN dbo.Departments d ON d.id = t.department_id
                    WHERE d.company_id = ? AND r.role_code IS NOT NULL""",
                identity.company_id,
            )
            team_by_code: Dict[str, str] = {}
            for r in cur.fetchall():
                team_by_code.setdefault(r.role_code, r.team_name)

        return _json({
            "roles": [
                {
                    **r,
                    "questionCount": counts.get(r["role_code"], 0) + counts.get("ALL", 0),
                    "team": team_by_code.get(r["role_code"]),
                }
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


# --------------------------------------------------------------------------
# Track E: daily certificate-expiry reminders
# --------------------------------------------------------------------------

@app.timer_trigger(schedule="0 0 8 * * *", arg_name="mytimer",
                    run_on_startup=False, use_monitor=True)
def send_expiry_reminders(mytimer: func.TimerRequest) -> None:
    """
    Daily at 08:00 UTC (NCRONTAB "0 0 8 * * *"). Finds Certificates rows expiring
    within EXPIRY_WARNING_DAYS days that have not already been reminded about, emails
    the holder via Resend, then stamps reminder_sent_at so the same certificate is
    never reminded twice.

    Reads dbo.Certificates, not dbo.Completions -- Completions has had its own
    reminder_sent_at column since 004_create_completions.sql, but nothing writes to
    Completions any more. POST /quiz/submit issues real certificates into
    dbo.Certificates (018_extend_certificates.sql). A job built against Completions
    would run daily against a table nothing populates and silently do nothing.

    A cross-tenant scan, deliberately: this runs once for every company, not once per
    company_id, the same way the quarterly regeneration job (Track B, not yet built)
    will need to. Every row already carries its own company via the Employees/Companies
    join, so there is nothing to leak between tenants -- each email only ever
    references its own recipient's own certificate.
    """
    warning_days = int(os.getenv("EXPIRY_WARNING_DAYS", "30"))
    sent, failed, skipped_unconfigured, skipped_disabled = 0, 0, 0, 0

    from shared.comms import CommsNotConfigured, send_expiry_email

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT cert.id, cert.doc_title, cert.expires_at,
                          e.email, e.name AS employee_name, e.notifications_enabled,
                          comp.name AS company_name
                     FROM dbo.Certificates cert
                     JOIN dbo.Employees e   ON e.id = cert.employee_id
                     JOIN dbo.Companies comp ON comp.id = e.company_id
                    WHERE cert.status = 'Active'
                      AND cert.reminder_sent_at IS NULL
                      AND cert.expires_at BETWEEN SYSUTCDATETIME()
                          AND DATEADD(DAY, ?, SYSUTCDATETIME())""",
                warning_days,
            )
            rows = cur.fetchall()

            for row in rows:
                if not row.notifications_enabled:
                    # Left unstamped, deliberately: this person is still genuinely due,
                    # just opted out today. Re-checking them tomorrow is a cheap no-op,
                    # and stamping reminder_sent_at would mean re-enabling notifications
                    # later gets them silently skipped for the rest of this cert's life.
                    skipped_disabled += 1
                    continue
                try:
                    send_expiry_email(
                        row.email, row.employee_name, row.doc_title,
                        row.expires_at, row.company_name,
                    )
                except CommsNotConfigured:
                    # Not a per-recipient failure -- nobody has set RESEND_API_KEY yet.
                    # Every remaining row would fail the identical way, so stop the
                    # loop rather than log the same cause N times, but this run is not
                    # an error: it is expected until Resend is configured.
                    skipped_unconfigured = len(rows) - sent - failed - skipped_disabled
                    break
                except Exception:  # noqa: BLE001
                    failed += 1
                    logging.exception(
                        "send_expiry_reminders: failed to email certificate %s (%s)",
                        row.id, row.email)
                    continue

                cur.execute(
                    "UPDATE dbo.Certificates SET reminder_sent_at = SYSUTCDATETIME() "
                    "WHERE id = ?",
                    row.id,
                )
                c.commit()
                sent += 1

        logging.info(
            "send_expiry_reminders: sent=%d failed=%d skipped_unconfigured=%d skipped_disabled=%d",
            sent, failed, skipped_unconfigured, skipped_disabled)
    except Exception:  # noqa: BLE001
        logging.exception("send_expiry_reminders: run failed")
