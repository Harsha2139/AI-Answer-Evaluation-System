"""Similarity computations: TF-IDF cosine (word + character n-grams), pairwise matrices, fuzzy stem matching."""
from difflib import SequenceMatcher
from functools import lru_cache

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

WORD_VECTORIZER_ARGS = dict(ngram_range=(1, 2), sublinear_tf=True, token_pattern=r"(?u)\b\w+\b")


def new_word_vectorizer() -> TfidfVectorizer:
    return TfidfVectorizer(**WORD_VECTORIZER_ARGS)


def _cos_pair(vectorizer, a: str, b: str, fit: bool) -> float:
    if not a.strip() or not b.strip():
        return 0.0
    try:
        matrix = vectorizer.fit_transform([a, b]) if fit else vectorizer.transform([a, b])
        if matrix.getnnz() == 0:
            return 0.0
        return float(np.clip(cosine_similarity(matrix[0], matrix[1])[0, 0], 0.0, 1.0))
    except ValueError:  # empty vocabulary
        return 0.0


def word_cosine(stems_a: str, stems_b: str, vectorizer=None) -> float:
    """TF-IDF cosine on stemmed words. Uses a corpus-fitted vectorizer when given, else fits on the pair."""
    if vectorizer is not None:
        return _cos_pair(vectorizer, stems_a, stems_b, fit=False)
    return _cos_pair(new_word_vectorizer(), stems_a, stems_b, fit=True)


def char_cosine(tokens_a: str, tokens_b: str) -> float:
    """Character n-gram TF-IDF cosine; tolerant to spelling variations and inflections."""
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
    return _cos_pair(vec, tokens_a, tokens_b, fit=True)


def pairwise_similarity(texts: list) -> np.ndarray:
    """Cosine similarity matrix between many texts (used for copy / plagiarism detection)."""
    n = len(texts)
    if n < 2:
        return np.zeros((n, n))
    try:
        matrix = new_word_vectorizer().fit_transform(texts)
        return cosine_similarity(matrix)
    except ValueError:
        return np.zeros((n, n))


@lru_cache(maxsize=65536)
def stems_match(a: str, b: str) -> bool:
    """True when two stems are equal, share a long prefix, or are near-identical spellings."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 5 and long_.startswith(short):
        return True
    if len(short) >= 5 and abs(len(a) - len(b)) <= 3 and SequenceMatcher(None, a, b).ratio() >= 0.84:
        return True
    return False


def stem_coverage(term_stems: list, student_stems) -> float:
    """Fraction of a term's stems found (exactly or fuzzily) among the student's stems."""
    if not term_stems:
        return 0.0
    pool = set(student_stems)
    hits = sum(1 for t in term_stems if t in pool or any(stems_match(t, s) for s in pool))
    return hits / len(term_stems)
