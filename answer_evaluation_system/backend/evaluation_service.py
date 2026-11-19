"""Runs the NLP engine on submitted answers and manages teacher review / approval.

The AI NEVER publishes marks: students only see rows whose approval_status = 'approved'.
"""
import numpy as np

import config
from backend.examination_service import get_exam_row
from backend.user_service import require_role
from database import repository as repo
from database.connection import transaction
from ml import model_loader
from ml.evaluator import ENGINE_NAME, evaluate_answer
from ml.feedback import build_feedback
from ml.preprocessing import preprocess
from ml.similarity import pairwise_similarity
from utils.helpers import ConflictError, NotFound, ValidationError, dumps, loads, now_str, round_half

_ANSWER_SQL = """SELECT sa.id AS answer_id, sa.answer_text, sa.submission_id, q.id AS question_id, q.question_number,
    q.question_text, q.reference_answer, q.maximum_marks, q.expected_keywords, q.expected_concepts, q.alternate_answers,
    sb.student_id, sb.examination_id, ev.id AS evaluation_id, ev.approval_status
    FROM student_answers sa JOIN questions q ON q.id = sa.question_id JOIN submissions sb ON sb.id = sa.submission_id
    LEFT JOIN evaluations ev ON ev.student_answer_id = sa.id"""


def _score_row(row, weights, vectorizer):
    result = evaluate_answer(
        row["answer_text"], row["reference_answer"], row["maximum_marks"],
        keywords=loads(row["expected_keywords"], []), concepts=loads(row["expected_concepts"], []),
        alternate_answers=loads(row["alternate_answers"], []), weights=weights, vectorizer=vectorizer)
    return result, build_feedback(result)


def _vectorizer_for(conn, exam_id):
    refs = [r["reference_answer"] for r in repo.fetch_all(
        "SELECT reference_answer FROM questions WHERE examination_id=?", (exam_id,), conn)]
    return model_loader.get_corpus_vectorizer(exam_id, refs)


def _write_evaluation(conn, row, result, feedback, actor_id, reevaluation=False):
    stamp = now_str()
    data = {
        "similarity_score": result.similarity, "keyword_coverage": result.keyword_coverage,
        "concept_coverage": result.concept_coverage, "completeness_score": result.completeness,
        "confidence": result.confidence, "needs_review": 1 if result.needs_review else 0,
        "suggested_marks": result.suggested_marks, "final_marks": None, "ai_feedback": feedback["text"],
        "approval_status": "pending", "approved_by": None, "approved_at": None, "evaluated_by": actor_id,
        "evaluated_at": stamp, "engine": ENGINE_NAME, "details_json": dumps(result.to_dict())}
    existing = repo.fetch_one("SELECT id, final_marks, suggested_marks FROM evaluations WHERE student_answer_id=?",
                              (row["answer_id"],), conn)
    if existing:
        repo.update("evaluations", existing["id"], data, conn)
        old = existing["final_marks"] if existing["final_marks"] is not None else existing["suggested_marks"]
        repo.insert("evaluation_audit", {"evaluation_id": existing["id"], "action": "re-evaluated", "old_marks": old,
                                         "new_marks": result.suggested_marks, "changed_by": actor_id,
                                         "reason": "AI evaluation re-run", "changed_at": stamp}, conn)
        return existing["id"]
    eid = repo.insert("evaluations", {"student_answer_id": row["answer_id"], **data}, conn)
    repo.insert("evaluation_audit", {"evaluation_id": eid, "action": "ai_suggested", "old_marks": None,
                                     "new_marks": result.suggested_marks, "changed_by": actor_id,
                                     "reason": "Initial AI suggestion", "changed_at": stamp}, conn)
    return eid


def _update_plagiarism(conn, exam_id):
    """Store each answer's highest similarity to another student's answer to the same question."""
    flagged = 0
    for q in repo.fetch_all("SELECT id FROM questions WHERE examination_id=?", (exam_id,), conn):
        rows = repo.fetch_all("""SELECT sa.id AS answer_id, sa.answer_text, ev.id AS evaluation_id FROM student_answers sa
            JOIN submissions sb ON sb.id = sa.submission_id AND sb.status = 'submitted'
            JOIN evaluations ev ON ev.student_answer_id = sa.id WHERE sa.question_id = ?""", (q["id"],), conn)
        eligible = [r for r in rows if len(preprocess(r["answer_text"]).content_tokens) >= 8]
        scores = {r["evaluation_id"]: 0.0 for r in rows}
        if len(eligible) >= 2:
            sims = pairwise_similarity([preprocess(r["answer_text"]).stem_text for r in eligible])
            np.fill_diagonal(sims, 0.0)
            for i, r in enumerate(eligible):
                scores[r["evaluation_id"]] = round(float(sims[i].max()) * 100, 1)
        for eid, score in scores.items():
            conn.execute("UPDATE evaluations SET plagiarism_score=? WHERE id=?", (score, eid))
            if score >= config.PLAGIARISM_THRESHOLD:
                conn.execute("UPDATE evaluations SET needs_review=1 WHERE id=?", (eid,))
                flagged += 1
    return flagged


def evaluate_exam(actor, exam_id, submission_ids=None, only_new=True):
    """Bulk evaluation of every submitted answer (skips already-evaluated answers when only_new)."""
    require_role(actor, "teacher")
    with transaction() as conn:
        exam = get_exam_row(conn, actor, exam_id, write=True)
        weights = loads(exam["scoring_weights"])
        vectorizer = _vectorizer_for(conn, exam_id)
        sql = _ANSWER_SQL + " WHERE sb.examination_id=? AND sb.status='submitted'"
        params = [exam_id]
        if submission_ids:
            sql += f" AND sb.id IN ({','.join('?' * len(submission_ids))})"
            params += list(submission_ids)
        rows = repo.fetch_all(sql, params, conn)
        done = skipped = 0
        for row in rows:
            if only_new and row["evaluation_id"] and row["approval_status"] != "outdated":
                skipped += 1
                continue
            result, fb = _score_row(row, weights, vectorizer)
            _write_evaluation(conn, row, result, fb, actor["id"])
            done += 1
        flagged = _update_plagiarism(conn, exam_id)
    return {"evaluated": done, "skipped": skipped, "plagiarism_flagged": flagged}


def reevaluate_answer(actor, answer_id):
    require_role(actor, "teacher")
    with transaction() as conn:
        row = repo.fetch_one(_ANSWER_SQL + " WHERE sa.id=?", (answer_id,), conn)
        if not row:
            raise NotFound("Answer not found.")
        exam = get_exam_row(conn, actor, row["examination_id"], write=True)
        result, fb = _score_row(row, loads(exam["scoring_weights"]), _vectorizer_for(conn, exam["id"]))
        eid = _write_evaluation(conn, row, result, fb, actor["id"], reevaluation=True)
        _update_plagiarism(conn, exam["id"])
    return eid


_EVAL_LIST_SQL = """SELECT ev.id AS evaluation_id, sa.id AS answer_id, sa.answer_text, sb.id AS submission_id, u.id AS student_id,
    u.name AS student_name, u.registration_number, q.question_number, q.question_text, q.reference_answer,
    q.maximum_marks, ev.similarity_score, ev.keyword_coverage, ev.concept_coverage, ev.completeness_score,
    ev.confidence, ev.needs_review, ev.plagiarism_score, ev.suggested_marks, ev.final_marks, ev.ai_feedback,
    ev.teacher_feedback, ev.approval_status, ev.approved_at, ev.evaluated_at, ev.details_json
    FROM evaluations ev JOIN student_answers sa ON sa.id = ev.student_answer_id
    JOIN submissions sb ON sb.id = sa.submission_id JOIN users u ON u.id = sb.student_id
    JOIN questions q ON q.id = sa.question_id"""


def list_evaluations(actor, exam_id, status=None, student_id=None, only_flagged=False):
    require_role(actor, "teacher", "admin")
    with transaction() as conn:
        get_exam_row(conn, actor, exam_id)
    sql, params = _EVAL_LIST_SQL + " WHERE q.examination_id=?", [exam_id]
    if status:
        sql += " AND ev.approval_status=?"; params.append(status)
    if student_id:
        sql += " AND u.id=?"; params.append(student_id)
    if only_flagged:
        sql += " AND ev.needs_review=1"
    rows = repo.fetch_all(sql + " ORDER BY u.name, q.question_number", params)
    for r in rows:
        r["details"] = loads(r.pop("details_json"), {})
    return rows


def review_evaluation(actor, evaluation_id, action, marks=None, teacher_feedback=None, reason=""):
    """Teacher decision: 'approve' (accept AI marks), 'edit' (set own marks) or 'reject' (withhold marks)."""
    require_role(actor, "teacher")
    if action not in ("approve", "edit", "reject"):
        raise ValidationError("Unknown review action.")
    with transaction() as conn:
        ev = repo.fetch_one("""SELECT ev.*, q.maximum_marks, q.examination_id FROM evaluations ev
            JOIN student_answers sa ON sa.id = ev.student_answer_id JOIN questions q ON q.id = sa.question_id
            WHERE ev.id=?""", (evaluation_id,), conn)
        if not ev:
            raise NotFound("Evaluation not found.")
        get_exam_row(conn, actor, ev["examination_id"], write=True)
        if ev["approval_status"] == "outdated":
            raise ConflictError("The student changed this answer. Re-evaluate it before approving.")
        old = ev["final_marks"] if ev["final_marks"] is not None else ev["suggested_marks"]
        data, stamp = {}, now_str()
        if action == "approve":
            new, data = ev["suggested_marks"], {"final_marks": ev["suggested_marks"], "approval_status": "approved"}
        elif action == "edit":
            try:
                new = round_half(float(marks))
            except (TypeError, ValueError):
                raise ValidationError("Marks must be a number.")
            if new < 0 or new > ev["maximum_marks"]:
                raise ValidationError(f"Marks must be between 0 and {ev['maximum_marks']:g}.")
            if new != ev["suggested_marks"] and not (reason or "").strip() and not (teacher_feedback or "").strip():
                raise ValidationError("Please give a reason or feedback when changing the AI marks.")
            data = {"final_marks": new, "approval_status": "approved"}
        else:
            new, data = None, {"final_marks": None, "approval_status": "rejected"}
        data.update(approved_by=actor["id"], approved_at=stamp)
        if teacher_feedback is not None:
            data["teacher_feedback"] = (teacher_feedback or "").strip()[:2000]
        repo.update("evaluations", evaluation_id, data, conn)
        repo.insert("evaluation_audit", {"evaluation_id": evaluation_id,
                                         "action": {"approve": "approved", "edit": "edited", "reject": "rejected"}[action],
                                         "old_marks": old, "new_marks": new, "changed_by": actor["id"],
                                         "reason": (reason or "").strip()[:500], "changed_at": stamp}, conn)


def bulk_approve(actor, exam_id, include_flagged=False):
    """Approve all pending AI suggestions (by default skipping low-confidence / flagged ones)."""
    require_role(actor, "teacher")
    count, stamp = 0, now_str()
    with transaction() as conn:
        get_exam_row(conn, actor, exam_id, write=True)
        sql = _EVAL_LIST_SQL + " WHERE q.examination_id=? AND ev.approval_status='pending'"
        if not include_flagged:
            sql += " AND ev.needs_review=0"
        for r in repo.fetch_all(sql, (exam_id,), conn):
            repo.update("evaluations", r["evaluation_id"], {"final_marks": r["suggested_marks"], "approval_status": "approved",
                                                            "approved_by": actor["id"], "approved_at": stamp}, conn)
            repo.insert("evaluation_audit", {"evaluation_id": r["evaluation_id"], "action": "approved",
                                             "old_marks": r["suggested_marks"], "new_marks": r["suggested_marks"],
                                             "changed_by": actor["id"], "reason": "Bulk approval", "changed_at": stamp}, conn)
            count += 1
    return count


def get_audit_history(actor, evaluation_id):
    require_role(actor, "teacher", "admin")
    ev = repo.fetch_one("SELECT q.examination_id FROM evaluations ev JOIN student_answers sa ON sa.id=ev.student_answer_id "
                        "JOIN questions q ON q.id=sa.question_id WHERE ev.id=?", (evaluation_id,))
    if not ev:
        raise NotFound("Evaluation not found.")
    with transaction() as conn:
        get_exam_row(conn, actor, ev["examination_id"])
    return repo.fetch_all("""SELECT a.changed_at, a.action, a.old_marks, a.new_marks, COALESCE(u.name,'system') AS changed_by,
        a.reason FROM evaluation_audit a LEFT JOIN users u ON u.id=a.changed_by WHERE a.evaluation_id=? ORDER BY a.id""",
                          (evaluation_id,))


def get_plagiarism_pairs(actor, exam_id, threshold=None):
    """Pairs of students whose answers to the same question are suspiciously similar."""
    require_role(actor, "teacher", "admin")
    threshold = (threshold if threshold is not None else config.PLAGIARISM_THRESHOLD) / 100.0
    with transaction() as conn:
        get_exam_row(conn, actor, exam_id)
    pairs = []
    for q in repo.fetch_all("SELECT id, question_number FROM questions WHERE examination_id=?", (exam_id,)):
        rows = repo.fetch_all("""SELECT u.name, u.registration_number, sa.answer_text FROM student_answers sa
            JOIN submissions sb ON sb.id=sa.submission_id AND sb.status='submitted' JOIN users u ON u.id=sb.student_id
            WHERE sa.question_id=?""", (q["id"],))
        rows = [r for r in rows if len(preprocess(r["answer_text"]).content_tokens) >= 8]
        if len(rows) < 2:
            continue
        sims = pairwise_similarity([preprocess(r["answer_text"]).stem_text for r in rows])
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if sims[i, j] >= threshold:
                    pairs.append({"question": q["question_number"], "student_a": rows[i]["name"],
                                  "student_b": rows[j]["name"], "similarity_%": round(float(sims[i, j]) * 100, 1)})
    return sorted(pairs, key=lambda p: -p["similarity_%"])


# ------------------------------------------------------------------ student-facing results
def get_student_results(actor, exam_id=None):
    """Approved evaluations only. Marks, AI feedback and breakdown are hidden until the teacher approves."""
    require_role(actor, "student")
    sql = """SELECT e.id AS exam_id, e.title, s.subject_name, e.total_marks, q.question_number, q.question_text,
        q.maximum_marks, sa.answer_text, ev.final_marks, ev.ai_feedback, ev.teacher_feedback, ev.approved_at, ev.details_json
        FROM submissions sb JOIN examinations e ON e.id = sb.examination_id JOIN subjects s ON s.id = e.subject_id
        JOIN student_answers sa ON sa.submission_id = sb.id JOIN questions q ON q.id = sa.question_id
        JOIN evaluations ev ON ev.student_answer_id = sa.id AND ev.approval_status = 'approved'
        WHERE sb.student_id = ? AND sb.status = 'submitted'"""
    params = [actor["id"]]
    if exam_id:
        sql += " AND e.id = ?"; params.append(exam_id)
    rows = repo.fetch_all(sql + " ORDER BY e.exam_date DESC, q.question_number", params)
    for r in rows:
        d = loads(r.pop("details_json"), {})
        r["missing_concepts"] = d.get("missing_concepts", [])
        r["missing_keywords"] = d.get("missing_keywords", [])
        r["breakdown"] = {"Similarity": d.get("similarity"), "Keyword coverage": d.get("keyword_coverage"),
                          "Concept coverage": d.get("concept_coverage"), "Completeness": d.get("completeness")}
    return rows
