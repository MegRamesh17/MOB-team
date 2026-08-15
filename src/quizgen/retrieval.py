"""
Cross-document retrieval — BM25, implemented in pure Python.

No vector database, no embedding API, no extra dependency. At this scale that is the
right call: a few hundred chunks of policy text where the vocabulary is shared between
query and document. BM25 handles that well, runs instantly, and works offline.

Swap for embeddings when the corpus grows past a few thousand chunks, or when queries
stop sharing vocabulary with the documents (paraphrased questions, synonyms). Keep this
interface and the callers will not notice.

Two jobs:
  * gather source material for a role requirement across every document
  * find related passages elsewhere in the corpus to check a question against
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Sequence, Tuple

from .models import Chunk

_TOKEN = re.compile(r"[a-z0-9]+")

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "at", "by", "with",
    "is", "are", "was", "were", "be", "been", "that", "this", "these", "those", "it",
    "as", "from", "must", "may", "any", "all", "not", "no", "if", "when", "which",
}


def tokenise(text: str) -> List[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 2]


class BM25:
    """Standard Okapi BM25."""

    def __init__(self, chunks: Sequence[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = list(chunks)
        self.k1 = k1
        self.b = b

        self.docs: List[List[str]] = [tokenise(c.text) for c in self.chunks]
        self.lengths = [len(d) for d in self.docs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0

        self.freqs: List[Dict[str, int]] = []
        doc_count: Dict[str, int] = {}
        for doc in self.docs:
            counts: Dict[str, int] = {}
            for token in doc:
                counts[token] = counts.get(token, 0) + 1
            self.freqs.append(counts)
            for token in counts:
                doc_count[token] = doc_count.get(token, 0) + 1

        n = len(self.docs)
        self.idf = {
            token: math.log(1 + (n - df + 0.5) / (df + 0.5)) for token, df in doc_count.items()
        }

    def search(
        self,
        query: str,
        limit: int = 5,
        exclude_chunk_ids: Sequence[str] = (),
        exclude_docs: Sequence[str] = (),
    ) -> List[Tuple[Chunk, float]]:
        tokens = tokenise(query)
        if not tokens or not self.docs:
            return []

        excluded_ids = set(exclude_chunk_ids)
        excluded_docs = set(exclude_docs)

        scored: List[Tuple[Chunk, float]] = []
        for i, chunk in enumerate(self.chunks):
            if chunk.chunk_id in excluded_ids or chunk.doc_id in excluded_docs:
                continue
            score = 0.0
            length = self.lengths[i] or 1
            for token in tokens:
                tf = self.freqs[i].get(token, 0)
                if not tf:
                    continue
                idf = self.idf.get(token, 0.0)
                denom = tf + self.k1 * (1 - self.b + self.b * length / (self.avg_len or 1))
                score += idf * (tf * (self.k1 + 1)) / denom
            if score > 0:
                scored.append((chunk, score))

        scored.sort(key=lambda pair: -pair[1])
        return scored[:limit]

    def related_to_question(self, question_text: str, source_chunk_id: str, limit: int = 4):
        """
        Passages elsewhere in the corpus that bear on this question.

        The source chunk is excluded on purpose — the point is to find what *other*
        documents say, which is where contradictions live.
        """
        return [c for c, _ in self.search(question_text, limit=limit, exclude_chunk_ids=[source_chunk_id])]
