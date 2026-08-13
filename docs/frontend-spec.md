# Frontend spec — what the user sees

Written so the frontend can be built and demoed against mocks before the API exists.
Every JSON shape below matches the real data models in `src/quizgen/models.py`, so when
the backend lands the payloads will look like this.

Base path assumed to be `/api`. All responses are JSON. All timestamps are UTC ISO-8601.

---

## How to use this document

**If you are handing this to an AI coding tool**, this is the prompt:

> Build the frontend described in this spec. Use React with TypeScript and Tailwind.
> Serve every endpoint from local mock JSON files under `src/mocks/` using the exact
> payloads given — do not invent different shapes. Put all API calls behind a single
> `src/api/client.ts` so swapping mocks for the real backend is a base-URL change.
> Build all six screens plus the loading, empty and error states listed at the end.

Then paste this whole file after it.

**Visual direction:** Khan Academy — a visible learning *path* rather than a list, clear
progress indicators, status pills, celebratory but not childish on completion.

### The frontend needs no API keys. Ever.

Azure keys live in the backend only. The browser calls your own API; your API talks to
Azure. Anything else puts a key in JavaScript that anyone can read with devtools.

So the frontend needs **no `.env`, no Azure credentials, no Key Vault access** — just a
base URL. If a tool suggests putting an OpenAI key in the frontend, that is wrong and it
must be moved server-side.

### What this gets you, and what it doesn't

**Will work:** all six screens, fully clickable, running on mock data. Enough to demo and
to hand back for styling feedback.

**Will not work until the backend exists:** real questions, real scoring, real
certificates. The mocks are static — answering a quiz returns the same canned result
every time. That is expected and fine for building the UI.

---

## Vocabulary the UI must know

**Status pill** — every assigned training carries exactly one:

| Value | Meaning | Suggested colour |
|---|---|---|
| `NotStarted` | assigned, untouched | grey |
| `InProgress` | opened, not finished | blue |
| `Completed` | passed, still valid | green |
| `Expiring` | passed, expires within 30 days | amber |
| `Expired` | passed, validity ran out | red |
| `Overdue` | past due date, not completed | red |

**Mastery level** — per topic and overall: `Beginner` (<60%), `Mediocre` (60–84%), `Expert` (≥85%).

**Question type** — four, each needs a different input widget:
`MultipleChoice` (radio), `MultiSelect` (checkboxes), `TrueFalse` (two buttons),
`FillInBlank` (text input).

**Provenance class** — must be visible to the learner:

- `Documented` → badge "Company policy", cites document + page
- `RoleKnowledge` → badge "Professional practice", **no** company citation

That distinction is a compliance requirement, not decoration. A `RoleKnowledge` question
must never be presented as if it were company policy.

---

## 1. Home / Dashboard

The landing screen after sign-in.

**Shows:** Q Score dial, certificate progress, what needs attention, continue-where-you-left-off.

`GET /api/me/dashboard`

```json
{
  "employee": {
    "id": 42,
    "fullName": "Maya Patel",
    "role": "Software Development Engineer 2",
    "roleCode": "SDE2",
    "department": "Engineering"
  },
  "qScore": {
    "value": 74,
    "band": "Developing",
    "certificatesEarned": 5,
    "certificatesRequired": 7,
    "averageScore": 88.4,
    "trend": "+6",
    "explanation": "5 of 7 required certificates, averaging 88%."
  },
  "compliance": {
    "status": "AtRisk",
    "compliancePercent": 71,
    "overdueCount": 1,
    "expiringCount": 2,
    "expiredCount": 0
  },
  "continueLearning": {
    "trainingId": 12,
    "title": "Engineering Leadership & People Management",
    "progressPercent": 40,
    "nextStep": "Module 3: Performance Management"
  },
  "needsAttention": [
    {
      "type": "Overdue",
      "trainingId": 8,
      "title": "Code of Conduct & Core Values",
      "dueOn": "2026-08-01",
      "daysOverdue": 9
    },
    {
      "type": "ExpiringCertificate",
      "certificateId": 3,
      "name": "Data Privacy Fundamentals",
      "expiresOn": "2026-09-15",
      "daysUntilExpiry": 36
    }
  ]
}
```

**Q Score dial.** 0–100. Bands: 0–39 red, 40–69 amber, 70–89 blue, 90–100 green.
Always render `explanation` next to it — a bare number invites misreading. A new joiner
legitimately sits at 0 and is not failing.

---

## 2. My Training / Learning path

The Khan-Academy-style path. Grouped by topic, ordered by urgency.

`GET /api/me/trainings`

```json
{
  "path": [
    {
      "category": "Code of Conduct & Core Values",
      "items": [
        {
          "assignmentId": 101,
          "trainingId": 8,
          "title": "Why a Code of Conduct",
          "status": "Completed",
          "progressPercent": 100,
          "difficulty": "Beginner",
          "durationMinutes": 20,
          "dueOn": "2026-08-01",
          "expiresOn": "2027-08-01",
          "daysUntilDue": null,
          "latestScore": 92,
          "isMandatory": true,
          "locked": false,
          "prerequisites": []
        },
        {
          "assignmentId": 102,
          "trainingId": 9,
          "title": "Conflicts of Interest",
          "status": "NotStarted",
          "progressPercent": 0,
          "difficulty": "Intermediate",
          "durationMinutes": 35,
          "dueOn": "2026-08-20",
          "daysUntilDue": 10,
          "latestScore": null,
          "isMandatory": true,
          "locked": true,
          "prerequisites": [
            { "trainingId": 8, "title": "Why a Code of Conduct", "satisfied": true }
          ]
        }
      ]
    }
  ]
}
```

**Rendering notes.** `locked: true` means prerequisites are unmet — show it greyed with a
tooltip listing what's needed. Draw connectors between prerequisite pairs to make the
path read as a sequence rather than a list. Sort within a category by the order given;
the backend has already sorted by urgency.

---

## 3. Training detail / reading view

`GET /api/trainings/{id}`

```json
{
  "training": {
    "id": 9,
    "title": "Conflicts of Interest",
    "description": "What counts as a conflict, and what you must disclose.",
    "category": "Code of Conduct & Core Values",
    "durationMinutes": 35,
    "status": "InProgress",
    "progressPercent": 40
  },
  "resources": [
    {
      "resourceId": 55,
      "type": "Reading",
      "title": "Recognising a conflict",
      "content": "A conflict of interest is a situation where...",
      "isCompleted": true
    },
    {
      "resourceId": 56,
      "type": "Video",
      "title": "Five scenarios",
      "url": "https://...",
      "durationMinutes": 12,
      "isCompleted": false
    }
  ],
  "quiz": {
    "quizId": 21,
    "title": "Conflicts of Interest — Knowledge Check",
    "questionCount": 8,
    "passingScorePercent": 80,
    "attemptsUsed": 0,
    "maxAttempts": 3,
    "canStart": true
  }
}
```

`POST /api/trainings/{id}/start` marks it in progress. Idempotent — safe to fire on open.

---

## 4. Quiz — the important one

### 4a. Pre-quiz screen

Show before the first question: number of questions, pass mark, time limit, attempts
remaining, and whether it's a certificate assessment. Once started, a secure-sitting quiz
cannot be paused.

`POST /api/quizzes/{id}/attempts` → starts an attempt and returns the questions.

```json
{
  "attemptId": "att_9f3c21",
  "quizId": 21,
  "startedAt": "2026-08-10T14:02:11Z",
  "timeLimitMinutes": 20,
  "passingScorePercent": 80,
  "isRemedial": true,
  "focusMessage": "This quiz focuses on topics you found difficult last time.",
  "questions": [
    {
      "questionId": "q_a1b2c3",
      "order": 1,
      "type": "MultipleChoice",
      "topic": "Conflicts of Interest",
      "difficulty": "Medium",
      "points": 1,
      "provenanceClass": "Documented",
      "prompt": "A supplier offers you concert tickets shortly before a contract renewal. What should you do?",
      "options": [
        { "optionId": "o_1", "text": "Decline and disclose the offer" },
        { "optionId": "o_2", "text": "Accept — it is under the gift limit" },
        { "optionId": "o_3", "text": "Accept and give them to a colleague" },
        { "optionId": "o_4", "text": "Accept but do not mention it" }
      ]
    },
    {
      "questionId": "q_d4e5f6",
      "order": 2,
      "type": "FillInBlank",
      "topic": "Conflicts of Interest",
      "difficulty": "Hard",
      "points": 1,
      "provenanceClass": "Documented",
      "prompt": "A situation where personal interest could improperly influence a work decision is called a conflict of ______.",
      "options": []
    },
    {
      "questionId": "q_g7h8i9",
      "order": 3,
      "type": "MultiSelect",
      "topic": "Reporting Channels",
      "difficulty": "Medium",
      "points": 2,
      "provenanceClass": "RoleKnowledge",
      "prompt": "Which of these should a manager escalate rather than handle alone? (Select all that apply)",
      "options": [
        { "optionId": "o_5", "text": "An allegation involving a direct report" },
        { "optionId": "o_6", "text": "A disagreement about sprint scope" },
        { "optionId": "o_7", "text": "A suspected data breach" },
        { "optionId": "o_8", "text": "A request for annual leave" }
      ]
    }
  ]
}
```

**Critical: options never contain `isCorrect`.** The answer key is not sent to the browser
at any point during an attempt. Do not build UI that assumes it's available — it isn't,
by design, and anyone who "adds it for convenience" has broken the product.

**MultiSelect scores all-or-nothing.** Tell the user so in the UI.

**`provenanceClass`** drives a small badge on each question:
- `Documented` → "Company policy"
- `RoleKnowledge` → "Professional practice"

### 4b. Question screen

One question at a time, no going back. Progress indicator (`3 of 8`), countdown if there's
a time limit, and a Submit that's disabled until an answer exists.

Autosave each answer as it's given:

`PATCH /api/attempts/{attemptId}/answers`

```json
{ "questionId": "q_a1b2c3", "selectedOptionIds": ["o_1"], "textAnswer": "" }
```

For `FillInBlank`, send `textAnswer` and leave `selectedOptionIds` empty.

**Secure-sitting behaviour** (certificate assessments only — flag comes back as
`isSecure: true`): warn on tab blur, log it, and show a visible "monitored" indicator.
Be honest in the UI that it's monitored rather than pretending it's locked down.

### 4c. Results screen

`POST /api/attempts/{attemptId}/submit`

```json
{
  "attemptId": "att_9f3c21",
  "scorePercent": 75,
  "pointsAwarded": 6,
  "pointsPossible": 8,
  "passed": false,
  "passingScorePercent": 80,
  "attemptsRemaining": 2,
  "certificateIssued": null,
  "masteryChanges": [
    { "topic": "Conflicts of Interest", "before": "Beginner", "after": "Mediocre", "accuracy": 67 },
    { "topic": "Reporting Channels", "before": "Mediocre", "after": "Mediocre", "accuracy": 72 }
  ],
  "results": [
    {
      "questionId": "q_a1b2c3",
      "topic": "Conflicts of Interest",
      "isCorrect": true,
      "pointsAwarded": 1,
      "yourAnswer": ["Decline and disclose the offer"],
      "correctAnswer": ["Decline and disclose the offer"],
      "explanation": "The timing makes it a conflict of interest regardless of intent.",
      "source": {
        "documentTitle": "Behavioral Compliance for Employees",
        "page": 4,
        "quote": "Employees must decline and disclose any gift offered during an active procurement."
      }
    },
    {
      "questionId": "q_g7h8i9",
      "topic": "Reporting Channels",
      "isCorrect": false,
      "pointsAwarded": 0,
      "yourAnswer": ["An allegation involving a direct report"],
      "correctAnswer": ["An allegation involving a direct report", "A suspected data breach"],
      "explanation": "Both must be escalated; neither is a manager's to resolve alone.",
      "source": null
    }
  ]
}
```

**Only now do correct answers appear.** For wrong answers show the explanation and, when
`source` is non-null, a "See: <document> p.<n>" link with the quote. When `source` is
null the question was `RoleKnowledge` — show the explanation with no company citation.

If `passed` is true and this was a certificate assessment, `certificateIssued` is
populated (shape in §5) — celebrate and link to it.

---

## 5. Certificates

`GET /api/me/certificates`

```json
{
  "required": 7,
  "earned": 5,
  "certificates": [
    {
      "certificateId": 3,
      "code": "QC-2026-04812",
      "name": "Data Privacy Fundamentals",
      "issuedOn": "2025-09-15",
      "expiresOn": "2026-09-15",
      "status": "Expiring",
      "daysUntilExpiry": 36,
      "scorePercent": 91,
      "downloadUrl": "/api/certificates/3/pdf",
      "verifyUrl": "/verify/QC-2026-04812"
    }
  ],
  "outstanding": [
    { "name": "Secure Coding Practices", "trainingId": 14, "status": "NotStarted" },
    { "name": "Incident Response Basics", "trainingId": 15, "status": "InProgress" }
  ]
}
```

**Certificate card** — company logo, holder name, certificate name, issue and expiry
dates, and the verification code shown as text (it's what makes the PDF checkable rather
than just a picture). Expired certificates stay visible but greyed with a "Renew" action.

---

## 6. Manager view

`GET /api/manager/team`

```json
{
  "summary": {
    "teamSize": 8,
    "compliant": 5,
    "atRisk": 2,
    "nonCompliant": 1,
    "averageQScore": 71
  },
  "team": [
    {
      "employeeId": 42,
      "fullName": "Maya Patel",
      "role": "Software Development Engineer 2",
      "qScore": 74,
      "complianceStatus": "AtRisk",
      "compliancePercent": 71,
      "certificatesEarned": 5,
      "certificatesRequired": 7,
      "overdueCount": 1,
      "expiringCount": 2,
      "weakestTopic": "Reporting Channels",
      "lastActivity": "2026-08-09T16:20:00Z"
    }
  ]
}
```

Render as a sortable grid, defaulting to worst-first (NonCompliant → AtRisk → InProgress
→ Compliant). Row click opens the same detail as §1 for that person, read-only.

`GET /api/manager/employees/{id}` returns the dashboard payload plus per-topic mastery.

---

## States to build for every screen

Easy to forget and always needed:

- **Loading** — skeletons, not spinners, on the dashboard and path
- **Empty** — new joiner with no assignments; new manager with no reports. Both are
  normal, neither is an error
- **Error** — API down. Show what's cached and a retry
- **Zero Q Score** — must read as "not started", never as "failing"
- **No certificates yet** — show the required list as a checklist, not an empty state
- **Quiz in progress on reload** — attempt is server-side; resuming returns the same
  `attemptId` and the answers already saved

---

## Mock data

Build against a static JSON file per endpoint using the payloads above — they're the real
shapes, so swapping to live calls later is a base-URL change.

Two rules worth enforcing in the mock layer from day one, because they're properties of
the real API and code written against a looser mock will break:

1. Quiz-delivery payloads contain **no correct answers**. If your mock includes them,
   someone will build a UI that depends on them.
2. `source` is **null** on `RoleKnowledge` questions. Don't assume a citation is always
   present.

---

## Not yet decided

- Whether Q Score is visible to the employee or manager-only. Build it behind a flag.
- Whether behavioural (scenario) questions get a distinct visual treatment.
- Retake policy: whether Q Score uses the first passing score or the best one. Affects
  what the results screen should say about retaking.
