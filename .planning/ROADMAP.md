# Roadmap: MOB-team — Role-Scoped Training & Certification

## Overview

The engine exists and the infrastructure exists. `src/quizgen/` runs a full pipeline offline —
15 chunks to 20 questions to a graded quiz. Terraform provisions the network, Key Vault, SQL,
Functions, App Service, and storage. Since the `origin/main` merge (`f111882`),
`.github/workflows/migrate-database.yml` applies `db/migrations/*.sql` to Azure SQL with a
`SchemaMigrations` tracking table, and the Function App's app settings expose the SQL variables
`api/function_app.py` actually reads.

What is missing is the application layer that connects a real person to any of it. Nobody can
sign in. The org tables the app would read a role from have never had a row inserted, because
the migration workflow applies schema and never runs `db/seed/`. So Phase 1 is real sign-in end
to end — seed data included, because sign-in cannot resolve a role from empty tables. Phase 2
puts both storage backends behind one interface and makes CI prove they agree, immediately after
sign-in gives us the first real dual-backend read to protect.

Everything after that is product: server-side enforcement, role-scoped training, a real
extractor, manager upload, certificates that expire, Q Score, renewal, and a final phase that
proves tenant isolation and leaves nothing fake presented as measured.

This is a brownfield rebuild. `src/quizgen/`, `registry.py`/`web.py`, `web-app/src/App.jsx`,
`db/migrations/001..011`, `infra/`, and the merged workflows are kept and extended — not replaced.

## Scope Boundary: Deployment Is Owned By Teammates

Deployment is a teammate track, not this roadmap's work. Six DEPLOY requirements were withdrawn
after the merge — DEPLOY-01 is built (`migrate-database.yml`), DEPLOY-05 is merged (`24a6b70`),
DEPLOY-06 is resolved (`terraform apply` stays gated to `main`, deliberately), and DEPLOY-03/04/07
belong to teammate Track 0 and Track G.

Only **DEPLOY-02 (seed data)** survives, because nobody owns it and Phase 1 cannot start without
it. It sits inside Phase 1 rather than in a phase of its own.

**Consequence to plan around:** a phase here can be code-complete while nothing runs in Azure,
because publishing `api/` and `web-app/` is someone else's step. Phase success criteria are
written against behaviour that is verifiable locally and in CI, so they do not silently depend
on a teammate's pipeline landing first.

## Multi-Tenancy: Woven, Not Isolated

Multi-tenancy is deliberately **not** its own phase. Reasoning:

- Migration `009_add_multitenancy.sql` already added the Companies root and `company_id`
  columns. The schema exists; only enforcement is missing. There is no "build tenancy"
  work to concentrate into a phase.
- Tenancy is a property of two things: **identity resolution** (which company is this
  caller?) and **every query** (is this row theirs?). Both are already phases —
  Phase 1 and Phase 3. Splitting tenancy out would mean resolving identity in Phase 1,
  then re-opening the same code later to add company to it.
- The alternative — a late tenancy phase — is exactly the retrofit PROJECT.md's Key
  Decisions table rejected. The accepted tradeoff was "tenant-scoping work in every phase."

So TENANT-01 (resolve company at sign-in) sits in Phase 1 alongside role resolution.
TENANT-02/03 (every query scoped, no cross-read) sit in Phase 3 alongside the server-side
`access_role` gate, because they are the same enforcement layer. TENANT-04 (uploads scoped
to uploader's company) sits in Phase 6 where uploads are built.

**TENANT-05 — the proof — is deliberately last (Phase 10).** It requires two companies to
have employees, documents, questions, attempts, certificates, *and* scores. Four of those
six do not exist until Phase 8 completes. Placed early it could only assert isolation over
employees and documents — a test that passes while proving almost nothing. Placed at Phase 10
it exercises the whole data surface, which is what the requirement asks for. Phase 2's
dual-backend harness is what makes it cheap to write, since it already runs the suite against
Azure SQL, where cross-tenant reads actually matter.

## Two Hierarchies, Two Questions

Both org hierarchies are authoritative, for different questions. This governs Phases 6 and 8:

- `Employees.manager_id` (migration 001) answers **which people report to you**.
- `Departments > Teams > Roles` (migration 006) answers **which role each person holds**.

UPLOAD-02: a manager may upload for the set of roles held by anyone in their `manager_id`
subtree — walk the reporting chain to get people, then map people to roles.
QSCORE-06: visibility follows the `manager_id` reporting chain.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Real Sign-In End to End** - A real person signs in and lands on their own screen, with role and company resolved from seeded rows
- [ ] **Phase 2: Data Layer & Backend Parity** - One storage interface over Azure SQL and SQLite, with divergence caught by CI
- [ ] **Phase 3: Server-Side Authorization & Tenant Scoping** - Every endpoint gates on `access_role` and company; the `x-learner-id` stub dies
- [ ] **Phase 4: Role-Scoped Training on Authenticated Identity** - Training and adaptive quizzes driven by who you are, not what header you sent
- [ ] **Phase 5: Document Intelligence Extraction** - Azure Document Intelligence replaces pypdf, with citable page and section structure
- [ ] **Phase 6: Manager Upload Scoped by Org Chart** - Managers upload PDFs for roles held by their reports, ingested without a CLI step
- [ ] **Phase 7: Certificates with Expiry** - Passing issues a certificate that carries validity, expires, and reopens training
- [ ] **Phase 8: Q Score** - Real role-relative Q Score, split behavioural/technical, visible up the reporting chain
- [ ] **Phase 9: Renewal** - Expiring certificates surface and renew against the current bank, prioritising changed sources
- [ ] **Phase 10: Tenant Isolation Proof & Honest Surface** - Two-company isolation test across the full data surface; nothing fake presented as measured

## Phase Details

### Phase 1: Real Sign-In End to End
**Goal**: A real person signs in with a username and password and lands on their own screen, with their role, reporting position, and company resolved from seeded database rows
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, AUTH-07, TENANT-01, UI-01, DEPLOY-02
**Success Criteria** (what must be TRUE):
  1. A person opens the web app, signs in with a username and password, and lands on their own home screen; the demo sign-in screen and its hardcoded learner are gone from `web-app/`.
  2. Seeded Companies, Departments, Teams, Roles, and Employees exist in the database — `db/seed/org_seed.sql` and `seed_data.sql` are applied by a repeatable step alongside `migrate-database.yml`'s schema run, and running it twice does not duplicate rows.
  3. After sign-in the server states the caller's `access_role`, `manager_id` reporting position, and company from those seeded rows — not from a header, not from a constant.
  4. A signed-in user reloads the browser and is still signed in; signing out from any page returns them to sign-in, and the previous session no longer works.
  5. No plaintext password exists anywhere — the credential store holds only hashes and `git status` shows it ignored — and swapping the JSON provider for an Entra implementation of the same interface requires no change to any caller.
**Plans**: TBD
**UI hint**: yes

### Phase 2: Data Layer & Backend Parity
**Goal**: Both storage backends sit behind one interface and CI proves they behave identically, so nothing built after this can diverge between a laptop and a demo
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: DATA-01, DATA-02, DATA-03, DATA-04, DATA-05
**Success Criteria** (what must be TRUE):
  1. The same test suite runs against SQLite and against Azure SQL and passes identically; a behavioural difference between the two turns CI red rather than surfacing during a demo.
  2. Sign-in's role and company resolution — the first real dual-backend read — returns the same answer on both backends, proven by that suite rather than by inspection.
  3. Choosing a backend is a configuration change: no caller in `src/quizgen/` or `api/` names `sqlite3` or `pyodbc`, and no caller branches on which backend is active.
  4. Chunks, questions, attempts, responses, and mastery all read and write through the shared interface, so `bank.py`'s SQLite file becomes one implementation rather than the operational store.
  5. `QUIZGEN_PROVIDER=mock` against SQLite still runs the full pipeline offline with no Azure account, and migrations 003 and 010 remain on disk marked superseded so replaying history against a fresh database still works.
**Plans**: TBD

### Phase 3: Server-Side Authorization & Tenant Scoping
**Goal**: Every API endpoint enforces the caller's role and company on the server, and the client can no longer assert who it is
**Mode:** mvp
**Depends on**: Phase 2
**Requirements**: AUTH-06, TENANT-02, TENANT-03
**Success Criteria** (what must be TRUE):
  1. An employee account calling a manager-or-above endpoint directly with curl — no UI involved — is refused by the server, not merely hidden from in the interface.
  2. Sending an `x-learner-id` header has no effect on who the server believes the caller is; `_caller_id`'s trust-the-header path is gone from `api/function_app.py` and from `scripts/devserver.py`.
  3. A signed-in user requesting a record that belongs to another company gets nothing back — for employees, documents, questions, and attempts alike.
  4. A data-access path written without a company filter is caught rather than silently returning another company's rows.
  5. The parity suite from Phase 2 still passes against both backends with the authorization gate in place, so enforcement behaves the same on each.
**Plans**: TBD

### Phase 4: Role-Scoped Training on Authenticated Identity
**Goal**: An employee's training list and adaptive quiz come from their authenticated identity, and an admin controls what each role is required to complete
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: TRAIN-01, TRAIN-02, TRAIN-03, TRAIN-04, TRAIN-05
**Success Criteria** (what must be TRUE):
  1. Two employees in different roles sign in and each sees only the training their own role requires; changing an id in the URL does not surface the other's training.
  2. Across successive quiz rounds an employee's questions visibly concentrate on the subjects they answered wrong.
  3. Inspecting the `/api/quiz/start` response in devtools shows options with no correct answer marked — the key arrives only after grading.
  4. Every question served carries a citation to a blob PDF or a `registry.py` allowlisted source; no question without one is ever displayed.
  5. An admin sets the required course list for a role, and an employee in that role sees the updated list on their next visit.
**Plans**: TBD
**UI hint**: yes

### Phase 5: Document Intelligence Extraction
**Goal**: PDF extraction handles scanned, tabular, and multi-column documents, preserving the page and section structure that citation depends on
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: DOC-01, DOC-02, DOC-03, DOC-04, DOC-05, DOC-06, DOC-07
**Success Criteria** (what must be TRUE):
  1. A scanned, image-only PDF in a blob container produces readable text and answerable questions, where pypdf previously produced nothing.
  2. A PDF containing a table or a two-column layout produces text in correct reading order, and a question drawn from it quotes the passage as it actually reads.
  3. Every generated question cites document, page number, and section heading, and the quoted passage is found verbatim on that page.
  4. `terraform plan` in `infra/` shows the Document Intelligence resource and its Key Vault secret, and no Document Intelligence key appears in the repository or in a workflow variable.
  5. The deployed ingestion path reads only from Azure Blob Storage with role scope still derived from container name, and `QUIZGEN_PROVIDER=mock` still runs the pipeline end to end with no Azure account.
**Plans**: TBD

### Phase 6: Manager Upload Scoped by Org Chart
**Goal**: A manager uploads a PDF from the web UI for the roles held by their reports, and it becomes quiz questions without anyone touching a CLI
**Mode:** mvp
**Depends on**: Phase 5
**Requirements**: UPLOAD-01, UPLOAD-02, UPLOAD-03, UPLOAD-04, UPLOAD-05, TENANT-04, UI-02
**Success Criteria** (what must be TRUE):
  1. A manager opens an upload screen and sees exactly the roles held by people in their `Employees.manager_id` reporting subtree — the reporting chain selects the people, `Departments > Teams > Roles` names the roles they hold, and no separate permission list is maintained.
  2. An employee account has no upload screen, and a forged upload request from that account is rejected by the server.
  3. A manager forging an upload aimed at a role outside their own reporting subtree is rejected server-side, with the file never reaching storage.
  4. An uploaded PDF lands in the blob container matching its target role and is recorded against the uploader's company.
  5. In one flow with no manual CLI step, the uploaded document's content appears as answerable, correctly-cited questions for employees in the target role.
**Plans**: TBD
**UI hint**: yes

### Phase 7: Certificates with Expiry
**Goal**: Passing an assessment issues a certificate that carries a validity period, expires, and reopens training when it does
**Mode:** mvp
**Depends on**: Phase 4
**Requirements**: CERT-01, CERT-02, CERT-03, CERT-04, CERT-05, CERT-06, UI-04
**Success Criteria** (what must be TRUE):
  1. An employee who passes an assessment sees a certificate appear on their certificates screen with issue date, expiry date, and a behavioural or technical label.
  2. A certificate's validity defaults to 12 months and can be set per course; the expiry date shown is derived from that value.
  3. Once a certificate passes its expiry date it stops counting as current, and the training it covered reappears in that employee's list.
  4. The certificates screen shows records read from the database for the signed-in employee only — no sample data remains on that screen.
**Plans**: TBD
**UI hint**: yes

### Phase 8: Q Score
**Goal**: Every employee has a real, role-relative Q Score driven by unexpired certificate coverage, visible to them and to everyone above them in the reporting chain
**Mode:** mvp
**Depends on**: Phase 7
**Requirements**: QSCORE-01, QSCORE-02, QSCORE-03, QSCORE-04, QSCORE-05, QSCORE-06, QSCORE-07, QSCORE-08, QSCORE-09, UI-03
**Success Criteria** (what must be TRUE):
  1. An employee opens the Q Score screen and sees `Coverage x Quality` computed from their own unexpired certificates against their role's required list — not sample data. Completing every required course at the pass mark shows roughly the pass mark, not a number that reads as failing.
  2. Each certificate carries a difficulty-weighted attempt score that never changes after it is issued, and the Q Score is the average of those scaled by coverage.
  3. Behavioural and technical Q Scores are shown separately as well as combined, so a gap in one is visible rather than averaged away.
  4. A manager sees the Q Score of everyone in their `Employees.manager_id` reporting subtree, and cannot see the Q Score of anyone outside it or outside their company.
  5. An employee who retakes an assessment and scores lower does not see their Q Score fall — the best attempt score remains the one of record — and when a certificate expires, Coverage and Q Score drop on the next view with nobody having acted.
  6. Q Score is read from a view, not a stored column: expiring a certificate directly in the database changes the number on the next read, with no recalculation step to run.
**Plans**: TBD
**UI hint**: yes

### Phase 9: Renewal
**Goal**: An expiring certificate surfaces to the employee and renews against the current question bank, weighted toward material whose source has changed
**Mode:** mvp
**Depends on**: Phase 8
**Requirements**: RENEW-01, RENEW-02, RENEW-03
**Success Criteria** (what must be TRUE):
  1. An employee whose certificate is expiring or already expired sees it flagged in the UI with its date and a way to start renewal.
  2. A renewal assessment presents questions from the current bank, including questions that did not exist when the original certificate was issued.
  3. When a source document has changed since the certificate was issued, questions drawn from the changed material appear more often in the renewal assessment than questions from unchanged material.
  4. Passing a renewal issues a fresh certificate with a new expiry, and the employee's Coverage and Q Score recover.
**Plans**: TBD
**UI hint**: yes

### Phase 10: Tenant Isolation Proof & Honest Surface
**Goal**: The tenancy claim is proven by a test running two companies side by side over the full data surface, and no panel in the app presents invented data as measured
**Mode:** mvp
**Depends on**: Phase 9
**Requirements**: TENANT-05, UI-05
**Success Criteria** (what must be TRUE):
  1. An automated test provisions two companies each with their own employees, documents, questions, attempts, certificates, and scores, and asserts that a caller from company A receives nothing belonging to company B for any of those six.
  2. That test runs against Azure SQL as well as SQLite, so isolation is proven on the backend that actually serves a demo — and removing a company filter from any query path makes it fail.
  3. Every panel in `web-app/` either shows real data from the backend or is visibly labelled as mock; nothing sample-shaped is presented as measured.
  4. The gamification panels (badges, companion pet, focus timer, teammates) are still present and still labelled mock — neither quietly dropped nor quietly given fake backends.
**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Real Sign-In End to End | 0/TBD | Not started | - |
| 2. Data Layer & Backend Parity | 0/TBD | Not started | - |
| 3. Server-Side Authorization & Tenant Scoping | 0/TBD | Not started | - |
| 4. Role-Scoped Training on Authenticated Identity | 0/TBD | Not started | - |
| 5. Document Intelligence Extraction | 0/TBD | Not started | - |
| 6. Manager Upload Scoped by Org Chart | 0/TBD | Not started | - |
| 7. Certificates with Expiry | 0/TBD | Not started | - |
| 8. Q Score | 0/TBD | Not started | - |
| 9. Renewal | 0/TBD | Not started | - |
| 10. Tenant Isolation Proof & Honest Surface | 0/TBD | Not started | - |

## Coverage

All 58 v1 requirements map to exactly one phase. No orphans, no duplicates.

| Phase | Requirements | Count |
|-------|--------------|-------|
| 1 | AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, AUTH-07, TENANT-01, UI-01, DEPLOY-02 | 9 |
| 2 | DATA-01, DATA-02, DATA-03, DATA-04, DATA-05 | 5 |
| 3 | AUTH-06, TENANT-02, TENANT-03 | 3 |
| 4 | TRAIN-01, TRAIN-02, TRAIN-03, TRAIN-04, TRAIN-05 | 5 |
| 5 | DOC-01, DOC-02, DOC-03, DOC-04, DOC-05, DOC-06, DOC-07 | 7 |
| 6 | UPLOAD-01, UPLOAD-02, UPLOAD-03, UPLOAD-04, UPLOAD-05, TENANT-04, UI-02 | 7 |
| 7 | CERT-01, CERT-02, CERT-03, CERT-04, CERT-05, CERT-06, UI-04 | 7 |
| 8 | QSCORE-01, QSCORE-02, QSCORE-03, QSCORE-04, QSCORE-05, QSCORE-06, QSCORE-07, QSCORE-08, QSCORE-09, UI-03 | 10 |
| 9 | RENEW-01, RENEW-02, RENEW-03 | 3 |
| 10 | TENANT-05, UI-05 | 2 |
| **Total** | | **58** |

## Notes on Sequencing

- **Phase 1 is the whole application layer in miniature.** It is deliberately not "build auth" —
  it is not done until a real person signs in and lands on their own screen. That forces seed
  data (DEPLOY-02), the credential provider, session persistence, and the sign-in screen to land
  together rather than as four separately-plausible half-features.
- **Seed data is a Phase 1 dependency, not a chore.** `migrate-database.yml` applies schema and
  never runs `db/seed/`, so Companies, Departments, Teams, Roles, and Employees are empty in
  Azure SQL. AUTH-07 and TENANT-01 cannot resolve anything from empty tables. Nobody else owns
  this step.
- **Phase 2 second, not first.** Parity is worth most immediately after the first real
  dual-backend read exists to protect — sign-in resolving role and company — and before the
  large surface of training, certificates, and scoring is built on top. The teammate tracks make
  this urgent: their Track B extends adaptive selection inside `quizgen/` (SQLite) while their
  Track C puts scoring in `POST /quiz/submit` (Azure SQL). Nobody owns the seam between them.
- **DATA-03 cannot be discharged by adding secrets to `tests.yml`.** That file holds no Azure
  credentials by design, so a pull request cannot deploy. The Azure-SQL leg of the parity suite
  must respect that boundary — this is a design constraint on how Phase 2 is planned, not an
  oversight to fix.
- **Phase 3 after Phase 1, not merged into it.** Authentication (who you are) and authorization
  (what you may do) are different surfaces. Phase 1 produces an identity; Phase 3 makes every
  endpoint respect it. Splitting them keeps the enforcement sweep across `api/function_app.py`
  and `scripts/devserver.py` verifiable on its own.
- **Phase 5 before Phase 6.** Manager upload (UPLOAD-05) auto-ingests without a CLI step. It
  should feed the Document Intelligence path, not the pypdf path it would then replace.
- **Phase 7 depends on Phase 4, not Phase 6.** Certificates need assessments scoped to
  authenticated identity. They do not need manager upload. Phases 6 and 7 could run in either
  order; the stated order follows the user's declared sequence.
- **Phase 8's naming conflict is resolved.** Two definitions were live — a per-employee
  compliance rollup and a per-attempt performance score. They are now two named levels, with the
  attempt score feeding the rollup: `docs/q-score.md` is authoritative, and every other formula
  in the repo is marked superseded. What remains is a coordination cost, not a design one: the
  `add-certificates` branch computes the attempt score under the name `q_score`, so it needs the
  rename plus two formula fixes listed in that document.
- **Phase 9 needs Phase 5.** RENEW-02 prioritises questions whose source document changed. That
  requires document-version tracking established by the Document Intelligence ingestion path.
- **Offline path is a standing constraint, not a phase.** `QUIZGEN_PROVIDER=mock` plus the SQLite
  dev server must keep working through every phase; it appears as a success criterion wherever a
  phase could break it (Phases 2 and 5 in particular).
- **Deployment is not on the critical path here.** Publishing `api/` and `web-app/` is a teammate
  step, so phases are written to be verifiable locally and in CI. A phase can be code-complete
  while nothing runs in Azure — track that as an external dependency, not as phase failure.
