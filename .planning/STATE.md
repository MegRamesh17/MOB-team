---
gsd_state_version: '1.0'
status: planning
progress:
  total_phases: 10
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-14)

**Core value:** An employee signs in once and gets training scoped to their role, where every question traces to a source someone approved — never to something the model invented.
**Current focus:** Phase 1 — Real Sign-In End to End

## Current Position

Phase: 1 of 10 (Real Sign-In End to End)
Plan: 0 of TBD in current phase
Status: Ready to plan
Last activity: 2026-08-15 — Roadmap rewritten after `origin/main` merge (`f111882`). Deployment phase deleted, sign-in promoted to Phase 1, 55/55 requirements mapped across 10 phases.

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**
- Last 5 plans: —
- Trend: —

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap rev 2]: Phase 1 is real sign-in end to end, not "build auth" — seed data (DEPLOY-02) is in scope because sign-in cannot resolve a role from empty tables.
- [Roadmap rev 2]: Deployment phase deleted. Six DEPLOY requirements withdrawn as built or teammate-owned; `migrate-database.yml` (`2d0586c`) already applies migrations with a `SchemaMigrations` tracking table.
- [Roadmap rev 2]: Data Layer & Backend Parity moved to Phase 2 — immediately after the first real dual-backend read exists to protect, before the large surface is built on top.
- [Roadmap rev 2]: Both hierarchies authoritative. `Employees.manager_id` answers which people report to you; `Departments > Teams > Roles` answers which role each holds. Governs UPLOAD-02 and QSCORE-06.
- [Roadmap]: Multi-tenancy woven, not a phase. TENANT-01 → Phase 1, TENANT-02/03 → Phase 3, TENANT-04 → Phase 6, TENANT-05 → Phase 10.
- [Merge]: Auth confirmed greenfield — zero auth files, libraries, or `azuread_application` resources across all 11 remote branches and full history. Credential-provider approach stands with Entra as the v2 drop-in.

### Blockers/Concerns

- **Q Score is settled — `docs/q-score.md` is authoritative.** Two definitions were live; they are now two named levels, `attempt_score` (per attempt, stored) feeding `Q Score = Coverage x Quality` (per employee, a view). Every other formula in the repo is marked superseded. Remaining work is coordination, not design: the `add-certificates` branch computes the attempt score under the name `q_score` and needs a rename plus two formula fixes, both listed in that document.
- **Deployment is a teammate dependency.** Publishing `api/` (Track 0 step 4, Track G step 2) and `web-app/` (Track G step 3) is not this roadmap's work. A phase here can be code-complete while nothing runs in Azure. Verify phases locally and in CI; do not treat an unpublished app as phase failure.
- **`tests.yml` holding no Azure credentials is a DESIGN PROPERTY, not a gap.** It exists so a pull request cannot deploy. DATA-03's Azure-SQL parity leg must NOT be discharged by adding secrets to `tests.yml` — that would hand PRs a live database. Phase 2 must find another route.
- **`terraform apply` stays gated to `main`, deliberately** (commit 4a601c1, to stop PRs applying infrastructure). `migrate-database.yml` chains off it via `workflow_run` filtered to `main`. Do not "fix" this.
- **Seed data has no owner and no runner.** `migrate-database.yml` applies schema but never runs `db/seed/org_seed.sql` or `seed_data.sql`, so the org tables are empty in Azure SQL. Phase 1 blocks on this.
- **Nobody owns the storage seam.** Teammate Track B extends adaptive selection inside `quizgen/` (SQLite); Track C puts scoring in `POST /quiz/submit` (Azure SQL). Divergence between them is exactly what Phase 2 exists to catch.
- **Brownfield.** Extend `src/quizgen/`, `App.jsx`, `registry.py`, `infra/`, and the merged workflows — do not rebuild them.
- **Offline path is a hard constraint.** `QUIZGEN_PROVIDER=mock` + `scripts/devserver.py` must survive every phase, especially the Phase 2 storage-interface refactor.
- **Public repository.** No credential, secret, or company document may be committed. Phases 1 and 5 both touch secrets.
- **Branch discipline.** Work happens on `ai-retry`; `first-ai-agent` is the push target. Never `main`.
- **Q Score becomes de facto performance data** once managers can see it (QSCORE-06). HR sign-off not obtained — surface before Phase 8.
- **Best-score-counts is on the watchlist.** PROJECT.md records the opposing argument. Revisit if Q Score inflation appears.

### Pending Todos

None yet.

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-08-15
Stopped at: ROADMAP.md rewritten to 10 phases post-merge; STATE.md and REQUIREMENTS.md traceability updated to 55 requirements
Resume file: None
