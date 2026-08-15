# Requirements: MOB-team — Role-Scoped Training & Certification

**Defined:** 2026-08-14
**Core Value:** An employee signs in once and gets training scoped to their role, where every question traces to a source someone approved — never to something the model invented.

## v1 Requirements

Requirements for this rebuild. Each maps to exactly one roadmap phase.

Requirements marked **(preserve)** describe behaviour that works today but changes
substantively under authentication and tenancy — the capability exists, the code path does
not survive untouched.

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
- [ ] **UPLOAD-02**: The set of roles a user may upload for is computed from their position in the Departments > Teams > Roles hierarchy
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

- [ ] **QSCORE-01**: Q Score is computed as `100 x Coverage x (0.75 + 0.25 x Quality)`
- [ ] **QSCORE-02**: Coverage counts only unexpired certificates, against the role's admin-configured required list
- [ ] **QSCORE-03**: When an assessment is retaken, the best score is the score of record
- [ ] **QSCORE-04**: Behavioural and technical Q Scores are reported separately as well as combined
- [ ] **QSCORE-05**: An employee can see their own Q Score
- [ ] **QSCORE-06**: Everyone above an employee in the org chart can see that employee's Q Score

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

- **QSCORE-07**: A pace indicator sits alongside Q Score, distinguishing 2-of-7 in January from 2-of-7 in November

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
| _(pending roadmap)_ | — | — |

**Coverage:**
- v1 requirements: 49 total
- Mapped to phases: 0
- Unmapped: 49 ⚠️

---
*Requirements defined: 2026-08-14*
*Last updated: 2026-08-14 after initial definition*
