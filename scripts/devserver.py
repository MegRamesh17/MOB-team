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
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse, parse_qs

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import devauth  # noqa: E402  (scripts/ is on sys.path as the entry point's dir)
from quizgen.adaptive import build_quiz  # noqa: E402
from quizgen.bank import Bank  # noqa: E402
from quizgen.config import CONFIG  # noqa: E402
from quizgen.models import Attempt, QuestionType, Response, ReviewStatus  # noqa: E402

DB = Path(os.getenv("QUIZGEN_DB", REPO / "data" / "output" / "quizgen.db"))
WEB = REPO / "web"
PASSING_SCORE = float(os.getenv("QUIZGEN_PASSING_SCORE", "80"))
QUIZ_LENGTH = int(os.getenv("QUIZGEN_QUIZ_LENGTH", "8"))
WEAK_THRESHOLD = float(os.getenv("QUIZGEN_WEAK_THRESHOLD", "0.70"))
MIN_ANSWERS = int(os.getenv("QUIZGEN_MIN_ANSWERS", "3"))
# How long a certificate stays current. 12 months per docs/q-score.md; an env
# override exists so expiry can be demonstrated without waiting a year.
CERT_VALIDITY_MONTHS = int(os.getenv("QUIZGEN_CERT_VALIDITY_MONTHS", "12"))

# In-flight quizzes: attempt_id -> question ids, in the order served.
#
# Deliberately in memory. The answer key must not travel to the browser, so the server
# has to remember what it asked; restarting the server abandons quizzes in progress,
# which for a dev server is the right trade against persisting half-finished state.
_IN_FLIGHT: dict = {}

DOCUMENTS = Path(os.getenv("QUIZGEN_DOCUMENTS", REPO / "data" / "documents"))
MAX_UPLOAD = int(os.getenv("QUIZGEN_MAX_UPLOAD_MB", "25")) * 1024 * 1024

# Generation jobs, keyed by id. In memory, like in-flight quizzes: a dev server that
# restarts abandons them, which is the right trade against persisting job state.
_JOBS: dict = {}
_JOB_LOCK = threading.Lock()


def _read_failure_reason(exc: Exception) -> str:
    """
    Translate a PDF library error into something a manager can act on.

    "startxref not found" tells a training coordinator nothing. What they need to
    know is whether to re-export the file, remove a password, or run OCR.
    """
    text = str(exc).lower()
    if "decrypt" in text or "password" in text:
        return ("This PDF is password-protected. Remove the password (open it and "
                "re-save / export without encryption) and upload it again.")
    if "startxref" in text or "eof marker" in text or "invalid" in text:
        return ("This file is not a readable PDF — it may be truncated or corrupted. "
                "Try re-exporting it from the original document.")
    if "scan" in text or "no extractable text" in text:
        return ("No text could be extracted, so this is probably a scan. This pipeline "
                "does not run OCR — export a text-based PDF instead.")
    return str(exc)[:280]


def _human_size(n: int) -> str:
    return "{:.1f} MB".format(n / (1024 * 1024)) if n >= 1024 * 1024 else "{} KB".format(n // 1024)


def _generator_label() -> str:
    """Which provider will actually run — the UI warns about cost when it is not mock."""
    from quizgen.config import CONFIG as QC

    provider = (QC.provider or "mock").lower()
    return "mock" if provider == "mock" else provider


def _parse_multipart(raw: bytes, boundary: str) -> dict:
    """
    Pull the first file part out of a multipart body.

    Hand-rolled because the stdlib's cgi module was removed in Python 3.13 and
    email.parser mangles binary payloads unless the message is assembled carefully.
    Only what is needed is parsed: the first part carrying a filename.
    """
    delim = ("--" + boundary).encode()
    out: dict = {}

    for segment in raw.split(delim):
        if not segment or segment in (b"--", b"--\r\n", b"\r\n"):
            continue
        # Headers and body are separated by a blank line.
        split_at = segment.find(b"\r\n\r\n")
        if split_at == -1:
            continue
        head = segment[:split_at].decode("utf-8", "replace")
        body = segment[split_at + 4:]
        # The trailing CRLF belongs to the delimiter, not to the file.
        if body.endswith(b"\r\n"):
            body = body[:-2]

        if "filename=" not in head:
            continue
        filename = head.split("filename=", 1)[1].split("\r\n", 1)[0].strip().strip('"')
        if not filename:
            continue
        out["filename"] = filename
        out["content"] = body
        break
    return out


def _start_generation_job(doc_title: str) -> str:
    """Generate questions for one document on a background thread."""
    job_id = "job_" + uuid.uuid4().hex[:12]
    with _JOB_LOCK:
        _JOBS[job_id] = {
            "jobId": job_id,
            "title": doc_title,
            "state": "running",
            "done": 0,
            "total": 0,
            "kept": 0,
            "rejected": 0,
            "failed": 0,
            "generator": _generator_label(),
            "message": "Starting…",
            "startedAt": _now(),
        }

    def work():
        from quizgen.pipeline import generate_questions, select_chunks

        try:
            # No request here — this runs on a background thread after the response
            # has gone out. Falls back to the configured company, which is correct
            # for a single-tenant local bank and is the reason this is a DEV server.
            with Bank(DB) as bank:
                chunks, _ = select_chunks(bank, doc_title=doc_title, regenerate=True)
                with _JOB_LOCK:
                    _JOBS[job_id]["total"] = len(chunks)
                    _JOBS[job_id]["message"] = (
                        "Reading {} section(s)…".format(len(chunks)) if chunks
                        else "Already generated for this document."
                    )
                if not chunks:
                    with _JOB_LOCK:
                        _JOBS[job_id].update(state="done", message="Already generated.")
                    return

                def report(p):
                    with _JOB_LOCK:
                        j = _JOBS[job_id]
                        j["done"] = p.index
                        j["kept"] += p.kept_in_batch
                        j["rejected"] = p.rejected_total
                        if p.error:
                            j["failed"] += 1
                        j["message"] = "{} ({}/{})".format(p.chunk.topic[:44], p.index, p.total)

                result = generate_questions(
                    bank, chunks, per_chunk=6, difficulty_ladder=True,
                    on_progress=report)

            with _JOB_LOCK:
                _JOBS[job_id].update(
                    state="done",
                    kept=len(result.kept),
                    written=result.written,
                    rejected=len(result.rejected),
                    failed=len(result.failed),
                    message="{} question(s) ready.".format(result.written),
                    finishedAt=_now(),
                )
        except Exception as exc:  # noqa: BLE001
            # The job must always reach a terminal state, or the UI polls forever.
            with _JOB_LOCK:
                _JOBS[job_id].update(
                    state="error",
                    message="{}: {}".format(type(exc).__name__, str(exc)[:200]),
                    finishedAt=_now(),
                )

    threading.Thread(target=work, daemon=True, name=job_id).start()
    return job_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _plus_one_year(iso: str) -> str:
    """Renewal window: every pass expires one year after it was earned."""
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    try:
        return dt.replace(year=dt.year + 1).isoformat(timespec="seconds")
    except ValueError:  # Feb 29
        return dt.replace(year=dt.year + 1, day=28).isoformat(timespec="seconds")


def _latest_passes(bank, learner: str) -> dict:
    """doc_title -> most recent passing submitted_at for this learner."""
    rows = bank.conn.execute(
        """SELECT q.source_doc_title AS doc, MAX(a.submitted_at) AS passed_at
           FROM attempts a
           JOIN responses r ON r.attempt_id = a.attempt_id
           JOIN questions q ON q.question_id = r.question_id
           WHERE a.learner_id = ? AND a.passed = 1 AND a.submitted_at IS NOT NULL
                 AND q.source_doc_title != ''
           GROUP BY doc""",
        (learner,),
    ).fetchall()
    return {r["doc"]: r["passed_at"] for r in rows}


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
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
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

    def _identity(self):
        """The authenticated caller, from a signed token, or None."""
        return devauth.identity_from_header(self.headers.get("Authorization", ""))

    def _company(self):
        """
        The tenant every Bank in this request is scoped to.

        Taken from the caller's token, so the dev server behaves like the deployed API
        rather than trusting a process-wide config value. An unauthenticated request
        falls back to the configured company — the only routes that reach a Bank without
        a token are open ones, and there is nobody to scope them to.
        """
        identity = self._identity()
        return str(identity.company_id) if identity else CONFIG.company_id

    def _require(self, tier: str = "employee"):
        """
        Return the caller's identity, or send an error and return None.

        Callers MUST stop when this returns None — the response has already gone out.
        401 and 403 are kept distinct: a legitimately expired session looks like a
        permissions bug otherwise.
        """
        identity = self._identity()
        if identity is None:
            self._error(401, "Unauthorized",
                        "Sign in at POST /api/login and send the token as "
                        "'Authorization: Bearer <token>'.")
            return None
        if not identity.at_least(tier):
            self._error(403, "Forbidden",
                        "Requires {} access or above; you have {}.".format(
                            tier, identity.access_role))
            return None
        return identity

    def _learner_role(self) -> str:
        """The caller's TRAINING role, from the token. Not client-supplied any more."""
        identity = self._identity()
        return identity.role_code if identity else ""

    def _learner(self) -> str:
        """
        The learner key for attempt history and mastery.

        Email, matching what the deployed API uses (_learner_key in function_app.py), so
        a learner's history means the same thing in both.
        """
        identity = self._identity()
        return identity.email if identity else ""

    # ------------------------------------------------------------------
    # auth — mirrors api/auth/login.py's contract exactly
    # ------------------------------------------------------------------

    def _login(self):
        body = self._body()
        email = (body.get("email") or "").strip()
        password = body.get("password") or ""
        if not email or not password:
            return self._error(400, "Bad Request", "email and password are required")

        identity = devauth.authenticate(email, password)
        if identity is None:
            # Identical for unknown email and wrong password, as the deployed login is.
            return self._error(401, "Unauthorized", "Invalid email or password")

        return self._send({
            "token": devauth.create_token(identity),
            "expiresInHours": devauth.TOKEN_TTL_HOURS,
            "principal": identity.to_public(),
        })

    def _auth_me(self):
        identity = self._require()
        if identity is None:
            return None
        return self._send({"principal": identity.to_public()})

    def _logout(self):
        # Honest about the limit: the client drops its token and that is all. Revoking it
        # server-side needs shared state this single process does not have.
        return self._send({"ok": True, "detail": "Discard the token client-side."})

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
            # /health stays open so the sign-in screen can tell "API is down" apart
            # from "your password is wrong".
            if route == "/api/health":
                return self._health()
            if route == "/api/auth/me":
                return self._auth_me()

            if self._require() is None:
                return None

            if route == "/api/team":
                return self._team()
            if route == "/api/team/leaderboard":
                return self._team_leaderboard()
            if route == "/api/team/completion":
                return self._team_completion()
            if route == "/api/settings":
                return self._settings_get()
            if route == "/api/qscore":
                return self._qscore(query)
            if route == "/api/requirements":
                return self._requirements()
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
            if route == "/api/pet":
                return self._pet_get()
            if route == "/api/documents":
                return self._list_documents()
            if route == "/api/links":
                return self._list_links()
            if route == "/api/roles":
                return self._list_roles()
            if route.startswith("/api/jobs/"):
                return self._job_status(route.rsplit("/", 1)[-1])
        except Exception as exc:  # noqa: BLE001
            return self._error(500, type(exc).__name__, str(exc)[:300])
        self._error(404, "Not found", route)

    def do_POST(self):  # noqa: N802
        route = urlparse(self.path).path.rstrip("/")
        try:
            # Signing in is what you do when you have no token.
            if route == "/api/login":
                return self._login()
            if route == "/api/auth/logout":
                return self._logout()

            # Uploading material and editing roles change what an entire role is taught
            # and certified against. Manager tier or above, enforced here — the UI hides
            # these too, but a hidden button is not a permission check.
            if route == "/api/documents":
                if self._require("manager") is None:
                    return None
                return self._upload()
            if route == "/api/links/add":
                return self._add_link()
            if route == "/api/documents/confirm":
                if self._require("manager") is None:
                    return None
                return self._confirm_document()
            if route == "/api/roles":
                if self._require("manager") is None:
                    return None
                return self._add_role()
            if route.startswith("/api/roles/") and route.endswith("/delete"):
                if self._require("manager") is None:
                    return None
                return self._remove_role(route.split("/")[3])

            if self._require() is None:
                return None

            if route == "/api/requirements":
                return self._set_requirements()
            if route == "/api/team/remind":
                return self._team_remind()
            if route == "/api/settings":
                return self._settings_set()
            if route == "/api/pet/purchase":
                return self._pet_purchase()
            if route == "/api/pet/equip":
                return self._pet_equip()
            if route == "/api/quiz/start":
                return self._start()
            if route == "/api/quiz/answer":
                return self._answer()
            if route == "/api/quiz/submit":
                return self._submit()
        except Exception as exc:  # noqa: BLE001
            return self._error(500, type(exc).__name__, str(exc)[:300])
        self._error(404, "Not found", route)

    def _team(self):
        """
        Who reports to me, and which roles I may upload for.

        Two different questions, deliberately answered separately:

          people        everyone below me in the reporting chain, however deep, so a
                        director sees their managers' reports too
          uploadTargets the roles those people hold, with the ones held by my DIRECT
                        reports marked. The UI defaults to those, but the whole subtree
                        is permitted — a director may want to push something to every
                        engineer beneath them, not only their own managers.

        An employee with nobody under them gets empty lists rather than a 403: "you
        manage no one" is a fact about the org chart, not a permissions failure, and the
        UI renders it as an empty state.
        """
        identity = self._require()
        if identity is None:
            return None

        subtree = devauth.reports_of(identity.employee_id)
        direct_ids = {p["employee_id"] for p in
                      devauth.reports_of(identity.employee_id, direct_only=True)}

        # Peers: everyone else who shares my manager, available to everyone -- an
        # SDE1 has SDE2/SDE3 as teammates because they share a manager, even with
        # nobody reporting to the SDE1 themselves. Plain Python "==" handles the
        # top-of-chain case (manager_id None on both sides) correctly on its own,
        # unlike SQL's "=" on NULL.
        directory = devauth.directory()
        by_id = {p["employee_id"]: p for p in directory}
        peers = [
            p for p in directory
            if p["employee_id"] != identity.employee_id
            and p.get("manager_id") == identity.manager_id
        ]

        # What each peer's robot is wearing -- cosmetic only, never points or
        # trainings completed (peers' own progress is theirs, not something this
        # screen exposes).
        with Bank(DB, self._company()) as bank:
            peer_equipped = {
                p["employee_id"]: [
                    row["item_id"] for row in bank.pet_purchases(p.get("email", ""))
                    if row["equipped"]
                ]
                for p in peers
            }

        manager = None
        if identity.manager_id is not None:
            m = by_id.get(identity.manager_id)
            if m:
                manager = {
                    "employeeId": m["employee_id"], "name": m.get("name", ""),
                    "email": m.get("email", ""), "title": m.get("title", ""),
                    "roleCode": (m.get("role_code") or "ALL").upper(),
                }

        # Role -> whether anyone directly reporting to me holds it. A role held by both
        # a direct report and someone deeper counts as direct: it is the closer
        # relationship that decides the default.
        # Must agree with _permitted_upload_roles, or the UI offers a target the
        # server then refuses. A subtree member with an unmapped role surfaces as ALL,
        # and ALL is company-wide — not something a manager controls.
        permitted = self._permitted_upload_roles(identity)

        targets = {}
        for person in subtree:
            code = (person.get("role_code") or "ALL").upper()
            if code not in permitted:
                continue
            entry = targets.setdefault(code, {
                "roleCode": code,
                "title": person.get("title", code),
                "direct": False,
                "headcount": 0,
            })
            entry["headcount"] += 1
            if person["employee_id"] in direct_ids:
                entry["direct"] = True

        return self._send({
            "manages": bool(subtree),
            "people": [
                {
                    "employeeId": p["employee_id"],
                    "name": p.get("name", ""),
                    "email": p.get("email", ""),
                    "title": p.get("title", ""),
                    "roleCode": (p.get("role_code") or "ALL").upper(),
                    "accessRole": p.get("access_role", "employee"),
                    "managerId": p.get("manager_id"),
                    "direct": p["employee_id"] in direct_ids,
                }
                for p in subtree
            ],
            "peers": [
                {
                    "employeeId": p["employee_id"],
                    "name": p.get("name", ""),
                    "email": p.get("email", ""),
                    "title": p.get("title", ""),
                    "roleCode": (p.get("role_code") or "ALL").upper(),
                    "equippedItemIds": peer_equipped.get(p["employee_id"], []),
                }
                for p in peers
            ],
            "manager": manager,
            "uploadTargets": sorted(
                targets.values(), key=lambda t: (not t["direct"], t["title"])),
        })

    def _team_leaderboard(self):
        """
        Everyone in the caller's department, ranked by points earned. Deliberately
        department-wide rather than just GET /team's peers -- a leaderboard of two or
        three people sharing one manager isn't much of a competition.

        Ranked by pointsEarned (100 per training actually completed), not
        pointsBalance -- balance falls when someone spends it in the shop, and
        standing should reward finishing training, not hoarding points.
        """
        from quizgen import pet_shop

        identity = self._require()
        if identity is None:
            return None

        members = [p for p in devauth.directory() if p.get("department") == identity.department]

        with Bank(DB, self._company()) as bank:
            board = []
            for m in members:
                completed = bank.trainings_completed_count(m.get("email", ""))
                equipped = [row["item_id"] for row in bank.pet_purchases(m.get("email", ""))
                            if row["equipped"]]
                board.append({
                    "employeeId": m["employee_id"],
                    "name": m.get("name", ""),
                    "title": m.get("title", ""),
                    "isYou": m["employee_id"] == identity.employee_id,
                    "trainingsCompleted": completed,
                    "pointsEarned": pet_shop.points_earned(completed),
                    "equippedItemIds": equipped,
                })
        board.sort(key=lambda x: (-x["pointsEarned"], x["name"]))
        return self._send({"departmentName": identity.department, "leaderboard": board})

    def _team_completion(self):
        """
        Real coverage numbers for everyone in the caller's reporting subtree, batched.

        Same qscore.standing call GET /qscore uses for one person, just looped over the
        whole subtree so the My Team roster is one request instead of one per row. No
        invented numbers: a person with nothing required counts as fully covered, same
        as everywhere else Standing is used.
        """
        from quizgen import qscore

        identity = self._require()
        if identity is None:
            return None

        subtree = devauth.reports_of(identity.employee_id)

        with Bank(DB, self._company()) as bank:
            self._seed_requirements_if_empty(bank)
            req_cache = {}
            rows = []
            for person in subtree:
                role = (person.get("role_code") or "ALL").upper()
                if role not in req_cache:
                    req_cache[role] = bank.role_requirements(role)
                held = bank.certificates(person["email"])
                overall = qscore.standing(req_cache[role], held)["overall"]
                renewal_due = [r for r in qscore.renewal_candidates(held) if not r["expired"]]
                rows.append({
                    "employeeId": person["employee_id"],
                    **overall.to_dict(),
                    "renewalDueCount": len(renewal_due),
                })

        return self._send({"people": rows})

    def _team_remind(self):
        """
        Manager-triggered nudge for one person in the caller's reporting subtree.

        What would be sent is computed here from that person's real missing/expired
        requirements, same as the deployed route -- but this dev server does not send
        email (no Resend key, no such dependency in requirements.txt; see devauth.py's
        note on why a dev shim does not gain a production dependency). It reports
        honestly what WOULD go out rather than pretending delivery happened, which is
        enough to demo the button and wire the real send in api/function_app.py.
        """
        from quizgen import qscore

        identity = self._require()
        if identity is None:
            return None

        body = self._body()
        target_id = body.get("employeeId")
        if not isinstance(target_id, int):
            return self._error(400, "Bad request", "employeeId is required.")

        subtree = {p["employee_id"]: p for p in devauth.reports_of(identity.employee_id)}
        person = subtree.get(target_id)
        if person is None:
            return self._error(404, "Not found", "No such employee in your team.")

        role = (person.get("role_code") or "ALL").upper()
        with Bank(DB, self._company()) as bank:
            self._seed_requirements_if_empty(bank)
            requirements = bank.role_requirements(role)
            held = bank.certificates(person["email"])

        overall = qscore.standing(requirements, held)["overall"]
        if not overall.missing and not overall.expired:
            return self._send({
                "sent": False, "reason": "Already compliant -- nothing outstanding.",
                "missing": [], "expired": [],
            })

        if not devauth.get_notifications_enabled(target_id):
            return self._send({
                "sent": False,
                "reason": "This person has email notifications turned off in Settings.",
                "missing": overall.missing, "expired": overall.expired,
            })

        return self._send({
            "sent": False,
            "reason": "The local dev server does not send email. In Azure this calls "
                       "shared.comms.send_manager_reminder_email once RESEND_API_KEY is set.",
            "missing": overall.missing, "expired": overall.expired,
        })

    def _settings_get(self):
        identity = self._require()
        if identity is None:
            return None
        return self._send({
            "notificationsEnabled": devauth.get_notifications_enabled(identity.employee_id),
            "petVisible": devauth.get_pet_visible(identity.employee_id),
        })

    def _settings_set(self):
        """Each preference is independently optional in the body -- the caller sends
        only the one it changed, and whichever it omits is left exactly as stored."""
        identity = self._require()
        if identity is None:
            return None
        body = self._body()
        touched = False
        if "notificationsEnabled" in body:
            enabled = body.get("notificationsEnabled")
            if not isinstance(enabled, bool):
                return self._error(400, "Bad request", "notificationsEnabled must be a boolean.")
            if not devauth.set_notifications_enabled(identity.employee_id, enabled):
                return self._error(404, "Not found", "No credential record for this account.")
            touched = True
        if "petVisible" in body:
            visible = body.get("petVisible")
            if not isinstance(visible, bool):
                return self._error(400, "Bad request", "petVisible must be a boolean.")
            if not devauth.set_pet_visible(identity.employee_id, visible):
                return self._error(404, "Not found", "No credential record for this account.")
            touched = True
        if not touched:
            return self._error(400, "Bad request",
                                "Provide notificationsEnabled and/or petVisible (booleans).")
        return self._send({
            "notificationsEnabled": devauth.get_notifications_enabled(identity.employee_id),
            "petVisible": devauth.get_pet_visible(identity.employee_id),
        })

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
        with Bank(DB, self._company()) as bank:
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
        from quizgen import qscore

        identity = self._require()
        if identity is None:
            return None

        learner = self._learner()
        with Bank(DB, self._company()) as bank:
            mastery = bank.mastery(learner)
            attempts = bank.attempt_count(learner)
            submitted = bank.submitted_attempts(learner)
            self._seed_requirements_if_empty(bank)
            requirements = bank.role_requirements(identity.role_code)
            held = bank.certificates(identity.email)

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

        streak = qscore.training_streak([a["submitted_at"] for a in submitted])
        overall_q_score = qscore.standing(requirements, held)["overall"].q_score
        badges = qscore.earned_badges(attempts=submitted, streak=streak, q_score=overall_q_score)

        self._send({
            "learnerId": learner,
            "attempts": attempts,
            "streak": streak,
            "badges": badges,
            "topics": topics,
            "weakTopics": [t["topic"] for t in weak],
            "passingScore": PASSING_SCORE,
        })

    def _topics(self):
        with Bank(DB, self._company()) as bank:
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
        with Bank(DB, self._company()) as bank:
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
    # document upload
    # ------------------------------------------------------------------

    def _list_documents(self):
        """What has been uploaded, and how far each one got."""
        with Bank(DB, self._company()) as bank:
            chunks = bank.all_chunks()
            approved = bank.questions(status=ReviewStatus.APPROVED)

        chunk_counts: Dict[str, int] = {}
        for c in chunks:
            chunk_counts[c.doc_title] = chunk_counts.get(c.doc_title, 0) + 1
        q_counts: Dict[str, int] = {}
        for q in approved:
            key = q.source_doc_title or q.topic
            q_counts[key] = q_counts.get(key, 0) + 1

        docs = []
        for title in sorted(chunk_counts):
            docs.append({
                "title": title,
                "chunks": chunk_counts[title],
                "questions": q_counts.get(title, 0),
                # A document with chunks but no questions has been read but not yet
                # turned into a quiz — the UI offers to generate for exactly these.
                "ready": q_counts.get(title, 0) > 0,
            })

        files = []
        if DOCUMENTS.exists():
            files = sorted(p.name for p in DOCUMENTS.iterdir()
                           if p.suffix.lower() in (".pdf", ".txt", ".md"))

        return self._send({
            "documents": docs,
            "files": files,
            "generator": _generator_label(),
            "uploadDir": str(DOCUMENTS),
        })

    def _upload(self):
        """
        Accept an uploaded document, extract it, and start generating questions.

        Extraction is fast and happens inline, so the response can immediately say how
        many chunks and sections were found — or that the file was a scan with no
        extractable text, which is the single most common failure and worth reporting
        straight away rather than after a long silence.

        Generation is slow (tens of seconds per chunk against a real model) so it runs
        on a background thread and the browser polls /api/jobs/<id>. Doing it inline
        would hold the request open for minutes and time out.
        """
        identity = self._require("manager")
        if identity is None:
            return None

        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype or "boundary=" not in ctype:
            return self._error(400, "Expected a file upload",
                               "Send multipart/form-data with a 'file' part.")

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._error(400, "Empty upload", "No content was sent.")
        if length > MAX_UPLOAD:
            return self._error(
                413, "File too large",
                "{} is over the {} MB limit.".format(
                    _human_size(length), MAX_UPLOAD // (1024 * 1024)),
            )

        boundary = ctype.split("boundary=", 1)[1].strip().strip('"')
        raw = self.rfile.read(length)
        parts = _parse_multipart(raw, boundary)

        filename = parts.get("filename") or ""
        content = parts.get("content") or b""
        if not filename or not content:
            return self._error(400, "No file received", "The 'file' part was missing or empty.")

        # Take only the basename: an uploaded name like ../../etc/passwd must not be
        # able to escape the documents directory.
        safe = Path(filename).name
        if Path(safe).suffix.lower() not in (".pdf", ".txt", ".md"):
            return self._error(
                415, "Unsupported file type",
                "Only .pdf, .txt and .md can be read. Got {!r}.".format(Path(safe).suffix or "no extension"),
            )

        DOCUMENTS.mkdir(parents=True, exist_ok=True)
        target = DOCUMENTS / safe
        target.write_bytes(content)

        # Extract now so problems surface immediately.
        sys.path.insert(0, str(REPO / "src"))
        from quizgen.ingest import ingest_document

        try:
            chunks = ingest_document(target)
        except Exception as exc:  # noqa: BLE001
            # A file we cannot read is not kept — it would otherwise be retried on
            # every future ingest and fail the same way each time.
            target.unlink(missing_ok=True)
            return self._error(422, "Could not read this document", _read_failure_reason(exc))

        if not chunks:
            target.unlink(missing_ok=True)
            return self._error(
                422, "No teachable content found",
                "Text was extracted but nothing usable was found in it.",
            )

        return self._ingest_and_propose(
            chunks, identity, label=safe, retitle_suffix=Path(safe).stem.replace("_", " "))

    def _ingest_and_propose(self, chunks, identity, label: str, retitle_suffix: str):
        """
        Save already-extracted chunks, seed the role catalog if empty, and ask the
        model to propose a section->role mapping -- the part _upload and _add_link
        share in full. Generation itself happens later, in /api/documents/confirm, once
        a manager approves the mapping. Mirrors api/function_app.py's
        _ingest_and_propose exactly, so the two backends cannot drift apart on this.

        label is what the response's "file" field shows (a filename or a URL).
        retitle_suffix is the parenthetical used only if doc_title collides with a
        different document (Path(safe).stem for an upload, the URL's host for a link).
        """
        doc_title = chunks[0].doc_title
        # Two different documents must never share a title: they would merge into one
        # training, mixing roles and letting set_chunk_roles tag the wrong sections.
        # A real pack of 16 role briefs all began with the same letterhead and would
        # have collapsed into a single module.
        with Bank(DB, self._company()) as bank:
            existing_ids = {
                c.doc_title: c.doc_id for c in bank.all_chunks()
            }
        if doc_title in existing_ids and existing_ids[doc_title] != chunks[0].doc_id:
            doc_title = "{} ({})".format(doc_title, retitle_suffix)
            for c in chunks:
                c.doc_title = doc_title

        topics = sorted({c.topic for c in chunks})

        # Chunks are saved now (so nothing is lost), but NOT tagged with roles and
        # NOT generated from. gpt-5 proposes a section->role mapping; the manager
        # confirms it in the UI; only then does /api/documents/confirm tag chunks
        # and start generation. The AI proposes, the manager decides.
        with Bank(DB, self._company()) as bank:
            bank.save_chunks(chunks)
            from quizgen.rolemap import analyze_document, seed_roles
            seed_roles(bank)
            known_roles = bank.roles()

        # The AI proposes against every role the company has; the manager may only
        # confirm the ones they control. Sending both lets the UI show a proposal it
        # cannot accept and say why, rather than silently dropping it — a manager whose
        # document genuinely belongs to another team needs to know that, not to watch a
        # section quietly vanish.
        permitted = self._permitted_upload_roles(identity)

        sections = {}
        for c in chunks:
            sections.setdefault(c.topic, c.text)

        try:
            mapping = analyze_document(doc_title, sections, known_roles)
        except RuntimeError as exc:
            # No credentials. The chunks stay saved; the error says exactly what is
            # missing rather than degrading to a silent guess.
            return self._error(503, "Role mapping needs the real model", str(exc)[:300])
        except Exception as exc:  # noqa: BLE001
            return self._error(502, "Role analysis failed", "{}: {}".format(
                type(exc).__name__, str(exc)[:250]))

        return self._send({
            "file": label,
            "title": doc_title,
            "chunks": len(chunks),
            "topics": topics,
            "summary": mapping.summary,
            # topic -> proposed role ("ALL", a known code, or the document's own
            # words for a role the company hasn't defined)
            "proposedRoles": mapping.assignments,
            # Role codes this manager may actually publish to, from their reporting
            # subtree. The UI offers these and nothing else.
            "permittedRoles": sorted(permitted),
            "unknownRoles": mapping.unknown_roles,
            # Sections too thin to build a module from. Per the team's rule this is
            # the manager's problem to solve with more material — never the web's.
            "thinTopics": mapping.thin_topics,
            "knownRoles": known_roles,
            "generator": _generator_label(),
            "needsConfirmation": True,
        }, 201)

    def _add_link(self):
        """
        Manager submits a trusted reference URL. Same targeting rule as an upload
        (own reporting subtree, or company-wide for admin/executive only), then the
        fetched page goes through _ingest_and_propose exactly like an uploaded PDF
        does. Mirrors api/function_app.py's add_trusted_link.
        """
        identity = self._require("manager")
        if identity is None:
            return None

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._error(400, "Bad request", "Body must be JSON")

        url = str(body.get("url", "")).strip()
        scope = str(body.get("scope", "")).strip().lower()
        role_code = str(body.get("roleCode", "")).strip().upper()

        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return self._error(400, "Bad request", "A valid http(s) url is required.")
        if scope not in ("team", "company_wide"):
            return self._error(400, "Bad request", "scope must be 'team' or 'company_wide'.")

        if scope == "company_wide":
            if identity.access_role not in ("admin", "executive"):
                return self._error(403, "Forbidden",
                                   "Only admin/executive may add a company-wide trusted link.")
            role_code = "ALL"
        else:
            if not role_code:
                return self._error(400, "Bad request", "roleCode is required for a team-scoped link.")
            permitted = self._permitted_upload_roles(identity)
            if role_code not in permitted:
                return self._error(403, "Forbidden",
                                   "You may only target roles within your own reporting chain.")

        sys.path.insert(0, str(REPO / "src"))
        from quizgen.web import fetch
        try:
            title, text, fetched_at = fetch(url)
        except Exception as exc:  # noqa: BLE001
            return self._error(422, "Could not fetch this URL",
                               "{}: {}".format(type(exc).__name__, str(exc)[:200]))

        if not text or not text.strip():
            return self._error(422, "No teachable content found",
                               "The page was reachable but had no readable text.")

        from urllib.parse import urlparse as _urlparse
        host = _urlparse(url).netloc or url[:40]

        from quizgen.ingest import chunks_from_text
        display_title = title or host
        chunks = chunks_from_text(text, source_name=display_title)
        for c in chunks:
            c.source_type = "web"
            c.source_url = url
            c.fetched_at = fetched_at
            c.role_scope = role_code

        # Recorded once the page has actually yielded something teachable -- same point
        # _upload's chunks are considered "saved" -- before the AI role-mapping step, so
        # a manager can still see and re-confirm this link even if that step fails for
        # lack of model credentials.
        with Bank(DB, self._company()) as bank:
            bank.add_trusted_link(identity.email, scope, role_code, url)

        return self._ingest_and_propose(chunks, identity, label=url, retitle_suffix=host)

    def _list_links(self):
        """A company's trusted links, active and retired. Mirrors GET /links."""
        with Bank(DB, self._company()) as bank:
            return self._send({"links": bank.trusted_links()})

    def _permitted_upload_roles(self, identity, extra=()):
        """Thin wrapper — the rule lives in devauth so it can be tested directly."""
        return devauth.permitted_upload_roles(identity, extra)

    def _confirm_document(self):
        """
        The manager has reviewed the proposed mapping. Apply it and generate.

        Body: { title, assignments: {topic: role_code|ALL}, newRoles: [{roleCode,
        title, description}], supersede: "<existing title>"|"" }

        newRoles here are roles the MANAGER chose to add after seeing the document's
        unknown-role flags — the AI only ever surfaced them.
        """
        identity = self._require("manager")
        if identity is None:
            return None

        body = self._body()
        doc_title = str(body.get("title", "")).strip()
        assignments = body.get("assignments") or {}
        # See api/function_app.py's confirm_document for the full reasoning -- default
        # true, still a per-request opt-out rather than inferring "required" silently
        # from role_scope after the fact.
        make_required = bool(body.get("makeRequired", True))
        if not doc_title or not isinstance(assignments, dict):
            return self._error(400, "Missing title or assignments")

        with Bank(DB, self._company()) as bank:
            for r in body.get("newRoles") or []:
                code = str(r.get("roleCode", "")).strip().upper().replace(" ", "_")
                if code:
                    bank.add_role(code, str(r.get("title", code)),
                                  str(r.get("description", "")))

            known = {r["role_code"] for r in bank.roles()} | {"ALL"}
            bad = {t: r for t, r in assignments.items()
                   if str(r).upper().replace(" ", "_") not in known}
            if bad:
                return self._error(
                    422, "Unknown role in assignments",
                    "Not in the company role list: {}. Add the role first or map "
                    "these sections to an existing one.".format(
                        ", ".join(sorted({str(v) for v in bad.values()}))),
                )

            normalized = {t: str(r).upper().replace(" ", "_")
                          for t, r in assignments.items()}

            # UPLOAD-03, enforced here rather than in the UI. The upload screen only
            # offers roles this manager controls, but a hidden option is not a
            # permission check — this is the same request re-sent with a different
            # role_code in the body.
            permitted = self._permitted_upload_roles(
                identity,
                extra=[str(r.get("roleCode", "")).strip().upper().replace(" ", "_")
                       for r in body.get("newRoles") or []],
            )
            out_of_scope = sorted({r for r in normalized.values() if r not in permitted})
            if out_of_scope:
                return self._error(
                    403, "Outside your team",
                    "You can publish training to roles held by people who report to "
                    "you. {} {} not among them. You can use: {}.".format(
                        ", ".join(out_of_scope),
                        "is" if len(out_of_scope) == 1 else "are",
                        ", ".join(sorted(permitted)) or "no roles — nobody reports to you",
                    ),
                )

            tagged = bank.set_chunk_roles(doc_title, normalized)

            # Same reasoning as function_app.py's confirm_document: gated by the
            # "manager" tier already required above, not the admin/executive-only
            # bar on set_role_requirements, because `permitted` just above already
            # confines this to roles in the caller's own reporting subtree -- a
            # manager can only make required the exact thing they were already
            # trusted to assign.
            required_for = []
            # (email, name) pairs to notify, collected here but sent after the `with`
            # block closes -- same reasoning as function_app.py's confirm_document:
            # a slow or failing Resend call must not hold the Bank connection open.
            to_notify = []
            if make_required:
                for role_code in set(normalized.values()):
                    newly_required = bank.add_role_requirement(role_code, doc_title)
                    required_for.append(role_code)
                    if newly_required:
                        to_notify.extend(
                            (p["email"], p["name"])
                            for p in devauth.employees_with_role_code(role_code))

            # Update-vs-add was decided by gpt-5 and shown to the manager before
            # this call; supersede arrives here already reviewed. Old questions stop
            # being served; passes already earned hold until their one-year expiry.
            retired = 0
            supersede = str(body.get("supersede", "")).strip()
            if supersede and supersede != doc_title:
                retired = bank.retire_document_questions(supersede)

        if to_notify:
            sys.path.insert(0, str(REPO / "api"))
            from shared.comms import send_new_training_email
            for email, name in dict.fromkeys(to_notify):
                try:
                    send_new_training_email(email, name, doc_title, "Your company")
                except Exception as exc:  # noqa: BLE001
                    # A notification failure must never fail the confirm itself -- the
                    # document is already saved and generating either way. print, not
                    # logging -- this file never imports logging, only print, for
                    # exactly this kind of "surface it, don't crash" diagnostic.
                    print("    ! failed to notify {} of new training {!r}: {}".format(
                        email, doc_title, exc))

        job_id = _start_generation_job(doc_title)
        return self._send({
            "title": doc_title,
            "taggedChunks": tagged,
            "retiredQuestions": retired,
            "superseded": supersede,
            "requiredFor": sorted(required_for),
            "jobId": job_id,
        })

    def _list_roles(self):
        with Bank(DB, self._company()) as bank:
            from quizgen.rolemap import seed_roles
            seed_roles(bank)
            roles = bank.roles()
            # Which roles actually have servable material, for the login picker.
            counts = {}
            for q in bank.questions(status=ReviewStatus.APPROVED):
                for code in (q.role_code or "ALL").split(","):
                    counts[code.strip().upper()] = counts.get(code.strip().upper(), 0) + 1
        return self._send({
            # team is always null here: there is no org-chart Teams table in the local
            # SQLite schema to join against (see api/function_app.py's list_roles for
            # the real lookup deployed uses). The upload screen's role picker already
            # falls back to an ungrouped list when team is missing, so this is a
            # shape-compatible no-op locally, not a bug.
            "roles": [
                {**r, "questionCount": counts.get(r["role_code"], 0) + counts.get("ALL", 0),
                 "team": None}
                for r in roles
            ],
        })

    def _add_role(self):
        body = self._body()
        code = str(body.get("roleCode", "")).strip().upper().replace(" ", "_")
        title = str(body.get("title", "")).strip()
        if not code or not title:
            return self._error(400, "roleCode and title are required")
        with Bank(DB, self._company()) as bank:
            bank.add_role(code, title, str(body.get("description", "")))
        return self._send({"roleCode": code, "title": title}, 201)

    def _remove_role(self, code: str):
        with Bank(DB, self._company()) as bank:
            removed = bank.remove_role(code)
        if not removed:
            return self._error(404, "No such role", code)
        return self._send({"removed": code})

    def _job_status(self, job_id: str):
        job = _JOBS.get(job_id)
        if job is None:
            return self._error(404, "Unknown job", job_id)
        return self._send(dict(job))

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
        role = self._learner_role()
        with Bank(DB, self._company()) as bank:
            self._seed_requirements_if_empty(bank)
            approved = bank.questions(status=ReviewStatus.APPROVED)
            mastery = bank.mastery(learner)
            chunks = bank.all_chunks()
            passes = _latest_passes(bank, learner)
            # Same table Q Score reads (bank.role_requirements), so "Mandatory" here
            # can never disagree with "missing" there. Visible-but-not-required is real
            # optional material -- a document confirmed with makeRequired unchecked.
            required_titles = {r["doc_title"] for r in bank.role_requirements(role or "ALL")}

        # Role isolation happens HERE, on the serving side. A Sales Manager must not
        # even see that Cloud DevOps modules exist, let alone take them. A document
        # counts for this learner if any of its questions is in scope for their role
        # (or scoped ALL — the miscellaneous-everyone material).
        from quizgen.adaptive import scope_matches
        if role:
            approved = [q for q in approved if scope_matches(q.role_code, role)]

        modules_by_doc: Dict[str, list] = {}
        visible_docs = {q.source_doc_title or q.topic for q in approved}
        for c in chunks:
            if c.doc_title not in visible_docs:
                continue
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
            passed_at = passes.get(doc)
            expires_at = _plus_one_year(passed_at) if passed_at else None
            expired = bool(expires_at and expires_at < _now())
            out.append({
                "id": doc,
                "title": doc,
                "status": status,
                # Compliance is passed-within-a-year: a pass older than the renewal
                # window no longer counts, and the module owes a retake.
                "compliant": bool(passed_at) and not expired,
                "passedAt": passed_at,
                "expiresAt": expires_at,
                "expired": expired,
                "mastery": int(accuracy),
                "answered": answered,
                "questionCount": n,
                "modules": modules_by_doc.get(doc, []),
                "required": doc in required_titles,
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
        with Bank(DB, self._company()) as bank:
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
        """
        Real certificate records.

        Previously this derived certificates from passed attempts on the fly, which meant
        a certificate had no identity, no stored score, and no expiry of its own — it was
        recomputed from whatever the attempt happened to say. Certificates are now rows
        (CERT-01..06), so a retake issues a new one and the old remains as history.
        """
        from quizgen import qscore

        learner = self._learner()
        with Bank(DB, self._company()) as bank:
            held = bank.certificates(learner)

        best = qscore.best_certificates(held)
        out = []
        for cert in held:
            expired = qscore.is_expired(cert["expires_at"])
            out.append({
                "certificateId": cert["certificate_id"],
                "title": cert["doc_title"],
                "date": (cert["issued_at"] or "")[:10],
                "score": round(cert["attempt_score"], 1),
                "category": cert["category"],
                "expiresAt": (cert["expires_at"] or "")[:10],
                "expired": expired,
                "daysUntilExpiry": qscore.days_until_expiry(cert["expires_at"]),
                # Which of several passes for the same training currently counts.
                "ofRecord": best.get(cert["doc_title"], {}).get(
                    "certificate_id") == cert["certificate_id"],
                # Null until the artefact exists. An honest absence beats a link that 404s.
                "certificateUrl": cert.get("certificate_url"),
            })
        return self._send({
            "certificates": out,
            "renewalsDue": qscore.renewal_candidates(held),
        })

    def _pet_state(self, bank, learner):
        """Shared by GET /api/pet and both mutating routes, so all three agree on the
        same numbers right after a purchase or an equip instead of trusting the
        caller's stale copy."""
        from quizgen import pet_shop

        completed = bank.trainings_completed_count(learner)
        owned = bank.pet_purchases(learner)
        owned_ids = [p["item_id"] for p in owned]
        equipped_ids = [p["item_id"] for p in owned if p["equipped"]]
        return {
            "trainingsCompleted": completed,
            "pointsEarned": pet_shop.points_earned(completed),
            "pointsBalance": pet_shop.points_balance(completed, owned_ids),
            "ownedItemIds": owned_ids,
            "equippedItemIds": equipped_ids,
            "catalog": pet_shop.catalog_public(),
        }

    def _pet_get(self):
        learner = self._learner()
        with Bank(DB, self._company()) as bank:
            return self._send(self._pet_state(bank, learner))

    def _pet_purchase(self):
        from quizgen import pet_shop

        learner = self._learner()
        body = self._body()
        item_id = (body.get("itemId") or "").strip()
        with Bank(DB, self._company()) as bank:
            completed = bank.trainings_completed_count(learner)
            owned_ids = [p["item_id"] for p in bank.pet_purchases(learner)]
            if not pet_shop.can_afford(completed, owned_ids, item_id):
                return self._error(400, "Bad request",
                                    "Cannot buy {!r} -- not enough points, already owned, "
                                    "or not a real item.".format(item_id))
            bank.pet_purchase(learner, item_id)
            return self._send(self._pet_state(bank, learner))

    def _pet_equip(self):
        learner = self._learner()
        body = self._body()
        item_id = (body.get("itemId") or "").strip()
        with Bank(DB, self._company()) as bank:
            if not bank.pet_equip(learner, item_id):
                return self._error(400, "Bad request",
                                    "{!r} is not owned.".format(item_id))
            return self._send(self._pet_state(bank, learner))

    def _qscore(self, query):
        """
        Q Score for the caller, or for someone they manage.

        ?employee=<email> is how a manager reads a report's score. Permitted only for
        someone in their reporting subtree — QSCORE-08 says "everyone above you in the
        chain", which is the same statement read from the other end. Anyone else is a
        404, not a 403: whether a given person exists is not something to confirm to
        someone with no business asking.
        """
        from quizgen import qscore

        identity = self._require()
        if identity is None:
            return None

        target_email = (query.get("employee", [""])[0] or "").strip().lower()
        target = identity
        if target_email and target_email != identity.email.lower():
            reports = {p["email"].lower(): p
                       for p in devauth.reports_of(identity.employee_id)}
            person = reports.get(target_email)
            if person is None:
                return self._error(404, "Not found", "No such employee in your team.")
            target = devauth.Identity(
                employee_id=person["employee_id"], email=person["email"], company_id=1,
                access_role=person.get("access_role"), name=person.get("name", ""),
                role_code=(person.get("role_code") or "ALL").upper(),
                manager_id=person.get("manager_id"))

        with Bank(DB, self._company()) as bank:
            self._seed_requirements_if_empty(bank)
            requirements = bank.role_requirements(target.role_code)
            held = bank.certificates(target.email)

        standing = qscore.standing(requirements, held)
        return self._send({
            "employee": {"email": target.email, "name": target.name,
                         "roleCode": target.role_code},
            "overall": standing["overall"].to_dict(),
            "behavioural": standing["behavioural"].to_dict(),
            "technical": standing["technical"].to_dict(),
            # Stated rather than implied. A Q Score with no required list is 0 against a
            # denominator of nothing, and a screen showing 0 without saying why reads as
            # "you have done nothing" instead of "nobody has said what you must do".
            "requirementsConfigured": bool(requirements),
        })

    def _seed_requirements_if_empty(self, bank):
        """
        Give every role a starting required list, once, if nobody has set one.

        Without a denominator Q Score is 0 for everyone and the screen looks broken
        rather than unconfigured. Seeded from what is actually in the bank: every
        document scoped ALL becomes required of everyone, and each role-scoped document
        becomes required of that role.

        This is a DEV convenience and is honest about it — real requirements are a
        compliance decision, and POST /api/requirements overwrites this entirely. It runs
        only when the table is empty, so an admin's list is never quietly replaced.
        """
        if bank.all_role_requirements():
            return
        rows = bank.conn.execute(
            "SELECT DISTINCT role_scope, doc_title FROM chunks WHERE doc_title != ''"
        ).fetchall()
        by_role = {}
        for r in rows:
            by_role.setdefault((r["role_scope"] or "ALL").upper(), []).append(
                # Behavioural vs technical cannot be inferred from a title, so everything
                # starts technical. An admin recategorises; guessing would put real
                # conduct training in the wrong bucket and skew the split it exists for.
                {"doc_title": r["doc_title"], "category": "technical"})
        for role, items in by_role.items():
            bank.set_role_requirements(role, items)

    def _requirements(self):
        """The required training list per role. Admin-set, never inferred."""
        identity = self._require()
        if identity is None:
            return None
        with Bank(DB, self._company()) as bank:
            self._seed_requirements_if_empty(bank)
            return self._send({
                "requirements": bank.all_role_requirements(),
                "mine": bank.role_requirements(identity.role_code),
            })

    def _set_requirements(self):
        """
        Replace the required list for one role. Admin or executive only.

        Not a manager action, deliberately: this is Coverage's denominator, so whoever
        edits it moves everyone-in-that-role's Q Score. That is a compliance decision,
        not a team one.
        """
        identity = self._require("admin")
        if identity is None:
            return None

        body = self._body()
        role = str(body.get("roleCode", "")).strip().upper()
        items = body.get("requirements")
        if not role or not isinstance(items, list):
            return self._error(400, "Bad request",
                               "roleCode and a requirements list are required.")
        with Bank(DB, self._company()) as bank:
            count = bank.set_role_requirements(role, items)
        return self._send({"roleCode": role, "count": count})

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

        with Bank(DB, self._company()) as bank:
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
        # From the token only. Accepting it from the body would let a signed-in
        # employee write into someone else's attempt history.
        learner = self._learner()
        length = int(body.get("length") or QUIZ_LENGTH)
        role = body.get("role", "")

        training = body.get("training", "")
        # The header wins over the body: an employee cannot request another role's
        # quiz by editing the POST payload in devtools.
        role = self._learner_role()

        with Bank(DB, self._company()) as bank:
            plan = build_quiz(bank, learner_id=learner, length=length, role=role)

            # Scoping to one training happens after assembly rather than inside
            # build_quiz: the adaptive engine still decides which topics within the
            # document are worth asking about, and only the document is constrained.
            if training:
                from quizgen.adaptive import scope_matches
                scoped = [q for q in plan.questions if (q.source_doc_title or q.topic) == training]
                if len(scoped) < length:
                    # The top-up pool must apply the SAME role filter build_quiz did.
                    # It once refilled from the whole bank unfiltered, which handed a
                    # Customer Service rep a Sales Manager question — the exact leak
                    # the role scope exists to prevent.
                    pool = [
                        q for q in bank.questions(status=ReviewStatus.APPROVED)
                        if (q.source_doc_title or q.topic) == training
                        and (not role or scope_matches(q.role_code, role))
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

    def _issue_certificate(self, bank, learner, attempt_id, detail):
        """
        Issue a certificate for a passed attempt.

        The training it certifies is the source DOCUMENT, not the topic — the same grain
        trainings and mastery use, so a certificate lines up with the card the learner
        pressed "start" on. A quiz spanning two documents certifies the one it drew most
        of its questions from; mixed quizzes are an assembly artefact, not a thing to
        certify twice.

        attempt_score is the difficulty-weighted score from qscore.py, not the raw
        percentage the results screen shows. Those differ on purpose: the raw score is
        "how many did you get right", the attempt score is what feeds Q Score.
        """
        from quizgen import qscore

        # "correct", not "isCorrect" — the deployed API uses the latter, this detail
        # dict uses the former, and reading the wrong one scores every answer as wrong
        # while still issuing the certificate. Silent, and only visible as a suspiciously
        # round 0.0 on a passing attempt.
        graded = [{"difficulty": d.get("difficulty", "Medium"),
                   "correct": bool(d.get("correct"))} for d in detail]
        score = qscore.attempt_score(graded)

        titles = [d.get("sourceTitle") or d.get("topic") for d in detail]
        titles = [t for t in titles if t]
        if not titles:
            return None
        doc_title = max(set(titles), key=titles.count)

        # Category comes from the role's requirement list, which is where an admin
        # declared it. Defaulting to technical when nothing says otherwise keeps a
        # certificate out of the behavioural bucket rather than guessing it into one.
        category = "technical"
        for req in bank.role_requirements(self._learner_role()):
            if req["doc_title"] == doc_title:
                category = req.get("category") or "technical"
                break

        return bank.issue_certificate(
            certificate_id="cert_" + uuid.uuid4().hex[:12],
            learner_id=learner,
            doc_title=doc_title,
            attempt_id=attempt_id,
            attempt_score=score,
            category=category,
            expires_at=qscore.expiry_from(_now(), CERT_VALIDITY_MONTHS),
        )

    def _submit(self):
        body = self._body()
        attempt_id = body.get("attemptId", "")
        learner = self._learner()   # token only, as in _start
        answers = {a.get("questionId"): a for a in body.get("answers", [])}

        served = _IN_FLIGHT.get(attempt_id)
        if served is None:
            return self._error(
                404, "Unknown attempt",
                "Start a quiz first. Attempts are lost when the dev server restarts.",
            )

        with Bank(DB, self._company()) as bank:
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
                    # Needed by qscore.attempt_score. Without it every question weighs
                    # 1.0 and the difficulty weighting is a no-op that looks like it
                    # works — the score is plausible, just not what it claims to be.
                    "difficulty": question.difficulty.value
                    if hasattr(question.difficulty, "value") else str(question.difficulty),
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

            # A pass earns a certificate. Issued here rather than by a later sweep so
            # the learner sees it on the results screen — a certificate that appears
            # minutes later reads as if something went wrong.
            certificate = None
            if attempt.passed:
                certificate = self._issue_certificate(bank, learner, attempt_id, detail)

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
            "certificate": certificate,
            "results": detail,
            "weakTopics": [
                {"topic": m.topic, "accuracyPercent": round(m.accuracy * 100, 1)}
                for m in weak[:5]
            ],
        })


def main() -> int:
    # An empty bank is a normal starting state, not an error. Documents can be
    # uploaded through the UI, and refusing to start here made that impossible:
    # the one case where you most need the server is the case it rejected.
    DB.parent.mkdir(parents=True, exist_ok=True)

    # Startup summary: no request here, so the configured company.
    with Bank(DB) as bank:
        approved = len(bank.questions(status=ReviewStatus.APPROVED))
        total = bank.stats().get("questions", 0)

    port = int(os.getenv("PORT", "8000"))
    print("\n  Employee Training — local dev server")
    print("  " + "-" * 46)
    print("  bank      {} ({} approved of {})".format(DB.name, approved, total))
    print("  docs      {}".format(DOCUMENTS))
    print("  generator {}{}".format(
        _generator_label(),
        "  (no API key needed, no cost)" if _generator_label() == "mock" else "  (billed per question)",
    ))
    print("  app       http://localhost:{}".format(port))
    print("  api       http://localhost:{}/api/health".format(port))
    if not approved:
        print("\n  The bank is empty. Upload a PDF in the app to build one,")
        print("  or from the command line:")
        print("    python scripts/make_sample_pdfs.py")
        print("    PYTHONPATH=src python -m quizgen.cli ingest --source local --pdf-dir data/documents")
        print("    QUIZGEN_PROVIDER=mock PYTHONPATH=src python -m quizgen.cli generate")
    print("\n  Ctrl-C to stop.\n")

    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n  stopped.")
