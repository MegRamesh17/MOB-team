#!/usr/bin/env bash
#
# One command instead of five: get real (if fictional) training content into Azure SQL
# so /trainings stops returning an empty list.
#
# Chains together commands that already exist and already work individually:
#   1. make_sample_pdfs.py  -- writes 3 sample PDFs to data/documents/, if not already there
#   2. quizgen ingest        -- extracts + chunks them (role_scope ALL, company-wide)
#   3. quizgen generate       -- mock provider by default: free, deterministic, no API key
#   4. quizgen review         -- --approve-all: fine for a demo, NOT for real company docs
#   5. quizgen push           -- copies the bank into Azure SQL (idempotent)
#
# Deliberately does NOT run scripts/set_passwords.py. That script prints generated
# passwords to your terminal exactly once and is meant to be run attended, by a human,
# so it stays a separate manual step -- see its own docstring for why.
#
# Needs, same as set_passwords.py: SQL_PASSWORD set, and a SQL firewall rule open for
# your IP (az sql server firewall-rule create ...).
#
# Set QUIZGEN_PROVIDER=<real provider name> before running this to use the real model
# instead of mock -- costs roughly a cent per question, and needs the model's API key
# configured the same way the rest of quizgen expects.

set -euo pipefail
cd "$(dirname "$0")/.."

if [ -z "${SQL_PASSWORD:-}" ]; then
  echo "SQL_PASSWORD is not set. Same requirement as scripts/set_passwords.py:" >&2
  echo "  export SQL_PASSWORD='<the sql admin password>'" >&2
  echo "Your IP also needs a SQL firewall rule before this can connect." >&2
  exit 1
fi

export PYTHONPATH="src"
export QUIZGEN_PROVIDER="${QUIZGEN_PROVIDER:-mock}"

echo "== Provider: $QUIZGEN_PROVIDER =="
if [ "$QUIZGEN_PROVIDER" != "mock" ]; then
  echo "   (real provider -- this will spend money, roughly 1 cent per question)"
fi
echo

if [ -z "$(ls -A data/documents 2>/dev/null || true)" ]; then
  echo "== 1/5: Writing sample documents (data/documents/ is empty) =="
  python scripts/make_sample_pdfs.py
else
  echo "== 1/5: data/documents/ already has files -- skipping make_sample_pdfs.py =="
fi
echo

echo "== 2/5: Ingesting documents (role_scope ALL) =="
python -m quizgen.cli ingest --source local --role-scope ALL
echo

echo "== 3/5: Generating candidate questions =="
python -m quizgen.cli generate
echo

echo "== 4/5: Approving all pending questions (demo content -- not a substitute for real review) =="
python -m quizgen.cli review --approve-all
echo

echo "== 5/5: Pushing to Azure SQL =="
python -m quizgen.cli push
echo

echo "Done. /trainings should now return real content for role_scope ALL."
echo "Passwords are still a separate step: python scripts/set_passwords.py --all --generate"
