# Requirements: MOB-team — Role-Scoped Training & Certification

**Defined:** 2026-08-14
**Core Value:** An employee signs in once and gets training scoped to their role, where every question traces to a source someone approved — never to something the model invented.

## v1 Requirements

Requirements for this rebuild. Each maps to exactly one roadmap phase.

Requirements marked **(preserve)** describe behaviour that works today but changes
substantively under authentication and tenancy — the capability exists, the code path does
not survive untouched.

### Data Layer & Backend Parity

- [ ] **DATA-01**: A single storage interface serves both Azure SQL and SQLite; no caller knows which backend is active
- [ ] **DATA-02**: The question bank — chunks, questions, attempts, responses, mastery — goes through that interface; no direct `sqlite3` or `pyodbc` call remains in a caller
- [ ] **DATA-03**: The same test suite runs against both SQLite and Azure SQL and passes identically
- [ ] **DATA-04**: A behavioural difference between the two backends fails CI, rather than surfacing during a demo
- [ ] **DATA-05**: Migrations 003 and 010 are documented as superseded and unused, so nobody builds on them

### Deployment & Seed

Deployment is owned by teammates (their Track 0 and Track G). Only the gap nobody owns
stays in this roadmap.

- [ ] **DEPLOY-02**: A pipeline loads seed data — companies, departments, teams, roles, employees — into Azure SQL. `migrate-database.yml` applies schema but has no seed step, so `db/seed/org_seed.sql` and `seed_data.sql` have never run. Without seeded rows, sign-in has no role or company to resolve.

**Owned by teammates, tracked as external dependencies — not built here:**

| Was | Now covered by |
|-----|----------------|
| DEPLOY-01 migration runner | Built: `.github/workflows/migrate-database.yml` (commit `2d0586c`) |
| DEPLOY-03 deploy `api/` | Teammate Track 0 step 4, Track G step 2 |
| DEPLOY-04 deploy `web-app/` | Teammate Track G step 3 |
| DEPLOY-05 Key Vault secret | Teammate Track 0 step 2 (merged as `24a6b70`) |
| DEPLOY-06 deploy branch | Resolved: `terraform apply` stays gated to `main` |
| DEPLOY-07 health check | Teammate Track 0 step 5 |

### Authentication & Authorization

- [ ] **AUTH-01**: User can sign in with a username and password
- [ ] **AUTH-02**: Passwords are stored hashed; no plaintext credential exists in the repository or in a tracked file
- [ ] **AUTH-03**: The credential store sits behind a provider interface, so an Entra ID provider can replace the JSON file without changing any caller
- [ ] **AUTH-04**: User stays signed in across a browser refresh
- [ ] **AUTH-05**: User can sign out from any page
- [ ] **AUTH-06**: Every API endpoint enforces the caller's `access_role` server-side; hiding a control in the UI is never the only protection
- [ ] **AUTH-07**: Sign-in resolves the user's role, org-chart position, and company from the database

### Multi-Tenancy

- [ ] **TENANT-01**: Sign-in resolves which company the caller belongs to
- [ ] **TENANT-02**: Every data query is scoped to the caller's company
- [ ] **TENANT-03**: A user of one company cannot read another company's employees, documents, questions, attempts, certificates, or scores
- [ ] **TENANT-04**: An uploaded document is scoped to the uploader's company
- [ ] **TENANT-05**: Tenant isolation is proven by an automated test that runs two companies side by side and asserts no cross-read

### Document Ingestion

- [ ] **DOC-01**: PDF text extraction uses Azure Document Intelligence in place of pypdf
- [ ] **DOC-02**: Scanned and image-only PDFs are extracted via OCR and produce usable text
- [ ] **DOC-03**: Tables and multi-column layouts survive extraction as readable, correctly ordered text
- [ ] **DOC-04**: Extracted text retains page numbers and section headings, so a question can cite document, page, and quote
- [ ] **DOC-05**: The deployed ingestion path reads from Azure Blob Storage only — no local filesystem source **(preserve)**
- [ ] **DOC-06**: Terraform provisions the Document Intelligence resource and its Key Vault secret
- [ ] **DOC-07**: A document's role scope is derived from its blob container **(preserve)**

### Document Upload

- [ ] **UPLOAD-01**: A manager or above can upload a PDF from the web UI
- [ ] **UPLOAD-02**: The set of roles a user may upload for is the set of roles held by anyone in their `Employees.manager_id` reporting subtree. Both hierarchies are used for what each is good at: `manager_id` (migration 001) determines *which people* report to you; `Departments > Teams > Roles` (migration 006) determines *which role* each of them holds, and roles are what an upload targets
- [ ] **UPLOAD-03**: An upload targeting a role outside the uploader's org subtree is rejected server-side
- [ ] **UPLOAD-04**: An uploaded document lands in the blob container matching its target role
- [ ] **UPLOAD-05**: An uploaded document is ingested into the question bank without a manual CLI step

### Role-Scoped Training

- [ ] **TRAIN-01**: An employee sees only the training their role requires, determined by their authenticated identity rather than a client-supplied header **(preserve)**
- [ ] **TRAIN-02**: An employee takes an adaptive quiz that concentrates on the subjects they are weak at **(preserve)**
- [ ] **TRAIN-03**: The answer key never reaches the browser before an attempt is graded **(preserve)**
- [ ] **TRAIN-04**: Questions are drawn only from blob PDFs and the vetted allowlist in `registry.py` — never from open web search or model invention **(preserve)**
- [ ] **TRAIN-05**: An admin can set the required course list for each role

### Certificates

- [ ] **CERT-01**: An employee earns a certificate on passing an assessment
- [ ] **CERT-02**: A certificate carries `validity_months`, defaulting to 12
- [ ] **CERT-03**: A certificate expires, and an expired certificate stops counting toward Coverage
- [ ] **CERT-04**: Expiry reopens the associated training for that employee
- [ ] **CERT-05**: An employee can view their own certificates with issue and expiry dates
- [ ] **CERT-06**: Each certificate is classified as behavioural or technical

### Q Score

> Defined in full in `docs/q-score.md`. That document replaces the two competing
> definitions that were in flight — the per-attempt score on the `add-certificates`
> branch and the per-employee rollup in `docs/roadmap-online-sourcing-and-renewal.md`.
> They are now two named levels rather than one ambiguous name.

- [ ] **QSCORE-01**: Attempt Score is computed per quiz as `100 x sum(weight of correct questions) / sum(weight of all questions)`, with weights Easy 0.95, Medium 1.0, Hard 1.08
- [ ] **QSCORE-02**: Attempt Score is stored on the certificate and never changes
- [ ] **QSCORE-03**: Q Score is computed per employee as `Coverage x Quality`, where Coverage is unexpired certificates over required certificates (capped at 1) and Quality is the average Attempt Score across those certificates
- [ ] **QSCORE-04**: Expired certificates leave both Coverage and the Quality average, so Q Score falls on expiry without anyone acting
- [ ] **QSCORE-05**: When an assessment is retaken, the best Attempt Score is the one of record
- [ ] **QSCORE-06**: Behavioural and technical Q Scores are reported separately as well as combined
- [ ] **QSCORE-07**: An employee can see their own Q Score
- [ ] **QSCORE-08**: Everyone above an employee in the `Employees.manager_id` chain can see that employee's Q Score
- [ ] **QSCORE-09**: Q Score is exposed as a database view rather than a stored column, so it is never stale

### Renewal

- [ ] **RENEW-01**: A renewal assessment draws from the current question bank, not the bank as it stood when the certificate was issued
- [ ] **RENEW-02**: Questions whose source document changed since the certificate was issued are prioritised in the renewal assessment
- [ ] **RENEW-03**: An expiring or expired certificate is visible to the employee in the UI

### Frontend

- [ ] **UI-01**: The demo sign-in screen is replaced with real authentication
- [ ] **UI-02**: A manager sees an upload screen scoped to the roles they control
- [ ] **UI-03**: The Q Score screen shows real computed data rather than sample data
- [ ] **UI-04**: The certificates screen shows real certificates with expiry dates
- [ ] **UI-05**: Any panel still lacking a backend remains visibly labelled as mock

## v2 Requirements

Deferred. Tracked but not in the current roadmap.

### Notifications

- **NOTIF-01**: Employee receives an email reminder before a certificate expires
- **NOTIF-02**: Employee receives an email when a certificate expires
- **NOTIF-03**: Reminder tiers are configurable
- **NOTIF-04**: Manager receives a digest of their team's expiring certificates

### Identity

- **IDENT-01**: Entra ID SSO replaces the JSON credential provider
- **IDENT-02**: Employee records link to Entra object ids

### Q Score Refinements

- **QSCORE-10**: A pace indicator sits alongside Q Score, distinguishing 2-of-7 in January from 2-of-7 in November

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Open web search for source material | A random blog could become the basis of a mandatory certification. Sources come from blob PDFs and the curated `registry.py` allowlist only. |
| Model-selected sources | The AI does not decide what to trust. A human curates the registry. This is the anti-hallucination constraint, and it is the point of the whole provenance model. |
| Plaintext passwords, or any credential in a tracked file | `MOB-team` is a public repository. |
| Entra ID / real SSO | Deferred to v2, not rejected. AUTH-03 exists specifically so it stays reachable. |
| Real backends for gamification panels (badges, companion pet, focus timer, teammates) | They are the product's design direction, not measurements. They stay labelled mock (UI-05) rather than being quietly dropped or quietly faked. |
| Expanding the source registry beyond its current seeded entries | The seeded sources are treated as approved for v1. Governance of who endorses new sources is a v2 question. |
| Email and comms infrastructure | Follows from deferring NOTIF-01..04. The `infra/modules/comms` Terraform module stays provisioned but unused. |

## Traceability

Which phases cover which requirements. Populated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| DATA-01 | Phase 2 — Data Layer & Backend Parity | Pending |
| DATA-02 | Phase 2 — Data Layer & Backend Parity | Pending |
| DATA-03 | Phase 2 — Data Layer & Backend Parity | Pending |
| DATA-04 | Phase 2 — Data Layer & Backend Parity | Pending |
| DATA-05 | Phase 2 — Data Layer & Backend Parity | Pending |
| DEPLOY-02 | Phase 1 — Real Sign-In End to End | Pending |
| AUTH-01 | Phase 1 — Real Sign-In End to End | Pending |
| AUTH-02 | Phase 1 — Real Sign-In End to End | Pending |
| AUTH-03 | Phase 1 — Real Sign-In End to End | Pending |
| AUTH-04 | Phase 1 — Real Sign-In End to End | Pending |
| AUTH-05 | Phase 1 — Real Sign-In End to End | Pending |
| AUTH-06 | Phase 3 — Server-Side Authorization & Tenant Scoping | Pending |
| AUTH-07 | Phase 1 — Real Sign-In End to End | Pending |
| TENANT-01 | Phase 1 — Real Sign-In End to End | Pending |
| TENANT-02 | Phase 3 — Server-Side Authorization & Tenant Scoping | Pending |
| TENANT-03 | Phase 3 — Server-Side Authorization & Tenant Scoping | Pending |
| TENANT-04 | Phase 6 — Manager Upload Scoped by Org Chart | Pending |
| TENANT-05 | Phase 10 — Tenant Isolation Proof & Honest Surface | Pending |
| DOC-01 | Phase 5 — Document Intelligence Extraction | Pending |
| DOC-02 | Phase 5 — Document Intelligence Extraction | Pending |
| DOC-03 | Phase 5 — Document Intelligence Extraction | Pending |
| DOC-04 | Phase 5 — Document Intelligence Extraction | Pending |
| DOC-05 | Phase 5 — Document Intelligence Extraction | Pending |
| DOC-06 | Phase 5 — Document Intelligence Extraction | Pending |
| DOC-07 | Phase 5 — Document Intelligence Extraction | Pending |
| UPLOAD-01 | Phase 6 — Manager Upload Scoped by Org Chart | Pending |
| UPLOAD-02 | Phase 6 — Manager Upload Scoped by Org Chart | Pending |
| UPLOAD-03 | Phase 6 — Manager Upload Scoped by Org Chart | Pending |
| UPLOAD-04 | Phase 6 — Manager Upload Scoped by Org Chart | Pending |
| UPLOAD-05 | Phase 6 — Manager Upload Scoped by Org Chart | Pending |
| TRAIN-01 | Phase 4 — Role-Scoped Training on Authenticated Identity | Pending |
| TRAIN-02 | Phase 4 — Role-Scoped Training on Authenticated Identity | Pending |
| TRAIN-03 | Phase 4 — Role-Scoped Training on Authenticated Identity | Pending |
| TRAIN-04 | Phase 4 — Role-Scoped Training on Authenticated Identity | Pending |
| TRAIN-05 | Phase 4 — Role-Scoped Training on Authenticated Identity | Pending |
| CERT-01 | Phase 7 — Certificates with Expiry | Pending |
| CERT-02 | Phase 7 — Certificates with Expiry | Pending |
| CERT-03 | Phase 7 — Certificates with Expiry | Pending |
| CERT-04 | Phase 7 — Certificates with Expiry | Pending |
| CERT-05 | Phase 7 — Certificates with Expiry | Pending |
| CERT-06 | Phase 7 — Certificates with Expiry | Pending |
| QSCORE-01 | Phase 9 — Q Score | Pending |
| QSCORE-02 | Phase 9 — Q Score | Pending |
| QSCORE-03 | Phase 9 — Q Score | Pending |
| QSCORE-04 | Phase 9 — Q Score | Pending |
| QSCORE-05 | Phase 9 — Q Score | Pending |
| QSCORE-06 | Phase 9 — Q Score | Pending |
| QSCORE-07 | Phase 9 — Q Score | Pending |
| QSCORE-08 | Phase 9 — Q Score | Pending |
| QSCORE-09 | Phase 9 — Q Score | Pending |
| RENEW-01 | Phase 9 — Renewal | Pending |
| RENEW-02 | Phase 9 — Renewal | Pending |
| RENEW-03 | Phase 9 — Renewal | Pending |
| UI-01 | Phase 1 — Real Sign-In End to End | Pending |
| UI-02 | Phase 6 — Manager Upload Scoped by Org Chart | Pending |
| UI-03 | Phase 8 — Q Score | Pending |
| UI-04 | Phase 7 — Certificates with Expiry | Pending |
| UI-05 | Phase 10 — Tenant Isolation Proof & Honest Surface | Pending |

**Coverage:**
- v1 requirements: 55 total
- Mapped to phases: 55
- Unmapped: 0 ✓

Every v1 requirement maps to exactly one phase. No orphans, no duplicates.

Scope history: v1 was 49, rose to 61 when the data-layer and deployment categories were added,
then fell to **55** after the `origin/main` merge (`f111882`) — six DEPLOY requirements were
withdrawn as built or teammate-owned, leaving only DEPLOY-02 (seed data), which now sits in
Phase 1 as a sign-in dependency.

**By phase:**

| Phase | Requirements | Count |
|-------|--------------|-------|
| 1 — Real Sign-In End to End | DEPLOY-02, AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, AUTH-07, TENANT-01, UI-01 | 9 |
| 2 — Data Layer & Backend Parity | DATA-01, DATA-02, DATA-03, DATA-04, DATA-05 | 5 |
| 3 — Server-Side Authorization & Tenant Scoping | AUTH-06, TENANT-02, TENANT-03 | 3 |
| 4 — Role-Scoped Training on Authenticated Identity | TRAIN-01, TRAIN-02, TRAIN-03, TRAIN-04, TRAIN-05 | 5 |
| 5 — Document Intelligence Extraction | DOC-01, DOC-02, DOC-03, DOC-04, DOC-05, DOC-06, DOC-07 | 7 |
| 6 — Manager Upload Scoped by Org Chart | TENANT-04, UPLOAD-01, UPLOAD-02, UPLOAD-03, UPLOAD-04, UPLOAD-05, UI-02 | 7 |
| 7 — Certificates with Expiry | CERT-01, CERT-02, CERT-03, CERT-04, CERT-05, CERT-06, UI-04 | 7 |
| 8 — Q Score | QSCORE-01, QSCORE-02, QSCORE-03, QSCORE-04, QSCORE-05, QSCORE-06, UI-03 | 7 |
| 9 — Renewal | RENEW-01, RENEW-02, RENEW-03 | 3 |
| 10 — Tenant Isolation Proof & Honest Surface | TENANT-05, UI-05 | 2 |
| **Total** | | **55** |

---
*Requirements defined: 2026-08-14*
*Last updated: 2026-08-15 after post-merge roadmap revision (55 requirements, 10 phases)*
