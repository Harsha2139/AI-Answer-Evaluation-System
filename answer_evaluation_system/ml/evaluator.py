"""Baseline NLP answer-scoring engine.

IMPORTANT - what this is and is not
-----------------------------------
This is a transparent, rule-based *baseline* built from real NLP computations (TF-IDF cosine similarity,
fuzzy keyword/concept matching, completeness). It is NOT a trained supervised model, and it has not been
validated against human grading. Its output is a *suggestion* that a teacher must review.

Final score = w1*similarity + w2*keyword + w3*concept + w4*completeness   (weights configurable, normalised)

Known limitations: it cannot verify factual truth; lexical similarity can be high for wrong answers that reuse
the question's words, and low for correct answers phrased very differently. Negations are only detected
heuristically. Optional sentence embeddings improve paraphrase handling but are not proof of correctness either.
"""
from dataclasses import asdict, dataclass, field

import numpy as np

import config
from ml import model_loader
from ml.preprocessing import auto_concepts, auto_keywords, preprocess
from ml.similarity import char_cosine, stem_coverage, word_cosine
from utils.helpers import round_half

ENGINE_NAME = "baseline-nlp-tfidf-v1"


@dataclass
class EvaluationResult:
    max_marks: float
    suggested_marks: float = 0.0
    similarity: float = 0.0          # percent, calibrated credit
    raw_similarity: float = 0.0      # percent, raw cosine
    semantic_similarity: float = None
    keyword_coverage: float = 0.0    # percent
    concept_coverage: float = 0.0    # percent
    completeness: float = 0.0        # percent
    confidence: float = 0.0          # 0..1
    needs_review: bool = False
    matched_keywords: list = field(default_factory=list)
    missing_keywords: list = field(default_factory=list)
    matched_concepts: list = field(default_factory=list)
    partial_concepts: list = field(default_factory=list)
    missing_concepts: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    weights: dict = field(default_factory=dict)
    breakdown: dict = field(default_factory=dict)
    performance_level: str = "Needs Improvement"
    engine: str = ENGINE_NAME
    keywords_auto: bool = False
    concepts_auto: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_weights(weights: dict = None, active: dict = None) -> dict:
    w = {k: max(0.0, float((weights or {}).get(k, v))) for k, v in config.DEFAULT_WEIGHTS.items()}
    for k, on in (active or {}).items():
        if not on:
            w[k] = 0.0
    total = sum(w.values())
    if total <= 0:
        w = dict(config.DEFAULT_WEIGHTS)
        total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def performance_level(pct: float) -> str:
    return ("Excellent" if pct >= 85 else "Good" if pct >= 65 else "Average" if pct >= 40 else "Needs Improvement")


def _term_variants(spec: str):
    parts = [p.strip() for p in spec.split("|") if p.strip()]
    return (parts[0] if parts else spec), parts


def _match_term(variants: list, student_stems: list) -> float:
    """Best stem coverage over a term and its synonyms ('term|synonym1|synonym2')."""
    best = 0.0
    for v in variants:
        stems = preprocess(v).stems
        if stems:
            best = max(best, stem_coverage(stems, student_stems))
    return best


def _keyword_matched(frac: float, n_stems: int) -> bool:
    return frac >= (1.0 if n_stems <= 2 else 0.8)


def _concept_score(concept: str, student, vectorizer) -> float:
    c = preprocess(concept)
    if not c.stems:
        return 0.0
    frac = stem_coverage(c.stems, student.stems)
    token_score = float(np.clip((frac - 0.25) / 0.5, 0.0, 1.0))   # <=25% -> 0, >=75% -> 1
    if token_score >= 0.99:
        return token_score
    best_sentence = 0.0
    for sent in student.sentences or [student.cleaned]:
        s = preprocess(sent)
        if s.stems:
            best_sentence = max(best_sentence, word_cosine(c.stem_text, s.stem_text, vectorizer))
    semantic_score = float(np.clip((best_sentence - 0.2) / 0.4, 0.0, 1.0)) * 0.9
    return max(token_score, semantic_score)


def _empty_result(max_marks, flag, weights) -> EvaluationResult:
    return EvaluationResult(max_marks=max_marks, confidence=1.0, flags=[flag], weights=weights,
                            breakdown={"final_fraction": 0.0})


def evaluate_answer(student_answer: str, reference_answer: str, max_marks: float, keywords=None, concepts=None,
                    alternate_answers=None, weights: dict = None, vectorizer=None,
                    use_semantic: bool = None) -> EvaluationResult:
    max_marks = float(max_marks)
    stu, ref = preprocess(student_answer), preprocess(reference_answer)
    weights_n = normalize_weights(weights)
    if not stu.content_tokens:
        return _empty_result(max_marks, "Empty or meaningless answer (no content words).", weights_n)

    refs = [ref] + [p for p in (preprocess(a) for a in (alternate_answers or [])) if p.stems]
    flags = []

    # 1) similarity (best over the reference and any alternate acceptable answers)
    raw_lex, best_ref = 0.0, ref
    for r in refs:
        lex = 0.65 * word_cosine(stu.stem_text, r.stem_text, vectorizer) + \
              0.35 * char_cosine(" ".join(stu.content_tokens), " ".join(r.content_tokens))
        if lex >= raw_lex:
            raw_lex, best_ref = lex, r
    semantic = None
    if use_semantic is None:
        use_semantic = config.USE_SENTENCE_TRANSFORMERS
    if use_semantic:
        sims = [model_loader.semantic_similarity(stu.cleaned, r.cleaned) for r in refs]
        sims = [s for s in sims if s is not None]
        semantic = max(sims) if sims else None
    if semantic is not None:
        calibrated_sem = float(np.clip((semantic - 0.25) / 0.6, 0, 1))
        sim_credit = 0.5 * min(1.0, raw_lex / config.SIMILARITY_FULL_CREDIT) + 0.5 * calibrated_sem
        raw_similarity = 0.5 * raw_lex + 0.5 * semantic
    else:
        sim_credit = min(1.0, raw_lex / config.SIMILARITY_FULL_CREDIT)
        raw_similarity = raw_lex

    # 2) keywords (teacher supplied, otherwise auto-extracted from the reference answer)
    kw_specs = list(keywords or [])
    keywords_auto = False
    if not kw_specs:
        kw_specs, keywords_auto = auto_keywords(ref), True
    matched_kw, missing_kw = [], []
    for spec in kw_specs:
        label, variants = _term_variants(spec)
        n_stems = max((len(preprocess(v).stems) for v in variants), default=1)
        (matched_kw if _keyword_matched(_match_term(variants, stu.stems), n_stems) else missing_kw).append(label)
    keyword_cov = len(matched_kw) / len(kw_specs) if kw_specs else 0.0

    # keyword-stuffing guard: many keywords but almost no overall resemblance to the reference
    if keyword_cov >= 0.6 and raw_lex < 0.12 and len(stu.content_tokens) <= len(kw_specs) * 2:
        flags.append("Possible keyword stuffing: keywords present but the answer does not resemble the reference.")
        keyword_cov *= 0.5

    # 3) concepts
    concept_specs = list(concepts or [])
    concepts_auto = False
    if not concept_specs:
        concept_specs, concepts_auto = auto_concepts(ref), True
    matched_c, partial_c, missing_c, c_scores = [], [], [], []
    for c in concept_specs:
        label, variants = _term_variants(c)
        score = max(_concept_score(v, stu, vectorizer) for v in variants)
        c_scores.append(score)
        (matched_c if score >= 0.6 else partial_c if score >= 0.3 else missing_c).append(label)
    concept_cov = float(np.mean(c_scores)) if c_scores else 0.0

    # 4) completeness: length relative to the reference answer (not a quality signal on its own)
    ref_len = max(len(r.content_tokens) for r in refs)
    completeness = float(np.clip(len(stu.content_tokens) / (0.6 * max(ref_len, 1)), 0.0, 1.0))

    # 5) relevance gate and contradiction heuristic
    if raw_lex < 0.08 and keyword_cov < 0.15 and concept_cov < 0.15:
        flags.append("Answer appears irrelevant to the question.")
        res = EvaluationResult(max_marks=max_marks, raw_similarity=round(raw_similarity * 100, 1),
                               semantic_similarity=None if semantic is None else round(semantic * 100, 1),
                               similarity=0.0, keyword_coverage=round(keyword_cov * 100, 1),
                               concept_coverage=round(concept_cov * 100, 1), completeness=round(completeness * 100, 1),
                               matched_keywords=matched_kw, missing_keywords=missing_kw, matched_concepts=matched_c,
                               partial_concepts=partial_c, missing_concepts=missing_c, flags=flags,
                               weights=weights_n, confidence=0.9, keywords_auto=keywords_auto,
                               concepts_auto=concepts_auto, breakdown={"final_fraction": 0.0})
        return res
    negation_mismatch = (stu.negations > 0) != (best_ref.negations > 0) and raw_lex >= 0.3
    if negation_mismatch:
        flags.append("Negation differs from the reference answer; the student may contradict it. "
                      "Marks are capped at 50% until a teacher verifies.")
    function_ratio = 1 - len(stu.content_tokens) / max(len(stu.tokens), 1)
    keyword_list_like = len(stu.tokens) >= 6 and function_ratio < 0.08
    if keyword_list_like:
        flags.append("Answer looks like a list of keywords rather than sentences. Please verify understanding.")

    # 6) combine signals
    weights_n = normalize_weights(weights, {"keyword": bool(kw_specs), "concept": bool(concept_specs)})
    signals = {"similarity": sim_credit, "keyword": keyword_cov, "concept": concept_cov, "completeness": completeness}
    fraction = float(np.clip(sum(weights_n[k] * signals[k] for k in signals), 0.0, 1.0))
    if negation_mismatch:
        fraction = min(fraction, 0.5)
    marks = min(max_marks, max(0.0, round_half(fraction * max_marks)))

    # 7) confidence: do independent signals agree? is the answer long enough? any warnings?
    core = [sim_credit, keyword_cov, concept_cov]
    confidence = 1.0 - min(1.0, float(np.std(core)) * 1.6)
    if len(stu.content_tokens) < 5:
        confidence *= 0.6
    if 0.35 <= fraction <= 0.65:
        confidence *= 0.85
    if keywords_auto and concepts_auto:
        confidence *= 0.85
    if flags:
        confidence *= 0.7
    confidence = float(np.clip(confidence, 0.05, 1.0))
    needs_review = (confidence < config.LOW_CONFIDENCE_THRESHOLD or negation_mismatch or keyword_list_like)

    pct = fraction * 100
    return EvaluationResult(
        max_marks=max_marks, suggested_marks=marks, similarity=round(sim_credit * 100, 1),
        raw_similarity=round(raw_similarity * 100, 1),
        semantic_similarity=None if semantic is None else round(semantic * 100, 1),
        keyword_coverage=round(keyword_cov * 100, 1), concept_coverage=round(concept_cov * 100, 1),
        completeness=round(completeness * 100, 1), confidence=round(confidence, 2), needs_review=needs_review,
        matched_keywords=matched_kw, missing_keywords=missing_kw, matched_concepts=matched_c,
        partial_concepts=partial_c, missing_concepts=missing_c, flags=flags, weights=weights_n,
        breakdown={**{f"{k}_signal": round(v, 3) for k, v in signals.items()},
                   **{f"{k}_weight": round(weights_n[k], 3) for k in weights_n},
                   "final_fraction": round(fraction, 3)},
        performance_level=performance_level(pct), keywords_auto=keywords_auto, concepts_auto=concepts_auto)
