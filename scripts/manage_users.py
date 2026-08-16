#!/usr/bin/env python3
"""
Create and manage sign-in credentials.

The credential file it writes (`.auth/users.json`) is gitignored and holds only PBKDF2
hashes — no plaintext password is ever stored. This script is the only supported way to
create a user, precisely so nobody hand-edits a password into a file.

Quick start — eight demo accounts matching `db/seed/seed_data.sql`:

    python scripts/manage_users.py seed-demo

That prints one shared password for all demo accounts. Sign in as different people to
watch role scoping and manager permissions diverge against the same question bank.

Other commands:

    python scripts/manage_users.py list
    python scripts/manage_users.py add --username you@example.com --name "Your Name" \\
        --role-code SDE2 --access-role employee --employee-id 3
    python scripts/manage_users.py passwd --username you@example.com
"""

from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quizgen.auth import AUTH_DIR, USERS_FILE, hash_password  # noqa: E402

# Mirrors db/seed/seed_data.sql. employee_id is the IDENTITY value each row gets from
# that file's insert order, and manager_id matches the same file — so a principal from
# the JSON store lines up with the Employees row it represents once the seed has run.
#
# role_code maps the seed's job titles onto the role codes quizgen actually scopes
# material by (src/quizgen/registry.py and roles.py). Sales roles have no engineering
# material, so they get ALL — company-wide training only.
DEMO_USERS = [
    dict(username="dana.whitfield@demo.com", name="Dana Whitfield", employee_id=1,
         role_code="SWE_MANAGER", access_role="manager", manager_id=None),
    dict(username="priya.n@demo.com", name="Priya Nandakumar", employee_id=2,
         role_code="MANAGER", access_role="director", manager_id=None),
    dict(username="ethan.brooks@demo.com", name="Ethan Brooks", employee_id=3,
         role_code="SDE2", access_role="employee", manager_id=1),
    dict(username="maya.osei@demo.com", name="Maya Osei", employee_id=4,
         role_code="SDE1", access_role="employee", manager_id=1),
    dict(username="liam.chen@demo.com", name="Liam Chen", employee_id=5,
         role_code="SEC_ANALYST", access_role="employee", manager_id=1),
    dict(username="sofia.delgado@demo.com", name="Sofia Delgado", employee_id=6,
         role_code="ALL", access_role="employee", manager_id=2),
    dict(username="noah.whitaker@demo.com", name="Noah Whitaker", employee_id=7,
         role_code="ALL", access_role="employee", manager_id=2),
    dict(username="ava.thompson@demo.com", name="Ava Thompson", employee_id=8,
         role_code="ALL", access_role="employee", manager_id=2),
]


def _load() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {"users": []}


def _save(data: dict) -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        USERS_FILE.chmod(0o600)
    except OSError:
        pass  # Windows and some mounts refuse chmod; the gitignore entry still applies.


def cmd_seed_demo(args: argparse.Namespace) -> int:
    password = args.password or secrets.token_urlsafe(9)
    data = _load()
    existing = {u["username"].lower() for u in data["users"]}

    added = 0
    for spec in DEMO_USERS:
        if spec["username"].lower() in existing and not args.force:
            continue
        data["users"] = [u for u in data["users"]
                         if u["username"].lower() != spec["username"].lower()]
        data["users"].append({
            **spec,
            "email": spec["username"],
            "company_id": 1,
            "password_hash": hash_password(password),
        })
        added += 1

    _save(data)

    if added == 0:
        print("All demo users already exist. Re-create them with --force.")
        return 0

    print("\nCreated {} demo accounts in {}\n".format(added, USERS_FILE))
    print("  Password for all of them:  {}\n".format(password))
    print("  {:<28} {:<18} {:<10} {}".format("USERNAME", "ROLE", "ACCESS", "NAME"))
    for spec in DEMO_USERS:
        print("  {:<28} {:<18} {:<10} {}".format(
            spec["username"], spec["role_code"], spec["access_role"], spec["name"]))
    print("\nThis password is not stored anywhere — only its hash. Note it now.")
    print("Reset one with:  python scripts/manage_users.py passwd --username <user>\n")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("Password for {}: ".format(args.username))
    if not password:
        print("Refusing to create a user with an empty password.", file=sys.stderr)
        return 1

    data = _load()
    if any(u["username"].lower() == args.username.lower() for u in data["users"]):
        print("{} already exists. Change the password with `passwd`.".format(args.username),
              file=sys.stderr)
        return 1

    data["users"].append({
        "username": args.username,
        "email": args.email or args.username,
        "name": args.name or args.username,
        "employee_id": args.employee_id,
        "role_code": args.role_code.upper(),
        "access_role": args.access_role.lower(),
        "company_id": args.company_id,
        "manager_id": args.manager_id,
        "password_hash": hash_password(password),
    })
    _save(data)
    print("Added {} ({} / {}).".format(args.username, args.role_code, args.access_role))
    return 0


def cmd_passwd(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("New password for {}: ".format(args.username))
    if not password:
        print("Refusing to set an empty password.", file=sys.stderr)
        return 1

    data = _load()
    for user in data["users"]:
        if user["username"].lower() == args.username.lower():
            user["password_hash"] = hash_password(password)
            _save(data)
            print("Password updated for {}.".format(args.username))
            return 0

    print("No such user: {}".format(args.username), file=sys.stderr)
    return 1


def cmd_list(args: argparse.Namespace) -> int:
    data = _load()
    if not data["users"]:
        print("No users yet. Create the demo set with:")
        print("  python scripts/manage_users.py seed-demo")
        return 0

    print("\n  {:<28} {:<18} {:<10} {:<6} {}".format(
        "USERNAME", "ROLE", "ACCESS", "EMP", "NAME"))
    for user in sorted(data["users"], key=lambda u: u["username"]):
        print("  {:<28} {:<18} {:<10} {:<6} {}".format(
            user["username"],
            user.get("role_code", "ALL"),
            user.get("access_role", "employee"),
            user.get("employee_id", "-"),
            user.get("name", ""),
        ))
    print("\n  {} user(s) in {}\n".format(len(data["users"]), USERS_FILE))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage sign-in credentials (hashed, gitignored).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("seed-demo", help="create demo accounts matching db/seed/seed_data.sql")
    p.add_argument("--password", help="use this password instead of generating one")
    p.add_argument("--force", action="store_true", help="recreate users that already exist")
    p.set_defaults(func=cmd_seed_demo)

    p = sub.add_parser("add", help="add one user")
    p.add_argument("--username", required=True)
    p.add_argument("--name")
    p.add_argument("--email")
    p.add_argument("--password", help="prompted for if omitted")
    p.add_argument("--employee-id", type=int, default=0)
    p.add_argument("--role-code", default="ALL", help="training role, e.g. SDE2")
    p.add_argument("--access-role", default="employee",
                   choices=["employee", "manager", "director", "admin", "executive"])
    p.add_argument("--company-id", type=int, default=1)
    p.add_argument("--manager-id", type=int, default=None)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("passwd", help="change a user's password")
    p.add_argument("--username", required=True)
    p.add_argument("--password", help="prompted for if omitted")
    p.set_defaults(func=cmd_passwd)

    p = sub.add_parser("list", help="list users (never shows hashes)")
    p.set_defaults(func=cmd_list)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
