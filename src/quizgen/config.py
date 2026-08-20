"""
Configuration.

Reads the Group7 key names your Azure resources were provisioned with, and falls back
to the AZURE_OPENAI_* names that src/content_agent.py and src/chatbot.py already use —
so both naming conventions work and nobody's existing code breaks.

Note on the names: env keys containing hyphens (Group7-7OpenAIAPIKey) cannot be
`export`ed from a POSIX shell, but python-dotenv loads them into os.environ from .env
without trouble. Keep them in .env; do not try to export them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

# Repo root is two levels up from src/quizgen/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
DOCUMENT_DIR = DATA_DIR / "documents"
OUTPUT_DIR = DATA_DIR / "output"

PLACEHOLDER = "REPLACE_ME"


# ---------------------------------------------------------------------------
# Key Vault
# ---------------------------------------------------------------------------
# Deployed code gets secrets through Key Vault references resolved by the Function
# App's managed identity — no file, no app setting holding a real value.
#
# Locally there is no managed identity, so the options are a .env file or fetching
# from the vault with your own `az login` identity. The second is better: nothing is
# written to disk, so nothing can be committed, screenshotted or left stale after a
# rotation. A storage key was exposed exactly that way earlier in this project.
#
# Set QUIZGEN_USE_KEYVAULT=true (and QUIZGEN_VAULT_NAME) to use it.

_VAULT_CACHE: dict = {}


def _vault_secret(name: str) -> str:
    """Fetch one secret using the developer's own Azure login. Cached per process."""
    if name in _VAULT_CACHE:
        return _VAULT_CACHE[name]

    vault = os.getenv("QUIZGEN_VAULT_NAME", "Group7-8")
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        client = SecretClient(
            vault_url="https://{}.vault.azure.net".format(vault),
            credential=DefaultAzureCredential(),
        )
        value = client.get_secret(name).value or ""
    except Exception:  # noqa: BLE001
        # Vault unreachable or secret absent: fall through to the environment rather
        # than crashing. Missing values surface in `quizgen doctor` as "(unset)".
        value = ""

    _VAULT_CACHE[name] = value
    return value


_USE_VAULT = (os.getenv("QUIZGEN_USE_KEYVAULT", "false") or "").lower() == "true"


def _first(*names: str, default: str = "") -> str:
    """
    First value that is set and not a placeholder.

    Order: environment (.env) first, then Key Vault if enabled. Environment wins so a
    developer can override one value locally without touching the vault.
    """
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value and value != PLACEHOLDER:
            return value

    if _USE_VAULT:
        for name in names:
            value = _vault_secret(name).strip()
            if value and value != PLACEHOLDER:
                return value

    return default


def _base_endpoint(url: str) -> str:
    """
    Reduce an endpoint to scheme + host.

    The vault stores Group7-8OpenAIEndPoint with a full API path on the end
    (".../openai/v1/responses"). The OpenAI SDK appends its own path, so passing that
    through builds ".../openai/v1/responses/openai/deployments/..." and fails with a
    bare "Connection error" that points nowhere useful.

    The embedding secret on the same resource is stored correctly as just the host,
    which is what confirms the host is right and only the path is the problem.
    """
    url = (url or "").strip()
    if not url:
        return ""
    match = re.match(r"^(https?://[^/]+)", url)
    return match.group(1) if match else url


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class Config:
    # --- provider ---
    # "mock" runs fully offline. "azure" calls Azure OpenAI.
    provider: str = os.getenv("QUIZGEN_PROVIDER", "mock")

    # --- Azure OpenAI (chat) ---
    # Group7-7 / Group7-8 is how the keys were issued; the mismatch in the middle digit
    # is theirs, not a typo here. AZURE_OPENAI_* are the fallbacks the existing
    # content_agent.py and chatbot.py read.
    azure_openai_endpoint: str = _base_endpoint(_first("Group7-8OpenAIEndPoint", "AZURE_OPENAI_ENDPOINT"))
    azure_openai_key: str = _first("Group7-7OpenAIAPIKey", "AZURE_OPENAI_KEY", "AZURE_OPENAI_API_KEY")
    azure_chat_deployment: str = _first("AZURE_OPENAI_CHAT_DEPLOYMENT", default="gpt-5")
    azure_api_version: str = _first("AZURE_OPENAI_API_VERSION", default="2024-10-21")

    # --- Azure OpenAI (embeddings) ---
    embedding_endpoint: str = _base_endpoint(_first(
        "Group7-8text-embedding-3-large-Endpoint", "AZURE_OPENAI_EMBEDDING_ENDPOINT"
    ))
    embedding_key: str = _first(
        "Group7-8text-embedding-3-large-APIKEY", "AZURE_OPENAI_EMBEDDING_KEY"
    )
    embedding_deployment: str = _first(
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", default="text-embedding-3-large"
    )

    # --- Azure AI Search ---
    # "AISearchEnpoint" is the spelling on the issued key; the corrected spelling is
    # accepted too so a fixed .env keeps working.
    search_endpoint: str = _base_endpoint(_first("AISearchEnpoint", "AISearchEndpoint", "AZURE_SEARCH_ENDPOINT"))
    search_key: str = _first("AISearchAPIKEY", "AISearchAPIKey", "AZURE_SEARCH_API_KEY")
    search_index: str = _first("AISearchIndexName", default="training-chunks")

    # --- Azure SQL (target for the loader and the API) ---
    # SQL auth to match the team's GitHub Actions workflow. SQL_PASSWORD is the same
    # value as its SQL_ADMIN_PASSWORD secret.
    sql_server: str = _first("SQL_SERVER", default="mob-sql-server-02.database.windows.net")
    sql_database: str = _first("SQL_DATABASE", default="mob-training-db")
    sql_user: str = _first("SQL_USER", default="mobsqladmin")
    sql_password: str = _first("SQL_PASSWORD", "SQL_ADMIN_PASSWORD")

    # --- Blob storage (used by src/pdf_extractor.py) ---
    storage_connection_string: str = _first("AZURE_STORAGE_CONNECTION_STRING")

    # Containers to ingest, as "container:ROLE_CODE" pairs. ROLE_CODE is "ALL" for
    # company-wide material that every role must know.
    #
    # This mapping is the role taxonomy. It beats inferring roles from the text
    # because someone made a deliberate filing decision when they uploaded the PDF.
    document_containers_raw: str = _first(
        "DOCUMENT_CONTAINERS",
        default="company-docs:ALL,software-engineering-docs:SWE",
    )

    # --- Azure Document Intelligence ---
    #
    # Replaces pypdf for extraction. pypdf returns nothing at all for a scanned page and
    # flattens tables and multi-column layouts into unreadable order — and a question can
    # only be as good as the passage it was grounded in.
    #
    # Both empty by default, and that is meaningful: extraction falls back to pypdf when
    # they are unset, so `QUIZGEN_PROVIDER=mock` with no Azure account keeps working.
    # That offline path is a hard constraint in PROJECT.md and tests.yml depends on it.
    doc_intelligence_endpoint: str = _base_endpoint(
        _first("DOCUMENT_INTELLIGENCE_ENDPOINT", "AZURE_DOC_INTELLIGENCE_ENDPOINT"))
    doc_intelligence_key: str = _first(
        "DOCUMENT_INTELLIGENCE_KEY", "AZURE_DOC_INTELLIGENCE_KEY")

    # prebuilt-layout is the model that earns its cost here: it does OCR, and it returns
    # tables and heading roles rather than a flat text blob. prebuilt-read is cheaper and
    # gives OCR without structure, which loses the section headings the chunker uses.
    doc_intelligence_model: str = _first(
        "DOCUMENT_INTELLIGENCE_MODEL", default="prebuilt-layout")

    @property
    def doc_intelligence_configured(self) -> bool:
        return bool(self.doc_intelligence_endpoint and self.doc_intelligence_key)

    # Which company the chunks produced by this process belong to.
    #
    # Defaults to "1" — Companies.id 1, "Quadrant Technologies", seeded by
    # 009_add_multitenancy.sql. A string because the search index stores it as one
    # (see docs/company-isolation-gap.md), and because the value that will eventually
    # drive it is the company_id claim on a session token.
    #
    # A default is acceptable HERE and not on Chunk.company_id, which is the
    # distinction worth keeping straight: this is "which company is this ingestion
    # run for", answered once per process by whoever started it. Chunk.company_id is
    # "which company does this passage belong to", and a passage that never got an
    # answer must not fall back to one — it fails instead. Config supplies the value;
    # it does not excuse its absence.
    company_id: str = _first("QUIZGEN_COMPANY_ID", default="1")

    @property
    def document_containers(self) -> List[tuple]:
        """[(container_name, role_code), ...]"""
        pairs = []
        for item in self.document_containers_raw.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                name, role = item.split(":", 1)
                pairs.append((name.strip(), role.strip().upper()))
            else:
                # No role given: treat as company-wide rather than guessing.
                pairs.append((item, "ALL"))
        return pairs

    # --- retrieval backend ---
    # "bm25"   - offline, no API, good enough for a few hundred chunks
    # "vector" - embeddings stored locally, cosine similarity in process
    # "search" - Azure AI Search (hybrid keyword + vector)
    retrieval: str = os.getenv("QUIZGEN_RETRIEVAL", "bm25")

    # --- ingestion ---
    chunk_target_chars: int = _int("QUIZGEN_CHUNK_CHARS", 1200)
    chunk_overlap_chars: int = _int("QUIZGEN_CHUNK_OVERLAP", 150)
    chunk_min_chars: int = _int("QUIZGEN_CHUNK_MIN", 250)

    # --- generation ---
    questions_per_chunk: int = _int("QUIZGEN_QUESTIONS_PER_CHUNK", 2)
    seed: int = _int("QUIZGEN_SEED", 1337)

    # How much the model may draw on its own knowledge.
    #
    # "augmented" (default): the passage sets the TOPIC, and the model teaches the
    #   subject properly using what it knows about the field. A question about CI/CD
    #   can cover things the internal document never mentions. Output is tagged
    #   RoleKnowledge and may NOT state company-specific rules.
    #
    # "grounded": every answer must be traceable to a verbatim sentence in the passage.
    #   Safer, but limited to what the documents happen to say — and these documents are
    #   bullet-point course outlines, not comprehensive teaching material.
    #
    # The company-policy ban holds in BOTH modes. That is the load-bearing rule: the
    # model may teach the subject freely, and may never invent an internal rule.
    generation_mode: str = _first("QUIZGEN_MODE", default="augmented")

    # Auto-approve generated questions instead of holding them for human review.
    # True because the team has no reviewer capacity. The mechanical checks still run
    # (exactly one correct answer, no fabricated company rules, cross-document
    # contradiction flags) — what is lost is a person reading each question.
    auto_approve: bool = (_first("QUIZGEN_AUTO_APPROVE", default="true").lower() == "true")

    # --- instructional course generation ---
    # A module that cannot meet these floors is merged or withheld. Question count is
    # derived from validated learning points later; chunks never set assessment volume.
    course_min_words: int = _int("QUIZGEN_COURSE_MIN_MODULE_WORDS", 600)
    course_min_learning_points: int = _int("QUIZGEN_COURSE_MIN_LEARNING_POINTS", 5)
    course_min_pages: int = _int("QUIZGEN_COURSE_MIN_PAGES", 3)
    course_max_pages: int = _int("QUIZGEN_COURSE_MAX_PAGES", 8)
    course_max_modules: int = _int("QUIZGEN_COURSE_MAX_MODULES", 10)
    course_max_web_sources: int = _int("QUIZGEN_COURSE_MAX_WEB_SOURCES", 6)
    web_enrichment: bool = (
        _first("QUIZGEN_WEB_ENRICHMENT", default="true").lower() == "true"
    )
    # Recording-friendly path. It keeps normal module coverage, grounding, citations,
    # and lesson quality while reducing latency through bounded parallel authoring,
    # avoiding unnecessary web research for already-substantial source material, and
    # using one balanced assessment request instead of three sequential calls.
    demo_fast: bool = (
        _first("QUIZGEN_DEMO_FAST", default="false").lower() == "true"
    )
    demo_fast_author_workers: int = _int("QUIZGEN_DEMO_AUTHOR_WORKERS", 2)
    demo_fast_question_count: int = _int("QUIZGEN_DEMO_QUESTION_COUNT", 18)

    # --- quiz assembly / adaptivity ---
    quiz_length: int = _int("QUIZGEN_QUIZ_LENGTH", 8)
    passing_score: float = _float("QUIZGEN_PASSING_SCORE", 80.0)
    weak_topic_share: float = _float("QUIZGEN_WEAK_SHARE", 0.7)
    weak_threshold: float = _float("QUIZGEN_WEAK_THRESHOLD", 0.70)
    min_answers_for_weakness: int = _int("QUIZGEN_MIN_ANSWERS", 3)
    target_success_rate: float = _float("QUIZGEN_TARGET_SUCCESS", 0.70)
    repeat_cooldown_attempts: int = _int("QUIZGEN_COOLDOWN", 2)

    # What "a topic" means for mastery and weak-topic targeting: "subject" (the source
    # document) or "topic" (the section heading within it).
    #
    # Defaults to subject because topic does not work with real documents. Section
    # headings produced 112 topics from 6 documents — 2.1 questions per topic against
    # an evidence floor of 3 answers, so most topics could never accumulate enough
    # evidence to be judged weak, and adaptive targeting never engaged at all. Measured
    # over six rounds: zero topics targeted, learner never rose above one answer per
    # topic. Grouping by document gives 32-44 questions per subject, which clears the
    # floor in a single quiz.
    #
    # Set QUIZGEN_MASTERY_GRAIN=topic for fine-grained targeting once there are enough
    # questions per section to support it (roughly 6+ each).
    mastery_grain: str = _first("QUIZGEN_MASTERY_GRAIN", default="subject").lower()

    # --- storage ---
    db_path: Path = Path(os.getenv("QUIZGEN_DB", str(OUTPUT_DIR / "quizgen.db")))

    # --- readiness ---

    def missing_for_azure(self) -> List[str]:
        gaps = []
        if not self.azure_openai_endpoint:
            gaps.append("Group7-8OpenAIEndPoint")
        if not self.azure_openai_key:
            gaps.append("Group7-7OpenAIAPIKey")
        return gaps

    def missing_for_embeddings(self) -> List[str]:
        gaps = []
        if not self.embedding_endpoint:
            gaps.append("Group7-8text-embedding-3-large-Endpoint")
        if not self.embedding_key:
            gaps.append("Group7-8text-embedding-3-large-APIKEY")
        return gaps

    def missing_for_search(self) -> List[str]:
        gaps = []
        if not self.search_endpoint:
            gaps.append("AISearchEnpoint")
        if not self.search_key:
            gaps.append("AISearchAPIKEY")
        return gaps

    def require_azure(self) -> None:
        gaps = self.missing_for_azure()
        if gaps:
            raise RuntimeError(
                "QUIZGEN_PROVIDER=azure but these are unset in .env: {}\n"
                "Fill them in, or run with QUIZGEN_PROVIDER=mock for the offline "
                "generator.".format(", ".join(gaps))
            )


CONFIG = Config()


def _mirror_to_legacy_names() -> None:
    """
    src/content_agent.py reads AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY directly.

    Rather than making anyone paste the same two values into .env twice, set those
    names from the Group7 values at import time. Only fills what is not already set,
    so an explicit override still wins.
    """
    for name, value in (
        ("AZURE_OPENAI_ENDPOINT", CONFIG.azure_openai_endpoint),
        ("AZURE_OPENAI_KEY", CONFIG.azure_openai_key),
        ("AZURE_OPENAI_EMBEDDING_ENDPOINT", CONFIG.embedding_endpoint),
        ("AZURE_OPENAI_EMBEDDING_KEY", CONFIG.embedding_key),
        ("AZURE_SEARCH_ENDPOINT", CONFIG.search_endpoint),
        ("AZURE_SEARCH_API_KEY", CONFIG.search_key),
    ):
        if value and not os.getenv(name):
            os.environ[name] = value


_mirror_to_legacy_names()
