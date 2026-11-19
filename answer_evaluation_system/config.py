"""Central configuration. Every value can be overridden with an environment variable (see .env.example)."""
import os
from pathlib import Path

try:  # python-dotenv is optional
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


APP_NAME = "AI Answer Evaluation System"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "evaluation.db")))
MODEL_DIR = Path(os.getenv("MODEL_DIR", str(BASE_DIR / "models")))

# --- Security ---------------------------------------------------------------
MIN_PASSWORD_LENGTH = int(os.getenv("MIN_PASSWORD_LENGTH", "8"))
MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "15"))
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "60"))
MAX_ANSWER_LENGTH = int(os.getenv("MAX_ANSWER_LENGTH", "10000"))

# --- Evaluation engine ------------------------------------------------------
# Weights of the four evaluation signals. They are normalised to sum to 1.
DEFAULT_WEIGHTS = {"similarity": 0.35, "keyword": 0.25, "concept": 0.30, "completeness": 0.10}
# Raw TF-IDF cosine similarity at which an answer earns FULL similarity credit.
# Paraphrased correct answers rarely exceed ~0.7 on TF-IDF, so raw values are calibrated.
SIMILARITY_FULL_CREDIT = float(os.getenv("SIMILARITY_FULL_CREDIT", "0.65"))
LOW_CONFIDENCE_THRESHOLD = float(os.getenv("LOW_CONFIDENCE_THRESHOLD", "0.60"))
PLAGIARISM_THRESHOLD = float(os.getenv("PLAGIARISM_THRESHOLD", "85"))  # percent
PASS_PERCENTAGE = float(os.getenv("PASS_PERCENTAGE", "40"))
USE_SENTENCE_TRANSFORMERS = _bool("USE_SENTENCE_TRANSFORMERS", False)
SENTENCE_MODEL_NAME = os.getenv("SENTENCE_MODEL_NAME", "all-MiniLM-L6-v2")
