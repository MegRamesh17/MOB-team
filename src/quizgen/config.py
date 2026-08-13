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


def _first(*names: str, default: str = "") -> str:
    """First env var that is set and not a placeholder."""
    for name in names:
        value = (os.getenv(name) or "").strip()
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

    # --- quiz assembly / adaptivity ---
    quiz_length: int = _int("QUIZGEN_QUIZ_LENGTH", 8)
    passing_score: float = _float("QUIZGEN_PASSING_SCORE", 80.0)
    weak_topic_share: float = _float("QUIZGEN_WEAK_SHARE", 0.7)
    weak_threshold: float = _float("QUIZGEN_WEAK_THRESHOLD", 0.70)
    min_answers_for_weakness: int = _int("QUIZGEN_MIN_ANSWERS", 3)
    target_success_rate: float = _float("QUIZGEN_TARGET_SUCCESS", 0.70)
    repeat_cooldown_attempts: int = _int("QUIZGEN_COOLDOWN", 2)

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
