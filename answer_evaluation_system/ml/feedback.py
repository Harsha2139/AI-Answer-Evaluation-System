"""Template-based feedback built from the evaluation signals (no generative model involved)."""


def build_feedback(result) -> dict:
    positives, suggestions = [], []
    if result.matched_keywords:
        positives.append("Key terms you covered: " + ", ".join(result.matched_keywords) + ".")
    if result.matched_concepts:
        positives.append("Concepts you explained well: " + "; ".join(result.matched_concepts) + ".")
    if result.partial_concepts:
        suggestions.append("These ideas were only partly covered - add more detail: " + "; ".join(result.partial_concepts) + ".")
    if result.missing_concepts:
        suggestions.append("Important ideas missing from your answer: " + "; ".join(result.missing_concepts) + ".")
    if result.missing_keywords:
        suggestions.append("Try to include these terms: " + ", ".join(result.missing_keywords) + ".")
    if result.completeness < 50 and result.suggested_marks < result.max_marks:
        suggestions.append("Your answer is quite short; explain your points more fully.")
    for flag in result.flags:
        suggestions.append("Note for review: " + flag)
    if not positives and result.suggested_marks == 0:
        positives.append("No relevant content was found in the answer.")
    summary = (f"Suggested {result.suggested_marks:g} / {result.max_marks:g} marks "
               f"({result.performance_level}). Confidence {result.confidence:.0%}.")
    lines = [summary] + [f"+ {p}" for p in positives] + [f"- {s}" for s in suggestions]
    return {"summary": summary, "positives": positives, "suggestions": suggestions, "text": "\n".join(lines)}
