"""
Q Score — how well you did on your training, scaled by how much of it you have finished.

The definition lives in `docs/q-score.md`; this is the arithmetic. Two levels, deliberately
named apart, because the same word meaning two things is what this replaced:

    attempt_score   one quiz. Difficulty-weighted. Stored on the certificate, never changes.
    Q Score         one employee. Coverage x Quality. Computed on read, never stored.

WHY Q SCORE IS NOT STORED
It changes when nobody does anything. A certificate expires at midnight and the number has
to fall on its own. A stored copy goes stale silently, and a stale compliance number is
worse than no number — it reads as "you are fine" while you are not. So it is derived here
on every read, from rows that ARE facts: certificates issued, and their expiry dates.

WHY THERE IS NO ARTIFICIAL FLOOR
An earlier draft multiplied by (0.75 + 0.25 x Quality) so quality could not swamp coverage.
It had a bad property: finishing every required course at exactly the pass mark scored 75,
which reads as a failing grade for doing everything asked of you. The floor turns out to be
unnecessary — a certificate is only issued on a PASS, so every score in the average is
already at or above the pass mark, and the pass mark is the floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

# Difficulty weights. Applied to every question in both numerator and denominator, so a
# hard question is worth more whether you get it right or wrong.
#
# Deliberately NOT the "average the multiplier over correct answers only" approach, which
# has the property that getting fewer easy questions right RAISES your multiplier.
DIFFICULTY_WEIGHTS = {"EASY": 0.95, "MEDIUM": 1.0, "HARD": 1.08}

DEFAULT_VALIDITY_MONTHS = 12
CATEGORIES = ("behavioural", "technical")


def weight_for(difficulty: str) -> float:
    return DIFFICULTY_WEIGHTS.get(str(difficulty or "Medium").strip().upper(), 1.0)


def attempt_score(graded: Sequence[dict]) -> float:
    """
    Score one attempt, 0-100, weighted by question difficulty.

        100 x sum(weight of questions answered correctly) / sum(weight of all questions)

    `graded` is [{"difficulty": "Hard", "correct": True}, ...].

    An empty attempt scores 0 rather than raising: an attempt with no questions is a bank
    problem, and a learner should not see a stack trace because of one.
    """
    total = sum(weight_for(g.get("difficulty")) for g in graded)
    if total <= 0:
        return 0.0
    earned = sum(weight_for(g.get("difficulty")) for g in graded if g.get("correct"))
    return round(100.0 * earned / total, 2)


def expiry_from(issued_at: str, validity_months: int = DEFAULT_VALIDITY_MONTHS) -> str:
    """
    Absolute expiry date, computed once at issue.

    Months are approximated as 30-day steps rather than pulled in as a dependency. The
    error is a day or two on an annual certificate, which does not matter here and is
    worth stating rather than hiding — if it ever does matter, use dateutil.
    """
    issued = _parse(issued_at) or datetime.now(timezone.utc)
    return (issued + timedelta(days=30 * max(1, validity_months))).isoformat(timespec="seconds")


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_expired(expires_at: Optional[str], *, now: Optional[datetime] = None) -> bool:
    when = _parse(expires_at)
    if when is None:
        # No expiry date is not "never expires" — it is a certificate we cannot vouch
        # for, and Coverage counts only what it can vouch for.
        return True
    return when <= (now or datetime.now(timezone.utc))


def days_until_expiry(expires_at: Optional[str], *, now: Optional[datetime] = None) -> Optional[int]:
    when = _parse(expires_at)
    if when is None:
        return None
    return (when - (now or datetime.now(timezone.utc))).days


@dataclass
class Standing:
    """One employee's compliance picture, for one category or overall."""

    required: int = 0
    current: int = 0                       # unexpired certificates against requirements
    coverage: float = 0.0                  # 0.0 - 1.0
    quality: float = 0.0                   # 0 - 100, mean of best attempt scores
    q_score: float = 0.0                   # coverage x quality
    missing: List[str] = field(default_factory=list)
    expired: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "required": self.required,
            "current": self.current,
            "coverage": round(self.coverage * 100, 1),
            "quality": round(self.quality, 1),
            "qScore": round(self.q_score, 1),
            "missing": self.missing,
            "expired": self.expired,
        }


def best_certificates(certificates: Sequence[dict]) -> Dict[str, dict]:
    """
    The certificate of record per training: the highest attempt_score.

    Best-score-wins was chosen deliberately (PROJECT.md Key Decisions), with the
    objection on the record — retaking until the number improves is rational, so this is
    revisited if scores inflate. Ties keep the most recently issued, so a retake that
    matches an old score refreshes the expiry rather than being ignored.
    """
    best: Dict[str, dict] = {}
    for cert in certificates:
        title = cert["doc_title"]
        held = best.get(title)
        if held is None:
            best[title] = cert
            continue
        if (cert["attempt_score"], cert.get("issued_at", "")) >= (
                held["attempt_score"], held.get("issued_at", "")):
            best[title] = cert
    return best


def standing(
    requirements: Sequence[dict],
    certificates: Sequence[dict],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Standing]:
    """
    Q Score overall and per category.

    `requirements` is [{"doc_title": ..., "category": ...}] for this person's ROLE.
    `certificates` is every certificate they hold, expired ones included — expiry is
    decided here rather than by the caller's query, so "expired" and "missing" can be
    told apart in the result.

    Returns {"overall": Standing, "behavioural": Standing, "technical": Standing}.

    Coverage counts only requirements. A certificate for something no longer required
    does not inflate the score, and is not counted as missing either — it simply stops
    mattering, which is what happens when a role's required list changes.
    """
    best = best_certificates(certificates)
    buckets: Dict[str, List[dict]] = {"overall": list(requirements)}
    for category in CATEGORIES:
        buckets[category] = [r for r in requirements
                             if (r.get("category") or "technical").lower() == category]

    out: Dict[str, Standing] = {}
    for name, reqs in buckets.items():
        result = Standing(required=len(reqs))
        scores: List[float] = []

        for req in reqs:
            title = req["doc_title"]
            cert = best.get(title)
            if cert is None:
                result.missing.append(title)
            elif is_expired(cert.get("expires_at"), now=now):
                result.expired.append(title)
            else:
                result.current += 1
                scores.append(float(cert["attempt_score"]))

        if result.required == 0:
            # Nothing was asked of you, so nothing is owed. Coverage 1.0 rather than a
            # division by zero — and quality 0, because there is no evidence either way.
            result.coverage = 1.0
            result.quality = 0.0
            result.q_score = 0.0
        else:
            # Capped at 1: extra certificates beyond what the role requires do not
            # inflate the score.
            result.coverage = min(1.0, result.current / result.required)
            result.quality = sum(scores) / len(scores) if scores else 0.0
            result.q_score = result.coverage * result.quality

        out[name] = result
    return out


def training_streak(attempt_dates: Sequence[str], *, now: Optional[datetime] = None) -> int:
    """
    Consecutive days of training activity, ending at the most recent day with a
    submitted attempt.

    `attempt_dates` is every GeneratedQuizAttempts.submitted_at for this learner (any
    ISO date/datetime string; only the calendar date is used, duplicates within a day
    collapse to one). Counts backward from the most recent day and stops at the first
    gap.

    A streak that stopped is not still "on": if the most recent activity is neither
    today nor yesterday, this returns 0 rather than whatever count it once reached —
    a number that still read "6" a month after the last quiz would say the opposite of
    what a streak means. Yesterday is included as a grace day so the count does not
    drop to 0 the moment midnight passes and before today's first attempt.
    """
    days = {d.date() for d in (_parse(v) for v in attempt_dates) if d is not None}
    if not days:
        return 0
    today = (now or datetime.now(timezone.utc)).date()
    most_recent = max(days)
    if (today - most_recent).days > 1:
        return 0
    streak = 0
    cursor = most_recent
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


# Badge ids match web-app/src/App.jsx's BADGES catalog. 2 ("Privacy Pro") and 6 ("Early
# Bird") are deliberately absent below -- there is no "privacy" question category and no
# assignment-due-date concept in the schema, so there is no real criterion to evaluate.
# Leaving them out (rather than inventing one) means the UI shows them as permanently
# unearned instead of lying about an achievement nothing backs.
def earned_badges(
    *,
    attempts: Sequence[dict],
    streak: int,
    q_score: float,
) -> Dict[int, Optional[str]]:
    """
    Which of the fixed badge catalog this person has actually earned.

    `attempts` is every submitted GeneratedQuizAttempts row for this learner:
    [{"submitted_at": ..., "score_percent": ...}, ...].

    Returns {badge_id: earned_at_iso_or_None}. A badge id present in the result has
    been earned; the value is the date it was first earned for the two one-time badges
    (1 "First Steps", 4 "Sharpshooter"), and None for the two live badges (3 "On a
    Roll", 5 "Top of the Class") that track current state and can go back to unearned
    -- there is nothing true to date-stamp about a condition that is not permanent.
    """
    out: Dict[int, Optional[str]] = {}

    submitted = sorted(
        (a for a in attempts if a.get("submitted_at")),
        key=lambda a: a["submitted_at"],
    )
    if submitted:
        out[1] = submitted[0]["submitted_at"]

    perfect = [a for a in submitted if float(a.get("score_percent") or 0) >= 100]
    if perfect:
        out[4] = perfect[0]["submitted_at"]

    if streak >= 6:
        out[3] = None

    if q_score >= 90:
        out[5] = None

    return out


def renewal_candidates(
    certificates: Sequence[dict],
    *,
    within_days: int = 30,
    now: Optional[datetime] = None,
) -> List[dict]:
    """
    Certificates that have expired, or will within `within_days`.

    Sorted by urgency — already expired first, then soonest. The UI shows this so an
    employee finds out before their Q Score drops rather than after.
    """
    out = []
    for cert in best_certificates(certificates).values():
        days = days_until_expiry(cert.get("expires_at"), now=now)
        if days is None or days <= within_days:
            out.append({
                **cert,
                "expired": is_expired(cert.get("expires_at"), now=now),
                "daysUntilExpiry": days,
            })
    return sorted(out, key=lambda c: (c["daysUntilExpiry"] is None,
                                      c["daysUntilExpiry"] if c["daysUntilExpiry"] is not None else 0))
