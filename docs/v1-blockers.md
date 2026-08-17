# What is blocking v1, and who owns it

Everything below is someone else's file. Nothing here can be unblocked from the
`quizgen`/frontend side, which is why it is a list of asks rather than a list of work.

Sorted by what unblocks the most people.

---

## 1. Seed data into Azure SQL — owner: Megha

`migrate-database.yml` applies schema and stops. `db/seed/org_seed.sql` and
`seed_data.sql` have never run against Azure SQL, so `Companies`, `Departments`, `Teams`,
`Roles`, and `Employees` are all empty.

**This blocks almost everything else.** With no `Employees` rows:

- there is nobody to give a `password_hash` to, so the auth backfill cannot start
- `login.py`'s query returns no row, so sign-in cannot be tested even once the signing
  secret exists
- no role can be resolved, so role-scoped training cannot be demonstrated

Both files are plain `INSERT` with no `IF NOT EXISTS` or `MERGE` guard, so a second run
duplicates every row. `migrate-database.yml`'s `SchemaMigrations` pattern solves exactly
this for migrations and would work for seeds — a `SeedRuns` table, or a `WHERE NOT EXISTS`
guard on each insert.

Worth deciding: whether seeding runs automatically after migrations, or manually via
`workflow_dispatch`. Seeding is a one-time bootstrap, not a per-deploy step.

---

## 2. `JWT_SIGNING_SECRET` — owner: Megha (infra)

`api/shared/auth.py` raises `RuntimeError("JWT_SIGNING_SECRET is not set")` when the
variable is absent, which is correct behaviour. Nothing sets it:

- `infra/modules/keyvault/main.tf` defines `sql-connection-string`, `openai-api-key`,
  `sql-password`. No `jwt-signing-secret`.
- `infra/modules/functions/main.tf` `app_settings` has no `JWT_SIGNING_SECRET`.

So `POST /login` returns 500 on every call. The fix follows the `SQL_PASSWORD` pattern
already in that file:

```hcl
"JWT_SIGNING_SECRET" = "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/jwt-signing-secret/)"
```

The secret must be **stable** — regenerating it on each `terraform apply` signs every
user out on every deploy.

---

## 3. Deploy `api/` and `web-app/` — owner: Megha / Track G

No workflow deploys the Function App. `terraform.yml`, `migrate-database.yml` and
`tests.yml` are the three that exist; `tests.yml` builds the frontend purely to catch
syntax errors and discards the artefact.

`api/auth/login.py` exists in git and does not exist in Azure. Blockers 1 and 2 cannot be
verified until this lands.

Track G notes an open team decision — Azure DevOps pipelines versus the existing GitHub
Actions — and flags that running both against the same resource risks conflicting
deployments. That decision is upstream of this work, not part of it.

---

## 4. Two claims missing from the auth token — owner: sureshalampur

`create_token()` carries `access_role` (the permission tier) but not:

- **`role_code`** — the *training* role (SDE2, SWE_MANAGER, ALL). Every chunk carries a
  `role_scope`; serving the right training means filtering on it, and the value to filter
  by is the caller's training role, not their permission tier. Without the claim, every
  training endpoint re-queries the database for something already known at login.
- **`manager_id`** — needed for the manager's team view, for scoping which roles a
  manager may upload for, and for Q Score visibility.

The join is already in `login.py`'s query; it needs `r.title` added to the SELECT and one
line in the payload. Default `role_code` to `"ALL"` when unmapped — that serves
company-wide training only, so a missing role under-serves rather than leaking.

Do not derive one role from the other: an employee whose title contains "lead" is not a
manager, and a `SWE_MANAGER` training role does not by itself grant `manager` access.

Full detail in `docs/auth-integration-notes.md`.

---

## 5. Nobody has a password — owner: sureshalampur

`012_add_auth.sql` adds `password_hash` nullable and nothing ever writes one.
`login.py:114` returns 401 when it is `NULL`, which is every row. The migration comment
already anticipates this ("backfill via a one-off admin script, or an interactive set
your password first-login flow") — it just has not been written.

Depends on blocker 1: there are no employees to give passwords to yet.

---

## 6. Nothing is actually protected — owner: sureshalampur

`get_current_employee()` is defined in `api/shared/auth.py` and called nowhere.
`function_app.py` still resolves identity through `_caller_id()` at lines 182, 268, 376
and 559.

`_caller_id()` falls back to the `x-learner-id` header and then to the literal string
`"demo-learner"`. That fallback is the part that matters: a client sending no header does
not get an error, it silently becomes `demo-learner`. Every user would share one identity
and one quiz history, and nothing would report a problem.

Document upload and role editing additionally want `require_manager()` — both change what
an entire role is taught and certified against.

---

## 7. Q Score means two things — owner: whoever owns `add-certificates`

`_calculate_q_score()` on that branch computes a per-attempt performance score. This
project's Q Score is a per-employee compliance rollup. Both were being built under one
name, so "Q Score 82" was ambiguous between "82% compliant" and "scored 82 on one quiz".

Resolved in `docs/q-score.md`, which is now authoritative: they become two named levels,
and the per-attempt score feeds the rollup. Three changes needed on that branch:

1. Rename `_calculate_q_score` → `_calculate_attempt_score`, and the column to
   `attempt_score`
2. Weight questions by difficulty in both numerator and denominator, rather than averaging
   the multiplier over correct answers only (today, getting fewer easy questions right
   raises your multiplier)
3. Drop the consistency factor — a topic at 51% costs nothing, at 49% it costs a flat 8%,
   and the adaptive engine already concentrates quizzes on weak topics

`014_create_certificates.sql` also needs a `category` column for the
behavioural/technical split.

---

## Not blocked any more

**Shyam's isolation work.** `docs/company-isolation-gap.md` asked whoever owns
`search_index.py` to add a `company_id` field. Done — `Chunk.company_id`, a filterable
index field, `upload()` validating through `isolation.validate_company_id`, and
`retrieve()` taking `company_id` positionally so it cannot be called unscoped.
`isolation.py` itself is untouched, only imported. 13 tests in `tests/test_isolation.py`.

Still separate work, as that document says: Azure SQL needs the same `company_id`
filtering, and blob containers should be company-scoped before a second company's
documents are ingested.
