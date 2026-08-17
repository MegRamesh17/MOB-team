"""
Local implementation of the DEPLOYED auth contract, for the dev server.

This is not a second auth design. `api/auth/login.py` and `api/shared/auth.py` own the
contract; this implements the same one against a local file so the app can be signed into
without an Azure SQL connection. Same route, same request body, same response shape, same
token claims, same algorithm and TTL — so `web-app/` cannot tell which one it is talking
to, and a bug in the frontend's auth handling shows up locally instead of only in Azure.

WHAT MUST STAY IN STEP WITH api/shared/auth.py
    algorithm       HS256
    TTL             12 hours
    claims          sub (employee_id as a string), email, company_id,
                    access_role, role_code, manager_id, iat, exp
    login route     POST /api/login   {email, password}
    login response  {token, expiresInHours, principal{...}}
    me route        GET  /api/auth/me {principal{...}}

If that file changes, this changes. A drift between them is the "works locally, fails in
the demo" failure the whole project is trying to design against, so it is worth checking
both when either moves.

DIFFERENCES, DELIBERATE
    - Credentials live in a gitignored JSON file, not Employees.password_hash, because
      there is no Azure SQL here. Hashes are PBKDF2 rather than bcrypt: bcrypt is in
      api/requirements.txt for the Function App but not in the root requirements.txt that
      tests.yml installs, and adding a dependency to make a dev shim work is the wrong
      trade. The hash never crosses between the two stores, so the formats need not match.
    - The employee directory mirrors db/seed/seed_data.sql exactly — same ids, emails,
      role codes and reporting lines — so a scenario tested locally behaves the same way
      deployed.

NOT A DEPLOYMENT TARGET. No rate limiting, no revocation, HTTP on localhost.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

AUTH_DIR = Path(os.getenv("QUIZGEN_AUTH_DIR", ".auth"))
USERS_FILE = AUTH_DIR / "users.json"
SESSION_KEY_FILE = AUTH_DIR / "session_key"

PBKDF2_ITERATIONS = 480_000
TOKEN_TTL_HOURS = 12          # matches api/shared/auth.py
_ALGO = "HS256"               # matches api/shared/auth.py

# Mirrors db/seed/seed_data.sql and db/migrations/016_add_role_code.sql. employee_id is
# the IDENTITY value each row gets from the seed's insert order; manager_id and role_code
# match what the deployed login would resolve for the same person.
#
# role_code "ALL" where 016 leaves the mapping NULL — VP of Sales and Security Analyst
# have no quizgen training role, so they get company-wide material only. Same behaviour as
# the deployed path, rather than a locally-invented mapping that would flatter the demo.
DIRECTORY: List[Dict] = [
    dict(employee_id=1, name="Dana Whitfield", email="dana.whitfield@demo.com",
         title="Software Engineering Manager", role_code="SWE_MANAGER",
         access_role="manager", manager_id=None),
    dict(employee_id=2, name="Priya Nandakumar", email="priya.n@demo.com",
         title="VP of Sales", role_code="ALL",
         access_role="director", manager_id=None),
    dict(employee_id=3, name="Ethan Brooks", email="ethan.brooks@demo.com",
         title="SDE 2", role_code="SDE2",
         access_role="employee", manager_id=1),
    dict(employee_id=4, name="Maya Osei", email="maya.osei@demo.com",
         title="SDE 1", role_code="SDE1",
         access_role="employee", manager_id=1),
    dict(employee_id=5, name="Liam Chen", email="liam.chen@demo.com",
         title="Security Analyst", role_code="ALL",
         access_role="employee", manager_id=1),
    dict(employee_id=6, name="Sofia Delgado", email="sofia.delgado@demo.com",
         title="Account Executive", role_code="ACCOUNT_TEAM",
         access_role="employee", manager_id=2),
    dict(employee_id=7, name="Noah Whitaker", email="noah.whitaker@demo.com",
         title="Senior Account Executive", role_code="ACCOUNT_TEAM",
         access_role="employee", manager_id=2),
    dict(employee_id=8, name="Ava Thompson", email="ava.thompson@demo.com",
         title="Account Executive", role_code="ACCOUNT_TEAM",
         access_role="employee", manager_id=2),
]

_TIERS = ("employee", "manager", "director", "admin", "executive")


@dataclass(frozen=True)
class Identity:
    """Field-for-field the same as api/shared/auth.py's Identity."""

    employee_id: int
    email: str
    company_id: int
    access_role: Optional[str]
    role_code: str = "ALL"
    manager_id: Optional[int] = None

    def at_least(self, tier: str) -> bool:
        try:
            return _TIERS.index(self.access_role or "") >= _TIERS.index(tier)
        except ValueError:
            return False          # an unrecognised tier is not a reason to grant access

    def to_public(self) -> Dict:
        return {
            "employee_id": self.employee_id,
            "email": self.email,
            "company_id": self.company_id,
            "access_role": self.access_role,
            "role_code": self.role_code,
            "manager_id": self.manager_id,
        }


# ---------------------------------------------------------------------------
# password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Refusing to hash an empty password.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = stored.split("$")
    except (ValueError, AttributeError):
        return False
    if algo != "pbkdf2_sha256":
        return False
    try:
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(iterations))
    except (ValueError, TypeError):
        return False
    # compare_digest so the comparison does not leak, through timing, how many leading
    # bytes were right.
    return hmac.compare_digest(digest, base64.b64decode(hash_b64))


# ---------------------------------------------------------------------------
# credential store
# ---------------------------------------------------------------------------


def _load_users() -> List[Dict]:
    if not USERS_FILE.exists():
        return []
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8")).get("users", [])
    except json.JSONDecodeError:
        return []


def seed_demo_users(password: str) -> int:
    """Give every employee in DIRECTORY the same password. Returns how many were written."""
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    users = [{**person, "password_hash": hash_password(password)} for person in DIRECTORY]
    USERS_FILE.write_text(json.dumps({"users": users}, indent=2) + "\n", encoding="utf-8")
    try:
        USERS_FILE.chmod(0o600)
    except OSError:
        pass                      # Windows and some mounts refuse chmod
    return len(users)


def authenticate(email: str, password: str) -> Optional[Identity]:
    email = (email or "").strip().lower()
    for user in _load_users():
        if user.get("email", "").strip().lower() != email:
            continue
        if not verify_password(password or "", user.get("password_hash", "")):
            # Same None as an unknown user. Distinguishing them hands an attacker a way
            # to enumerate valid addresses — and it is what the deployed login does.
            return None
        return Identity(
            employee_id=int(user["employee_id"]),
            email=user["email"],
            company_id=int(user.get("company_id", 1)),
            access_role=user.get("access_role", "employee"),
            role_code=(user.get("role_code") or "ALL").upper(),
            manager_id=user.get("manager_id"),
        )
    return None


def directory() -> List[Dict]:
    """Everyone, from the credential store if seeded, else the built-in list."""
    return _load_users() or DIRECTORY


def reports_of(employee_id: int, *, direct_only: bool = False) -> List[Dict]:
    """
    Everyone below this person in the reporting chain.

    Walks breadth-first rather than recursing, and tracks who has been seen: manager_id
    is a self-referencing foreign key with nothing stopping a cycle, and a malformed org
    chart should return an odd team rather than hang the server.
    """
    people = directory()
    by_manager: Dict[Optional[int], List[Dict]] = {}
    for person in people:
        by_manager.setdefault(person.get("manager_id"), []).append(person)

    direct = by_manager.get(employee_id, [])
    if direct_only:
        return list(direct)

    out: List[Dict] = []
    seen = {employee_id}
    queue = list(direct)
    while queue:
        person = queue.pop(0)
        if person["employee_id"] in seen:
            continue
        seen.add(person["employee_id"])
        out.append(person)
        queue.extend(by_manager.get(person["employee_id"], []))
    return out


# ---------------------------------------------------------------------------
# tokens — claim-for-claim identical to api/shared/auth.py
# ---------------------------------------------------------------------------


def _secret() -> bytes:
    env = os.getenv("JWT_SIGNING_SECRET") or os.getenv("QUIZGEN_SESSION_KEY")
    if env:
        return env.encode()
    if SESSION_KEY_FILE.exists():
        return SESSION_KEY_FILE.read_bytes().strip()
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48).encode()
    SESSION_KEY_FILE.write_bytes(key)
    try:
        SESSION_KEY_FILE.chmod(0o600)
    except OSError:
        pass
    return key


def create_token(identity: Identity) -> str:
    import jwt

    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(identity.employee_id),
            "email": identity.email,
            "company_id": identity.company_id,
            "access_role": identity.access_role,
            "role_code": identity.role_code,
            "manager_id": identity.manager_id,
            "iat": now,
            "exp": now + timedelta(hours=TOKEN_TTL_HOURS),
        },
        _secret(),
        algorithm=_ALGO,
    )


def decode_token(token: str) -> Optional[Identity]:
    import jwt

    if not token:
        return None
    try:
        # algorithms is not optional: without it a caller can present "alg": "none" and
        # have an unsigned token accepted.
        claims = jwt.decode(token, _secret(), algorithms=[_ALGO])
    except Exception:
        return None
    try:
        manager_id = claims.get("manager_id")
        return Identity(
            employee_id=int(claims["sub"]),
            email=claims["email"],
            company_id=int(claims.get("company_id", 1)),
            access_role=claims.get("access_role"),
            role_code=(claims.get("role_code") or "ALL").upper(),
            manager_id=int(manager_id) if manager_id is not None else None,
        )
    except (KeyError, ValueError, TypeError):
        return None


def identity_from_header(authorization: str) -> Optional[Identity]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return decode_token(authorization[len("Bearer "):].strip())
