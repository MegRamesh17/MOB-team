#!/usr/bin/env python3
"""
Set bcrypt password hashes on Employees, so people can actually sign in.

`012_add_auth.sql` adds `Employees.password_hash` as nullable and nothing ever writes
one. `api/auth/login.py` returns 401 when it is NULL — which is every row — so until this
runs, sign-in fails for everybody however correct the rest of the auth is. The migration
comment anticipated this ("backfill via a one-off admin script"); this is that script.

    # give every employee without a password a generated one, and print them once
    python scripts/set_passwords.py --all --generate

    # intentionally simple presentation credentials: intern01 -> password1, etc.
    python scripts/set_passwords.py --role-code INTERN --demo-intern-passwords

    # set one person's password, prompted for, never echoed
    python scripts/set_passwords.py --email ethan.brooks@quizrant.com

    # see who can and cannot sign in
    python scripts/set_passwords.py --status

WHAT THIS WILL NOT DO
Take a password on the command line. A `--password` flag lands the plaintext in your
shell history, in `ps` output, and — if anyone ever wires this into CI — in a workflow
log. Passwords are either prompted for or generated here and printed once.

The one intentionally insecure exception is `--demo-intern-passwords`. It is restricted
to the INTERN role and creates numbered presentation credentials only.

Generated passwords are printed to stdout exactly once and never stored. Redirecting
that output to a file in the repo is the one way to turn a safe script into a committed
credential, so don't.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import string
import sys

# Word-shaped rather than a raw token: these get typed by a human at a demo, and
# "correct-horse-42" survives that where "x9Kq2wZ" does not. Length carries the entropy.
_WORDS = (
    "amber anchor basil cedar cobalt copper dahlia ember falcon garnet harbor indigo "
    "jasper kelp lantern maple nectar onyx pewter quartz raven saffron tundra umber "
    "velvet willow yarrow zephyr"
).split()


def _generate_password() -> str:
    """
    Three distinct words plus five random alphanumerics.

    Entropy, actually counted rather than asserted: 28 words choose 3 without repetition
    is 28x27x26 = 19,656 (~14.3 bits), and 62^5 = 916 million (~29.8 bits), for ~44 bits
    total. The random tail carries most of it — three words from a 28-word list is only
    ~14 bits on its own, which is not enough for anything, and allowing repeats
    ("amber-amber-amber") would make it worse.

    These are bootstrap credentials. They are meant to be handed over once and changed,
    not to be a long-lived secret.
    """
    words = "-".join(secrets.SystemRandom().sample(_WORDS, 3))
    alphabet = string.ascii_letters + string.digits
    tail = "".join(secrets.choice(alphabet) for _ in range(5))
    return "{}-{}".format(words, tail)


def _demo_intern_password(email: str) -> str:
    match = re.fullmatch(r"intern(\d{2})@quizrant\.com", email.lower())
    if match is None:
        raise ValueError("unexpected demo intern account: {}".format(email))
    return "password{}".format(int(match.group(1)))


def _connection_string() -> str:
    """
    Mirrors api/auth/login.py's connection logic.

    Duplicated rather than imported for the reason that file already gives: api/ is a
    flat Functions package, not something importable from scripts/. If the SQL_* names
    or the driver ever change, both copies change.
    """
    conn = (os.getenv("SQL_CONNECTION_STRING") or "").strip()
    if conn and "driver=" in conn.lower():
        return conn

    server = os.getenv("SQL_SERVER", "mob-sql-server-02.database.windows.net")
    database = os.getenv("SQL_DATABASE", "mob-training-db")
    user = os.getenv("SQL_USER", "mobsqladmin")
    password = os.getenv("SQL_PASSWORD", "")

    if not password:
        raise SystemExit(
            "SQL_PASSWORD is not set.\n"
            "  Locally:  source scripts/load_env_from_vault.sh, or set it in .env\n"
            "  In CI:    pass the SQL_ADMIN_PASSWORD secret through as SQL_PASSWORD\n"
            "Your IP also needs a SQL firewall rule before this can connect."
        )

    return (
        "DRIVER={{ODBC Driver 18 for SQL Server}};"
        "SERVER=tcp:{},1433;DATABASE={};UID={};PWD={};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    ).format(server, database, user, password)


def _connect():
    try:
        import pyodbc
    except ImportError:
        raise SystemExit("pyodbc is not installed. pip install pyodbc")
    return pyodbc.connect(_connection_string())


def _hash(password: str) -> str:
    try:
        import bcrypt
    except ImportError:
        raise SystemExit(
            "bcrypt is not installed. pip install bcrypt\n"
            "(It is in api/requirements.txt because the Function App needs it; this "
            "script needs it for the same reason — the hash format has to match what "
            "login.py verifies with bcrypt.checkpw.)"
        )
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def cmd_status(conn) -> int:
    rows = conn.cursor().execute(
        """SELECT e.email, e.name, r.title, r.access_role, r.role_code,
                  CASE WHEN e.password_hash IS NULL THEN 0 ELSE 1 END AS has_password
             FROM dbo.Employees e
             LEFT JOIN dbo.Roles r ON r.id = e.role_id
            ORDER BY has_password, e.email"""
    ).fetchall()

    if not rows:
        print("No employees. Run the seed first — see .github/workflows/seed-database.yml.")
        return 1

    print("\n  {:<28} {:<30} {:<10} {:<14} {}".format(
        "EMAIL", "ROLE", "ACCESS", "TRAINING", "SIGN IN?"))
    for r in rows:
        print("  {:<28} {:<30} {:<10} {:<14} {}".format(
            r.email, (r.title or "—")[:30], r.access_role or "—",
            r.role_code or "ALL", "yes" if r.has_password else "NO"))

    without = sum(1 for r in rows if not r.has_password)
    print("\n  {} employee(s), {} cannot sign in.\n".format(len(rows), without))
    if without:
        print("  Fix with:  python scripts/set_passwords.py --all --generate\n")
    return 0


def cmd_set(conn, args) -> int:
    cursor = conn.cursor()

    if args.email:
        targets = cursor.execute(
            "SELECT email FROM dbo.Employees WHERE email = ?", args.email
        ).fetchall()
        if not targets:
            print("No employee with email {}".format(args.email), file=sys.stderr)
            return 1
    elif args.role_code:
        clause = (
            "" if (args.force or args.demo_intern_passwords)
            else " AND e.password_hash IS NULL"
        )
        targets = cursor.execute(
            "SELECT e.email FROM dbo.Employees e JOIN dbo.Roles r ON r.id = e.role_id "
            "WHERE r.role_code = ?" + clause + " ORDER BY e.email",
            args.role_code.upper(),
        ).fetchall()
        if not targets:
            print("No matching accounts need passwords for role {}.".format(
                args.role_code.upper()))
            return 0
    else:
        # --all means "everyone who cannot currently sign in", not "everyone". Resetting
        # a password someone is already using, as a side effect of running a backfill, is
        # a surprise nobody wants. --force is the way to say you meant it.
        clause = "" if args.force else " WHERE password_hash IS NULL"
        targets = cursor.execute(
            "SELECT email FROM dbo.Employees" + clause + " ORDER BY email").fetchall()
        if not targets:
            print("Every employee already has a password. Use --force to reset them anyway.")
            return 0

    emails = [t.email for t in targets]

    if args.demo_intern_passwords:
        assigned = {}
        for email in emails:
            try:
                assigned[email] = _demo_intern_password(email)
            except ValueError:
                print("Refusing demo password for unexpected account {}.".format(email),
                      file=sys.stderr)
                return 1
    elif args.generate:
        assigned = {e: _generate_password() for e in emails}
    else:
        if len(emails) > 1:
            print("Refusing to prompt once and apply it to {} accounts — that is a shared "
                  "password.\nUse --generate for a distinct one each, or --email for a "
                  "single account.".format(len(emails)), file=sys.stderr)
            return 1
        entered = getpass.getpass("Password for {}: ".format(emails[0]))
        if not entered:
            print("Refusing to set an empty password.", file=sys.stderr)
            return 1
        if entered != getpass.getpass("Confirm: "):
            print("Passwords did not match.", file=sys.stderr)
            return 1
        assigned = {emails[0]: entered}

    for email, password in assigned.items():
        cursor.execute(
            "UPDATE dbo.Employees SET password_hash = ? WHERE email = ?",
            _hash(password), email,
        )
    conn.commit()

    print("\nSet {} password(s).".format(len(assigned)))
    if args.generate or args.demo_intern_passwords:
        print("\n  {:<30} {}".format("EMAIL", "PASSWORD"))
        for email in sorted(assigned):
            print("  {:<30} {}".format(email, assigned[email]))
        if args.generate:
            print("\nPrinted once and not stored — only the bcrypt hash is in the database.")
            print("Do not redirect this into a file in the repo.\n")
        else:
            print("\nThese intentionally public credentials are for the presentation only.\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set bcrypt password hashes on Employees.")
    parser.add_argument("--status", action="store_true",
                        help="show who can and cannot sign in, then exit")
    parser.add_argument("--email", help="set the password for one employee")
    parser.add_argument("--all", action="store_true",
                        help="set passwords for everyone who has none")
    parser.add_argument("--role-code",
                        help="set passwords only for employees with this training role")
    parser.add_argument("--generate", action="store_true",
                        help="generate passwords and print them once, instead of prompting")
    parser.add_argument("--demo-intern-passwords", action="store_true",
                        help="set intern01..intern10 to password1..password10")
    parser.add_argument("--force", action="store_true",
                        help="with --all, also reset employees who already have a password")
    args = parser.parse_args()

    if args.demo_intern_passwords:
        if (args.role_code or "").upper() != "INTERN":
            parser.error("--demo-intern-passwords requires --role-code INTERN")
        if args.generate:
            parser.error("--demo-intern-passwords cannot be combined with --generate")

    if not (args.status or args.email or args.all or args.role_code):
        parser.print_help()
        return 1

    conn = _connect()
    try:
        if args.status:
            return cmd_status(conn)
        return cmd_set(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
