"""
auth/login.py -- POST /login. Track A (self-hosted email/password auth).

Its own Azure Functions Blueprint, registered into function_app.py with the two lines
at the bottom of this file. This is the only edit to the existing file, and it's
additive -- nothing already in function_app.py changes.

Duplicates function_app.py's _connection_string()/_odbc_from_ado() rather than
importing them, since api/ is currently a flat script (function_app.py), not an
importable package -- there's nothing to import from. Worth consolidating into one
shared db.py once a second consumer needs it beyond these two files; not necessary for
this to work correctly today. If SQL_* env vars or the ADO->ODBC conversion ever
change, change both copies.
"""

from __future__ import annotations

import json
import os

import azure.functions as func
import bcrypt

from shared.auth import create_token

bp = func.Blueprint()


def _odbc_from_ado(value: str) -> str:
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
    conn = (os.getenv("SQL_CONNECTION_STRING") or "").strip()
    if conn:
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


def _json(body, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body, default=str), status_code=status, mimetype="application/json"
    )


def _error(status: int, title: str, detail: str = "") -> func.HttpResponse:
    return _json({"title": title, "detail": detail, "status": status}, status)


@bp.route(route="login", methods=["POST"])
def login(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Bad Request", "Body must be JSON")

    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not email or not password:
        return _error(400, "Bad Request", "email and password are required")

    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute(
                """SELECT e.id, e.email, e.company_id, e.password_hash, r.access_role
                       FROM dbo.Employees e
                       LEFT JOIN dbo.Roles r ON r.id = e.role_id
                       WHERE e.email = ?""",
                email,
            )
            row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        return _error(503, "Service Unavailable", type(exc).__name__)

    # Same generic message whether the email doesn't exist or the password's wrong --
    # a different message for "no such account" would let someone enumerate real
    # employee emails by trying them against /login one at a time.
    invalid = _error(401, "Unauthorized", "Invalid email or password")

    if row is None or row.password_hash is None:
        return invalid

    if not bcrypt.checkpw(password.encode("utf-8"), row.password_hash.encode("utf-8")):
        return invalid

    token = create_token(
        employee_id=row.id,
        email=row.email,
        company_id=row.company_id,
        access_role=row.access_role,
    )
    return _json({"token": token, "expiresInHours": 12})


# --- The only change to the existing function_app.py. Add these two lines near the
# --- top, right after `app = func.FunctionApp(...)`:
#
#     from auth.login import bp as auth_bp
#     app.register_functions(auth_bp)
