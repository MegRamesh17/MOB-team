# v1 blockers

All six blockers are closed on `ai-retry`. What remains is running things against
Azure, which needs credentials this branch does not have.

---

## Closed

| Blocker | How |
|---|---|
| `JWT_SIGNING_SECRET` unprovisioned — `/login` 500s | `random_password` at the Terraform root → `jwt-signing-secret` in Key Vault → `JWT_SIGNING_SECRET` app setting, following the `SQL_PASSWORD` pattern |
| `api/` never deployed | `.github/workflows/deploy-backend.yml`, from `ai-retry`, with a health check that fails the run |
| Seed data never loaded | `db/seed/*.sql` rewritten idempotent; `.github/workflows/seed-database.yml` runs them |
| Nobody has a password | `scripts/set_passwords.py` |
| Token missing `role_code` / `manager_id` | Added to `create_token`, `Identity`, and `login.py`'s query; `016_add_role_code.sql` supplies the mapping |
| Nothing actually protected | `_caller_id` deleted; every route resolves identity from the token |

## Route posture now

| Route | Auth |
|---|---|
| `health`, `login` | open — deliberately |
| `auth/me`, `me`, `topics`, `quiz/start`, `quiz/submit` | signed in |
| `review/pending`, `review/decide` | manager or above |

`health` has to be open or `deploy-backend.yml` cannot tell "app is down" from "app needs
a password". `login` is what you call when you have no token.

---

## Found along the way

**`review/pending` was publishing the answer key.** It returns
`GeneratedOptions.is_correct` for the whole bank and had no authentication at all —
anyone who could reach the Function App could read every correct answer, no sign-in, no
role, no rate limit. It now takes the manager gate.

**`_caller_id`'s fallback was worse than a missing check.** A client sending no
`x-learner-id` header did not get an error; it silently became the string
`"demo-learner"`. Every user would have shared one identity and one quiz history, and
nothing anywhere would have reported a problem.

**The two role registries had no link.** `Roles.title` is `'SDE 2'`; quizgen's is
`'Software Development Engineer 2'`. Roles like Security Analyst have no quizgen code at
all. So no title match could work, and a fuzzy one would serve the wrong role's material.
`016_add_role_code.sql` maps the unambiguous ones and leaves the rest NULL, which becomes
`"ALL"` — company-wide training only, so a missing mapping under-serves rather than leaks.

**The seed could only ever run once.** Both files hardcoded surrogate ids — `team_id 8`
meaning the eighth row inserted, `employee_id 3`, `question_id 7`. Correct on an empty
database populated in exactly that order, wrong everywhere else.

---

## What has NOT been verified

Everything below was written against the schema and the Azure SDK contracts, and none of
it has run against Azure — there are no credentials on this branch and no local SQL
Server or Docker to stand one up.

- **The SQL has never executed.** Syntax and `VALUES`-arity are checked programmatically;
  that is not the same as running. `016_add_role_code.sql` uses `GO` batch separators,
  which `sqlcmd` handles and some other runners do not.
- **The workflows have never run.** `deploy-backend.yml` and `seed-database.yml` are
  written against the patterns in `terraform.yml` and `migrate-database.yml` and reuse
  the same three secrets, but a workflow's first real run always finds something.
- **`terraform apply` has not been run.** `terraform validate` passes.
- **No end-to-end sign-in has happened**, because that needs all of the above first.

---

## The order to run it in

Each step depends on the one before, and each has a way to tell whether it worked.

1. **`terraform apply`** — creates `jwt-signing-secret` and sets `JWT_SIGNING_SECRET`.
   *Check:* the secret exists in `mob-kv-dev`.
2. **`migrate-database`** — applies `016_add_role_code.sql` along with anything else
   outstanding. *Check:* `Roles` has a `role_code` column.
3. **`seed-database`** — loads companies, departments, teams, roles, employees. It prints
   its own counts, including how many employees have a password. *Check:* 8 employees, 0
   with a password.
4. **`deploy-backend`** — publishes `api/`. *Check:* the health step passes; it fails the
   run if the app cannot reach the database.
5. **`python scripts/set_passwords.py --all --generate`** — needs `SQL_PASSWORD` and a SQL
   firewall rule for your IP. Prints the passwords once. *Check:* `--status` shows
   everyone able to sign in.
6. **`POST /api/login`** with one of those. *Check:* a token comes back, and
   `GET /api/auth/me` with it returns that person's `role_code` and `manager_id`.

If step 6 works, the auth half of v1 works.

---

## Still open, and not auth

**The deployed API is missing most of the product surface.** `api/function_app.py` is the
quiz engine; `scripts/devserver.py` is the product. The frontend calls these, and they do
not exist deployed:

| Endpoint | What breaks |
|---|---|
| `GET /trainings` | the employee home screen |
| `GET /lesson?training=` | lesson content |
| `POST /quiz/answer` | per-question feedback — the round trip that keeps the key server-side |
| `GET`/`POST /documents`, `POST /documents/confirm` | manager upload |
| `GET /jobs/{id}` | generation progress |
| `GET`/`POST /roles`, `POST /roles/{code}/delete` | role management |
| `GET /certificates` | on `add-certificates`, not on `main` |

This is larger than the auth work was, and it is not a port: the dev server reads a SQLite
bank and the Function App reads Azure SQL.

**The frontend still uses the demo sign-in.** `web-app/` has not been pointed at
`POST /api/login`. Worth doing after step 6 above proves the contract.

**Q Score.** `docs/q-score.md` is authoritative; the `add-certificates` branch needs the
rename and two formula fixes listed there.

**Azure SQL and blob isolation.** `docs/company-isolation-gap.md` scoped both out of the
search-index work. `Chunk.company_id` and the index filter are done; the SQL side is not.
