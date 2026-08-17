"""
Azure AI Search — the vetted corpus.

This index is what makes "validated source" mean something concrete. Generation
retrieves from here and nowhere else: not the open web, not the model's own knowledge.
A question can only cite a passage that someone put in this index on purpose.

Hybrid retrieval: keyword (BM25, run by the service) fused with vector similarity over
text-embedding-3-large. Keyword alone misses paraphrase; vectors alone miss exact terms
like "SOLID" or "72 hours". The service merges both rankings.

Embeddings are computed at index time, once per chunk — not per query-time document.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from .config import CONFIG
from .isolation import IsolationError, validate_company_id
from .models import Chunk

INDEX_NAME_DEFAULT = "training-vetted-sources"
EMBED_DIMS = 3072  # text-embedding-3-large


def _credential():
    from azure.core.credentials import AzureKeyCredential

    if not CONFIG.search_key:
        raise RuntimeError(
            "AISearchAPIKEY is not set. Run `quizgen doctor` to confirm what loaded."
        )
    return AzureKeyCredential(CONFIG.search_key)


def _index_name() -> str:
    return CONFIG.search_index or INDEX_NAME_DEFAULT


def embed(texts: Sequence[str]) -> List[List[float]]:
    """Embed a batch with text-embedding-3-large."""
    from openai import AzureOpenAI

    gaps = CONFIG.missing_for_embeddings()
    if gaps:
        raise RuntimeError("Embeddings not configured: {}".format(", ".join(gaps)))

    client = AzureOpenAI(
        azure_endpoint=CONFIG.embedding_endpoint,
        api_key=CONFIG.embedding_key,
        api_version=CONFIG.azure_api_version,
        timeout=120.0,
        max_retries=4,
    )
    out: List[List[float]] = []
    # Batched: one call per chunk would be 100x the round trips for no benefit.
    for start in range(0, len(texts), 32):
        batch = list(texts[start:start + 32])
        response = client.embeddings.create(model=CONFIG.embedding_deployment, input=batch)
        out.extend(item.embedding for item in response.data)
    return out


def create_index(recreate: bool = False) -> str:
    """Create the index if absent. Safe to call repeatedly."""
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    name = _index_name()
    client = SearchIndexClient(endpoint=CONFIG.search_endpoint, credential=_credential())

    existing = set(client.list_index_names())
    if name in existing and not recreate:
        return name
    if name in existing and recreate:
        client.delete_index(name)

    fields = [
        SimpleField(name="chunk_id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="text", type=SearchFieldDataType.String),
        SearchableField(name="topic", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="doc_title", type=SearchFieldDataType.String),
        # A COLLECTION, not a string. A vetted source can be approved for SDE1, SDE2
        # and SDE3 but not a Director, and a comma-joined string cannot be filtered on
        # cleanly — search.ismatch() needs a searchable field, and eq cannot match one
        # role inside "SDE1,SDE2,SDE3". A collection filters with any(), which is the
        # idiom the service is built for.
        SearchField(
            name="role_scope",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
            facetable=True,
        ),
        # A plain string, NOT a collection like role_scope. A chunk can be approved for
        # several roles at once; it belongs to exactly one company. Filterable because
        # the whole point is a server-side filter that cannot be forgotten.
        SimpleField(name="company_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source_url", type=SearchFieldDataType.String),
        SimpleField(name="fetched_at", type=SearchFieldDataType.String, filterable=True, sortable=True),
        SimpleField(name="source_type", type=SearchFieldDataType.String, filterable=True),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=EMBED_DIMS,
            vector_search_profile_name="default-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="default-hnsw")],
        profiles=[VectorSearchProfile(name="default-profile", algorithm_configuration_name="default-hnsw")],
    )

    client.create_index(SearchIndex(name=name, fields=fields, vector_search=vector_search))
    return name


def upload(chunks: Sequence[Chunk], with_embeddings: bool = True) -> int:
    """Index the corpus. Re-uploading the same chunk_id replaces it."""
    from azure.search.documents import SearchClient

    if not chunks:
        return 0

    vectors: List[Optional[List[float]]] = [None] * len(chunks)
    if with_embeddings:
        vectors = embed([c.text for c in chunks])

    documents = []
    for chunk, vector in zip(chunks, vectors):
        doc: Dict = {
            "chunk_id": chunk.chunk_id,
            "text": chunk.text,
            "topic": chunk.topic,
            "doc_title": chunk.doc_title,
            # Split the comma-separated scope into the collection the index expects.
            "role_scope": [
                r.strip().upper() for r in (chunk.role_scope or "ALL").split(",") if r.strip()
            ],
            "company_id": chunk.company_id or "",
            "source_url": chunk.source_url or "",
            "fetched_at": chunk.fetched_at or "",
            "source_type": chunk.source_type or "document",
        }
        # Hard gate, before anything reaches the index. An untagged chunk in a shared
        # index is retrievable by every company, so this raises rather than dropping
        # the chunk quietly — a partially-tagged batch is the failure that goes
        # unnoticed until someone else's policy text turns up in your quiz.
        validate_company_id(doc)
        if vector is not None:
            doc["embedding"] = vector
        documents.append(doc)

    client = SearchClient(
        endpoint=CONFIG.search_endpoint, index_name=_index_name(), credential=_credential()
    )
    uploaded = 0
    for start in range(0, len(documents), 100):
        results = client.merge_or_upload_documents(documents[start:start + 100])
        uploaded += sum(1 for r in results if r.succeeded)
    return uploaded


def retrieve(query: str, company_id: str, role: str = "", topic: str = "", limit: int = 5) -> List[Chunk]:
    """
    Hybrid search over the vetted corpus, scoped to one company.

    Both filters are applied by the service, not after the fact — a source approved
    only for SDE1-3 is never returned for a Director, however well it matches, and a
    chunk belonging to another company is never returned at all.

    `company_id` is positional and required, unlike `role` and `topic`. That is
    deliberate: an optional company filter is one forgotten keyword argument away from
    querying every company's data at once, and the call would look perfectly normal in
    review. Making it structurally impossible to call unscoped is the only version of
    this that stays true as the codebase grows.
    """
    from azure.search.documents import SearchClient
    from azure.search.documents.models import VectorizedQuery

    if not company_id or not company_id.strip():
        raise IsolationError(
            "retrieve() requires a company_id — an unscoped query would search every "
            "company's material in a shared index."
        )

    client = SearchClient(
        endpoint=CONFIG.search_endpoint, index_name=_index_name(), credential=_credential()
    )

    filters = ["company_id eq '{}'".format(company_id.strip().replace("'", "''"))]
    if role:
        safe = role.upper().replace("'", "''")
        filters.append(
            "(role_scope/any(r: r eq 'ALL') or role_scope/any(r: r eq '{}'))".format(safe)
        )
    if topic:
        filters.append("topic eq '{}'".format(topic.replace("'", "''")))
    filter_expr = " and ".join(filters)

    vector_queries = None
    try:
        vector_queries = [
            VectorizedQuery(vector=embed([query])[0], k_nearest_neighbors=limit, fields="embedding")
        ]
    except Exception:  # noqa: BLE001
        # Embeddings unavailable: fall back to keyword-only rather than returning
        # nothing. Retrieval gets worse on paraphrased queries, not broken.
        pass

    results = client.search(
        search_text=query,
        vector_queries=vector_queries,
        filter=filter_expr,
        top=limit,
    )

    chunks: List[Chunk] = []
    for r in results:
        chunks.append(
            Chunk(
                chunk_id=r["chunk_id"],
                doc_id=r.get("source_url", ""),
                doc_title=r.get("doc_title", ""),
                topic=r.get("topic", ""),
                section=r.get("topic", ""),
                page_start=0,
                page_end=0,
                text=r["text"],
                container="vetted-sources",
                role_scope=",".join(r.get("role_scope") or ["ALL"]),
                company_id=r.get("company_id", ""),
                source_type=r.get("source_type", "web"),
                source_url=r.get("source_url", ""),
                fetched_at=r.get("fetched_at", ""),
            )
        )
    return chunks


def stats() -> Dict:
    """What is actually in the index."""
    from azure.search.documents import SearchClient

    client = SearchClient(
        endpoint=CONFIG.search_endpoint, index_name=_index_name(), credential=_credential()
    )
    result = client.search(search_text="*", top=0, include_total_count=True, facets=["topic,count:50"])
    total = result.get_count()
    facets = result.get_facets() or {}
    return {
        "index": _index_name(),
        "documents": total,
        "topics": {f["value"]: f["count"] for f in facets.get("topic", [])},
    }
