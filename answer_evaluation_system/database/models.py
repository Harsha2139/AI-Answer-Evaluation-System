"""Schema constants and schema verification (the DDL itself lives in migrations/)."""
ROLES = ("admin", "teacher", "student")
EXAM_STATUSES = ("draft", "published", "closed")
SUBMISSION_STATUSES = ("in_progress", "submitted", "reopened")
APPROVAL_STATUSES = ("pending", "approved", "rejected", "outdated")

TABLES = ("users", "classes", "subjects", "student_enrollments", "teacher_assignments",
          "examinations", "questions", "submissions", "student_answers", "evaluations", "evaluation_audit")


def verify_schema(conn) -> list:
    """Return the names of required tables that are missing (empty list = schema OK)."""
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return [t for t in TABLES if t not in existing]
