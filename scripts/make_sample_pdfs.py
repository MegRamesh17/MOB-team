"""
Write sample training PDFs so the pipeline can be run end to end without Azure.

The real documents live in blob storage and are not in this repo — they are company
material. Without something to ingest, a new clone cannot demo anything, so these
stand in.

TWO THINGS THESE DELIBERATELY GET RIGHT, because getting them wrong wastes a day:

  1. **Headings match `ingest.looks_like_heading`** — two to nine words, title case, no
     trailing punctuation. Heading detection is what produces topics, and topics are
     what the adaptive engine targets. A document whose headings are not detected
     collapses into one nameless section, and every quiz silently becomes untargeted.

  2. **The body is prose, not bullets.** The real documents are bullet-point syllabi,
     which is exactly why grounded generation off them produced shallow recall
     questions. Full sentences give the generator something to build a real question
     from, and give `source_quote` something quotable.

These are written for a fictional company (Northwind Systems) and contain no Quadrant
material. Any policy detail in them is invented for demo purposes.

Run:  python scripts/make_sample_pdfs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "documents"

# (filename, title, [(heading, [paragraph, ...]), ...])
DOCUMENTS = [
    (
        "information-security-basics.pdf",
        "Information Security Basics for All Employees",
        [
            ("Why Security Is Everyone's Job", [
                "Most successful breaches do not begin with a technical exploit. They begin "
                "with an ordinary employee doing something reasonable in the wrong context: "
                "opening an attachment from a familiar name, reusing a password, or "
                "approving a login prompt they did not trigger.",
                "This means security controls cannot be delegated entirely to the security "
                "team. The people best placed to notice that something is wrong are the "
                "people who know what normal looks like in their own work.",
            ]),
            ("Recognising Phishing Attempts", [
                "A phishing message tries to make you act before you think. The three "
                "signals that matter most are urgency, an unexpected request for "
                "credentials or payment, and a mismatch between the display name and the "
                "actual sending address.",
                "Hovering over a link reveals its true destination. Attackers rely on "
                "lookalike domains, where a single character is substituted, so the "
                "destination reads correctly at a glance but resolves elsewhere.",
                "If a message appears to come from a colleague and asks for something "
                "unusual, verify through a different channel. Reply-to addresses can be "
                "controlled by the attacker, so replying to the message proves nothing.",
            ]),
            ("Passwords And Multi Factor Authentication", [
                "Password length defeats brute force far more effectively than character "
                "substitution. A long passphrase of unrelated words is stronger and easier "
                "to remember than a short password with symbols substituted for letters.",
                "Reuse is the more serious risk. When one service is breached, the "
                "credentials are tried automatically against every other well-known "
                "service, which is why a password manager with a unique value per site "
                "matters more than complexity rules.",
                "Multi-factor authentication stops most credential attacks, but push "
                "notification fatigue is a real attack: an attacker who already has your "
                "password sends repeated prompts until one is approved out of irritation. "
                "An approval prompt you did not trigger should be denied and reported.",
            ]),
            ("Handling Confidential Information", [
                "Information should be shared on the basis of need, not seniority. The "
                "question to ask before sharing is whether the recipient needs the "
                "information to do their job, not whether they are senior enough to see it.",
                "Data that leaves a managed system stops being protected by it. Copying "
                "records into a personal spreadsheet, a personal email account, or an "
                "unapproved AI tool removes every control that applied to the original.",
            ]),
            ("Reporting A Suspected Incident", [
                "Report early and imperfectly rather than late and completely. The cost of "
                "investigating a false alarm is small; the cost of a delay while an "
                "attacker is active compounds by the hour.",
                "Do not attempt to investigate a suspicious message by clicking through it "
                "to see what happens. Preserve it, report it, and let the response team "
                "examine it in a controlled environment.",
            ]),
        ],
    ),
    (
        "code-review-and-collaboration.pdf",
        "Code Review and Engineering Collaboration",
        [
            ("The Purpose Of Code Review", [
                "The primary purpose of code review is to improve the health of the "
                "codebase over time. A reviewer is not looking for perfection; they are "
                "deciding whether the change definitely improves the overall state of the "
                "system, even if it is not flawless.",
                "A secondary purpose is shared understanding. Every review spreads context "
                "about a part of the system to at least one more person, which reduces the "
                "risk carried by any single engineer leaving.",
            ]),
            ("What Reviewers Should Look For", [
                "Design comes first. A change that is well written but architecturally "
                "wrong is more expensive to unwind than one that is structurally sound but "
                "needs tidying, so structural concerns should be raised before style.",
                "Reviewers should confirm that the change does what the author claims, that "
                "it is covered by tests that would fail if the behaviour regressed, and "
                "that naming will still make sense to someone reading it in a year.",
                "Comments should explain why something matters rather than only what to "
                "change. A comment that gives the reasoning teaches; one that gives only "
                "an instruction has to be repeated on the next change.",
            ]),
            ("Handling Disagreement In Review", [
                "Technical facts and data overrule opinion and personal preference. Where "
                "neither side has data, the established convention of the codebase "
                "generally wins over individual style preference.",
                "If a disagreement cannot be resolved in review comments, escalate to a "
                "conversation rather than continuing to exchange written arguments. Long "
                "comment threads tend to harden positions rather than resolve them.",
                "Authors should not be blocked indefinitely. If a reviewer's concern is "
                "genuine but not urgent, it can be filed as follow-up work rather than "
                "holding a correct change out of the codebase.",
            ]),
            ("Keeping Changes Small", [
                "Small changes are reviewed faster, reviewed better, and are easier to "
                "revert when something goes wrong. A large change tends to receive shallow "
                "review because the effort required to review it thoroughly is high.",
                "Where a change must be large, separating mechanical refactoring from "
                "behavioural change into distinct commits lets a reviewer verify the "
                "mechanical part quickly and concentrate on the part that carries risk.",
            ]),
            ("Review Turnaround Expectations", [
                "Responsiveness matters more than depth for the first response. "
                "Acknowledging a review promptly, even to say when it will be looked at, "
                "unblocks the author's planning far more than a thorough review that "
                "arrives two days later.",
            ]),
        ],
    ),
    (
        "incident-response-and-oncall.pdf",
        "Incident Response and On-Call Practice",
        [
            ("What Counts As An Incident", [
                "An incident is any unplanned disruption that affects users or threatens "
                "to. The definition is deliberately broad, because the cost of declaring "
                "an incident that turns out to be minor is far lower than the cost of "
                "treating a real outage as routine work for several hours.",
                "Severity should be assessed by user impact rather than by technical "
                "interest. A subtle bug affecting all users outranks a dramatic failure in "
                "a system nobody is currently depending on.",
            ]),
            ("Roles During An Incident", [
                "The incident commander coordinates and decides; they do not debug. The "
                "most common failure in incident response is the most knowledgeable person "
                "taking command and then disappearing into a terminal, leaving nobody "
                "tracking the overall picture.",
                "A separate communications role keeps stakeholders informed on a predictable "
                "cadence. Without one, responders are interrupted continuously for status "
                "updates, which lengthens the outage.",
            ]),
            ("Mitigate Before Diagnosing", [
                "Restoring service takes priority over understanding the cause. Rolling "
                "back a recent deployment, shifting traffic, or disabling a feature flag "
                "are all acceptable even when it is not yet known whether they address the "
                "true cause.",
                "Diagnosis performed while users are affected is diagnosis performed under "
                "time pressure, which is when mistakes are made. Preserving logs and "
                "metrics for later analysis costs little and protects the investigation.",
            ]),
            ("Writing A Blameless Postmortem", [
                "A postmortem assumes that everyone acted reasonably given the information "
                "they had at the time. The purpose is to find the conditions that made the "
                "failure possible, because those conditions will still be present after "
                "the individual has been more careful.",
                "Naming an individual as the cause reliably stops the flow of information. "
                "Once engineers expect to be blamed, near-misses stop being reported, and "
                "the organisation loses its cheapest source of warning.",
                "Action items should be specific and owned. An item reading improve "
                "monitoring will not be done; an item naming a specific alert, a threshold "
                "and an owner might be.",
            ]),
            ("Effective Alerting Practice", [
                "Every alert should be actionable and urgent. An alert that fires "
                "regularly and is routinely ignored is worse than no alert, because it "
                "trains responders to dismiss the whole channel.",
                "Alert on symptoms that users experience rather than on internal causes. "
                "High processor usage may be entirely normal, while a rise in request "
                "latency is a problem regardless of which internal cause produced it.",
            ]),
        ],
    ),
]


def build() -> int:
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        print("reportlab is required:\n    pip install reportlab", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    base = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle", parent=base["Title"], fontSize=17, spaceAfter=18, alignment=0
    )
    # Headings are rendered on their own line with no trailing punctuation, which is
    # what the ingest heading heuristic keys on. Changing this style is fine; adding a
    # trailing colon or a numeric prefix is not.
    heading_style = ParagraphStyle(
        "SectionHeading", parent=base["Heading2"], fontSize=13,
        spaceBefore=16, spaceAfter=7,
    )
    body_style = ParagraphStyle(
        "Body", parent=base["BodyText"], fontSize=10.5, leading=15, spaceAfter=9,
    )

    written = []
    for filename, title, sections in DOCUMENTS:
        path = OUT / filename
        doc = SimpleDocTemplate(
            str(path), pagesize=LETTER,
            leftMargin=inch, rightMargin=inch, topMargin=inch, bottomMargin=inch,
            title=title,
        )
        flow = [Paragraph(title, title_style), Spacer(1, 6)]
        for heading, paragraphs in sections:
            flow.append(Paragraph(heading, heading_style))
            for text in paragraphs:
                flow.append(Paragraph(text, body_style))
        doc.build(flow)
        written.append((path, sum(len(p) for _, p in sections), len(sections)))

    print("Wrote {} sample document(s) to {}\n".format(len(written), OUT))
    for path, paragraphs, sections in written:
        print("  {:<38} {} sections, {} paragraphs".format(
            path.name, sections, paragraphs))

    # Verify the headings actually survive extraction. Writing a PDF whose headings the
    # chunker cannot see is the exact failure these documents exist to avoid, and it is
    # invisible until topics come out wrong several steps later.
    sys.path.insert(0, str(REPO / "src"))
    try:
        from quizgen.ingest import ingest_document, looks_like_heading
    except ImportError:
        print("\n(skipped heading check — run from the repo with src/ importable)")
        return 0

    print("\nHeading check:")
    ok = True
    for path, _, expected in written:
        chunks = ingest_document(path)
        topics = sorted({c.topic for c in chunks})
        detected = sum(1 for t in topics if looks_like_heading(t))
        status = "ok" if detected >= expected - 1 else "PROBLEM"
        if status != "ok":
            ok = False
        print("  {:<38} {} chunks, {} topics [{}]".format(
            path.name, len(chunks), len(topics), status))
        for t in topics[:8]:
            print("        - {}".format(t))

    if not ok:
        print("\nSome headings were not detected, so those sections will have coarse")
        print("topics and adaptive targeting will be weaker for them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
