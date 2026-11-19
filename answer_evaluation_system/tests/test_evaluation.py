import unittest

from tests.base import GOOD, IRRELEVANT, REF
from ml.evaluator import ENGINE_NAME, evaluate_answer, normalize_weights
from ml.feedback import build_feedback
from ml.preprocessing import preprocess, stem

KW = ["machine learning", "artificial intelligence|AI", "data", "patterns", "predictions"]
CON = ["branch of artificial intelligence", "computers learn patterns from data", "make predictions"]


def ev(answer, **kw):
    return evaluate_answer(answer, REF, 5, keywords=KW, concepts=CON, **kw)


class TestPreprocessing(unittest.TestCase):
    def test_pipeline(self):
        p = preprocess("The Computers are LEARNING patterns!  Visit http://x.com now.")
        self.assertNotIn("the", p.content_tokens)
        self.assertNotIn("http", " ".join(p.tokens))
        self.assertEqual(stem("learning"), stem("learns"))
        self.assertEqual(stem("predictions"), stem("predict"))
        self.assertEqual(stem("class"), "class")

    def test_negation_count(self):
        self.assertEqual(preprocess("It doesn't work and is not good").negations, 2)


class TestEvaluator(unittest.TestCase):
    def test_identical_gets_full_marks(self):
        r = ev(REF)
        self.assertEqual(r.suggested_marks, 5.0)
        self.assertGreaterEqual(r.confidence, 0.9)

    def test_paraphrase_scores_between_irrelevant_and_identical(self):
        good, bad = ev(GOOD), ev(IRRELEVANT)
        self.assertGreater(good.suggested_marks, 2.0)
        self.assertLess(good.suggested_marks, 5.0)
        self.assertEqual(bad.suggested_marks, 0.0)
        self.assertTrue(any("irrelevant" in f.lower() for f in bad.flags))

    def test_empty_answer(self):
        for blank in ("", "   ", "the a is of", None):
            r = ev(blank)
            self.assertEqual(r.suggested_marks, 0.0)
            self.assertTrue(r.flags)

    def test_spelling_variations_still_credited(self):
        r = ev("Machine lerning is a brnch of artifical inteligence that lets computrs lern patterns from data and make predictions.")
        self.assertGreaterEqual(r.suggested_marks, 3.5)

    def test_synonym_keyword_matches(self):
        r = ev("AI systems learn patterns from data to make predictions.")
        self.assertIn("artificial intelligence", r.matched_keywords)

    def test_missing_concepts_reported(self):
        r = ev("Machine learning is part of AI.")
        self.assertTrue(r.missing_concepts or r.partial_concepts)
        self.assertLess(r.suggested_marks, 3.0)

    def test_negation_is_flagged_and_capped(self):
        r = ev("Machine Learning is not a branch of artificial intelligence and computers cannot learn patterns from data.")
        self.assertTrue(r.needs_review)
        self.assertLessEqual(r.suggested_marks, 2.5)

    def test_keyword_list_flagged(self):
        r = ev("machine learning artificial intelligence data patterns predictions computers")
        self.assertTrue(r.needs_review)

    def test_marks_bounded_and_half_steps(self):
        for text in (REF, GOOD, IRRELEVANT, "short"):
            r = ev(text)
            self.assertTrue(0 <= r.suggested_marks <= 5)
            self.assertEqual(r.suggested_marks * 2, int(r.suggested_marks * 2))

    def test_deterministic(self):
        self.assertEqual(ev(GOOD).to_dict(), ev(GOOD).to_dict())

    def test_weights_configurable_and_normalised(self):
        w = normalize_weights({"similarity": 2, "keyword": 2, "concept": 0, "completeness": 0})
        self.assertAlmostEqual(sum(w.values()), 1.0)
        kw_only = ev(GOOD, weights={"similarity": 0, "keyword": 1, "concept": 0, "completeness": 0})
        self.assertAlmostEqual(kw_only.breakdown["final_fraction"], kw_only.keyword_coverage / 100, places=2)

    def test_auto_keywords_when_none_given(self):
        r = evaluate_answer(GOOD, REF, 5)
        self.assertTrue(r.keywords_auto and r.concepts_auto)
        self.assertGreater(r.suggested_marks, 0)

    def test_alternate_answer_used(self):
        alt = "ML lets software improve its predictions by finding structure in examples."
        base = evaluate_answer("ML lets software improve predictions by finding structure in examples.", REF, 5)
        with_alt = evaluate_answer("ML lets software improve predictions by finding structure in examples.", REF, 5,
                                   alternate_answers=[alt])
        self.assertGreater(with_alt.similarity, base.similarity)

    def test_engine_labelled_as_baseline(self):
        self.assertIn("baseline", ENGINE_NAME)
        self.assertIn("baseline", __import__("ml.evaluator", fromlist=["x"]).__doc__.lower())

    def test_feedback_structure(self):
        r = ev("Machine learning is part of AI.")
        fb = build_feedback(r)
        self.assertTrue(fb["suggestions"])
        self.assertIn("/ 5", fb["summary"])


if __name__ == "__main__":
    unittest.main()
