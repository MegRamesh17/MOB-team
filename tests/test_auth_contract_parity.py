"""
The dev server and the deployed API must implement the SAME auth contract.

`scripts/devauth.py` exists so the app can be signed into without Azure SQL. That is only
useful if it behaves identically to `api/shared/auth.py` — the moment they drift, local
testing stops predicting the demo, which is the exact failure PROJECT.md's "no backend
divergence" constraint is about.

These tests fail when someone changes one side and not the other. That is the point: the
comment in devauth.py saying "if that file changes, this changes" is a request, and a
request is not a mechanism.

Not covered here, deliberately: the credential STORES differ on purpose (a gitignored
JSON file locally, Employees.password_hash deployed) and so do the hash formats (PBKDF2
vs bcrypt). No hash ever crosses between them, so those need not match. What must match is
everything the frontend and the token touch.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A 64-char key, matching what random_password generates in infra/main.tf. Shorter keys
# make PyJWT warn, and a suite that prints warnings teaches people to ignore them.
os.environ.setdefault("JWT_SIGNING_SECRET", "parity-test-key-" + "z" * 48)


def _load_deployed_auth():
    """
    Import api/shared/auth.py with the Functions runtime stubbed.

    Attaches `functions` to the real azure namespace package rather than replacing it —
    replacing it breaks azure.core / azure.search for every test that runs afterwards.
    """
    if "azure.functions" not in sys.modules:
        try:
            import azure as az
        except ImportError:
            az = types.ModuleType("azure")
            sys.modules["azure"] = az
        fn = types.ModuleType("azure.functions")
        fn.HttpRequest = object

        class HttpResponse:
            def __init__(self, *a, **k):
                pass

        fn.HttpResponse = HttpResponse
        sys.modules["azure.functions"] = fn
        az.functions = fn

    spec = importlib.util.spec_from_file_location(
        "deployed_shared_auth", ROOT / "api" / "shared" / "auth.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["deployed_shared_auth"] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT / "scripts"))
import devauth  # noqa: E402

deployed = _load_deployed_auth()

PERSON = dict(
    employee_id=1,
    email="dana.whitfield@demo.com",
    company_id=1,
    access_role="manager",
    name="Dana Whitfield",
    role_code="SWE_MANAGER",
    manager_id=None,
    department="Software Development",
    title="Software Engineering Manager",
)


def _decode(token):
    import jwt
    return jwt.decode(token, os.environ["JWT_SIGNING_SECRET"], algorithms=["HS256"])


def _local_token():
    return devauth.create_token(devauth.Identity(
        employee_id=PERSON["employee_id"], email=PERSON["email"],
        company_id=PERSON["company_id"], access_role=PERSON["access_role"],
        name=PERSON["name"], role_code=PERSON["role_code"],
        manager_id=PERSON["manager_id"], department=PERSON["department"],
        title=PERSON["title"]))


def _deployed_token():
    return deployed.create_token(
        PERSON["employee_id"], PERSON["email"], PERSON["company_id"],
        PERSON["access_role"], PERSON["role_code"], PERSON["manager_id"],
        PERSON["name"], PERSON["department"], PERSON["title"])


class TestTokenParity(unittest.TestCase):
    def test_same_claim_names(self):
        self.assertEqual(sorted(_decode(_local_token())), sorted(_decode(_deployed_token())))

    def test_same_claim_values(self):
        local, dep = _decode(_local_token()), _decode(_deployed_token())
        for claim in sorted(set(local) - {"iat", "exp"}):
            with self.subTest(claim=claim):
                self.assertEqual(local[claim], dep[claim])

    def test_employee_id_is_a_string_in_sub_on_both(self):
        # A JWT `sub` is conventionally a string, and one side emitting an int would make
        # int(claims["sub"]) work by luck on one and by type coercion on the other.
        for name, token in (("local", _local_token()), ("deployed", _deployed_token())):
            with self.subTest(side=name):
                self.assertIsInstance(_decode(token)["sub"], str)

    def test_same_algorithm_and_ttl(self):
        self.assertEqual(devauth._ALGO, deployed._ALGO)
        self.assertEqual(devauth.TOKEN_TTL_HOURS, deployed.TOKEN_TTL_HOURS)

    def test_each_side_accepts_the_others_token(self):
        # The strongest form of the check: same secret, same algorithm, same claim shape,
        # so a token minted by either decodes to the same identity in the other.
        from_local = deployed.decode_token(_local_token())
        from_deployed = devauth.decode_token(_deployed_token())
        for identity in (from_local, from_deployed):
            self.assertIsNotNone(identity)
            self.assertEqual(identity.employee_id, PERSON["employee_id"])
            self.assertEqual(identity.email, PERSON["email"])
            self.assertEqual(identity.role_code, PERSON["role_code"])
            self.assertEqual(identity.access_role, PERSON["access_role"])
            self.assertEqual(identity.department, PERSON["department"])
            self.assertEqual(identity.title, PERSON["title"])


class TestPrincipalParity(unittest.TestCase):
    """The shape web-app/ reads. A field on one side only is a frontend bug waiting."""

    def test_same_principal_fields(self):
        local = devauth.Identity(**PERSON).to_public()
        dep = deployed.decode_token(_deployed_token())
        deployed_shape = {
            "employee_id": dep.employee_id,
            "email": dep.email,
            "company_id": dep.company_id,
            "access_role": dep.access_role,
            "name": dep.name,
            "role_code": dep.role_code,
            "manager_id": dep.manager_id,
            "department": dep.department,
            "title": dep.title,
        }
        self.assertEqual(sorted(local), sorted(deployed_shape))
        self.assertEqual(local, deployed_shape)


class TestRejectionParity(unittest.TestCase):
    """Both sides must refuse the same things, in the same way."""

    def test_both_reject_a_tampered_token(self):
        bad = _local_token()[:-3] + "xyz"
        self.assertIsNone(devauth.decode_token(bad))
        self.assertIsNone(deployed.decode_token(bad))

    def test_both_reject_alg_none(self):
        forged = "eyJhbGciOiJub25lIn0.eyJzdWIiOiIxIn0."
        self.assertIsNone(devauth.decode_token(forged))
        self.assertIsNone(deployed.decode_token(forged))

    def test_both_reject_empty_and_malformed(self):
        for value in ("", "not-a-token", "a.b.c"):
            with self.subTest(value=value):
                self.assertIsNone(devauth.decode_token(value))
                self.assertIsNone(deployed.decode_token(value))

    def test_both_default_a_missing_role_code_to_ALL(self):
        # The safe direction: company-wide material only, so an unmapped role
        # under-serves rather than leaking another role's training.
        import jwt
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        legacy = jwt.encode(
            {"sub": "3", "email": "e@demo.com", "company_id": 1, "access_role": "employee",
             "iat": now, "exp": now + timedelta(hours=1)},
            os.environ["JWT_SIGNING_SECRET"], algorithm="HS256")
        self.assertEqual(devauth.decode_token(legacy).role_code, "ALL")
        self.assertEqual(deployed.decode_token(legacy).role_code, "ALL")


class TestPermissionTierParity(unittest.TestCase):
    def test_same_tiers_pass_and_fail(self):
        cases = [("employee", False), ("manager", True), ("director", True),
                 ("admin", True), ("executive", True), ("wizard", False), (None, False)]
        for tier, expected in cases:
            with self.subTest(tier=tier):
                local = devauth.Identity(
                    employee_id=1, email="x@d.com", company_id=1, access_role=tier)
                self.assertEqual(local.at_least("manager"), expected)

                dep = deployed.Identity(
                    employee_id=1, email="x@d.com", company_id=1, access_role=tier)
                # require_manager returns an error response to send, or None to proceed.
                self.assertEqual(deployed.require_manager(dep) is None, expected)


if __name__ == "__main__":
    unittest.main()
