# Roadmap: online sourcing, renewal, Q Score

Captured from a working session. **None of this is built.** What *is* built is the
structure that keeps it possible — see "Already done" at the end.

---

## 1. Course content comes mainly from online, not PDFs

The current assumption — that company PDFs contain the course material — turns out to be
wrong. The PDFs largely define **which roles exist and what they must cover**. The
substance (how CI/CD actually works, what SOLID means, current security practice) is
external, and changes faster than any internal document.

That inverts the pipeline:

```
company PDFs   ->  which roles exist, what each must cover   (the syllabus)
online sources ->  the actual teaching material              (the substance)
```

### Why this changes the safety model

The rule was two-sided: a company-specific claim needs a company document; general
knowledge doesn't but must not pose as policy. Online content is a **third** category —
it has a real citation, but a vendor's documentation is not your employer.

| Class | Source | May state company rules | Citation shown |
|---|---|---|---|
| `Documented` | company PDF | **yes** | document + page + quote |
| `ExternalSource` | web page | **no** | URL + retrieval date + quote |
| `RoleKnowledge` | model inference | **no** | none — labelled professional practice |

The failure to design against: a question sourced from AWS documentation rendering as
"company policy requires...". `validators.check_role_knowledge_voice` now covers
`ExternalSource` for exactly this.

### The unresolved question — needs a decision

**Who decides which online sources are trustworthy?**

Two options, and this is the single biggest open design question:

- **Curated allowlist.** A per-topic list of approved domains (vendor docs, standards
  bodies, the framework's own site). Slower to set up, but every citation is defensible
  and you can explain to an auditor why a source was trusted.
- **Open web search.** Faster, wider coverage, and the model may cite a random blog as
  the basis of a mandatory certification.

Recommendation: **allowlist**, for the same reason everything else here is conservative.
The cost is maintaining a list; the cost of the alternative is certifying people against
a stranger's blog post.

### What has to be built

- A fetcher: URL -> readable text (the existing chunker takes it from there unchanged)
- Source registry: which URLs feed which role/topic, when last retrieved
- Freshness: re-fetch on a schedule, detect when a page has changed materially
- Regeneration: when a source changes, mark questions derived from it for re-review

---

## 2. Annual re-certification

Certificates expire. On expiry the employee re-tests — and because the field moves, the
re-test should reflect **current** material, not the same questions a year on.

This is why online sourcing and renewal are the same project: renewal is only meaningful
if the content behind it can change.

### Design sketch

- Certificate definition carries `validity_months` (default 12)
- On expiry: certificate drops out of Q Score, training reopens, reminder tiers fire
- Renewal assessment draws from the **current** question bank, not the original one
- Questions whose source has changed since issue are prioritised — that is precisely
  what the learner has not been tested on

### Open question

Does a renewal need *new* questions, or the same bank re-served? Affects whether
regeneration must run before renewal season, and therefore the cost and the schedule.

---

## 3. Q Score

> **SUPERSEDED — see `docs/q-score.md` for the definition in use.**
>
> The formula below is kept as a record of the design session. It is no longer what gets
> built. Two things changed: the `0.75` floor was removed (it made an employee who
> completed every required course at the pass mark score 75, and it was unnecessary
> because a certificate is only issued on a pass, so the average is already floored
> there), and `Quality` now comes from a difficulty-weighted attempt score rather than a
> raw percentage. The shape — coverage scaled by quality — survived unchanged.
>
> Of the three open questions below, retakes and visibility have since been decided and
> are recorded in `docs/q-score.md`. Pace remains open.

Single 0-100 number per employee, role-relative.

```
Coverage = current certificates / required certificates
Quality  = (average score - pass mark) / (100 - pass mark)

Q Score  = 100 x Coverage x (0.75 + 0.25 x Quality)      <-- superseded
```

Coverage dominates because compliance is the point; quality modulates.

- **Expired certificates do not count** toward Coverage — that is the link back to §2
- Required count comes from the role (e.g. "7 per year for SDE2")
- Behavioural and technical certificates tracked separately so "strong technically, thin
  on conduct" is visible rather than averaged away

### Open questions

- **Retakes:** first passing score or best score? Unanswered. If best, retaking until
  the number looks good is rational and the score stops meaning anything.
- **Visibility:** employee-facing or manager-only? Once a manager sees it, it is de
  facto performance data and likely needs HR sign-off.
- **Pace:** "7 by year end" — someone at 2/7 in January is fine, in November is not.
  Q Score alone cannot tell them apart; it needs a pace indicator alongside.

---

## Already done — what keeps this open

Structural only. No behaviour has changed.

- `ProvenanceClass.EXTERNAL` exists and validators enforce it: an external question must
  carry a URL **and** a retrieval date, and may not speak with company authority.
- `Chunk` carries `source_type`, `source_url`, `fetched_at`. A web-sourced passage flows
  through the existing chunker, generator, grounding check and contradiction check with
  no changes.
- Grounding now applies to any retrieved source, not just PDFs — a web page is verified
  the same way, quote must appear verbatim in what was fetched.
- `Question` carries `source_url` and `source_fetched_at` for citation and staleness.

The generator, adaptive engine, scoring and review gate are all indifferent to where a
chunk came from. That was true by accident of the design; it is now true on purpose.

---

## Suggested order

1. **Decide the source policy** (allowlist vs open web). Blocks everything else.
2. Fetcher + source registry. Small, and immediately testable against the existing
   pipeline.
3. Certificate tables and expiry. Independent of §1, buildable in parallel.
4. Q Score. Needs §3 first — it is arithmetic over certificates.
5. Renewal. Needs §1 and §3 both.

The API and the schema migration (`011`) come before all of it. Nothing above matters
while the questions still live in a SQLite file on one laptop.
