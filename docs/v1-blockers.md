# What's needed, and from whom

Branch: `ai-retry` (pushed). `main` and `first-ai-agent` untouched.

Everything below is either an Azure action nobody has taken, or a decision on someone
else's code. Ordered by what unblocks the most people.

---

## 1. Run the deploy chain — NOBODY HAS, and everything waits on it

Not a single piece of the Azure side has ever executed. No migration has run, neither new
workflow has fired, `terraform apply` has not run, and no one has signed in to the
deployed app. Every claim about the deployed API is "it compiles and the routes register",
which is a real but low ceiling.

Each step tells you whether the next is possible:

| # | Run | It worked if |
|---|---|---|
| 1 | `terraform apply` (Actions, on `main`) | `jwt-signing-secret` exists in `mob-kv-dev` |
| 2 | `migrate-database` | `Roles` has `role_code`; `Certificates` has `attempt_score`; `RoleRequirements` exists |
| 3 | `seed-database` (Actions → Run workflow) | It prints its own counts: 8 employees, 0 with a password |
| 4 | `deploy-backend` | The health step passes — it fails the run if the app can't reach SQL |
| 5 | `scripts/set_passwords.py --all --generate` | `--status` shows everyone able to sign in |
| 6 | `POST /api/login` then `GET /api/auth/me` | A token comes back, and `role_code` / `manager_id` are populated |

Steps 1–4 need Azure permissions. Step 5 needs `SQL_PASSWORD` and a SQL firewall rule for
your IP.

**Expect step 1 or 2 to surface something.** Migrations `016`–`020` use `GO` batch
separators and this is the first time `sqlcmd` sees them.

The single test worth running afterwards:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  https://mob-functions-dev.azurewebsites.net/api/review/pending \
  -H "Authorization: Bearer <an employee's token>"
```

**Expect 403.** That endpoint returns the answer key for the whole question bank and was
completely unauthenticated until this branch. A 200 means the deploy did not take.

---

## 2. sureshalampur — a reversal of your change, which needs your agreement

PR #6 changed `_caller_id` to prefer a real JWT and fall back to the `x-learner-id`
header, then to the literal string `"demo-learner"`.

That makes the token an opt-in rather than a gate. With no `Authorization` header and
`x-learner-id: anyone@demo.com`, you are that person on every endpoint that calls it —
and a client sending nothing at all silently becomes `demo-learner`, so every user shares
one identity and one quiz history with nothing reporting a problem.

`ai-retry` removes `_caller_id` entirely and returns 401. **That is a deliberate reversal
of your commit and should be agreed rather than merged quietly.**

Your `decode_token` change is kept and is a good one — returning `None` when
`JWT_SIGNING_SECRET` is missing rather than raising. Worth knowing it was only safe once
the fallback went: with both, a missing signing secret in production would have turned
every caller into `demo-learner` instead of failing loudly, which is the opposite of what
`shared/auth.py`'s own comment intends.

Also on `ai-retry`, in your files:

- `role_code` and `manager_id` added to the token. `role_code` is the TRAINING role
  (SDE2, SWE_MANAGER) as distinct from `access_role`, and role-scoped training cannot work
  without it. `016_add_role_code.sql` supplies the mapping — `Roles.title` is `'SDE 2'`
  while quizgen's is `'Software Development Engineer 2'`, so no title match could work.
- `name` on the principal, so the UI can greet someone by name rather than email.
- All four `_caller_id` call sites moved to `get_current_employee` + `require_manager`.

---

## 3. Whoever owns `add-certificates` — three changes, and one that grows costlier

`docs/q-score.md` is now the single definition. Two different numbers were being built
under one name: your per-attempt score, and a per-employee compliance rollup. They are
compatible — yours is the natural input to the rollup's Quality term — but not under one
name, or "Q Score 82" is ambiguous between "82% compliant" and "scored 82 on one quiz".

1. Rename `_calculate_q_score` → `_calculate_attempt_score`, and the column to
   `attempt_score`. Migration `018` does the column rename, guarded both ways so it is
   safe whichever order the branches land in.
2. Weight questions by difficulty in **both** numerator and denominator. Today the
   multiplier is averaged over correct answers only, which means getting fewer easy
   questions right *raises* it.
3. Drop the consistency factor. It is a cliff — a topic at 51% costs nothing, at 49% it
   costs a flat 8% — and the adaptive engine already concentrates quizzes on weak topics,
   so uneven performance is taxed twice.

**Your `014_create_certificates.sql` is on `ai-retry` byte-identical**, so it merges with
nothing to reconcile. Everything it needed is in `018`: `doc_title` (quizgen certifies a
source document, not a `Courses` row), `category` for the behavioural/technical split, and
that rename.

The longer that branch grows, the more of it is written against the old name.

---

## 4. Shyam — your isolation work is wired in, and the SQL half is done

`docs/company-isolation-gap.md` asked whoever owns `search_index.py` to add a `company_id`
field. Done: `Chunk.company_id`, a filterable index field, `upload()` validating through
your `validate_company_id`, and `retrieve()` taking `company_id` positionally so it cannot
be called unscoped. `isolation.py` is untouched — only imported.

The two things that document scoped out are also done now:

- **Azure SQL** — `020_add_company_to_quizgen.sql` puts `company_id` on SourceChunks,
  GeneratedQuestions, GeneratedQuizAttempts, GeneratedQuizResponses and Certificates, and
  every endpoint filters on it. Seven endpoints had no filter at all before this.

  An eighth was missed on the first pass: `POST /review/decide` updated
  `GeneratedQuestions` with `WHERE question_id = ?` and no company check, so a manager in
  one company could change another company's question by guessing its id. The structural
  audit did not catch it because its pattern matched `FROM` and `INSERT` but not `UPDATE`
  — it never looked at that endpoint rather than looking and getting it wrong. Both are
  fixed, and the endpoint now reports rows actually changed rather than ids submitted.
- Both SQL **views** had to change too, which is the easy thing to miss — a view is a
  stored query, and `vw_LearnerTopicMastery` was aggregating responses across every
  learner regardless of how carefully its callers were scoped.

Worth a look from you, since it is your design: `tests/test_tenant_isolation.py` puts two
companies in one database and proves rather than asserts — one test opens an unscoped bank
and checks the leak appears, so if the filter ever stops applying the others go red.

**Blob containers are still not company-scoped**, as your document said. That is the
remaining piece.

---

## 5. Document Intelligence — still nothing

`DOC-01..04` and `DOC-06`. Extraction is still `pypdf`, so scanned PDFs produce nothing
and tables come out unusable. `DOC-06` (Terraform for the resource and its Key Vault
secret) has to land before `DOC-01` can use it.

---

## Not blocked on anyone — open work

- **Deployed upload.** `api/` has no `documents`, `documents/confirm` or `jobs` endpoints.
  Not a port: generation needs quizgen inside the Function App plus an async trigger,
  because an HTTP function cannot hold a request open for minutes the way the dev server's
  background thread does. Overlaps Track B.
- **Data layer parity** (`DATA-01..04`). `bank.py` is SQLite-only, `api/` is pyodbc-only,
  and no test runs the same behaviour against both.
- **RENEW-02** — prioritising questions whose source changed needs document-version
  tracking, which does not exist.
- **Certificate artefact.** Deliberately a placeholder; cards say "Downloadable
  certificate not generated yet" rather than showing a dead button.

---

## What already works

Verified end to end against the dev server, and mirrored in `api/` where it could be:

sign in → role-scoped training → adaptive quiz with per-question feedback → certificate on
passing → Q Score that falls on expiry → renewal surfaced. Manager sees their reporting
subtree and can upload only for roles their reports hold.

158 tests. Q Score matches every worked example in `docs/q-score.md`.
