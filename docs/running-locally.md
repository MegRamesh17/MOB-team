# Running the app locally

Four commands from a fresh clone to a working quiz in a browser. No Azure account, no
credentials, no Key Vault access, no SQL firewall rule.

```bash
python scripts/make_sample_pdfs.py                                    # sample documents
PYTHONPATH=src python -m quizgen.cli ingest --source local --pdf-dir data/documents
QUIZGEN_PROVIDER=mock PYTHONPATH=src python -m quizgen.cli generate
python scripts/devserver.py                                           # http://localhost:8000
```

`QUIZGEN_PROVIDER=mock` needs no API key and no network — it builds questions by pattern
matching over the text. Drop it to generate with gpt-5, which needs the `.env` described
in the README and costs roughly a cent per question.

Verified from empty: 15 chunks → 20 questions → a served quiz that grades 100% when
answered correctly.

---

## What each piece is

| Path | What it is |
|---|---|
| `scripts/make_sample_pdfs.py` | Three fictional training PDFs. The real documents are company material and are not in this repo. |
| `scripts/devserver.py` | Local API + static host. Serves the `docs/frontend-spec.md` contract from SQLite. |
| `web/index.html` | Working reference UI. One file, no build step, no dependencies. |
| `api/function_app.py` | The **real** API. Azure Functions + Azure SQL. Not used locally. |

### The dev server is not the deployed API

They serve the same contract and share the parts that matter — question selection
(`quizgen.adaptive.build_quiz`), grading arithmetic, the pass mark, mastery bands. A quiz
assembled locally is assembled by exactly the code that assembles one in Azure.

They differ in storage (SQLite vs Azure SQL) and identity (a header vs Entra). So this is
a faithful demo of *behaviour* and an unfaithful demo of *infrastructure*. Do not treat a
working local demo as evidence that the deployment works.

**The dev server is not a deployment target.** No auth, single process, serves to anything
that connects.

---

## Swapping in your own UI

Every call in `web/index.html` goes through one `api()` function, and `API_BASE` at the
top of the script is the only thing that points it at a host. To build the UI properly:

- **React/Vue dev server on another port** — leave `API_BASE` empty and proxy `/api` to
  `localhost:8000`, or set `API_BASE = "http://localhost:8000"`. CORS is already open on
  the dev server.
- **Against deployed Azure** — set `API_BASE` to the Function App URL. Nothing else
  changes; the payloads are identical.

### One property to preserve

`/api/quiz/start` does not include the answer key. No `is_correct`, no
`accepted_answers`, no `explanation` — only option ids and text. The key is revealed by
`/api/quiz/submit` once the attempt is graded and closed.

If you rebuild this screen, keep that. Sending the key and hiding it in the UI puts every
answer one devtools panel away, and there is no way to trust a score after that.

---

## Endpoints

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | Bank size, how many are servable |
| GET | `/api/me` | This learner's mastery per subject, weak list |
| GET | `/api/topics` | Topics with question counts |
| GET | `/api/questions?topic=&limit=` | Browse the bank (no keys) |
| POST | `/api/quiz/start` | Assemble an adaptive quiz |
| POST | `/api/quiz/submit` | Grade, persist, return the key and explanations |

The learner is the `x-learner-id` header, defaulting to `demo-learner`. Changing it in
the UI's top-right box switches learner, which is the quickest way to see adaptive
behaviour diverge between two people against the same bank.

---

## Seeing the adaptive behaviour

It needs several rounds, because a topic is only judged "weak" once there is enough
evidence — three answers by default. One bad answer is not a weakness.

Take a quiz, deliberately fail everything from one document, and repeat. Measured on the
real bank:

```
round 1:  62.5%   targeted=0
round 2:  87.5%   targeted=0    weak=[Behavioral Compliance]
round 3:  50.0%   targeted=4    -> Behavioral Compliance  4 of 8 questions  (0/3 correct)
round 4:  25.0%   targeted=3    -> Behavioral Compliance  6 of 8 questions  (0/7 correct)
round 5:  25.0%   targeted=3    -> Behavioral Compliance  6 of 8 questions  (0/13 correct)
```

The score *falling* is the feature working: the quiz has concentrated on the material
this learner cannot do.

### Why mastery is measured per document, not per section

`QUIZGEN_MASTERY_GRAIN` defaults to `subject` (the source document) rather than `topic`
(the section heading). This is not cosmetic — at topic grain the feature above does not
work at all.

The real bank holds 235 questions across 112 section-level topics: 2.1 questions per
topic, against an evidence floor of 3 answers. For most topics it is arithmetically
impossible to ever gather enough evidence to be judged weak. Measured over six rounds at
topic grain: zero topics targeted, and no topic ever exceeded one answer. Grouping by
document gives 32–44 questions per subject and clears the floor within a single quiz.

Set `QUIZGEN_MASTERY_GRAIN=topic` for finer targeting once there are roughly 6+ questions
per section to support it. Generating more questions per chunk is what makes that
possible.

---

## Troubleshooting

**"No approved questions in the bank"** — questions generated before auto-approve was the
default are still `PendingReview`, and only `Approved` questions are ever served:

```bash
PYTHONPATH=src python -m quizgen.cli review --approve-all
```

**"Unknown attempt" on submit** — in-flight quizzes are held in memory, so restarting the
dev server abandons them. Start a new quiz.

**"No approved questions are in scope for role X"** — deliberate. Rather than quietly
widening to the whole bank, which would serve exactly the material a role scope exists to
withhold, it fails. Start a quiz with no role selected, or add sources approved for that
role in `src/quizgen/registry.py`.

**Port 8000 in use** — `PORT=8080 python scripts/devserver.py`.

---

## Running the React UI

Two servers: the Python API, and Vite for the frontend.

```bash
python scripts/devserver.py
```

```bash
cd web-app && npm install && npm run dev
```

Then open **http://localhost:5173**. Vite proxies `/api` to the Python server on 8000,
so the browser only ever makes same-origin requests.

`web/index.html` is still there — a dependency-free reference UI served by the Python
server at http://localhost:8000. Useful when you want to check the API without Node.

### The frontend has no keys and needs none

Azure credentials live server-side. The browser calls its own origin; the API talks to
Azure. There is no `.env` in `web-app/`, and nothing there should ever need one.

To point at deployed Azure instead of the local server:

```bash
VITE_API_BASE=https://<your-function-app>.azurewebsites.net npm run dev
```

Every call goes through `web-app/src/api.js`, so that is the only thing that changes.

### What's real and what isn't

**Real** — driven by the question bank and this learner's actual answers:
trainings, modules, lesson text, quiz questions, grading, scores, certificates,
Q score, mastery breakdown.

**Still mock** — no backend exists for these yet, and each is tagged in the UI so a
demo cannot mistake them for measurements: badges, the companion pet, focus timer,
teammates, and the manager's team view.

### Grading is a round trip, on purpose

`/api/quiz/start` sends option ids and text — no `is_correct`, no explanations. When a
learner answers, the UI calls `POST /api/quiz/answer`, which grades that one question
server-side and returns the verdict. That is what makes immediate feedback possible
without the answer key ever reaching the browser.

If you rebuild the quiz screen, keep this. A key in the client is a key in devtools,
and no score is trustworthy after that.

### Sign-in is not real

Both personas are demo stand-ins; no password is checked. The learner is passed as the
`x-learner-id` header, which the deployed API ignores in favour of the
platform-injected Entra principal. Signing in as each persona keeps their quiz
histories separate, which is the quickest way to watch adaptive targeting diverge
between two people against the same bank.
