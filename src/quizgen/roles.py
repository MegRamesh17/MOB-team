"""
Role profiles, derived from the documents.

What derivation can and cannot do — worth being blunt, because it decides how much of
your requirement this actually covers:

  CAN  find which roles the documents mention, and which topics each is tied to. Policy
       text names roles constantly ("engineers must", "the security team operates").
       That gives a real role -> topic skeleton with zero invention.

  CANNOT surface a requirement no document mentions. If nothing in the corpus discusses
       evidence handling, no amount of reading the corpus will reveal that an analyst
       needs to know it.

The gap is closed by the inference step, not by derivation: for each role, the model is
asked what else this role must know, and every answer becomes a RoleKnowledge question —
ungrounded by definition, so it is barred from asserting company rules and always goes
to expert review. That is the conservative version of "AI would know this requirement".

The derived file is written to data/output/role_profiles.yaml as plain editable text.
It is a starting point for a human, not an authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .models import Chunk

# Role words that appear in policy prose. Extend as the real documents reveal more.
_ROLE_PATTERNS = {
    "SWE": r"\b(software engineer|developer|engineering team|engineers)\b",
    "SRE": r"\b(site reliability|sre|on-call|operations engineer)\b",
    "SEC_ANALYST": r"\b(security analyst|security team|soc analyst)\b",
    "OPS_TECH": r"\b(operations technician|floor staff|technician|warehouse)\b",
    "PEOPLE_OPS": r"\b(people operations|hr team|human resources|people team)\b",
    "ACCOUNTANT": r"\b(accountant|finance team|financial controller)\b",
    "MANAGER": r"\b(manager|supervisor|line manager|team lead)\b",
    "PRIVACY": r"\b(privacy team|data protection officer|dpo)\b",
    "ALL": r"\b(every employee|all employees|all staff|employees must|everyone)\b",
}

_ROLE_TITLES = {
    "ALL": "All Employees",
    "SWE": "Software Engineer",
    "SRE": "Site Reliability Engineer",
    "SEC_ANALYST": "Security Analyst",
    "OPS_TECH": "Operations Technician",
    "PEOPLE_OPS": "People Operations",
    "ACCOUNTANT": "Accountant",
    "MANAGER": "People Manager",
    "PRIVACY": "Privacy Team",
}


@dataclass
class RoleProfile:
    code: str
    title: str

    # Topics the documents explicitly tie to this role, with the evidence.
    documented_topics: List[str] = field(default_factory=list)
    evidence: Dict[str, List[str]] = field(default_factory=dict)  # topic -> quotes

    # Requirements no document states. Filled by the inference step; every one of these
    # produces RoleKnowledge questions only.
    inferred_requirements: List[str] = field(default_factory=list)

    def to_yaml_block(self) -> str:
        lines = [
            "- code: {}".format(self.code),
            "  title: {}".format(self.title),
            "  documented_topics:",
        ]
        for topic in self.documented_topics:
            lines.append("    - {}".format(topic))
        if not self.documented_topics:
            lines.append("    []")
        lines.append("  # Not stated in any document. Each becomes a RoleKnowledge")
        lines.append("  # question: barred from asserting company rules, expert review required.")
        lines.append("  inferred_requirements:")
        for req in self.inferred_requirements:
            lines.append("    - {}".format(req))
        if not self.inferred_requirements:
            lines.append("    []   # populated by 'quizgen roles --infer' (needs a model)")
        return "\n".join(lines)


def derive_role_profiles(chunks: Sequence[Chunk]) -> List[RoleProfile]:
    """
    Map roles to the topics they must know.

    Two signals, in order of trust:

    1. **Container scope** — where the document was filed. `company-docs` applies to
       everyone; `software-engineering-docs` applies to that role. This is a decision a
       human made when uploading, so it is treated as authoritative.

    2. **Role mentions in the prose** — a fallback that catches roles named inside a
       company-wide document ("the security team operates a 24-hour line"). Useful, but
       a guess, so it never overrides the filing.
    """
    profiles: Dict[str, RoleProfile] = {}

    # --- signal 1: the container the document was filed in ---
    for chunk in chunks:
        scope = (chunk.role_scope or "ALL").upper()
        profile = profiles.setdefault(
            scope, RoleProfile(code=scope, title=_ROLE_TITLES.get(scope, scope))
        )
        if chunk.topic not in profile.documented_topics:
            profile.documented_topics.append(chunk.topic)
        profile.evidence.setdefault(chunk.topic, [])
        if len(profile.evidence[chunk.topic]) < 2 and chunk.container:
            profile.evidence[chunk.topic].append(
                "filed in container '{}'".format(chunk.container)
            )

    # --- signal 2: roles named in the text ---
    for chunk in chunks:
        lowered = chunk.text.lower()
        for code, pattern in _ROLE_PATTERNS.items():
            match = re.search(pattern, lowered)
            if not match:
                continue

            profile = profiles.setdefault(
                code, RoleProfile(code=code, title=_ROLE_TITLES.get(code, code))
            )
            if chunk.topic not in profile.documented_topics:
                profile.documented_topics.append(chunk.topic)

            # Keep the sentence that tied this role to this topic — a reviewer will
            # want to see why the mapping was made.
            for sentence in re.split(r"(?<=[.!?])\s+", chunk.text):
                if re.search(pattern, sentence.lower()):
                    profile.evidence.setdefault(chunk.topic, [])
                    if len(profile.evidence[chunk.topic]) < 2:
                        profile.evidence[chunk.topic].append(sentence.strip())
                    break

    for profile in profiles.values():
        profile.documented_topics.sort()

    # Everything in ALL applies to every role; fold it in so per-role coverage is real.
    universal = profiles.get("ALL")
    if universal:
        for code, profile in profiles.items():
            if code == "ALL":
                continue
            for topic in universal.documented_topics:
                if topic not in profile.documented_topics:
                    profile.documented_topics.append(topic)
            profile.documented_topics.sort()

    return [profiles[c] for c in sorted(profiles)]


def write_profiles(profiles: Sequence[RoleProfile], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "# Role profiles derived from the source documents.",
        "#",
        "# documented_topics  - the documents explicitly tie this role to these topics.",
        "#                      Questions from them are Documented: grounded, quotable.",
        "# inferred_requirements - things the role needs to know that NO document states.",
        "#                      Questions from them are RoleKnowledge: cannot assert a",
        "#                      company rule, number or procedure, and always require",
        "#                      expert review before a learner sees them.",
        "#",
        "# This file is a starting point, not an authority. Edit it.",
        "",
        "roles:",
    ]
    body = "\n".join(p.to_yaml_block() for p in profiles)
    path.write_text("\n".join(header) + "\n" + body + "\n", encoding="utf-8")
    return path


def load_profiles(path: Path) -> List[RoleProfile]:
    """
    Minimal reader for the file written above.

    Deliberately not a YAML dependency — the format is a fixed shape we control, and one
    fewer package matters more here than generality.
    """
    if not path.exists():
        return []

    profiles: List[RoleProfile] = []
    current: Optional[RoleProfile] = None
    section = ""

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#") or line.strip() == "roles:":
            continue
        stripped = line.strip()

        if stripped.startswith("- code:"):
            if current:
                profiles.append(current)
            current = RoleProfile(code=stripped.split(":", 1)[1].strip(), title="")
            section = ""
        elif current is None:
            continue
        elif stripped.startswith("title:"):
            current.title = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("documented_topics:"):
            section = "documented"
        elif stripped.startswith("inferred_requirements:"):
            section = "inferred"
        elif stripped.startswith("- "):
            value = stripped[2:].strip()
            if value in ("[]", ""):
                continue
            value = value.split("   #")[0].strip()
            if section == "documented":
                current.documented_topics.append(value)
            elif section == "inferred":
                current.inferred_requirements.append(value)

    if current:
        profiles.append(current)
    return profiles
