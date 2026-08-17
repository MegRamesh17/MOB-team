# Q Score

One definition, replacing the two that were in flight.

## The two levels

**Attempt Score** — how well you did on one quiz. Stored, never changes.

```
attempt_score = 100 × Σ(weight of questions answered correctly)
                    ─────────────────────────────────────────────
                      Σ(weight of all questions in the quiz)

weights:  Easy 0.95   Medium 1.0   Hard 1.08
```

**Q Score** — one number per employee. Computed on read, never stored.

```
Coverage = min(1, unexpired certificates ÷ required certificates)
Quality  = average attempt_score across those unexpired certificates

Q Score  = Coverage × Quality
```

Both are 0–100.

## What it means

> Your Q Score is how well you did on your training, scaled down by how much of it
> you have actually finished.

Finish everything and average 90 → Q Score 90. Finish half of it and average 90 →
Q Score 45. Finish nothing → 0, however good you are at quizzes.

## Worked examples

Assume 7 required certificates and an 80% pass mark.

| Situation | Coverage | Quality | Q Score |
|---|---|---|---|
| All 7 done, scraped the pass mark | 1.00 | 80 | **80** |
| All 7 done, averaging 95 | 1.00 | 95 | **95** |
| 3 of 7 done, averaging 95 | 0.43 | 95 | **41** |
| All 7 done, but 3 have expired | 0.57 | 95 | **54** |
| Nothing done | 0.00 | — | **0** |

## Why there is no artificial floor

An earlier draft used `100 × Coverage × (0.75 + 0.25 × Quality)` to stop quality
swamping coverage. It had a bad property: an employee who completed every required
course at the pass mark scored 75, which reads like a failing grade for doing
everything asked of them.

The floor turns out to be unnecessary. **A certificate is only issued when you pass**,
so every attempt_score in the average is already at or above the pass mark. Quality
cannot realistically drop below ~80, and the floor was solving a problem that the pass
mark already solves.

## Rules

- **Retakes: the best score counts.** Known trade-off — retaking until the number
  improves is rational. Accepted deliberately; revisit if scores inflate.
- **Expired certificates count for nothing.** They leave Coverage and leave the
  average. This is what makes the number reflect *current* standing.
- **Coverage is capped at 1.** Extra training beyond what your role requires does not
  inflate the score.
- **No required courses?** Coverage is 1. Nothing was asked of you, so nothing is owed.
- **Behavioural and technical are computed separately**, plus combined, so "strong
  technically, thin on conduct" stays visible instead of being averaged away.
- **Visibility:** the employee, and everyone above them in the `Employees.manager_id`
  chain.

## Storage

**Attempt Score is stored** on `Certificates.q_score` — it is a fact about an event
that happened and never changes.

**Q Score is not stored.** It changes when nobody does anything: a certificate expires
overnight and the score must drop on its own. A stored copy goes stale silently, and a
stale compliance number is worse than none.

Expose it as a view — `vw_EmployeeQScore` — following `vw_LearnerTopicMastery`, which
already exists and is read by `function_app.py`. A view is always current and a manager
dashboard can sort and filter it like a table. Materialise it only if it becomes slow,
and then refresh on certificate issued, certificate expired, and required-list changed.

## Changes from what is on `add-certificates`

The branch is close. Three changes:

1. **Rename `_calculate_q_score` to `_calculate_attempt_score`**, and the column to
   `attempt_score`. It computes the per-attempt number, which is not the Q Score a
   manager sees. Same name for two different numbers is how "Q Score 82" becomes
   ambiguous between "82% compliant" and "scored 82 on one quiz".

2. **Weight the questions, not the multiplier.** Today the difficulty multiplier is
   averaged over *correct answers only*, so getting fewer easy questions right raises
   it. Weighting every question by difficulty — in both the numerator and the
   denominator — is simpler and behaves correctly: a hard question is worth more whether
   you get it right or wrong.

3. **Drop the consistency factor.** It is a cliff: a topic at 51% costs nothing, at 49%
   it costs a flat 8%. One question either side swings the number. The adaptive engine
   already concentrates quizzes on weak topics, so uneven performance already drags the
   score down over time — this taxed it a second time, discontinuously.

Nothing else on that branch changes. `Certificates` keeps `expires_at`; it needs one
addition, a `category` column for the behavioural/technical split.
