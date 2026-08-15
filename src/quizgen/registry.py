"""
The vetted source registry.

Questions are grounded in sources someone approved, never in the model's free-floating
knowledge and never in whatever the open web returns. This file is the list of what
"approved" means, and it is deliberately plain text so a non-engineer can read and edit
it without touching code.

The flow it feeds:

    registry -> fetch -> chunk -> index in Azure AI Search -> retrieve -> generate

Nothing is fetched at generation time. The corpus is assembled and indexed in advance,
so a question can always be traced to a specific passage that was reviewed into the
index — and an outage or a changed page cannot alter a quiz mid-flight.

SEEDED, NOT AUTHORITATIVE. The entries below are well-known primary sources for the
topics already in the training documents. Nobody at Quadrant has endorsed them yet.
Treat this as a first draft to correct, not a decision that has been made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Source:
    """One approved document, and which roles/topics it may be used for."""

    url: str
    title: str
    publisher: str
    topics: List[str] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)  # empty = every role

    # Why this source is trusted. Recorded because "who decided this was reliable?"
    # is the first question anyone will ask about a generated question.
    rationale: str = ""

    def applies_to(self, role: str) -> bool:
        return not self.roles or role.upper() in {r.upper() for r in self.roles}


# ---------------------------------------------------------------------------
# Seed registry
# ---------------------------------------------------------------------------
# Chosen to cover the topics the existing documents already teach. Primary sources
# only: the body that defines the thing, not a blog explaining it.

SEED_SOURCES: List[Source] = [
    # --- security, applies to everyone -------------------------------------
    Source(
        url="https://owasp.org/www-project-top-ten/",
        title="OWASP Top 10",
        publisher="OWASP Foundation",
        topics=["Secure Coding Practices", "Security Framework", "Acceptable Use Of Systems"],
        rationale="The reference list of web application risks; cited by most security standards.",
    ),
    Source(
        url="https://www.nist.gov/cyberframework",
        title="NIST Cybersecurity Framework",
        publisher="NIST",
        topics=["Security Framework", "Risk Identification & Assessment", "Compliance & Governance"],
        roles=["SWE_DIRECTOR", "SWE_MANAGER"],
        rationale="Already named in the Director training document.",
    ),

    # --- engineering fundamentals ------------------------------------------
    Source(
        url="https://git-scm.com/docs/gitworkflows",
        title="Git Workflows",
        publisher="Git project",
        topics=["Essential Git Commands", "Branching Strategies", "Merge Conflict Resolution"],
        roles=["SDE1", "SDE2", "SDE3"],
        rationale="The tool's own documentation — no closer primary source exists.",
    ),
    Source(
        url="https://google.github.io/eng-practices/review/",
        title="Google Engineering Practices: Code Review",
        publisher="Google",
        topics=["Pull Requests & Code Review", "Code Review Best Practices",
                "Google's Code Review Standards"],
        rationale="Named directly in the SDE3 document as the standard being taught.",
    ),
    Source(
        url="https://martinfowler.com/articles/continuousIntegration.html",
        title="Continuous Integration",
        publisher="Martin Fowler",
        topics=["Continuous Integration", "Deployment Strategies", "GitHub Actions (CI/CD Pipeline)"],
        roles=["SDE2", "SDE3", "SWE_MANAGER"],
        rationale="The article that defined the practice; still the common reference.",
    ),
    Source(
        url="https://sre.google/sre-book/table-of-contents/",
        title="Site Reliability Engineering",
        publisher="Google",
        topics=["Incident Response Process", "On-Call Best Practices", "Blameless Postmortems",
                "Effective Alerting", "Three Pillars Of Observability"],
        roles=["SDE3", "SWE_MANAGER", "SWE_DIRECTOR"],
        rationale="The primary text for incident response and on-call practice.",
    ),
    Source(
        url="https://opentelemetry.io/docs/concepts/observability-primer/",
        title="Observability Primer",
        publisher="OpenTelemetry / CNCF",
        topics=["Observability", "Three Pillars Of Observability", "Dashboard Design",
                "Performance Profiling"],
        roles=["SDE1", "SDE2", "SDE3"],
        rationale="Vendor-neutral definitions of traces, metrics and logs.",
    ),
    Source(
        url="https://learn.microsoft.com/en-us/azure/architecture/best-practices/api-design",
        title="API Design Best Practices",
        publisher="Microsoft Learn",
        topics=["API Design Best Practices", "REST Principles", "Versioning Strategies",
                "API Documentation"],
        roles=["SDE2", "SDE3"],
        rationale="Matches the Azure stack this product is built on.",
    ),

    # --- privacy and conduct, everyone --------------------------------------
    Source(
        url="https://gdpr-info.eu/",
        title="General Data Protection Regulation (full text)",
        publisher="EU",
        topics=["Customer & Personal Data", "Confidential Information",
                "Protecting Company Assets"],
        rationale="The regulation itself, rather than anyone's summary of it.",
    ),
]


# ---------------------------------------------------------------------------
# File format
# ---------------------------------------------------------------------------

def write_registry(sources: List[Source], path: Path) -> Path:
    """Write the registry as plain editable text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Vetted sources for question generation.",
        "#",
        "# Only these sources are fetched and indexed. A question can cite nothing else.",
        "# Edit freely: add a source, delete one you do not endorse, narrow the roles.",
        "#",
        "# roles:  blank means every role. Otherwise SDE1 / SDE2 / SDE3 / SWE_MANAGER /",
        "#         SWE_DIRECTOR / ALL.",
        "# topics: must match the topic names produced by ingest, or the source will",
        "#         never be retrieved for anything.",
        "#",
        "# THESE ARE SEEDED SUGGESTIONS. Nobody has endorsed them yet.",
        "",
        "sources:",
    ]
    for s in sources:
        lines.append("  - url: {}".format(s.url))
        lines.append("    title: {}".format(s.title))
        lines.append("    publisher: {}".format(s.publisher))
        lines.append("    rationale: {}".format(s.rationale))
        lines.append("    roles: {}".format(", ".join(s.roles) if s.roles else "(all)"))
        lines.append("    topics:")
        for t in s.topics:
            lines.append("      - {}".format(t))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def load_registry(path: Path) -> List[Source]:
    """Read the registry file. Falls back to the seed list if it does not exist."""
    if not path.exists():
        return list(SEED_SOURCES)

    sources: List[Source] = []
    current: Optional[Source] = None
    in_topics = False

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "sources:":
            continue

        if stripped.startswith("- url:"):
            if current:
                sources.append(current)
            current = Source(url=stripped.split(":", 1)[1].strip(), title="", publisher="")
            in_topics = False
        elif current is None:
            continue
        elif stripped.startswith("title:"):
            current.title = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("publisher:"):
            current.publisher = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("rationale:"):
            current.rationale = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("roles:"):
            value = stripped.split(":", 1)[1].strip()
            current.roles = [] if value in ("(all)", "") else [
                r.strip() for r in value.split(",") if r.strip()
            ]
            in_topics = False
        elif stripped.startswith("topics:"):
            in_topics = True
        elif stripped.startswith("- ") and in_topics:
            current.topics.append(stripped[2:].strip())

    if current:
        sources.append(current)
    return sources


def sources_for(sources: List[Source], topic: str = "", role: str = "") -> List[Source]:
    """Filter the registry by topic and role."""
    out = []
    for s in sources:
        if role and not s.applies_to(role):
            continue
        if topic and topic not in s.topics:
            continue
        out.append(s)
    return out
