"""
Authentication: who is calling, and what may they do.

Replaces the `x-learner-id` header stub. That header let the client declare its own
identity and role, which meant an employee could read another role's material by editing
one request — the team knew and parked it (`App.jsx`: "the team has parked role
verification until Entra"). This module unparks it.

WHAT THIS IS AND IS NOT
-----------------------
This is a credential provider behind an interface, with one implementation that reads a
local JSON file. It is deliberately NOT Entra. Entra is the intended end state — every
comment in the repo says so — but it needs a tenant, an app registration, and an OIDC
flow, none of which exist yet, and none of which are needed to stop the client asserting
its own role.

The interface is the point. `CredentialProvider` has two methods. An `EntraProvider`
implementing the same two methods drops in without any caller changing, because callers
only ever see a `Principal`.

WHY PBKDF2 AND NOT BCRYPT
-------------------------
bcrypt and argon2 are better password hashes. Both need a new pinned dependency, and
`requirements.txt` is installed verbatim by `tests.yml` on every push. PBKDF2-HMAC-SHA256
is in the standard library, is a legitimate password KDF (NIST SP 800-132), and at 480k
iterations costs an attacker roughly what bcrypt does. When a dependency change is
acceptable, swap `_hash_password`/`_verify_password` — nothing else touches the format.

WHAT THIS DOES NOT PROTECT AGAINST
----------------------------------
- The dev server speaks HTTP. A token on the wire is readable by anything on the path.
  That is fine on localhost and unacceptable anywhere else; the deployed API is HTTPS.
- There is no rate limiting, so this is brute-forceable given enough requests. PBKDF2's
  cost makes that slow rather than impossible.
- Tokens cannot be revoked before they expire. Signing out drops the client's copy; it
  does not invalidate the token server-side. A revocation list needs shared state that
  the single-process dev server does not have.

None of these are reasons to keep trusting a client-supplied header, which has no cost
to forge at all.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Protocol

# Where the credential store and signing key live. Outside the repo tree by name so a
# stray `git add -A` cannot pick them up, and gitignored as well — belt and braces,
# because this repository is public.
AUTH_DIR = Path(os.getenv("QUIZGEN_AUTH_DIR", ".auth"))
USERS_FILE = AUTH_DIR / "users.json"
SESSION_KEY_FILE = AUTH_DIR / "session_key"

# OWASP's floor for PBKDF2-HMAC-SHA256 at the time of writing. Raising this is safe:
# the iteration count is stored per-hash, so old hashes keep verifying.
PBKDF2_ITERATIONS = 480_000

TOKEN_TTL_HOURS = int(os.getenv("QUIZGEN_TOKEN_TTL_HOURS", "12"))


# ---------------------------------------------------------------------------
# Principal — what a caller is, once proven
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """
    An authenticated caller.

    `role_code` is the training role — which material this person is served (SDE2,
    SWE_MANAGER, ALL). `access_role` is the permission tier from
    `006_create_org_structure.sql` — what this person may DO (employee, manager,
    director, admin, executive). They are different questions and the schema keeps them
    in different columns; conflating them is how a senior engineer accidentally gets
    manager permissions because their title contains the word "lead".
    """

    username: str
    employee_id: int
    name: str
    email: str
    role_code: str = "ALL"
    access_role: str = "employee"
    company_id: int = 1
    manager_id: Optional[int] = None

    # Permission tiers, least to most. Comparing by index means "manager or above"
    # is one expression rather than a set literal repeated at every call site.
    _TIERS = ("employee", "manager", "director", "admin", "executive")

    def at_least(self, tier: str) -> bool:
        """True if this principal's access_role is `tier` or higher."""
        try:
            return self._TIERS.index(self.access_role) >= self._TIERS.index(tier)
        except ValueError:
            # An unrecognised tier is not a reason to grant access.
            return False

    def to_public(self) -> dict:
        """The shape sent to the browser. No hash, no token, no internal fields."""
        d = asdict(self)
        d.pop("_TIERS", None)
        return d


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """
    Hash a password for storage.

    Format: `pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`. The iteration count
    travels with the hash so it can be raised later without invalidating existing users.
    """
    if not password:
        raise ValueError("Refusing to hash an empty password.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """
    Check a password against a stored hash.

    Uses `hmac.compare_digest` rather than `==` so the comparison does not leak, through
    timing, how many leading bytes were correct.
    """
    try:
        algorithm, iterations, salt_b64, hash_b64 = stored.split("$")
    except (ValueError, AttributeError):
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    try:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(salt_b64),
            int(iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, base64.b64decode(hash_b64))


# ---------------------------------------------------------------------------
# Credential providers
# ---------------------------------------------------------------------------


class CredentialProvider(Protocol):
    """
    The seam that keeps Entra reachable.

    Two methods. An Entra implementation validates an OIDC token in `authenticate` and
    looks the employee up by email claim in `lookup`; callers never learn the difference
    because both return a `Principal`.
    """

    def authenticate(self, username: str, password: str) -> Optional[Principal]:
        """Return a Principal if the credentials are valid, else None."""
        ...

    def lookup(self, username: str) -> Optional[Principal]:
        """Return a Principal by username without checking a password."""
        ...


class JsonCredentialProvider:
    """
    Reads users from a gitignored JSON file.

    File shape:

        {
          "users": [
            {
              "username": "amara@quadrant.example",
              "password_hash": "pbkdf2_sha256$480000$...$...",
              "employee_id": 1,
              "name": "Amara Osei",
              "email": "amara@quadrant.example",
              "role_code": "SDE2",
              "access_role": "employee",
              "company_id": 1,
              "manager_id": 4
            }
          ]
        }

    Written and read by `scripts/manage_users.py`. Never edited by hand in practice,
    because a hand-written entry means someone typed a plaintext password somewhere.
    """

    def __init__(self, path: Path = USERS_FILE):
        self.path = Path(path)

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "{} is not valid JSON ({}). Recreate it with:\n"
                "  python scripts/manage_users.py seed-demo".format(self.path, exc)
            ) from exc
        return data.get("users", [])

    @staticmethod
    def _to_principal(record: dict) -> Principal:
        return Principal(
            username=record["username"],
            employee_id=int(record.get("employee_id", 0)),
            name=record.get("name", record["username"]),
            email=record.get("email", record["username"]),
            role_code=(record.get("role_code") or "ALL").upper(),
            access_role=(record.get("access_role") or "employee").lower(),
            company_id=int(record.get("company_id", 1)),
            manager_id=record.get("manager_id"),
        )

    def authenticate(self, username: str, password: str) -> Optional[Principal]:
        username = (username or "").strip().lower()
        for record in self._load():
            if record.get("username", "").strip().lower() != username:
                continue
            if verify_password(password or "", record.get("password_hash", "")):
                return self._to_principal(record)
            # Matching user, wrong password. Fall through to the same None as an
            # unknown user — telling the caller which of the two it was hands an
            # attacker a list of valid usernames.
            return None
        return None

    def lookup(self, username: str) -> Optional[Principal]:
        username = (username or "").strip().lower()
        for record in self._load():
            if record.get("username", "").strip().lower() == username:
                return self._to_principal(record)
        return None


def get_provider() -> CredentialProvider:
    """
    The active credential provider.

    One place to change when Entra arrives — every caller goes through here rather than
    constructing a provider itself.
    """
    return JsonCredentialProvider()


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------


def _session_key() -> bytes:
    """
    The key used to sign session tokens.

    Read from QUIZGEN_SESSION_KEY if set (how the deployed app should supply it, from
    Key Vault). Otherwise generated once into `.auth/session_key`, which is gitignored.

    Generating rather than hard-coding a default matters: a committed default key means
    anyone with the repo can mint a valid token for any user, and this repository is
    public.
    """
    env = os.getenv("QUIZGEN_SESSION_KEY")
    if env:
        return env.encode("utf-8")

    if SESSION_KEY_FILE.exists():
        return SESSION_KEY_FILE.read_bytes().strip()

    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48).encode("ascii")
    SESSION_KEY_FILE.write_bytes(key)
    try:
        SESSION_KEY_FILE.chmod(0o600)
    except OSError:
        # Windows and some mounts refuse chmod. The gitignore entry still applies.
        pass
    return key


def issue_token(principal: Principal, *, ttl_hours: int = TOKEN_TTL_HOURS) -> str:
    """
    Mint a signed session token.

    Claims carry the whole principal so the server does not re-read the credential file
    on every request. That is a deliberate trade: a role change does not take effect
    until the token expires. With a 12-hour TTL that is acceptable; if it stops being
    acceptable, look the principal up per-request instead of trusting the claims.
    """
    import jwt  # PyJWT, already pinned in requirements.txt

    now = datetime.now(timezone.utc)
    claims = {
        "sub": principal.username,
        "employee_id": principal.employee_id,
        "name": principal.name,
        "email": principal.email,
        "role_code": principal.role_code,
        "access_role": principal.access_role,
        "company_id": principal.company_id,
        "manager_id": principal.manager_id,
        "iat": now,
        "exp": now + timedelta(hours=ttl_hours),
    }
    return jwt.encode(claims, _session_key(), algorithm="HS256")


def principal_from_token(token: str) -> Optional[Principal]:
    """
    Verify a token and rebuild the principal, or return None.

    `algorithms=["HS256"]` is not optional: omitting it lets a caller present a token
    with `"alg": "none"` and have it accepted unsigned.
    """
    import jwt

    if not token:
        return None
    try:
        claims = jwt.decode(token, _session_key(), algorithms=["HS256"])
    except Exception:
        # Expired, wrong signature, malformed — all mean "not authenticated", and
        # distinguishing them for the caller only helps someone probing the endpoint.
        return None

    return Principal(
        username=claims.get("sub", ""),
        employee_id=int(claims.get("employee_id", 0)),
        name=claims.get("name", ""),
        email=claims.get("email", ""),
        role_code=(claims.get("role_code") or "ALL").upper(),
        access_role=(claims.get("access_role") or "employee").lower(),
        company_id=int(claims.get("company_id", 1)),
        manager_id=claims.get("manager_id"),
    )


def principal_from_header(authorization: str) -> Optional[Principal]:
    """Parse an `Authorization: Bearer <token>` header into a Principal, or None."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return principal_from_token(parts[1].strip())
