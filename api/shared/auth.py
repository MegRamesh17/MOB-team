"""
shared/auth.py -- Track A (self-hosted email/password auth).

Used by auth/login.py to issue tokens, and by any endpoint in function_app.py that
needs to know who's calling. Deliberately plain functions rather than a decorator --
function_app.py's existing endpoints all call `_caller_id(req)` inline at the top of
the function body, so `get_current_employee(req)` follows that same shape instead of
introducing a new, inconsistent pattern (a @require_auth decorator) alongside it.

JWT_SIGNING_SECRET is read from an app setting the same way SQL_PASSWORD already is --
a Key Vault reference resolved by the Function App's managed identity, never a literal
value in code or in Terraform. Generate the actual secret with `openssl rand -hex 32`
and store it in Key Vault as `jwt-signing-secret`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import azure.functions as func
import jwt

TOKEN_TTL_HOURS = 12
_ALGO = "HS256"


def _secret() -> str:
    secret = os.getenv("JWT_SIGNING_SECRET")
    if not secret:
        # Fail loud. Signing with a default/empty secret would make every token
        # forgeable -- that's a security bug, not something to paper over quietly.
        raise RuntimeError("JWT_SIGNING_SECRET is not set")
    return secret


@dataclass
class Identity:
    employee_id: int
    email: str
    company_id: int
    access_role: Optional[str]   # 'employee' | 'manager' | 'director' | 'admin' | 'executive'


def create_token(employee_id: int, email: str, company_id: int, access_role: Optional[str]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(employee_id),
        "email": email,
        "company_id": company_id,
        "access_role": access_role,
        "iat": now,
        "exp": now + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGO)


def decode_token(token: str) -> Optional[Identity]:
    try:
        secret = _secret()
    except RuntimeError:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
    try:
        return Identity(
            employee_id=int(payload["sub"]),
            email=payload["email"],
            company_id=int(payload["company_id"]),
            access_role=payload.get("access_role"),
        )
    except (KeyError, ValueError, TypeError):
        return None


def get_current_employee(req: func.HttpRequest) -> Optional[Identity]:
    """
    Call at the top of any protected endpoint -- same spot _caller_id(req) is called
    today. Returns None if there's no valid bearer token; callers should return 401
    in that case.

        identity = get_current_employee(req)
        if identity is None:
            return _error(401, "Unauthorized")
    """
    header = req.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return decode_token(header[len("Bearer "):])


def require_manager(identity: Optional[Identity]) -> Optional[func.HttpResponse]:
    """
    Call after get_current_employee(). Returns an error response to return
    immediately if identity is missing or below manager level. Returns None if clear
    to proceed. Fails closed -- a missing identity or unrecognized role never passes.

        identity = get_current_employee(req)
        forbidden = require_manager(identity)
        if forbidden:
            return forbidden
    """
    if identity is None:
        return func.HttpResponse(
            '{"title":"Unauthorized","status":401}',
            status_code=401,
            mimetype="application/json",
        )
    if identity.access_role not in ("manager", "director", "admin", "executive"):
        return func.HttpResponse(
            '{"title":"Forbidden","detail":"Requires manager access or above","status":403}',
            status_code=403,
            mimetype="application/json",
        )
    return None
