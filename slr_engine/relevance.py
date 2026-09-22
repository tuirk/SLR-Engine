"""Local, free relevance scoring via sentence-embedding cosine similarity.

Why this exists: source-native ranking scores (OpenAlex `relevance_score`,
Crossref `score`) are useful for early-stopping pagination within a single
adapter (see slr_engine/sources/openalex.py and crossref.py), but they are
NOT comparable across sources -- an OpenAlex score of 20 and a Crossref
score of 20 mean nothing relative to each other, and several sources
(arXiv, PubMed, Europe PMC, DBLP, IA Scholar) return no ranking score at
all. Screening needs one consistent signal across every record regardless
of where it came from.

The fix is a local embedding model: encode the project's seed paper(s) as
a reference vector, encode each candidate record's title+abstract, and
score by cosine similarity (0..1, comparable across all records). This
runs entirely on-device via `sentence-transformers` -- no API key, no
per-call cost, no network calls beyond the one-time model download (and
that download is already paid for by vocabulary extraction in
00c_extract_vocabulary.py, which uses the same all-MiniLM-L6-v2 model
via KeyBERT -- the weights are already cached locally).

This does NOT replace human/LLM screening decisions. It's a triage signal:
sort_by relevance_score ascending during screening prep to see the weakest
matches first, or use it to explain why a record showed up at all. Nothing
in this module deletes or auto-excludes a record.
"""
from __future__ import annotations

from typing import Optional

_MODEL_NAME = "all-MiniLM-L6-v2"
_model = None


def available() -> bool:
    """Whether sentence-transformers is installed. Callers should degrade
    gracefully (skip scoring) rather than error when this is False."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


class LocalRelevanceScorer:
    """Cosine similarity between a fixed reference text (seed papers, and
    optionally the curated vocabulary) and each candidate record."""

    def __init__(self, reference_texts: list[str]):
        reference_texts = [t for t in reference_texts if t and t.strip()]
        if not reference_texts:
            raise ValueError("LocalRelevanceScorer needs at least one non-empty reference text")
        import numpy as np
        model = _get_model()
        embeddings = model.encode(reference_texts, normalize_embeddings=True)
        ref = np.mean(embeddings, axis=0)
        norm = np.linalg.norm(ref)
        self._ref_vector = ref / norm if norm > 0 else ref

    def score(self, title: str, abstract: Optional[str] = None) -> float:
        """Cosine similarity in [-1, 1] (practically ~[0, 1] for related text)."""
        import numpy as np
        model = _get_model()
        text = title if not abstract else f"{title}. {abstract}"
        vec = model.encode([text], normalize_embeddings=True)[0]
        return float(np.dot(vec, self._ref_vector))

    def score_batch(self, records: list[tuple[str, Optional[str]]]) -> list[float]:
        """Batched version of score() -- much faster than scoring one at a time."""
        import numpy as np
        model = _get_model()
        texts = [t if not a else f"{t}. {a}" for t, a in records]
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [float(np.dot(v, self._ref_vector)) for v in vecs]


def build_reference_texts(project_paths) -> list[str]:
    """Collect seed paper title+abstract text to use as the relevance
    reference. Reads seeds/seed_*.json directly rather than the DB, so this
    works even before seeds are ingested into records."""
    import json as _json

    seeds_dir = project_paths.root / "seeds"
    texts: list[str] = []
    if not seeds_dir.exists():
        return texts
    for f in sorted(seeds_dir.glob("seed_*.json")):
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        title = data.get("title") or ""
        abstract = data.get("abstract") or ""
        text = f"{title}. {abstract}".strip(". ").strip()
        if text:
            texts.append(text)
    return texts
