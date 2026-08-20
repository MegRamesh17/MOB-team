"""
Role mapping — gpt-5 reads an uploaded document and maps its sections onto the
company's roles.

Three rules, all set by the team, all enforced here rather than left to prompt luck:

  1. The AI EXTRACTS roles the document names; it never invents one. The company's
     role list is the manager's to manage. A role found in the document that is not
     on the list is surfaced to the manager as a question, not auto-created.

  2. Nothing is fetched from the internet. If a role's material in the document is
     too thin to build a module from, that is reported to the manager — who supplies
     more material — instead of being padded from the web or from model memory.

  3. There is no mock fallback. These calls require the real model; without
     credentials the upload flow says so plainly instead of degrading silently.

Everything returned here is a *proposal*. The manager confirms before any chunk is
tagged or any question generated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import CONFIG
from .llm.azure_openai import _chat_kwargs, _client


@dataclass
class RoleMapping:
    # topic (section heading) -> role_code, "ALL" for everyone/miscellaneous
    assignments: Dict[str, str] = field(default_factory=dict)
    # roles the document names that are NOT in the company list — manager decides
    unknown_roles: List[str] = field(default_factory=list)
    # topics whose material is too thin to build a real module from
    thin_topics: List[str] = field(default_factory=list)
    # model's one-line reading of what the document is
    summary: str = ""
    # The model's read of what this document actually is, as a short course title.
    # Empty when the model declines (e.g. genuinely can't tell) -- callers fall back
    # to the heuristic title in that case, never to an empty string.
    suggested_title: str = ""


@dataclass
class UpdateDecision:
    action: str = "add"                # "add" | "update"
    supersedes: str = ""               # doc_title being replaced when action == update
    reason: str = ""


def _require_model() -> None:
    missing = CONFIG.missing_for_azure() if hasattr(CONFIG, "missing_for_azure") else []
    if not CONFIG.azure_openai_key or not CONFIG.azure_openai_endpoint:
        raise RuntimeError(
            "Role mapping needs the real model and no credentials are configured. "
            "There is deliberately no mock for this: load the Azure OpenAI keys "
            "(see .env.example) and retry. Missing: {}".format(
                ", ".join(missing) or "AZURE_OPENAI_KEY/ENDPOINT")
        )


def _chat(system: str, user: str) -> dict:
    client = _client()
    response = client.chat.completions.create(
        model=CONFIG.azure_chat_deployment,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        **_chat_kwargs(CONFIG.azure_chat_deployment, 4000, 0.0),
    )
    raw = response.choices[0].message.content or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


_MAP_SYSTEM = (
    "You organise workplace training documents. You are given the company's list of "
    "role codes and one document broken into sections. Map each section to the role "
    "it trains, using ONLY the roles the document itself names or clearly addresses.\n\n"
    "Rules:\n"
    "- A section that applies to every employee (general conduct, security basics, "
    "company-wide policy) maps to \"ALL\".\n"
    "- If a section addresses a role that is NOT in the company list, do not guess a "
    "company role for it: put the document's own name for that role in unknown_roles "
    "and map the section to that name verbatim.\n"
    "- Never invent roles. Never use outside knowledge about what roles a company "
    "should have.\n"
    "- Mark a section as thin if its text could not support 2 or more meaningful "
    "quiz questions (e.g. it is only a heading, a duration, or a table of contents).\n"
    "- You are also given AUTO-DETECTED TITLE, taken mechanically from the source "
    "(the first line of a PDF page, or a page's <title> tag for a web page). It is "
    "frequently wrong: cookie banners, promo bars (\"Save 20% today\"), nav menus and "
    "site-wide headers all end up there just as often as an actual title, because "
    "the mechanical extraction has no way to tell them apart from real content. Read "
    "the actual section bodies and judge what the document teaches, then propose "
    "suggested_title as a short, specific course title for THAT subject -- 3-8 words, "
    "no site name, no marketing copy, no trailing punctuation. If the auto-detected "
    "title already looks like a genuine title for this content, you may repeat it. "
    "Leave suggested_title empty only if the sections give you nothing to title -- "
    "never fill it with anything resembling an ad, a banner, a menu, or boilerplate.\n"
    "Return ONLY JSON: {\"assignments\": {\"<section>\": \"<ROLE_CODE|ALL|verbatim "
    "unknown role>\"}, \"unknown_roles\": [...], \"thin_topics\": [...], "
    "\"summary\": \"one line\", \"suggested_title\": \"short course title or empty\"}"
)


def analyze_document(
    doc_title: str,
    sections: Dict[str, str],
    known_roles: List[Dict[str, str]],
) -> RoleMapping:
    """
    Map one document's sections onto the company role list.

    `sections` is topic -> body text. Bodies are truncated per section: the model
    needs enough to judge audience and substance, not the full text — the full text
    is what generation reads later.
    """
    _require_model()

    roles_line = "\n".join(
        "  {} — {}".format(r["role_code"], r.get("title", "")) for r in known_roles
    ) or "  (none defined yet)"

    body = "\n\n".join(
        "SECTION: {}\n{}".format(topic, text[:1200])
        for topic, text in sections.items()
    )
    user = (
        "COMPANY ROLES:\n{}\n\nAUTO-DETECTED TITLE (may be wrong -- see system "
        "instructions): {}\n\n{}\n\n"
        "Map every section. Sections for everyone -> ALL."
    ).format(roles_line, doc_title, body)

    payload = _chat(_MAP_SYSTEM, user)

    known = {r["role_code"].upper() for r in known_roles}
    assignments: Dict[str, str] = {}
    unknown: List[str] = [str(u) for u in payload.get("unknown_roles", []) if str(u).strip()]

    for topic in sections:
        raw = str(payload.get("assignments", {}).get(topic, "ALL")).strip()
        code = raw.upper().replace(" ", "_")
        if code in known or code == "ALL":
            assignments[topic] = code
        else:
            # Not a known role: keep the document's own wording so the manager sees
            # exactly what the document said, and default the section to ALL only
            # after the manager decides. Until then it is unassigned-but-flagged.
            assignments[topic] = raw
            if raw not in unknown:
                unknown.append(raw)

    # Trimmed and length-capped, but otherwise trusted: the "don't hand back a promo
    # banner" judgment call already happened inside the model, per the system prompt.
    # Capped short enough that a model ignoring the "3-8 words" instruction still
    # can't hand back a full paragraph as a "title".
    suggested_title = str(payload.get("suggested_title", "")).strip()[:120]

    return RoleMapping(
        assignments=assignments,
        unknown_roles=unknown,
        thin_topics=[t for t in payload.get("thin_topics", []) if t in sections],
        summary=str(payload.get("summary", ""))[:200],
        suggested_title=suggested_title,
    )


_UPDATE_SYSTEM = (
    "You maintain a training-module catalogue. Given a NEW document (title and section "
    "list) and the EXISTING modules for the same roles, decide whether the new "
    "document is an updated version of one existing module or genuinely new material.\n"
    "It is an update only when it covers substantially the same subject for the same "
    "audience — a revised policy, a new edition. Different subject or audience is add.\n"
    "Return ONLY JSON: {\"action\": \"add\"|\"update\", \"supersedes\": "
    "\"<existing title or empty>\", \"reason\": \"one line\"}"
)


def decide_update_or_add(
    doc_title: str,
    topics: List[str],
    existing: List[Dict[str, object]],
) -> UpdateDecision:
    """
    `existing` is [{title, topics}] for modules serving the same roles. Empty existing
    list short-circuits: nothing to update means add, no model call, no cost.
    """
    if not existing:
        return UpdateDecision(action="add", reason="no existing modules for these roles")

    _require_model()
    user = (
        "NEW DOCUMENT: {}\nSections: {}\n\nEXISTING MODULES:\n{}"
    ).format(
        doc_title, ", ".join(topics[:30]),
        "\n".join("  - {}: {}".format(e["title"], ", ".join(list(e["topics"])[:15]))
                  for e in existing),
    )
    payload = _chat(_UPDATE_SYSTEM, user)

    action = str(payload.get("action", "add")).lower()
    supersedes = str(payload.get("supersedes", "")).strip()
    titles = {e["title"] for e in existing}
    # The model may only retire a module that actually exists; anything else is add.
    if action != "update" or supersedes not in titles:
        return UpdateDecision(action="add", reason=str(payload.get("reason", ""))[:200])
    return UpdateDecision(action="update", supersedes=supersedes,
                          reason=str(payload.get("reason", ""))[:200])


# Seeded from the team's own role list. Managers add and remove from the UI;
# this only runs on an empty table so removals stick.
SEED_ROLES: List[Dict[str, str]] = [
    {"role_code": "INTERN", "title": "Engineering Intern",
     "description": "Audience onboarding, safe delivery, testing, and engineering fundamentals."},
    {"role_code": "SDE1", "title": "Software Development Engineer 1",
     "description": "Junior engineer: clean code, testing, git, debugging."},
    {"role_code": "SDE2", "title": "Software Development Engineer 2",
     "description": "Mid-level: system design, APIs, CI/CD, technical writing."},
    {"role_code": "SDE3", "title": "Software Development Engineer 3",
     "description": "Senior: distributed systems, mentorship, incident response."},
    {"role_code": "SWE_MANAGER", "title": "Software Engineering Manager",
     "description": "Agile planning, engineering leadership, people management."},
    {"role_code": "SWE_DIRECTOR", "title": "Director of Software Engineering",
     "description": "Organisational strategy, enterprise risk management."},
    {"role_code": "CSM", "title": "Customer Success Manager",
     "description": "Customer success management."},
    {"role_code": "CSM_DIRECTOR", "title": "Director of Customer Success",
     "description": "Customer operations leadership."},
    {"role_code": "SALES_OPS", "title": "Sales Operations",
     "description": "Sales operations roles."},
    {"role_code": "VP_REVENUE_OPS", "title": "VP of Revenue Operations",
     "description": "Revenue operations leadership."},
    {"role_code": "ACCOUNT_TEAM", "title": "Account Team",
     "description": "Customer-facing account management."},
    {"role_code": "SALES_MANAGER", "title": "Sales Manager",
     "description": "Sales team leadership."},
    {"role_code": "SALES_REP", "title": "Sales Representative",
     "description": "Direct sales."},
    {"role_code": "CUSTOMER_SERVICE", "title": "Customer Service Representative",
     "description": "Customer support and service."},
    {"role_code": "CLOUD_DEVOPS", "title": "Cloud DevOps Engineer",
     "description": "Cloud infrastructure and DevOps practice."},
    {"role_code": "AI_ML", "title": "AI / ML Engineer",
     "description": "Agentic AI, machine learning engineering, Foundry."},
    # One code for the whole security practice, following CLOUD_DEVOPS: the Director,
    # the Architect, the Engineer and the Analyst read the same standards and respond to
    # the same incidents, so splitting by seniority would divide one body of material
    # rather than serve four. Added because the org chart has four people in these roles
    # and nothing to map them to -- they were falling back to company-wide training in a
    # product whose subject is compliance.
    {"role_code": "SECURITY", "title": "Information Security",
     "description": "Security operations, incident response, threat and risk analysis."},
]


def seed_roles(bank) -> int:
    """Populate the role table only when it is empty, so manager removals stick."""
    if bank.roles():
        return 0
    for r in SEED_ROLES:
        bank.add_role(r["role_code"], r["title"], r["description"])
    return len(SEED_ROLES)
