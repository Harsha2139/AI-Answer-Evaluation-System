"""Student answer submission, drafts, deadlines and resubmission."""
from datetime import timedelta

from backend.examination_service import get_exam_row, window_state
from backend.user_service import require_role
from config import MAX_ANSWER_LENGTH
from database import repository as repo
from database.connection import transaction
from utils.helpers import (ConflictError, NotFound, PermissionDenied, ValidationError, dumps, fmt_dt, loads, now,
                           now_str, parse_dt)
from utils.validators import clean_text


def can_edit(exam, submission, at=None) -> tuple:
    """(allowed, reason). A teacher-reopened submission may be edited regardless of the deadline."""
    if submission and submission["status"] == "reopened":
        return True, ""
    if submission and submission["status"] == "submitted":
        return False, "You have already submitted this examination."
    state = window_state(exam, at)
    if exam["status"] != "published":
        return False, "This examination is not open for submissions."
    if state == "upcoming":
        return False, f"This examination opens at {exam['exam_date']}."
    if state == "closed":
        return False, "The examination deadline has passed."
    return True, ""


def open_exam_for_student(actor, exam_id, at=None):
    """Return questions WITHOUT reference answers, plus any saved answers."""
    require_role(actor, "student")
    with transaction() as conn:
        exam = get_exam_row(conn, actor, exam_id)
        sub = repo.fetch_one("SELECT * FROM submissions WHERE examination_id=? AND student_id=?", (exam_id, actor["id"]), conn)
        questions = repo.fetch_all("SELECT id, question_number, question_text, maximum_marks FROM questions "
                                   "WHERE examination_id=? ORDER BY question_number", (exam_id,), conn)
        answers = {}
        if sub:
            answers = {r["question_id"]: r["answer_text"] for r in
                       repo.fetch_all("SELECT question_id, answer_text FROM student_answers WHERE submission_id=?", (sub["id"],), conn)}
    allowed, reason = can_edit(exam, sub, at)
    start = parse_dt(exam["exam_date"])
    return {"exam": exam, "questions": questions, "submission": sub, "answers": answers,
            "can_edit": allowed, "reason": reason, "ends_at": fmt_dt(start + timedelta(minutes=exam["duration"]))}


def save_answers(actor, exam_id, answers: dict, submit=False, at=None):
    """Persist answers (draft or final). Returns the submission row."""
    require_role(actor, "student")
    at = at or now()
    with transaction() as conn:
        exam = get_exam_row(conn, actor, exam_id)
        sub = repo.fetch_one("SELECT * FROM submissions WHERE examination_id=? AND student_id=?", (exam_id, actor["id"]), conn)
        allowed, reason = can_edit(exam, sub, at)
        if not allowed:
            raise ConflictError(reason)
        questions = {q["id"]: q for q in repo.fetch_all("SELECT id FROM questions WHERE examination_id=?", (exam_id,), conn)}
        cleaned = {}
        for qid, text in (answers or {}).items():
            qid = int(qid)
            if qid not in questions:
                raise ValidationError("Answer submitted for a question that is not part of this examination.")
            cleaned[qid] = clean_text(text, MAX_ANSWER_LENGTH)
        if submit and not any(cleaned.get(q) or _existing(conn, sub, q) for q in questions):
            raise ValidationError("Please answer at least one question before submitting.")
        stamp = fmt_dt(at)
        if not sub:
            sid = repo.insert("submissions", {"examination_id": exam_id, "student_id": actor["id"], "started_at": stamp,
                                              "updated_at": stamp, "status": "in_progress"}, conn)
        else:
            sid = sub["id"]
        for qid, text in cleaned.items():
            old = repo.fetch_one("SELECT id, answer_text FROM student_answers WHERE submission_id=? AND question_id=?", (sid, qid), conn)
            if old:
                if old["answer_text"] != text:
                    repo.update("student_answers", old["id"], {"answer_text": text, "saved_at": stamp}, conn)
                    _outdate_evaluation(conn, old["id"], actor["id"], stamp)
            else:
                repo.insert("student_answers", {"submission_id": sid, "question_id": qid, "answer_text": text, "saved_at": stamp}, conn)
        if submit:
            for qid in questions:  # every question gets a row so blank answers are evaluated as 0
                conn.execute("INSERT OR IGNORE INTO student_answers(submission_id, question_id, answer_text, saved_at) VALUES (?,?,?,?)",
                             (sid, qid, "", stamp))
            repo.update("submissions", sid, {"status": "submitted", "submitted_at": stamp, "updated_at": stamp}, conn)
        else:
            repo.update("submissions", sid, {"updated_at": stamp}, conn)
        return repo.fetch_one("SELECT * FROM submissions WHERE id=?", (sid,), conn)


def _existing(conn, sub, qid):
    if not sub:
        return ""
    r = repo.fetch_one("SELECT answer_text FROM student_answers WHERE submission_id=? AND question_id=?", (sub["id"], qid), conn)
    return (r or {}).get("answer_text", "")


def _outdate_evaluation(conn, answer_id, actor_id, stamp):
    ev = repo.fetch_one("SELECT id, final_marks, suggested_marks FROM evaluations WHERE student_answer_id=?", (answer_id,), conn)
    if ev:
        repo.update("evaluations", ev["id"], {"approval_status": "outdated", "final_marks": None, "approved_at": None}, conn)
        repo.insert("evaluation_audit", {"evaluation_id": ev["id"], "action": "answer_changed",
                                         "old_marks": ev["final_marks"] if ev["final_marks"] is not None else ev["suggested_marks"],
                                         "new_marks": None, "changed_by": actor_id,
                                         "reason": "Student changed the answer after a resubmission was permitted.",
                                         "changed_at": stamp}, conn)


def reopen_submission(actor, submission_id, reason=""):
    """Teacher permits a student to edit and resubmit."""
    require_role(actor, "teacher")
    with transaction() as conn:
        sub = repo.fetch_one("SELECT * FROM submissions WHERE id=?", (submission_id,), conn)
        if not sub:
            raise NotFound("Submission not found.")
        get_exam_row(conn, actor, sub["examination_id"], write=True)
        if sub["status"] != "submitted":
            raise ConflictError("Only submitted answers can be reopened.")
        repo.update("submissions", submission_id, {"status": "reopened", "updated_at": now_str()}, conn)


def list_exam_submissions(actor, exam_id):
    """One row per enrolled student, including students who have not started."""
    require_role(actor, "teacher", "admin")
    with transaction() as conn:
        exam = get_exam_row(conn, actor, exam_id)
    return repo.fetch_all("""
        SELECT u.id AS student_id, u.name, u.registration_number, sb.id AS submission_id,
               COALESCE(sb.status, 'not_started') AS status, sb.submitted_at,
               (SELECT COUNT(*) FROM questions q WHERE q.examination_id = ?) AS total_questions,
               COALESCE((SELECT COUNT(*) FROM student_answers sa JOIN evaluations ev ON ev.student_answer_id = sa.id
                         WHERE sa.submission_id = sb.id AND ev.approval_status <> 'outdated'), 0) AS evaluated,
               COALESCE((SELECT COUNT(*) FROM student_answers sa JOIN evaluations ev ON ev.student_answer_id = sa.id
                         WHERE sa.submission_id = sb.id AND ev.approval_status = 'approved'), 0) AS approved
        FROM users u JOIN student_enrollments se ON se.student_id = u.id AND se.class_id = ? AND se.subject_id = ?
        LEFT JOIN submissions sb ON sb.student_id = u.id AND sb.examination_id = ?
        WHERE u.role = 'student' ORDER BY u.name""", (exam_id, exam["class_id"], exam["subject_id"], exam_id))


def get_submission_detail(actor, submission_id):
    require_role(actor, "teacher", "admin", "student")
    sub = repo.fetch_one("""SELECT sb.*, u.name AS student_name, u.registration_number FROM submissions sb
                            JOIN users u ON u.id = sb.student_id WHERE sb.id=?""", (submission_id,))
    if not sub:
        raise NotFound("Submission not found.")
    if actor["role"] == "student" and sub["student_id"] != actor["id"]:
        raise PermissionDenied("You can only view your own submissions.")
    with transaction() as conn:
        if actor["role"] != "student":
            get_exam_row(conn, actor, sub["examination_id"])
    sub["answers"] = repo.fetch_all("""SELECT sa.id AS answer_id, q.id AS question_id, q.question_number, q.question_text,
        q.maximum_marks, sa.answer_text, sa.saved_at FROM student_answers sa JOIN questions q ON q.id = sa.question_id
        WHERE sa.submission_id=? ORDER BY q.question_number""", (submission_id,))
    return sub


def list_student_submissions(actor):
    require_role(actor, "student")
    return repo.fetch_all("""SELECT sb.id AS submission_id, e.id AS exam_id, e.title, s.subject_name, sb.status,
        sb.started_at, sb.submitted_at FROM submissions sb JOIN examinations e ON e.id = sb.examination_id
        JOIN subjects s ON s.id = e.subject_id WHERE sb.student_id=? ORDER BY sb.updated_at DESC""", (actor["id"],))
