"""Dashboards, analytics and Excel/CSV report generation (all figures come from the database)."""
from collections import Counter

import pandas as pd

import config
from backend.examination_service import get_exam_row
from backend.user_service import require_role
from database import repository as repo
from database.connection import transaction
from database.models import TABLES
from utils.excel_export import workbook_bytes
from utils.helpers import PermissionDenied, loads, percent

_TOTALS_SQL = """SELECT sb.id AS submission_id, e.id AS exam_id, e.title AS exam, e.exam_date, e.total_marks, s.subject_name AS subject,
    u.id AS student_id, u.name AS student, u.registration_number AS reg_no,
    COALESCE(SUM(CASE WHEN ev.approval_status='approved' THEN ev.final_marks END), 0) AS total,
    SUM(CASE WHEN ev.approval_status='approved' THEN 1 ELSE 0 END) AS approved_n,
    (SELECT COUNT(*) FROM questions q WHERE q.examination_id=e.id) AS question_n
    FROM submissions sb JOIN examinations e ON e.id=sb.examination_id JOIN subjects s ON s.id=e.subject_id
    JOIN users u ON u.id=sb.student_id JOIN student_answers sa ON sa.submission_id=sb.id
    LEFT JOIN evaluations ev ON ev.student_answer_id=sa.id WHERE sb.status='submitted'"""


def _totals(exam_ids=None, student_id=None, teacher_id=None):
    sql, params = _TOTALS_SQL, []
    if exam_ids:
        sql += f" AND e.id IN ({','.join('?' * len(exam_ids))})"; params += list(exam_ids)
    if student_id:
        sql += " AND u.id=?"; params.append(student_id)
    if teacher_id:
        sql += " AND e.teacher_id=?"; params.append(teacher_id)
    rows = repo.fetch_all(sql + " GROUP BY sb.id ORDER BY e.exam_date, u.name", params)
    for r in rows:
        r["complete"] = r["approved_n"] == r["question_n"] and r["question_n"] > 0
        r["percentage"] = percent(r["total"], r["total_marks"])
        r["result"] = ("Pass" if r["percentage"] >= config.PASS_PERCENTAGE else "Fail") if r["complete"] else "Pending approval"
    return rows


def _avg_pct(rows):
    done = [r["percentage"] for r in rows if r["complete"]]
    return round(sum(done) / len(done), 2) if done else 0.0


# ---------------------------------------------------------------- dashboards
def teacher_dashboard(actor):
    require_role(actor, "teacher")
    tid = actor["id"]
    one = lambda sql: repo.fetch_one(sql, (tid,))["n"]
    return {
        "total_students": one("""SELECT COUNT(DISTINCT se.student_id) AS n FROM student_enrollments se JOIN teacher_assignments ta
            ON ta.class_id=se.class_id AND ta.subject_id=se.subject_id WHERE ta.teacher_id=?"""),
        "total_exams": one("SELECT COUNT(*) AS n FROM examinations WHERE teacher_id=?"),
        "submitted_answers": one("""SELECT COUNT(*) AS n FROM student_answers sa JOIN submissions sb ON sb.id=sa.submission_id
            JOIN examinations e ON e.id=sb.examination_id WHERE sb.status='submitted' AND e.teacher_id=?"""),
        "evaluations_done": one("""SELECT COUNT(*) AS n FROM evaluations ev JOIN student_answers sa ON sa.id=ev.student_answer_id
            JOIN submissions sb ON sb.id=sa.submission_id JOIN examinations e ON e.id=sb.examination_id
            WHERE e.teacher_id=? AND ev.approval_status<>'outdated'"""),
        "pending_approvals": one("""SELECT COUNT(*) AS n FROM evaluations ev JOIN student_answers sa ON sa.id=ev.student_answer_id
            JOIN submissions sb ON sb.id=sa.submission_id JOIN examinations e ON e.id=sb.examination_id
            WHERE e.teacher_id=? AND ev.approval_status='pending'"""),
        "average_marks_pct": _avg_pct(_totals(teacher_id=tid)),
    }


def student_dashboard(actor):
    require_role(actor, "student")
    exams = repo.fetch_all("""SELECT e.id, e.status, sb.status AS sub_status FROM examinations e
        JOIN student_enrollments se ON se.class_id=e.class_id AND se.subject_id=e.subject_id AND se.student_id=?
        LEFT JOIN submissions sb ON sb.examination_id=e.id AND sb.student_id=? WHERE e.status IN ('published','closed')""",
                           (actor["id"], actor["id"]))
    totals = _totals(student_id=actor["id"])
    done = [t for t in totals if t["complete"]]
    return {"total_exams": len(exams),
            "completed": sum(1 for e in exams if e["sub_status"] == "submitted"),
            "pending": sum(1 for e in exams if e["sub_status"] != "submitted" and e["status"] == "published"),
            "average_pct": _avg_pct(totals), "results_published": len(done), "totals": totals}


def admin_dashboard(actor):
    require_role(actor, "admin")
    c = lambda sql: repo.fetch_one(sql)["n"]
    return {"teachers": c("SELECT COUNT(*) n FROM users WHERE role='teacher'"),
            "students": c("SELECT COUNT(*) n FROM users WHERE role='student'"),
            "pending_teachers": c("SELECT COUNT(*) n FROM users WHERE role='teacher' AND is_active=0"),
            "classes": c("SELECT COUNT(*) n FROM classes"), "subjects": c("SELECT COUNT(*) n FROM subjects"),
            "exams": c("SELECT COUNT(*) n FROM examinations"),
            "submissions": c("SELECT COUNT(*) n FROM submissions WHERE status='submitted'"),
            "evaluations": c("SELECT COUNT(*) n FROM evaluations"),
            "approved": c("SELECT COUNT(*) n FROM evaluations WHERE approval_status='approved'"),
            "pending": c("SELECT COUNT(*) n FROM evaluations WHERE approval_status='pending'")}


def database_table_counts(actor):
    require_role(actor, "admin")
    return pd.DataFrame([{"Table": t, "Rows": repo.fetch_one(f"SELECT COUNT(*) n FROM {t}")["n"]} for t in TABLES])


def read_table(actor, table, limit=500):
    """Read-only table browser for admins (password hashes are never selected)."""
    require_role(actor, "admin")
    if table not in TABLES:
        raise PermissionDenied("Unknown table.")
    cols = [c["name"] for c in repo.fetch_all(f"PRAGMA table_info({table})") if c["name"] not in ("password_hash",)]
    return pd.DataFrame(repo.fetch_all(f"SELECT {', '.join(cols)} FROM {table} ORDER BY id DESC LIMIT ?", (int(limit),)))


# ---------------------------------------------------------------- analytics frames
def _authorized_exam_ids(actor, exam_ids):
    with transaction() as conn:
        for eid in exam_ids:
            get_exam_row(conn, actor, eid)
    return list(exam_ids)


def student_results_df(actor, exam_ids):
    require_role(actor, "teacher", "admin")
    exam_ids = _authorized_exam_ids(actor, exam_ids)
    totals = {(t["submission_id"]): t for t in _totals(exam_ids)}
    rows = repo.fetch_all(f"""SELECT sb.id AS submission_id, u.name AS student, u.registration_number AS reg_no, e.title AS exam,
        s.subject_name AS subject, q.question_number, q.maximum_marks, ev.suggested_marks, ev.final_marks, ev.approval_status,
        ev.ai_feedback, ev.teacher_feedback FROM submissions sb JOIN examinations e ON e.id=sb.examination_id
        JOIN subjects s ON s.id=e.subject_id JOIN users u ON u.id=sb.student_id JOIN student_answers sa ON sa.submission_id=sb.id
        JOIN questions q ON q.id=sa.question_id LEFT JOIN evaluations ev ON ev.student_answer_id=sa.id
        WHERE sb.status='submitted' AND e.id IN ({','.join('?' * len(exam_ids))}) ORDER BY e.exam_date, u.name, q.question_number""",
                          exam_ids) if exam_ids else []
    out = []
    for r in rows:
        t = totals[r["submission_id"]]
        feedback = " | ".join(x for x in (f"Teacher: {r['teacher_feedback']}" if r["teacher_feedback"] else "",
                                          (r["ai_feedback"] or "").splitlines()[0] if r["ai_feedback"] else "") if x)
        out.append({"Student Name": r["student"], "Registration Number": r["reg_no"], "Examination": r["exam"],
                    "Subject": r["subject"], "Question No": r["question_number"], "Maximum Marks": r["maximum_marks"],
                    "AI Suggested Marks": r["suggested_marks"],
                    "Teacher Approved Marks": r["final_marks"] if r["approval_status"] == "approved" else None,
                    "Approval Status": r["approval_status"] or "not evaluated", "Feedback": feedback,
                    "Total Marks (approved)": t["total"], "Exam Total Marks": t["total_marks"],
                    "Percentage": t["percentage"], "Result": t["result"]})
    return pd.DataFrame(out, columns=["Student Name", "Registration Number", "Examination", "Subject", "Question No",
                                      "Maximum Marks", "AI Suggested Marks", "Teacher Approved Marks", "Approval Status",
                                      "Feedback", "Total Marks (approved)", "Exam Total Marks", "Percentage", "Result"])


def question_analysis_df(actor, exam_ids):
    require_role(actor, "teacher", "admin")
    exam_ids = _authorized_exam_ids(actor, exam_ids)
    out = []
    for eid in exam_ids:
        for q in repo.fetch_all("SELECT q.id, q.question_number, q.question_text, q.maximum_marks, e.title FROM questions q "
                                "JOIN examinations e ON e.id=q.examination_id WHERE q.examination_id=? ORDER BY q.question_number", (eid,)):
            evs = repo.fetch_all("""SELECT ev.suggested_marks, ev.final_marks, ev.approval_status, ev.details_json FROM student_answers sa
                JOIN submissions sb ON sb.id=sa.submission_id AND sb.status='submitted'
                LEFT JOIN evaluations ev ON ev.student_answer_id=sa.id WHERE sa.question_id=?""", (q["id"],))
            evaluated = [e for e in evs if e["suggested_marks"] is not None and e["approval_status"] != "outdated"]
            approved = [e for e in evs if e["approval_status"] == "approved"]
            missed = Counter(k for e in evaluated for k in loads(e["details_json"], {}).get("missing_keywords", []))
            avg_ai = sum(e["suggested_marks"] for e in evaluated) / len(evaluated) if evaluated else None
            avg_ap = sum(e["final_marks"] for e in approved) / len(approved) if approved else None
            basis = avg_ap if avg_ap is not None else avg_ai
            pct = percent(basis, q["maximum_marks"]) if basis is not None else None
            out.append({"Examination": q["title"], "Question No": q["question_number"], "Question": q["question_text"],
                        "Maximum Marks": q["maximum_marks"], "Answers Submitted": len(evs), "Evaluated": len(evaluated),
                        "Approved": len(approved), "Avg AI Marks": None if avg_ai is None else round(avg_ai, 2),
                        "Avg Approved Marks": None if avg_ap is None else round(avg_ap, 2), "Avg %": pct,
                        "Difficulty": "n/a" if pct is None else "Easy" if pct >= 75 else "Moderate" if pct >= 50 else "Difficult",
                        "Most Missed Keywords": ", ".join(k for k, _ in missed.most_common(3))})
    return pd.DataFrame(out)


def class_performance_df(actor, exam_ids):
    """Per-student ranking (approved results only)."""
    require_role(actor, "teacher", "admin")
    exam_ids = _authorized_exam_ids(actor, exam_ids)
    rows = [t for t in _totals(exam_ids) if t["complete"]]
    df = pd.DataFrame([{"Examination": t["exam"], "Student": t["student"], "Registration Number": t["reg_no"],
                        "Total Marks": t["total"], "Out Of": t["total_marks"], "Percentage": t["percentage"],
                        "Result": t["result"]} for t in rows],
                      columns=["Examination", "Student", "Registration Number", "Total Marks", "Out Of", "Percentage", "Result"])
    if not df.empty:
        df["Rank"] = df.groupby("Examination")["Percentage"].rank(ascending=False, method="min").astype(int)
        df = df.sort_values(["Examination", "Rank"]).reset_index(drop=True)
    return df


def class_summary_df(actor, exam_ids):
    require_role(actor, "teacher", "admin")
    exam_ids = _authorized_exam_ids(actor, exam_ids)
    allrows = _totals(exam_ids)
    out = []
    for eid in exam_ids:
        rows = [t for t in allrows if t["exam_id"] == eid]
        done = [t for t in rows if t["complete"]]
        pcts = [t["percentage"] for t in done]
        title = repo.fetch_one("SELECT title FROM examinations WHERE id=?", (eid,))["title"]
        out.append({"Examination": title, "Submitted": len(rows), "Fully Approved": len(done),
                    "Class Average %": round(sum(pcts) / len(pcts), 2) if pcts else None,
                    "Highest %": max(pcts) if pcts else None, "Lowest %": min(pcts) if pcts else None,
                    "Passed": sum(1 for t in done if t["result"] == "Pass"),
                    "Pass Rate %": percent(sum(1 for t in done if t["result"] == "Pass"), len(done)) if done else None})
    return pd.DataFrame(out)


def missed_keywords_df(actor, exam_id, top=10):
    require_role(actor, "teacher", "admin")
    _authorized_exam_ids(actor, [exam_id])
    rows = repo.fetch_all("""SELECT ev.details_json FROM evaluations ev JOIN student_answers sa ON sa.id=ev.student_answer_id
        JOIN questions q ON q.id=sa.question_id WHERE q.examination_id=? AND ev.approval_status<>'outdated'""", (exam_id,))
    counter = Counter(k for r in rows for k in loads(r["details_json"], {}).get("missing_keywords", []))
    return pd.DataFrame(counter.most_common(top), columns=["Keyword / Topic", "Times Missed"])


def score_distribution_df(actor, exam_id):
    df = class_performance_df(actor, [exam_id])
    bins = ["0-39", "40-59", "60-79", "80-100"]
    counts = [0, 0, 0, 0]
    for p in df["Percentage"] if not df.empty else []:
        counts[0 if p < 40 else 1 if p < 60 else 2 if p < 80 else 3] += 1
    return pd.DataFrame({"Score range (%)": bins, "Students": counts}).set_index("Score range (%)")


def export_excel(actor, exam_ids) -> bytes:
    """Real .xlsx with three sheets built from live database records."""
    require_role(actor, "teacher", "admin")
    if not exam_ids:
        raise ValueError("Select at least one examination.")
    results = student_results_df(actor, exam_ids)
    qa = question_analysis_df(actor, exam_ids)
    perf = class_performance_df(actor, exam_ids)
    summary = class_summary_df(actor, exam_ids)
    return workbook_bytes({"Student Results": results, "Question-wise Analysis": qa,
                           "Class Performance": [("Summary", summary), ("Student Ranking (fully approved results)", perf)]})


# ---------------------------------------------------------------- student analytics
def student_subject_performance(actor):
    require_role(actor, "student")
    rows = [t for t in _totals(student_id=actor["id"]) if t["complete"]]
    df = pd.DataFrame([{"Subject": t["subject"], "Percentage": t["percentage"]} for t in rows])
    return df.groupby("Subject")["Percentage"].mean().round(2).reset_index() if not df.empty else df


def student_progress(actor):
    require_role(actor, "student")
    rows = [t for t in _totals(student_id=actor["id"]) if t["complete"]]
    return pd.DataFrame([{"Examination": t["exam"], "Percentage": t["percentage"]} for t in rows])


def export_database(actor) -> bytes:
    """Consistent snapshot of the SQLite file (admin backup)."""
    import sqlite3
    require_role(actor, "admin")
    from database.connection import connect
    src, dest = connect(), sqlite3.connect(":memory:")
    try:
        src.backup(dest)
        return dest.serialize()
    finally:
        src.close()
        dest.close()
