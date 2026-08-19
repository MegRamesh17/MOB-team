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

    # Display name. In the token rather than looked up, because /auth/me answers from
    # the token alone -- without this the UI can only greet people by email address.
    name: str = ""

    # The TRAINING role -- which material this person is served (SDE2, SWE_MANAGER, ALL).
    # Distinct from access_role, which is the permission tier, and kept separate because
    # they answer different questions: an employee whose title contains "lead" is not a
    # manager, and a SWE_MANAGER training role does not by itself grant manager access.
    #
    # Defaults to "ALL", which serves company-wide material and nothing role-specific --
    # so an unmapped role under-serves rather than leaking another role's training.
    role_code: str = "ALL"

    # Who this person reports to. Needed for the manager's team view, for scoping which
    # roles a manager may upload for, and for Q Score visibility. Known at login, so
    # carrying it here saves a query on every request that needs the reporting line.
    manager_id: Optional[int] = None

    # Org-chart department name (e.g. "Software Development"), for display only -- never
    # used for authorization, that's what access_role and role_code are for. Carried here
    # so /auth/me can hand it back from the token alone, same reason name is.
    department: str = ""

    # Org-chart job title (e.g. "Director of DevOps"), for display only, same reason as
    # department above -- distinct from role_code, which is the training track, not the
    # person's actual title.
    title: str = ""


def create_token(
    employee_id: int,
    email: str,
    company_id: int,
    access_role: Optional[str],
    role_code: Optional[str] = "ALL",
    manager_id: Optional[int] = None,
    # Appended rather than slotted in beside the other identity fields, deliberately.
    # Inserting a positional parameter into a signature that already has callers silently
    # shifts every argument after it -- the failure is a type error somewhere unrelated,
    # not a missing-argument error at the call site.
    name: str = "",
    department: str = "",
    title: str = "",
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(employee_id),
        "email": email,
        "company_id": company_id,
        "access_role": access_role,
        "name": name or "",
        "role_code": (role_code or "ALL").upper(),
        "manager_id": manager_id,
        "department": department or "",
        "title": title or "",
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
        manager_id = payload.get("manager_id")
        return Identity(
            employee_id=int(payload["sub"]),
            email=payload["email"],
            company_id=int(payload["company_id"]),
            access_role=payload.get("access_role"),
            name=payload.get("name") or "",
            # .get with a default, not [], so a token minted before these claims
            # existed still decodes instead of logging everyone out on deploy.
            role_code=(payload.get("role_code") or "ALL").upper(),
            manager_id=int(manager_id) if manager_id is not None else None,
            # .get with a default, same reason as role_code above: a token minted
            # before this claim existed should still decode.
            department=payload.get("department") or "",
            title=payload.get("title") or "",
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
