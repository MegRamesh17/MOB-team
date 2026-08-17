<!-- GSD:project-start source:PROJECT.md -->

## Project

**MOB-team — Role-Scoped Training & Certification**

A corporate training and compliance platform. An employee signs in and receives the
training their role requires — assembled from company PDFs held in Azure Blob Storage and
from a curated allowlist of external sources — takes an adaptive quiz that concentrates on
what they are weak at, and earns certificates that expire. Managers and above see their
org's standing through a Q Score. Built for Quadrant Technologies, with multi-tenancy so
other companies can be onboarded.

**Core Value:** An employee signs in once and gets training scoped to their role, where every question
traces to a source someone approved — never to something the model invented.

### Constraints

- **Repository visibility**: `MOB-team` is public — no credentials, secrets, or company documents may be committed. `.env` is correctly gitignored today; only `.env.example` is tracked.
- **Branching**: work does not go to `main`. The active branch is `ai-retry`; `first-ai-agent` is the established push target.
- **Cloud platform**: Azure — Blob Storage, SQL, Functions, App Service, Key Vault, AI Search, OpenAI, and now Document Intelligence. Provisioned through Terraform in `infra/`.
- **Cost**: Document Intelligence is a new billable Azure resource not currently in `infra/` or `requirements.txt`. Question generation with gpt-5 costs roughly a cent per question; the `mock` provider must remain viable for offline development.
- **Provenance**: every served question must trace to an approved source. This constrains generation, not just display — it is why open web search is excluded.
- **Answer-key secrecy**: the answer key must never reach the browser before an attempt is graded. A key in the client is a key in devtools.
- **Offline development must keep working**: `QUIZGEN_PROVIDER=mock` plus the SQLite dev server let the pipeline run with no Azure account. Azure SQL becoming the real store must not remove this. `tests.yml` depends on it — that workflow has no Azure credentials by design, so a PR cannot deploy anything.
- **No backend divergence**: SQLite and Azure SQL must not disagree. Something that works locally and fails in Azure is the failure mode to design against — it surfaces during a demo, which is the worst possible moment. Parity is enforced by running the same tests against both, not by care.
- **Deployment branch**: the deploy pipeline runs from `ai-retry`. `terraform.yml` currently gates deploy to `main`, which never fires on the branches this project actually uses.

<!-- GSD:project-end -->

<!-- GSD:stack-start source:STACK.md -->

## Technology Stack

Technology stack not yet documented. Will populate after codebase mapping or first phase.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
