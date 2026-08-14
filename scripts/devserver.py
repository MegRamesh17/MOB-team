"""
Local dev server — the web app without Azure.

Serves the endpoint contract in docs/frontend-spec.md out of the SQLite bank that
`quizgen generate` already writes, so the whole app can be run, demoed and developed
against on a laptop with no Azure SQL, no Functions host and no credentials.

WHY THIS EXISTS. api/function_app.py is the real API and talks to Azure SQL through
pyodbc. That database has never been reachable from a laptop — the SQL firewall blocks
it — so there was no way to click through the product at all. This closes that gap
without weakening the real thing.

WHAT IS SHARED WITH PRODUCTION, and what is not:

  shared      question selection (quizgen.adaptive.build_quiz), the bank, grading
              arithmetic, the pass mark, mastery bands. A quiz here is assembled by
              exactly the code that assembles one in Azure.

  not shared  storage (SQLite, not Azure SQL) and identity (a header, not Entra).

So this is a faithful demo of behaviour and an unfaithful demo of infrastructure. It is
for development and demos. It is not a deployment target: single-threaded, no auth, and
it will happily serve to anything that connects.

Run:  python scripts/devserver.py            then open http://localhost:8000
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from quizgen.adaptive import build_quiz  # noqa: E402
from quizgen.bank import Bank  # noqa: E402
from quizgen.models import Attempt, QuestionType, Response, ReviewStatus  # noqa: E402

DB = Path(os.getenv("QUIZGEN_DB", REPO / "data" / "output" / "quizgen.db"))
WEB = REPO / "web"
PASSING_SCORE = float(os.getenv("QUIZGEN_PASSING_SCORE", "80"))
QUIZ_LENGTH = int(os.getenv("QUIZGEN_QUIZ_LENGTH", "8"))
WEAK_THRESHOLD = float(os.getenv("QUIZGEN_WEAK_THRESHOLD", "0.70"))
MIN_ANSWERS = int(os.getenv("QUIZGEN_MIN_ANSWERS", "3"))

# In-flight quizzes: attempt_id -> question ids, in the order served.
#
# Deliberately in memory. The answer key must not travel to the browser, so the server
# has to remember what it asked; restarting the server abandons quizzes in progress,
# which for a dev server is the right trade against persisting half-finished state.
_IN_FLIGHT: dict = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mastery_band(accuracy: float) -> str:
    if accuracy >= 90:
        return "Strong"
    if accuracy >= 70:
        return "Developing"
    return "Needs work"


class Handler(BaseHTTPRequestHandler):
    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, body, status=200, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        # The UI is served from this same origin, but a teammate running a React dev
        # server on :3000 against this API would otherwise be blocked by CORS.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, x-learner-id")
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, status, title, detail=""):
        self._send({"title": title, "detail": detail, "status": status}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _learner(self) -> str:
        return self.headers.get("x-learner-id", "demo-learner")

    def do_OPTIONS(self):  # noqa: N802
        self._send(b"", 204, "text/plain")

    # ------------------------------------------------------------------
    # routing
    # ------------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        route = urlparse(self.path).path.rstrip("/") or "/"
        query = parse_qs(urlparse(self.path).query)

        if route in ("/", "/index.html"):
            return self._static("index.html")
        if not route.startswith("/api"):
            return self._static(route.lstrip("/"))

        try:
            if route == "/api/health":
                return self._health()
            if route == "/api/me":
                return self._me()
            if route == "/api/topics":
                return self._topics()
            if route == "/api/questions":
                return self._questions(query)
            if route == "/api/trainings":
                return self._trainings()
            if route == "/api/lesson":
                return self._lesson(query)
            if route == "/api/certificates":
                return self._certificates()
        except Exception as exc:  # noqa: BLE001
            return self._error(500, type(exc).__name__, str(exc)[:300])
        self._error(404, "Not found", route)

    def do_POST(self):  # noqa: N802
        route = urlparse(self.path).path.rstrip("/")
        try:
            if route == "/api/quiz/start":
                return self._start()
            if route == "/api/quiz/answer":
                return self._answer()
            if route == "/api/quiz/submit":
                return self._submit()
        except Exception as exc:  # noqa: BLE001
            return self._error(500, type(exc).__name__, str(exc)[:300])
        self._error(404, "Not found", route)

    def _static(self, relative: str):
        target = (WEB / relative).resolve()
        # Path traversal guard: ../../ in a URL must not escape web/.
        if not str(target).startswith(str(WEB.resolve())) or not target.is_file():
            return self._error(404, "Not found", relative)
        types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                 ".svg": "image/svg+xml", ".json": "application/json"}
        self._send(target.read_bytes(), 200,
                   types.get(target.suffix, "application/octet-stream"))

    # ------------------------------------------------------------------
    # endpoints
    # ------------------------------------------------------------------

    def _health(self):
        with Bank(DB) as bank:
            stats = bank.stats()
            approved = len(bank.questions(status=ReviewStatus.APPROVED))
        self._send({
            "status": "ok",
            "database": "sqlite:{}".format(DB.name),
            "questionsApproved": approved,
            "questionsTotal": stats.get("questions", 0),
            "servable": approved > 0,
            # Says plainly that this is not the Azure path, so nobody demos it
            # believing they have proven the deployed system works.
            "mode": "local-dev",
        })

    def _me(self):
        learner = self._learner()
        with Bank(DB) as bank:
            mastery = bank.mastery(learner)
            attempts = bank.attempt_count(learner)

        topics = [
            {
                "topic": m.topic,
                "answered": m.answered,
                "correct": m.correct,
                "accuracyPercent": round(m.accuracy * 100, 1),
                "masteryLevel": _mastery_band(m.accuracy * 100),
            }
            for m in sorted(mastery.values(), key=lambda m: m.accuracy)
        ]
        # Weak needs evidence. Without the floor a single wrong answer reads as 0%
        # and the topic dominates every subsequent quiz.
        weak = [t for t in topics
                if t["answered"] >= MIN_ANSWERS and t["accuracyPercent"] < WEAK_THRESHOLD * 100]

        self._send({
            "learnerId": learner,
            "attempts": attempts,
            "topics": topics,
            "weakTopics": [t["topic"] for t in weak],
            "passingScore": PASSING_SCORE,
        })

    def _topics(self):
        with Bank(DB) as bank:
            approved = bank.questions(status=ReviewStatus.APPROVED)
        counts: dict = {}
        for q in approved:
            counts[q.topic] = counts.get(q.topic, 0) + 1
        self._send({
            "topics": [{"topic": t, "questionCount": n}
                       for t, n in sorted(counts.items())],
        })

    def _questions(self, query):
        """Browse the bank. Never includes is_correct — see the note in _start."""
        topic = (query.get("topic") or [""])[0]
        limit = min(int((query.get("limit") or ["50"])[0]), 200)
        with Bank(DB) as bank:
            items = bank.questions(topic=topic or None, status=ReviewStatus.APPROVED)
        self._send({
            "total": len(items),
            "questions": [{
                "questionId": q.question_id,
                "topic": q.topic,
                "difficulty": q.difficulty.value,
                "type": q.question_type.value,
                "prompt": q.prompt,
                "provenance": q.provenance_class.value,
                "sourceTitle": q.source_doc_title,
                "sourceUrl": q.source_url,
                "sourceQuote": q.source_quote,
                "timesServed": q.times_served,
            } for q in items[:limit]],
        })

    # ------------------------------------------------------------------
    # endpoints backing the React UI
    # ------------------------------------------------------------------

    def _trainings(self):
        """
        Source documents, presented as the UI's "trainings".

        The mapping the frontend assumes:
            training = source document       (Behavioral Compliance for Employees)
            module   = section heading in it (Recognising Phishing Attempts)

        This is the same grain mastery is measured on, so the progress ring on a
        training card and the weak-topic targeting in the quiz engine agree with each
        other. Deriving it any other way would let the UI show 90% on a subject the
        engine considers weak.
        """
        learner = self._learner()
        with Bank(DB) as bank:
            approved = bank.questions(status=ReviewStatus.APPROVED)
            mastery = bank.mastery(learner)
            chunks = bank.all_chunks()

        modules_by_doc: Dict[str, list] = {}
        for c in chunks:
            modules_by_doc.setdefault(c.doc_title, [])
            if c.topic not in modules_by_doc[c.doc_title]:
                modules_by_doc[c.doc_title].append(c.topic)

        counts: Dict[str, int] = {}
        for q in approved:
            key = q.source_doc_title or q.topic
            counts[key] = counts.get(key, 0) + 1

        out = []
        for doc, n in sorted(counts.items()):
            m = mastery.get(doc)
            answered = m.answered if m else 0
            accuracy = round(m.accuracy * 100, 1) if m else 0
            # Status is derived from evidence, not from a stored flag: a learner who
            # has answered nothing has not started, and one at/above the pass mark
            # has completed it.
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
                "questionCount": n,
                "modules": modules_by_doc.get(doc, []),
            })
        return self._send({"trainings": out})

    def _lesson(self, query):
        """
        The reading a learner sees before the quiz — the actual indexed source text.

        This is not filler. Questions are generated from these chunks, so the lesson a
        learner reads and the questions they are asked come from the same passages. If
        this returned anything else, the UI would promise "the questions come straight
        from it" and be lying.
        """
        training = (query.get("training") or [""])[0]
        with Bank(DB) as bank:
            chunks = [c for c in bank.all_chunks() if c.doc_title == training]

        if not chunks:
            return self._error(404, "No lesson content", "Nothing indexed for {!r}.".format(training))

        sections, seen = [], set()
        for c in chunks:
            if c.topic in seen:
                continue
            seen.add(c.topic)
            sections.append({
                "heading": c.topic,
                "body": c.text,
                "sourceUrl": c.source_url or "",
                "page": c.page_start,
            })

        words = sum(len(s["body"].split()) for s in sections)
        return self._send({
            "title": training,
            # 200wpm, floored at a minute so a short doc never reads "0 min".
            "readTime": "{} min read".format(max(1, round(words / 200))),
            "sections": sections,
        })

    def _certificates(self):
        """Passed attempts. A certificate is a passed attempt, not a separate record."""
        learner = self._learner()
        with Bank(DB) as bank:
            rows = bank.conn.execute(
                """SELECT attempt_id, submitted_at, score_percent
                   FROM attempts
                   WHERE learner_id = ? AND passed = 1 AND submitted_at IS NOT NULL
                   ORDER BY submitted_at DESC""",
                (learner,),
            ).fetchall()
            certs = []
            for r in rows:
                # Name the certificate after whatever the attempt actually covered.
                topics = bank.conn.execute(
                    """SELECT q.source_doc_title AS doc, COUNT(*) AS n
                       FROM responses r JOIN questions q ON q.question_id = r.question_id
                       WHERE r.attempt_id = ? AND q.source_doc_title != ''
                       GROUP BY doc ORDER BY n DESC LIMIT 1""",
                    (r["attempt_id"],),
                ).fetchone()
                certs.append({
                    "title": topics["doc"] if topics else "General Compliance",
                    "date": (r["submitted_at"] or "")[:10],
                    "score": round(r["score_percent"], 1),
                })
        return self._send({"certificates": certs})

    def _answer(self):
        """
        Grade ONE question, mid-quiz, so the UI can give immediate feedback.

        This endpoint exists because the alternative was worse. The React UI checks
        answers as they are given and shows an explanation straight away — good design,
        but it was doing that against a `correct` index held in the browser. Shipping
        the key to the client puts every answer one devtools panel away.

        So the browser sends what was picked and gets back a verdict. The key is
        revealed for THAT question only, and only after an answer was committed, which
        is exactly what the UI needs to render its correct/incorrect states and nothing
        more.

        Grading here is identical to the arithmetic in _submit; the final score is
        still computed server-side at submit and never trusted from the client.
        """
        body = self._body()
        attempt_id = body.get("attemptId", "")
        question_id = body.get("questionId", "")

        served = _IN_FLIGHT.get(attempt_id)
        if served is None:
            return self._error(404, "Unknown attempt", "Start a quiz first.")
        # Only a question actually served in this attempt may be graded, or this
        # becomes an oracle for reading the answer to any question in the bank.
        if question_id not in served:
            return self._error(403, "Not part of this attempt", question_id)

        with Bank(DB) as bank:
            question = bank.get_question(question_id)
        if question is None:
            return self._error(404, "Unknown question", question_id)

        if question.question_type == QuestionType.FILL_IN_BLANK:
            typed = str(body.get("textAnswer", "")).strip().lower()
            accepted = {a.strip().lower() for a in question.accepted_answers}
            correct = bool(typed) and typed in accepted
        else:
            selected = set(body.get("selectedOptionIds") or [])
            key = {o.option_id for o in question.options if o.is_correct}
            correct = bool(key) and selected == key

        return self._send({
            "questionId": question_id,
            "correct": correct,
            "correctOptionIds": [o.option_id for o in question.options if o.is_correct],
            "acceptedAnswers": question.accepted_answers,
            "explanation": question.explanation,
            "sourceTitle": question.source_doc_title,
            "sourceUrl": question.source_url,
            "sourceQuote": question.source_quote,
            "provenance": question.provenance_class.value,
        })

    def _start(self):
        body = self._body()
        learner = body.get("learnerId") or self._learner()
        length = int(body.get("length") or QUIZ_LENGTH)
        role = body.get("role", "")

        training = body.get("training", "")

        with Bank(DB) as bank:
            plan = build_quiz(bank, learner_id=learner, length=length, role=role)

            # Scoping to one training happens after assembly rather than inside
            # build_quiz: the adaptive engine still decides which topics within the
            # document are worth asking about, and only the document is constrained.
            if training:
                scoped = [q for q in plan.questions if (q.source_doc_title or q.topic) == training]
                if len(scoped) < length:
                    pool = [
                        q for q in bank.questions(status=ReviewStatus.APPROVED)
                        if (q.source_doc_title or q.topic) == training
                        and q.question_id not in {s.question_id for s in scoped}
                    ]
                    scoped.extend(pool[: length - len(scoped)])
                plan.questions = scoped

        if not plan.questions:
            return self._error(
                409, "No questions available",
                "The bank has no approved questions matching this learner and role. "
                "Run `quizgen generate`, then `quizgen review --approve-all`.",
            )

        attempt_id = "att_" + uuid.uuid4().hex[:12]
        _IN_FLIGHT[attempt_id] = [q.question_id for q in plan.questions]

        # THE ANSWER KEY IS NOT IN THIS PAYLOAD.
        #
        # No is_correct, no accepted_answers, no explanation. Option ids go out so the
        # browser can say which one was picked; only the server knows which id is right.
        # Sending the key and hiding it in the UI would put every answer one devtools
        # panel away.
        self._send({
            "attemptId": attempt_id,
            "learnerId": learner,
            "startedAt": _now(),
            "passingScore": PASSING_SCORE,
            "isRemedial": plan.is_remedial,
            # Why these topics. Shown in the UI so a learner is never guessing at why
            # a quiz looks the way it does.
            "rationale": [
                {
                    "topic": t.topic,
                    "questions": t.slots,
                    "reason": t.reason,
                    "accuracyPercent": None if t.accuracy is None else round(t.accuracy * 100, 1),
                }
                for t in plan.topic_plans
            ],
            "questions": [{
                "questionId": q.question_id,
                "topic": q.topic,
                "difficulty": q.difficulty.value,
                "type": q.question_type.value,
                "prompt": q.prompt,
                "points": q.points,
                "options": [{"optionId": o.option_id, "text": o.text} for o in q.options],
            } for q in plan.questions],
        })

    def _submit(self):
        body = self._body()
        attempt_id = body.get("attemptId", "")
        learner = body.get("learnerId") or self._learner()
        answers = {a.get("questionId"): a for a in body.get("answers", [])}

        served = _IN_FLIGHT.get(attempt_id)
        if served is None:
            return self._error(
                404, "Unknown attempt",
                "Start a quiz first. Attempts are lost when the dev server restarts.",
            )

        with Bank(DB) as bank:
            questions = {qid: bank.get_question(qid) for qid in served}

            responses = []
            awarded = possible = 0
            detail = []

            for qid in served:
                question = questions.get(qid)
                if question is None:
                    continue
                possible += question.points
                given = answers.get(qid, {})

                # Grading is arithmetic, not a model call. A learner disputing a score
                # has to be able to be shown exactly why it came out that way.
                if question.question_type == QuestionType.FILL_IN_BLANK:
                    typed = str(given.get("textAnswer", "")).strip().lower()
                    accepted = {a.strip().lower() for a in question.accepted_answers}
                    correct = bool(typed) and typed in accepted
                    selected = []
                else:
                    selected = list(given.get("selectedOptionIds") or [])
                    key = {o.option_id for o in question.options if o.is_correct}
                    correct = bool(key) and set(selected) == key

                points = question.points if correct else 0
                awarded += points

                responses.append(Response(
                    response_id="res_" + uuid.uuid4().hex[:12],
                    attempt_id=attempt_id,
                    learner_id=learner,
                    question_id=qid,
                    topic=question.topic,
                    selected_option_ids=selected,
                    text_answer=str(given.get("textAnswer", "")),
                    is_correct=correct,
                    points_awarded=points,
                    answered_at=_now(),
                ))

                # The key is only revealed once the attempt is graded and closed.
                detail.append({
                    "questionId": qid,
                    "topic": question.topic,
                    "prompt": question.prompt,
                    "correct": correct,
                    "explanation": question.explanation,
                    "correctOptionIds": [o.option_id for o in question.options if o.is_correct],
                    "acceptedAnswers": question.accepted_answers,
                    "sourceTitle": question.source_doc_title,
                    "sourceUrl": question.source_url,
                    "sourceQuote": question.source_quote,
                    "provenance": question.provenance_class.value,
                })

            score = round(100.0 * awarded / possible, 1) if possible else 0.0
            attempt = Attempt(
                attempt_id=attempt_id,
                learner_id=learner,
                started_at=_now(),
                submitted_at=_now(),
                score_percent=score,
                points_awarded=awarded,
                points_possible=possible,
                passed=score >= PASSING_SCORE,
                responses=responses,
            )
            bank.save_attempt(attempt)

            mastery = bank.mastery(learner)

        _IN_FLIGHT.pop(attempt_id, None)

        weak = sorted(
            (m for m in mastery.values()
             if m.answered >= MIN_ANSWERS and m.accuracy < WEAK_THRESHOLD),
            key=lambda m: m.accuracy,
        )
        self._send({
            "attemptId": attempt_id,
            "scorePercent": score,
            "pointsAwarded": awarded,
            "pointsPossible": possible,
            "passed": score >= PASSING_SCORE,
            "passingScore": PASSING_SCORE,
            "results": detail,
            "weakTopics": [
                {"topic": m.topic, "accuracyPercent": round(m.accuracy * 100, 1)}
                for m in weak[:5]
            ],
        })


def main() -> int:
    if not DB.exists():
        print("No question bank at {}.\n".format(DB))
        print("Build one first:")
        print("    python scripts/make_sample_pdfs.py")
        print("    PYTHONPATH=src python -m quizgen.cli ingest --path data/documents")
        print("    PYTHONPATH=src python -m quizgen.cli generate")
        return 1

    with Bank(DB) as bank:
        approved = len(bank.questions(status=ReviewStatus.APPROVED))
        total = bank.stats().get("questions", 0)

    port = int(os.getenv("PORT", "8000"))
    print("\n  Employee Training — local dev server")
    print("  " + "-" * 46)
    print("  bank      {} ({} approved of {})".format(DB.name, approved, total))
    print("  app       http://localhost:{}".format(port))
    print("  api       http://localhost:{}/api/health".format(port))
    if not approved:
        print("\n  WARNING: nothing is approved, so no quiz can start.")
        print("  Fix:     PYTHONPATH=src python -m quizgen.cli review --approve-all")
    print("\n  Ctrl-C to stop.\n")

    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n  stopped.")
