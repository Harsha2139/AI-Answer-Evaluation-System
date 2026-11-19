"""Model loading and persistence. Heavy objects are created once and reused (cached)."""
import hashlib
from functools import lru_cache

import joblib

import config
from ml.preprocessing import preprocess
from ml.similarity import new_word_vectorizer


@lru_cache(maxsize=1)
def get_sentence_model():
    """Load the sentence-transformers model ONCE. Returns None when disabled or not installed."""
    if not config.USE_SENTENCE_TRANSFORMERS:
        return None
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(config.SENTENCE_MODEL_NAME)
    except Exception:  # library or weights unavailable -> graceful fallback to TF-IDF only
        return None


def semantic_available() -> bool:
    return get_sentence_model() is not None


def semantic_similarity(text_a: str, text_b: str):
    """Embedding cosine similarity in [0, 1], or None if the model is unavailable."""
    model = get_sentence_model()
    if model is None or not text_a.strip() or not text_b.strip():
        return None
    emb = model.encode([text_a, text_b], normalize_embeddings=True)
    return float(max(0.0, min(1.0, float(emb[0] @ emb[1]))))


@lru_cache(maxsize=256)
def _load_vectorizer(path_str: str):
    return joblib.load(path_str)


def get_corpus_vectorizer(exam_id: int, reference_texts: list):
    """TF-IDF vectorizer fitted on an exam's reference answers; persisted with joblib and reused.

    The file name contains a hash of the reference texts, so editing a reference answer
    automatically produces a freshly fitted vectorizer.
    """
    docs = [preprocess(t).stem_text for t in reference_texts if t and t.strip()]
    docs = [d for d in docs if d]
    if len(docs) < 2:
        return None
    digest = hashlib.sha1("\x1f".join(docs).encode("utf-8")).hexdigest()[:12]
    path = config.MODEL_DIR / f"exam_{exam_id}_{digest}.joblib"
    if path.exists():
        try:
            return _load_vectorizer(str(path))
        except Exception:
            pass
    vec = new_word_vectorizer()
    try:
        vec.fit(docs)
    except ValueError:
        return None
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(vec, path)
    return vec
