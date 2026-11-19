"""Text cleaning, normalisation, tokenisation and light stemming (no external NLP downloads needed)."""
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

STOPWORDS = frozenset("""a about above after again all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he her here
hers him his how i if in into is it its itself just me more most my no nor not of off on once only or other our out over
own same she should so some such than that the their theirs them then there these they this those through to too under
until up very was we were what when where which while who whom why will with would you your yours""".split())

NEGATIONS = frozenset({"not", "no", "never", "neither", "nor", "cannot", "none"})

_SYNONYM_GROUPS = [
    ("big", "large", "huge", "massive", "enormous"), ("small", "tiny", "little"),
    ("fast", "quick", "rapid", "quickly", "rapidly"), ("use", "utilize", "utilise", "employ"),
    ("show", "display", "demonstrate", "exhibit"), ("make", "create", "generate", "produce", "build"),
    ("help", "assist", "aid"), ("allow", "permit"), ("predict", "forecast", "estimate"),
    ("branch", "subset", "subfield", "division"), ("increase", "grow", "rise"),
    ("decrease", "reduce", "decline", "drop"), ("start", "begin", "commence"), ("end", "finish", "terminate"),
    ("method", "technique", "approach"), ("error", "mistake", "fault"), ("goal", "aim", "objective", "purpose"),
]
SYNONYMS = {w: grp[0] for grp in _SYNONYM_GROUPS for w in grp}

_SUFFIXES = ("ations", "ation", "ments", "ment", "ness", "ings", "ing", "ions", "ion", "edly", "ers", "er",
             "ies", "ied", "ed", "es", "ly", "s")
_URL = re.compile(r"https?://\S+|www\.\S+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class ProcessedText:
    raw: str
    cleaned: str
    sentences: list = field(default_factory=list)
    tokens: list = field(default_factory=list)          # all words (normalised)
    content_tokens: list = field(default_factory=list)  # without stop-words, synonym-canonicalised
    stems: list = field(default_factory=list)
    negations: int = 0

    @property
    def stem_text(self) -> str:
        return " ".join(self.stems)


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = _URL.sub(" ", text)
    text = _CONTROL.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _expand_contractions(text: str) -> str:
    text = re.sub(r"\bcan't\b", "cannot", text)
    text = re.sub(r"\bwon't\b", "will not", text)
    return re.sub(r"n't\b", " not", text)


@lru_cache(maxsize=32768)
def stem(word: str) -> str:
    if len(word) <= 3 or word.isdigit():
        return word
    if word.endswith(("ss", "us", "is")):
        return word
    for suf in _SUFFIXES:
        if word.endswith(suf):
            base = word[: -len(suf)]
            if suf in ("ies", "ied"):
                return base + "y" if len(base) >= 2 else word
            if len(base) >= (4 if suf == "ing" else 3):
                return base
    return word


def preprocess(text: str) -> ProcessedText:
    cleaned = clean_text(text)
    sentences = [s.strip() for s in _SENT_SPLIT.split(cleaned) if s.strip()]
    lowered = _expand_contractions(cleaned.lower())
    tokens = _WORD.findall(lowered)
    content = [SYNONYMS.get(t, t) for t in tokens if t not in STOPWORDS and (len(t) > 1 or t.isdigit())]
    return ProcessedText(
        raw=str(text or ""), cleaned=cleaned, sentences=sentences, tokens=tokens, content_tokens=content,
        stems=[stem(t) for t in content], negations=sum(1 for t in tokens if t in NEGATIONS),
    )


def auto_keywords(reference: ProcessedText, limit: int = 8) -> list:
    """Fallback keyword list: the most informative content words of the reference answer."""
    freq, first = {}, {}
    for pos, (tok, st) in enumerate(zip(reference.content_tokens, reference.stems)):
        if len(tok) < 4:
            continue
        freq[st] = freq.get(st, 0) + 1
        first.setdefault(st, (pos, tok))
    ranked = sorted(freq, key=lambda s: (-freq[s], -len(first[s][1]), first[s][0]))[:limit]
    return [first[s][1] for s in sorted(ranked, key=lambda s: first[s][0])]


def auto_concepts(reference: ProcessedText, max_words: int = 12) -> list:
    """Fallback concepts: reference sentences, split further into clauses when they are long."""
    concepts = []
    for sent in reference.sentences:
        parts = [sent]
        if len(sent.split()) > max_words:
            parts = [p for p in re.split(r"[,;:]|\band\b|\bthat\b|\bwhich\b", sent) if p.strip()]
        for part in parts:
            if len(preprocess(part).stems) >= 2:
                concepts.append(part.strip(" ."))
    return concepts[:10]
