# Gap: no company-level isolation in the search index

## The problem

`search_index.py` already enforces **role-level** scoping correctly: every chunk
carries a `role_scope` field, and `retrieve()` applies a server-side filter so a
chunk approved for SDE1-3 is never returned to a Director query, no matter how
well it matches (see the `role_scope/any(...)` filter in `retrieve()`).

There is no equivalent for **company**. Every chunk in the index is implicitly
shared across every company that ever uses this platform. Today that's invisible
because we only have one demo company's documents in the index — but the moment
a second company's PDFs get ingested into the same index, their chunks become
retrievable by *any* query, from any company, with no filter stopping it. This
is the actual security boundary that matters if this platform is ever meant to
serve more than one company: a leak here means Company A's chatbot or quiz
generator could surface Company B's confidential policy text.

This is not a hypothetical for "later" — it's a gap in the schema itself, so it's
cheaper to close now than to retrofit once real data from multiple companies is
sitting in one index.

## Proposed fix — mirror the `role_scope` pattern

Add a `company_id` field to the index schema, chunk model, upload, and retrieve
functions, exactly the way `role_scope` already works:

**1. Schema (`create_index`)** — add a filterable field:
```python
SimpleField(name="company_id", type=SearchFieldDataType.String, filterable=True),
```
(Simple string, not a collection like `role_scope` — a chunk belongs to exactly
one company, whereas a chunk can be approved for multiple roles at once.)

**2. Upload (`upload`)** — tag every chunk at index time:
```python
doc["company_id"] = chunk.company_id  # required, no default/"ALL" fallback
```
Unlike `role_scope`, this should have **no permissive default**. `role_scope`
defaults to `"ALL"` on purpose — a document with no stated audience is meant to
be visible to everyone *within one company*. `company_id` should have no such
default: a chunk with a missing or blank `company_id` should fail to upload,
not silently become visible to every company. That asymmetry is the actual
security property we want.

**3. Retrieve (`retrieve`)** — require and enforce the filter:
```python
def retrieve(query: str, company_id: str, role: str = "", topic: str = "", limit: int = 5) -> List[Chunk]:
    ...
    safe_company = company_id.replace("'", "''")
    filters.append("company_id eq '{}'".format(safe_company))
```
Making `company_id` a required (non-default) parameter, rather than optional
like `role`/`topic`, is deliberate — it should be structurally impossible to
call `retrieve()` without specifying which company's data you're allowed to
see. An optional parameter here just recreates the same silent-bypass problem
we already flagged with `QUIZGEN_AUTO_APPROVE`.

**4. Chunk model (`models.py`)** — add `company_id: str` to the `Chunk`
dataclass, same as the existing `role_scope`/`topic`/`doc_title` fields.

**5. Ingestion (`ingest.py` / wherever chunks are first constructed)** — every
chunk needs a `company_id` set at creation time, not left to default. For the
current single-company demo, this can be a hardcoded constant (e.g.
`"demo-co"`), but it should be threaded through explicitly rather than assumed,
so a second company later is "pass a different value in," not "add the
concept."

## What this does NOT cover

- **Azure SQL isolation** — readings, quiz results, employee records, etc. need
  the same `company_id` column + `WHERE company_id = ?` treatment. Separate
  piece of work, same principle.
- **Blob Storage isolation** — containers/paths should be company-scoped before
  multi-company ingestion happens (e.g. `company-a/software-engineering/`
  rather than a shared `software-engineering-docs` container).
- **Auth** — none of this matters unless a user's `company_id` is reliably
  resolved at login and can't be spoofed by the client. That's a separate,
  larger piece (see auth/session design, not yet built).

## Scope for now

We're a single demo company, so this isn't urgent to actually deploy. But the
schema change is cheap to make now and expensive to retrofit later (every
already-indexed chunk would need re-tagging). Flagging as a known gap either
way — recommend closing it before any second company's documents are ingested.
