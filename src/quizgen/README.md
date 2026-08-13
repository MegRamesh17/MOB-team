# quizgen — documents → adaptive quizzes

The AI content pipeline. Turns policy documents into a reviewed question bank, then
serves each learner a quiz weighted toward what they keep getting wrong.

**Runs with no keys at all.** `QUIZGEN_PROVIDER=mock` uses an offline generator, so the
whole pipeline — chunking, provenance, validation, adaptivity, scoring — can be
exercised before any Azure credential exists. Switching to `azure` changes question
*prose*, not pipeline shape.

## Setup

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Paste your keys into `.env` (it is gitignored), then:

```bash
PYTHONPATH=src .venv/bin/python -m quizgen.cli doctor
```

`doctor` checks all four services and — importantly — tells you what your **deployment
names** actually are. Using the model name when the deployment was created under a
different name is the most common Azure OpenAI failure.

## Running it

Documents come from Azure Blob Storage — that is the default source. Both containers
are read in one pass:

```bash
PYTHONPATH=src .venv/bin/python -m quizgen.cli ingest
```

`--container <name>` reads just one. `--source local` reads `data/documents/` instead.

## Containers are the role taxonomy

`mobtrainingstorage` splits documents by who they apply to, and that split is the most
reliable role signal available — it reflects a filing decision a human made, not a guess
from the prose:

| Container | Scope | Meaning |
|---|---|---|
| `company-docs` | `ALL` | every role must know it |
| `software-engineering-docs` | `SWE` | Software Engineers only |

Set in `.env`:

```
DOCUMENT_CONTAINERS=company-docs:ALL,software-engineering-docs:SWE
```

Add a pair when a new role container appears. Role profiles inherit correctly: a
Software Engineer is tested on company-wide topics **and** engineering topics, while
company-wide questions never include role-specific material.

```bash
PYTHONPATH=src .venv/bin/python -m quizgen.cli generate --per-chunk 4
```

| Command | Purpose |
|---|---|
| `doctor` | check .env credentials, endpoints, deployment names |
| `ingest` | blob container → section-tagged chunks, via `pdf_extractor.py`. `--source local` for disk |
| `roles` | derive role → topic profiles from the documents |
| `generate` | chunks → candidate questions, validated, PendingReview |
| `review` | approve/reject; `--approve-all` for dry runs only |
| `quiz` | take an adaptive quiz |
| `simulate` | auto-answer rounds to watch adaptivity work |
| `status` | bank counts and per-topic mastery |

## How it uses `pdf_extractor.py`

`sources.py` calls `list_pdfs_in_container()` and `extract_text_from_blob_pdf()` per
file, deliberately **not** `extract_text_from_container()`.

That function concatenates every PDF into one string. Convenient for a single prompt,
but it destroys document identity — once five policies are one blob, a question can no
longer say which document and page it came from. Citations, the cross-document
contradiction check, and "see Security Policy p.3" on a wrong answer all depend on
keeping documents separate.

So: same extractor, called per-file, chunked per-document.

## Environment names

`config.py` reads the Group7 names your resources were issued with, and falls back to
the `AZURE_OPENAI_*` names that `content_agent.py` and `chatbot.py` already use. Both
work from one `.env`.

The `7-7` / `7-8` mismatch in the key names is how they were issued. `AISearchEnpoint`
is likewise the issued spelling; the corrected spelling is also accepted, so fixing it
later breaks nothing.

Note these names contain hyphens, so they **cannot be `export`ed from a shell**.
python-dotenv loads them from `.env` directly, which is why they work here.

## The safety rules

**A company-specific claim requires a source. General professional knowledge does not,
but must not be dressed up as company policy.**

Every question carries a `provenance_class`:

| | Documented | RoleKnowledge |
|---|---|---|
| Source | verbatim quote required | none — professional practice |
| May state company rules | yes | **no, enforced in `validators.py`** |
| Review | check against the quote | mandatory expert review |

A RoleKnowledge question saying "according to company policy…" or inventing a numeric
obligation is rejected before storage. That is what makes ungrounded role-knowledge
questions safe.

**Nothing reaches a learner unreviewed.** Generated questions are `PendingReview`;
quiz assembly only reads `Approved`.

**The model never decides a score.** It may judge whether one free-text answer matches
the key. Totals, the pass bar and mastery bands are arithmetic — a disputed result has
to be reproducible.

## Overlap with `content_agent.py` — needs a team decision

`content_agent.py` and this package both turn PDFs into quiz questions. **Pick one**;
running both produces two question banks with different guarantees.

The differences, honestly:

| | `content_agent.py` | `quizgen` |
|---|---|---|
| Input | whole container as one prompt | per-document, chunked by section |
| Long documents | will exceed context | chunked, no limit |
| Provenance | none | document + page + verbatim quote |
| Grounding check | none | quote must appear in the passage |
| Review gate | none | required before serving |
| Adaptivity | none | weak-topic targeting, difficulty calibration |
| Runs without keys | no | yes (mock) |

`content_agent.py` is simpler and was the right first step. `quizgen` is what you need
once questions have to be defensible. I have not modified or deleted it — that call is
yours and your teammate's.

The migration, if you want it: keep `generate_modules_for_role()` as the public entry
point and have it call `sources.chunks_from_blob_container()` then the generator, so
callers don't change.

## Tests

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

29 tests. Most encode a bug that actually occurred — section detection collapsing to one
topic, under-sampled topics being starved, alphabetical topic starvation, cross-unit
contradictions being missed.

`test_blob_source.py` stubs `pdf_extractor` to test the blob path without Azure. It
checks the thing that matters there: each PDF keeps its own document identity and page
numbers, a scanned PDF that extracts to nothing is skipped rather than killing the run,
and generated questions still carry citations.
