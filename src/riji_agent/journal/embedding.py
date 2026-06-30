"""Local embedding providers for semantic search.

Embeddings are computed and stored entirely on-device; nothing is sent to any
cloud service. The shipped ``HashingEmbeddingProvider`` is dependency-free and
deterministic (so stored vectors stay comparable across restarts). A stronger
local model (e.g. sentence-transformers) can be plugged in by implementing the
``EmbeddingProvider`` protocol.
"""

from __future__ import annotations

import hashlib
import math
from typing import List, Protocol, Sequence, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def dim(self) -> int:
        ...

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _char_ngrams(text: str, lo: int = 2, hi: int = 3):
    cleaned = "".join(text.split())
    for n in range(lo, hi + 1):
        for i in range(len(cleaned) - n + 1):
            yield cleaned[i : i + n]


class HashingEmbeddingProvider:
    """Deterministic local embedding via hashed character n-grams.

    Not a substitute for a trained model, but fully local, zero-dependency and
    stable, which is enough to wire and test hybrid ranking.
    """

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            vec = [0.0] * self._dim
            for ngram in _char_ngrams(text):
                digest = hashlib.md5(ngram.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self._dim
                vec[index] += 1.0
            norm = math.sqrt(sum(x * x for x in vec))
            if norm:
                vec = [x / norm for x in vec]
            vectors.append(vec)
        return vectors


def embedder_from_settings(settings) -> "EmbeddingProvider | None":
    """Return a local embedder when semantic search is enabled, else None.

    Swap in a stronger local model here (e.g. sentence-transformers) without
    changing call sites; the index just needs an ``EmbeddingProvider``.
    """
    if getattr(settings, "semantic_search_enabled", False):
        return HashingEmbeddingProvider()
    return None
