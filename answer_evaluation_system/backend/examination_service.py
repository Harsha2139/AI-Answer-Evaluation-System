"""Examinations and questions."""
import re
from datetime import timedelta

from backend.user_service import require_role
from database import repository as repo
from database.connection import transaction
from utils.helpers import (ConflictError, NotFound, PermissionDenied, ValidationError, dumps, fmt_dt, loads, now,
                           parse_dt)
from utils.validators import clean_text, parse_list, require_text, validate_positive_number

WEIGHT_KEYS = ("similarity", "keyword", "concept", "completeness")


def window_state(exam, at=None) -> str:
    """upcoming / open / closed for a published exam (closed status always wins)."""
    at = at or now()
    if exam["status"] == "closed":
        return "closed"
    start = parse_dt(exam["exam_date"])
    end = start + timedelta(minutes=int(exam["duration"]))
    return "upcoming" if at < start else "open" if at <= end else "closed"


def _clean_weights(weights):
    if weights is None:
        return None
    out = {}
    for k in WEIGHT_KEYS:
        try:
            out[k] = float(weights.get(k, 0))
        except (TypeError, ValueError):
            raise ValidationError("Scoring weights must be numbers.")
        if out[k] < 0:
            raise ValidationError("Scoring weights cannot be negative.")
    if sum(out.values()) <= 0:
        raise ValidationError("At least one scoring weight must be greater than zero.")
    return out


def _clean_question(q, number):
    text = require_text(q.get("question_text"), f"Question {number} text", 3000)
    ref = require_text(q.get("reference_answer"), f"Question {number} reference answer", 6000)
    marks = validate_positive_number(q.get("maximum_marks"), f"Question {number} maximum marks")
    return {"question_text": text, "reference_answer": ref, "maximum_marks": marks,
            "expected_keywords": dumps(parse_list(q.get("expected_keywords"))),
            "expected_concepts": dumps(parse_list(q.get("expected_concepts"))),
            "alternate_answers": dumps(_clean_alternates(q.get("alternate_answers")))}


def _clean_alternates(value):
    """Accepts a list, or text where alternate answers are separated by a line containing only '---'."""
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r"\n\s*---+\s*\n", str(value or ""))
    return [clean_text(a, 6000) for a in items if str(a).strip()]


def get_exam_row(conn, actor, exam_id, write=False):
    """Fetch an exam and enforce ownership / enrolment rules."""
    exam = repo.fetch_one("SELECT * FROM examinations WHERE id = ?", (exam_id,), conn)
    if not exam:
        raise NotFound("Examination not found.")
    role = actor["role"]
    if role == "admin":
        return exam
    if role == "teacher":
        if exam["teacher_id"] != actor["id"]:
            raise PermissionDenied("This examination belongs to another teacher.")
        return exam
    if write or exam["status"] == "draft":
        raise PermissionDenied("You do not have access to this examination.")
    if not repo.fetch_one("SELECT id FROM student_enrollments WHERE student_id=? AND class_id=? AND subject_id=?",
                          (actor["id"], exam["class_id"], exam["subject_id"]), conn):
        raise PermissionDenied("This examination is not assigned to you.")
    return exam


def _recalc_total(conn, exam_id):
    conn.execute("UPDATE examinations SET total_marks = COALESCE((SELECT SUM(maximum_marks) FROM questions "
                 "WHERE examination_id=?),0) WHERE id=?", (exam_id, exam_id))


def create_exam(actor, title, class_id, subject_id, exam_date, duration, questions, weights=None,
                total_marks=None, publish=False):
    require_role(actor, "teacher")
    title = require_text(title, "Examination title", 200)
    duration = int(validate_positive_number(duration, "Duration (minutes)", integer=True))
    start = parse_dt(exam_date)
    if not questions:
        raise ValidationError("Add at least one question.")
    cleaned = [_clean_question(q, i) for i, q in enumerate(questions, start=1)]
    computed = sum(q["maximum_marks"] for q in cleaned)
    if total_marks is not None and abs(float(total_marks) - computed) > 1e-9:
        raise ValidationError(f"Total marks ({total_marks}) must equal the sum of question marks ({computed:g}).")
    weights = _clean_weights(weights)
    with transaction() as conn:
        if not repo.fetch_one("SELECT id FROM teacher_assignments WHERE teacher_id=? AND class_id=? AND subject_id=?",
                              (actor["id"], class_id, subject_id), conn):
            raise PermissionDenied("You are not assigned to this class and subject.")
        exam_id = repo.insert("examinations", {
            "title": title, "class_id": class_id, "subject_id": subject_id, "teacher_id": actor["id"],
            "exam_date": fmt_dt(start), "duration": duration, "total_marks": computed,
            "status": "published" if publish else "draft", "scoring_weights": dumps(weights) if weights else None,
            "created_at": fmt_dt(now())}, conn)
        for i, q in enumerate(cleaned, start=1):
            repo.insert("questions", {"examination_id": exam_id, "question_number": i, **q}, conn)
        return exam_id


def _has_submissions(conn, exam_id):
    return repo.fetch_one("SELECT id FROM submissions WHERE examination_id=? LIMIT 1", (exam_id,), conn) is not None


def update_exam(actor, exam_id, title=None, exam_date=None, duration=None, weights=None, clear_weights=False):
    require_role(actor, "teacher", "admin")
    data = {}
    if title is not None:
        data["title"] = require_text(title, "Examination title", 200)
    if exam_date is not None:
        data["exam_date"] = fmt_dt(parse_dt(exam_date))
    if duration is not None:
        data["duration"] = int(validate_positive_number(duration, "Duration (minutes)", integer=True))
    if weights is not None:
        data["scoring_weights"] = dumps(_clean_weights(weights))
    if clear_weights:
        data["scoring_weights"] = None
    with transaction() as conn:
        get_exam_row(conn, actor, exam_id, write=True)
        repo.update("examinations", exam_id, data, conn)


def set_status(actor, exam_id, status):
    require_role(actor, "teacher", "admin")
    if status not in ("draft", "published", "closed"):
        raise ValidationError("Invalid status.")
    with transaction() as conn:
        get_exam_row(conn, actor, exam_id, write=True)
        if status == "draft" and _has_submissions(conn, exam_id):
            raise ConflictError("An examination with submissions cannot return to draft.")
        if status == "published" and not repo.fetch_one("SELECT id FROM questions WHERE examination_id=? LIMIT 1", (exam_id,), conn):
            raise ValidationError("Add at least one question before publishing.")
        repo.update("examinations", exam_id, {"status": status}, conn)


def delete_exam(actor, exam_id):
    require_role(actor, "teacher", "admin")
    with transaction() as conn:
        get_exam_row(conn, actor, exam_id, write=True)
        if _has_submissions(conn, exam_id):
            raise ConflictError("Examinations with student submissions cannot be deleted (close it instead).")
        repo.delete("examinations", exam_id, conn)


def add_question(actor, exam_id, question):
    require_role(actor, "teacher")
    with transaction() as conn:
        get_exam_row(conn, actor, exam_id, write=True)
        if _has_submissions(conn, exam_id):
            raise ConflictError("Questions cannot be changed after students have started this examination.")
        number = repo.fetch_one("SELECT COALESCE(MAX(question_number),0)+1 AS n FROM questions WHERE examination_id=?",
                                (exam_id,), conn)["n"]
        qid = repo.insert("questions", {"examination_id": exam_id, "question_number": number, **_clean_question(question, number)}, conn)
        _recalc_total(conn, exam_id)
        return qid


def update_question(actor, question_id, question):
    require_role(actor, "teacher")
    with transaction() as conn:
        q = repo.fetch_one("SELECT * FROM questions WHERE id=?", (question_id,), conn)
        if not q:
            raise NotFound("Question not found.")
        get_exam_row(conn, actor, q["examination_id"], write=True)
        if _has_submissions(conn, q["examination_id"]):
            raise ConflictError("Questions cannot be changed after students have started this examination.")
        repo.update("questions", question_id, _clean_question(question, q["question_number"]), conn)
        _recalc_total(conn, q["examination_id"])


def delete_question(actor, question_id):
    require_role(actor, "teacher")
    with transaction() as conn:
        q = repo.fetch_one("SELECT * FROM questions WHERE id=?", (question_id,), conn)
        if not q:
            raise NotFound("Question not found.")
        get_exam_row(conn, actor, q["examination_id"], write=True)
        if _has_submissions(conn, q["examination_id"]):
            raise ConflictError("Questions cannot be changed after students have started this examination.")
        repo.delete("questions", question_id, conn)
        for i, r in enumerate(repo.fetch_all("SELECT id FROM questions WHERE examination_id=? ORDER BY question_number",
                                             (q["examination_id"],), conn), start=1):
            repo.update("questions", r["id"], {"question_number": 1000 + i}, conn)
        for i, r in enumerate(repo.fetch_all("SELECT id FROM questions WHERE examination_id=? ORDER BY question_number",
                                             (q["examination_id"],), conn), start=1):
            repo.update("questions", r["id"], {"question_number": i}, conn)
        _recalc_total(conn, q["examination_id"])


def get_exam_with_questions(actor, exam_id):
    """Teacher/admin view including reference answers."""
    require_role(actor, "teacher", "admin")
    with transaction() as conn:
        exam = get_exam_row(conn, actor, exam_id)
        qs = repo.fetch_all("SELECT * FROM questions WHERE examination_id=? ORDER BY question_number", (exam_id,), conn)
    for q in qs:
        q["expected_keywords"] = loads(q["expected_keywords"], [])
        q["expected_concepts"] = loads(q["expected_concepts"], [])
        q["alternate_answers"] = loads(q["alternate_answers"], [])
    exam["scoring_weights"] = loads(exam["scoring_weights"])
    exam["questions"] = qs
    exam["has_submissions"] = repo.fetch_one("SELECT id FROM submissions WHERE examination_id=? LIMIT 1", (exam_id,)) is not None
    return exam


_EXAM_LIST_SQL = """SELECT e.id, e.title, e.class_id, e.subject_id, e.teacher_id, t.name AS teacher,
    TRIM(c.class_name||' '||c.section) AS class_label, s.subject_name, s.subject_code, e.exam_date, e.duration,
    e.total_marks, e.status,
    (SELECT COUNT(*) FROM questions q WHERE q.examination_id=e.id) AS question_count,
    (SELECT COUNT(*) FROM submissions sb WHERE sb.examination_id=e.id AND sb.status='submitted') AS submitted_count
    FROM examinations e JOIN classes c ON c.id=e.class_id JOIN subjects s ON s.id=e.subject_id
    JOIN users t ON t.id=e.teacher_id"""


def list_exams(actor):
    require_role(actor, "teacher", "admin")
    if actor["role"] == "teacher":
        return repo.fetch_all(_EXAM_LIST_SQL + " WHERE e.teacher_id=? ORDER BY e.exam_date DESC", (actor["id"],))
    return repo.fetch_all(_EXAM_LIST_SQL + " ORDER BY e.exam_date DESC")


def list_exams_for_student(actor, at=None):
    """Published exams for the student's own class + subject enrolments, with their personal status."""
    require_role(actor, "student")
    rows = repo.fetch_all(_EXAM_LIST_SQL + """ JOIN student_enrollments se ON se.class_id=e.class_id
        AND se.subject_id=e.subject_id AND se.student_id=? WHERE e.status IN ('published','closed')
        ORDER BY e.exam_date DESC""", (actor["id"],))
    for r in rows:
        sub = repo.fetch_one("SELECT status, submitted_at FROM submissions WHERE examination_id=? AND student_id=?",
                             (r["id"], actor["id"]))
        r["my_status"] = sub["status"] if sub else "not_started"
        r["submitted_at"] = sub["submitted_at"] if sub else None
        r["window"] = window_state(r, at)
    return rows
