"""
Company-level isolation checks, applied to every chunk before it reaches the
search index.

`role_scope` (see models.py / search_index.py) already answers "which roles
inside a company can see this chunk". Nothing in the pipeline today answers
"which company does this chunk belong to at all" — every chunk is implicitly
visible to every company that ever queries the index. That's invisible right
now because only one company's documents exist in the index, but it becomes a
real leak the moment a second company's data is ingested alongside it.

This module is deliberately narrow: it does not touch ingest.py, models.py, or
search_index.py, since those are owned elsewhere and a schema change there
(adding a real `company_id` field to Chunk and to the index) needs to be a
reviewed decision, not something bolted on unilaterally. See
docs/company-isolation-gap.md for the full proposal.

Until that schema change lands, this module is the enforcement point: nothing
should be uploaded to the search index without passing through
`stamp_company_id` and `validate_company_id` first.
"""

from __future__ import annotations

from typing import List, Sequence


class IsolationError(Exception):
    """Raised when a chunk cannot be safely attributed to a company."""


# Deliberately NO default value here, unlike role_scope's "ALL" default.
# role_scope defaulting to "ALL" is safe: it means "visible to everyone
# WITHIN one company". A company_id default would mean "visible to every
# company", which is the exact leak this module exists to prevent. A missing
# company_id must fail loudly, not fall back to something permissive.
_NO_DEFAULT = object()


def stamp_company_id(chunk_dict: dict, company_id: str) -> dict:
    """
    Attach a company_id to a chunk (as a plain dict, since Chunk itself does
    not have this field yet) before it's handed to search_index.upload().

    Takes a dict rather than a Chunk so this can be used today without a
    models.py change, and swapped for a real Chunk.company_id field later
    with minimal disruption at the call site.
    """
    if not company_id or not company_id.strip():
        raise IsolationError(
            "refusing to stamp an empty company_id onto chunk_id={!r} — "
            "an unscoped chunk must not be uploaded".format(
                chunk_dict.get("chunk_id", "<unknown>")
            )
        )
    stamped = dict(chunk_dict)
    stamped["company_id"] = company_id.strip()
    return stamped


def validate_company_id(chunk_dict: dict) -> None:
    """
    Hard gate before upload. Raises IsolationError rather than returning a
    reason string (unlike validators.py's check_* functions) because this is
    not a quality judgment call for a human reviewer — a chunk with no
    company_id is not safe to index under any circumstance, so there is
    nothing to review, only something to reject.
    """
    company_id = chunk_dict.get("company_id")
    if not company_id or not str(company_id).strip():
        raise IsolationError(
            "chunk_id={!r} has no company_id — cannot upload to a shared "
            "index without one".format(chunk_dict.get("chunk_id", "<unknown>"))
        )


def stamp_and_validate_batch(chunk_dicts: Sequence[dict], company_id: str) -> List[dict]:
    """
    Convenience wrapper for the common case: stamp a whole ingestion batch
    with one company_id (today, effectively always the single demo company),
    then validate every result before it's passed to search_index.upload().

    Fails the whole batch on the first bad chunk rather than silently
    dropping it — a partially-tagged batch is exactly the kind of quiet gap
    that's easy to miss until a second company's data is in the same index.
    """
    stamped = [stamp_company_id(c, company_id) for c in chunk_dicts]
    for chunk in stamped:
        validate_company_id(chunk)
    return stamped