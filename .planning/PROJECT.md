# MOB-team — Role-Scoped Training & Certification

## What This Is

A corporate training and compliance platform. An employee signs in and receives the
training their role requires — assembled from company PDFs held in Azure Blob Storage and
from a curated allowlist of external sources — takes an adaptive quiz that concentrates on
what they are weak at, and earns certificates that expire. Managers and above see their
org's standing through a Q Score. Built for Quadrant Technologies, with multi-tenancy so
other companies can be onboarded.

## Core Value

An employee signs in once and gets training scoped to their role, where every question
traces to a source someone approved — never to something the model invented.

## Business Context

- **Customer**: Companies that must certify employees against role-specific training. First tenant is Quadrant Technologies.
- **Revenue model**: Not yet defined — multi-tenancy is being built in on the assumption this serves more than one company.
- **Success metric**: Q Score — a 0-100 role-relative number per employee, driven by current (unexpired) certificate coverage.
- **Strategy notes**: `docs/roadmap-online-sourcing-and-renewal.md` — captures the online sourcing / renewal / Q Score design session.

## Requirements

### Validated

Inferred from the existing codebase on `ai-retry`. These work today and are not being rebuilt.

- ✓ PDF ingestion reads from Azure Blob Storage, not local disk — `src/quizgen/sources.py`
- ✓ Role scope derives from blob container name (`company-docs`, `software-engineering-docs`), carried on every `Chunk` and inherited by every `Question` — `sources.py:90`, `models.py:100`
- ✓ Chunking preserves document identity, page range, and section topic, so a question can cite where it came from — `ingest.py`
- ✓ Question generation grounded in retrieved chunks, with an Azure OpenAI provider and a keyless `mock` provider for offline work — `src/quizgen/llm/`
- ✓ Three-class provenance model (`Documented` / `ExternalSource` / `RoleKnowledge`) with validators enforcing that only company documents may speak with company authority — `validators.py`, `models.py`
- ✓ Grounding check: a quoted passage must appear verbatim in the retrieved source
- ✓ Adaptive quiz assembly targets weak subjects once evidence passes a floor — `adaptive.py`. Measured across five rounds in `docs/running-locally.md`
- ✓ Server-side grading; `/api/quiz/start` never sends the answer key to the browser — `scripts/devserver.py`, `api/function_app.py`
- ✓ Vetted source registry — a plain-text allowlist of approved external URLs with per-role and per-topic scoping — `registry.py`
- ✓ Web fetcher turning allowlisted URLs into chunks at corpus-build time, never at generation time — `web.py`
- ✓ Azure AI Search indexing and retrieval — `search_index.py`, `retrieval.py`
- ✓ React UI wired to the real backend, with mock panels visibly labelled rather than passed off as measured — `web-app/src/App.jsx`
- ✓ Terraform infrastructure: network, Key Vault, SQL, Functions, App Service, storage, comms — `infra/`
- ✓ Azure SQL schema through migration 011: employees, courses, quiz questions, completions, attempts, org structure, course roles, multi-tenancy, readings, quizgen bank — `db/migrations/`

### Active

The rebuild, in intended order. All are hypotheses until shipped.

- [ ] One storage interface serves both Azure SQL and SQLite, and the same tests pass against both — a difference between them fails CI rather than surfacing at demo time
- [ ] Database migrations and seed data are applied to Azure SQL by a pipeline, not by hand
- [ ] The API and the web app are deployed by a pipeline that runs from `ai-retry`
- [ ] A deployed environment is verifiable end to end: the deployed API reaches Azure SQL and reports a real question-bank count
- [ ] User authenticates with a username and password; credentials are stored hashed, never plaintext
- [ ] Credential store is a gitignored JSON file behind a provider interface, so Entra ID can replace it without touching callers
- [ ] A signed-in user's role determines what they can see and do — access is enforced server-side, not in the UI
- [ ] Employee sees only the training their role requires
- [ ] Manager and above can upload PDFs for the roles beneath them in the org chart
- [ ] Upload targets are computed from Departments > Teams > Roles, not from a separate permission list
- [ ] PDF text extraction uses Azure Document Intelligence, replacing pypdf — handling scanned documents, tables, and multi-column layout
- [ ] Document Intelligence output preserves page and section structure well enough for defensible citation
- [ ] Every query is tenant-scoped; a user of one company can never read another company's data
- [ ] Employee earns a certificate on passing an assessment
- [ ] Certificates carry `validity_months` and expire; an expired certificate stops counting toward Coverage and reopens training
- [ ] Q Score computed per employee as `Coverage x Quality` — see `docs/q-score.md`
- [ ] Behavioural and technical certificates tracked separately, so a gap in one is visible rather than averaged away
- [ ] Q Score visible to the employee and to everyone above them in the org chart
- [ ] Renewal assessment draws from the current question bank, prioritising questions whose source changed since the certificate was issued
- [ ] Demo sign-in in `web-app/` replaced with real authentication
- [ ] Mock panels in `web-app/` converted to real data or removed

### Out of Scope

- **Open web search for source material** — the model may only draw on blob PDFs and the vetted allowlist in `registry.py`. An open search lets a random blog become the basis of a mandatory certification.
- **Model-selected sources** — the AI does not choose what to trust. A human curates `registry.py`.
- **Plaintext passwords, or any credential committed to the repo** — `MOB-team` is a public repository.
- **Entra ID / real SSO** — deferred, not rejected. The auth provider interface must not block it.
- **Gamification panels** (badges, companion pet, focus timer, teammates) — stay labelled mock until there is a backend. They are the product's design direction, not measurements.
- **Pace indicator alongside Q Score** — acknowledged gap (someone at 2/7 in January differs from 2/7 in November), deferred to a later milestone.

## Context

**Where the work stands.** The engine is substantially built and the pipeline runs end to
end offline — 15 chunks to 20 questions to a graded quiz, verified from empty. What is
missing is everything that makes it a real application rather than a demo: there is no
authentication at all, no certificates, no Q Score, and PDF extraction is naive.

**The auth gap is total.** `api/function_app.py:122` documents its own `_caller_id` as a
demo stub that trusts an `x-learner-id` header, with the comment "Replace with Entra
before real use." `PyJWT` sits in `requirements.txt` imported by nothing.

**The RBAC schema already exists but has no code behind it.** Migration
`006_create_org_structure.sql` defines Departments > Teams > Roles with
`access_role ∈ (employee, manager, director, admin, executive)` and a `level` seniority
rank. Its own comment says `access_role` "is what actually gates permissions in the app."
Nothing reads it yet. This is why authorization is the first phase — the shape is already
decided, it just needs implementing.

**Two source types, one chunker.** Blob PDFs and allowlisted web pages both become
`Chunk`s and flow through the same grounding, contradiction, and generation path. The
roadmap doc notes this was "true by accident of the design; it is now true on purpose."

**A stale document.** `docs/roadmap-online-sourcing-and-renewal.md` argues that company
PDFs are the wrong primary content source and the substance should come from online. This
project keeps PDFs as the primary source and treats vetted online sources as a supplement.
The doc's section 1 framing is superseded, and so is its section 3 formula — Q Score is
now defined in `docs/q-score.md`, which is the single source of truth for it. Section 2
(renewal) still holds.

**Local storage is not a migration burden.** The database work so far was implemented
locally only, so moving to Azure SQL as the real backing store carries no data migration.

## Constraints

- **Repository visibility**: `MOB-team` is public — no credentials, secrets, or company documents may be committed. `.env` is correctly gitignored today; only `.env.example` is tracked.
- **Branching**: work does not go to `main`. The active branch is `ai-retry`; `first-ai-agent` is the established push target.
- **Cloud platform**: Azure — Blob Storage, SQL, Functions, App Service, Key Vault, AI Search, OpenAI, and now Document Intelligence. Provisioned through Terraform in `infra/`.
- **Cost**: Document Intelligence is a new billable Azure resource not currently in `infra/` or `requirements.txt`. Question generation with gpt-5 costs roughly a cent per question; the `mock` provider must remain viable for offline development.
- **Provenance**: every served question must trace to an approved source. This constrains generation, not just display — it is why open web search is excluded.
- **Answer-key secrecy**: the answer key must never reach the browser before an attempt is graded. A key in the client is a key in devtools.
- **Offline development must keep working**: `QUIZGEN_PROVIDER=mock` plus the SQLite dev server let the pipeline run with no Azure account. Azure SQL becoming the real store must not remove this. `tests.yml` depends on it — that workflow has no Azure credentials by design, so a PR cannot deploy anything.
- **No backend divergence**: SQLite and Azure SQL must not disagree. Something that works locally and fails in Azure is the failure mode to design against — it surfaces during a demo, which is the worst possible moment. Parity is enforced by running the same tests against both, not by care.
- **Deployment branch**: the deploy pipeline runs from `ai-retry`. `terraform.yml` currently gates deploy to `main`, which never fires on the branches this project actually uses.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Data layer and deployment pipelines come before authorization | Migrations have never been applied to Azure SQL by anything — `tests.yml` guards against UTF-16 breaking "sqlcmd in the deploy workflow," but no such step exists in `terraform.yml`. Auth cannot read a role or company from a database whose tables were never created. Getting deploy working first also makes every later phase demoable. | — Pending |
| SQLite and Azure SQL both supported, with parity enforced by tests | A demo must work against real Azure SQL, but `tests.yml` deliberately holds no Azure credentials so a PR cannot deploy. Keeping both and running the same suite against each is what prevents "works on my machine, fails in the demo." | — Pending |
| Deploy pipeline runs from `ai-retry`, not `main` | Commit 4a601c1 gated `terraform apply` to main-only to stop PRs applying infrastructure. The side effect is that nothing deploys from the branches this project uses. | — Pending |
| Migrations 003 and 010 stay in place, documented as superseded | 011's header already explains why they are unused — they were designed around `content_agent.py` and have nowhere to put a citation, review status, or role scope. Deleting them would break replaying history against a fresh database. | — Pending |
| Authorization is the next phase, before any new features | The RBAC schema exists but nothing enforces it. Every later feature (role-scoped training, manager upload, tenant isolation, Q Score visibility) depends on knowing who the caller is. Building them first means retrofitting auth through all of them. | — Pending |
| Credentials in a gitignored JSON file, hashed, behind a provider interface | Fast to build and needs no Azure dependency, while the interface keeps Entra reachable. Hashing and gitignoring are non-negotiable because the repo is public. | — Pending |
| Azure SQL is the real backing store; SQLite stays for local development | Migrations 001–011 already target Azure SQL, and the local DB work carries no migration burden. Keeping SQLite preserves the four-command offline path. | — Pending |
| Azure Document Intelligence replaces pypdf for extraction | pypdf returns nothing for scanned PDFs and flattens tables and multi-column layouts. Defensible citation needs reliable page and section structure. | — Pending |
| External sources restricted to the curated allowlist in `registry.py`; no open web search | This is the roadmap's own recommendation. The cost is maintaining a list; the cost of the alternative is certifying people against a stranger's blog post. | — Pending |
| Upload permissions derived from the org chart, not an explicit grant list | Departments > Teams > Roles already encodes who manages whom. A separate permission list would duplicate it and drift from it. | — Pending |
| Q Score is `Coverage x Quality`, defined in `docs/q-score.md`, and that definition is authoritative | Two different numbers were being built under one name — a per-attempt performance score on the `add-certificates` branch, and a per-employee compliance rollup in the roadmap doc. They are now two named levels, with the attempt score feeding the rollup. Any other definition in the repo is superseded, including this project's own earlier `0.75`-floor formula. Work on `add-certificates` needs three changes, listed in `docs/q-score.md`. | — Pending |
| Best score counts for retakes | Rewards mastery and re-learning. **Known tradeoff:** `docs/roadmap-online-sourcing-and-renewal.md` argues the opposite — if best score counts, retaking until the number looks good is rational and the score stops meaning anything. Chosen deliberately with that objection on the record. Revisit if Q Score inflation appears. | ⚠️ Revisit |
| Multi-tenancy built properly from the start, not deferred | Migration 009 already added the Companies root and `company_id` columns. **Known tradeoff:** this adds tenant-scoping work to every phase rather than concentrating it in one later migration. Chosen deliberately. | ⚠️ Revisit |
| Q Score visible to the employee and everyone above them in the org chart | Full hierarchy visibility. **Note:** the roadmap flags that once a manager sees it, it is de facto performance data and likely needs HR sign-off. That sign-off is not yet obtained. | ⚠️ Revisit |
| Certificate expiry is in scope, and drives Q Score Coverage | Without expiry, Coverage only ever rises and Q Score stops reflecting current standing. Expiry is what makes the number mean something over time. | — Pending |
| `web-app/` is kept and extended, not rewritten | 2,059 lines already wired to the real backend, with working quiz, lesson, and grading screens and honestly-labelled mock panels. The gap is auth and the mock panels, not the app. | — Pending |
| Online sourcing stays; the roadmap doc's section 1 is superseded | The doc argued PDFs were the wrong primary source. PDFs via Document Intelligence remain primary; allowlisted web sources supplement them. | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-08-14 after initialization*
