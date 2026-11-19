"""Student interface: take exams, view history, approved results and analytics."""
from collections import Counter

import pandas as pd
import streamlit as st

from backend import (auth_service, evaluation_service as evals, examination_service as exams,
                     report_service as reports, submission_service as subs)
from frontend import components as ui


def _exam_table(rows):
    df = pd.DataFrame(rows)[["id", "title", "subject_name", "teacher", "exam_date", "duration", "total_marks", "window", "my_status"]]
    df.columns = ["ID", "Examination", "Subject", "Teacher", "Starts", "Minutes", "Total marks", "Window", "My status"]
    return df


def page_dashboard(user):
    ui.page_header(f"Welcome, {user['name']}", "Your examinations and approved results at a glance.")
    s = reports.student_dashboard(user)
    ui.stat_cards([("Total examinations", s["total_exams"], "assigned to you"), ("Completed", s["completed"], "submitted"),
                   ("Pending", s["pending"], "open & not submitted"), ("Average marks", f"{s['average_pct']:.1f}%", "approved results only")])
    perf = reports.student_subject_performance(user)
    if perf.empty:
        ui.empty_state("Results appear here after your teacher approves your marks.")
    else:
        st.subheader("Subject-wise performance (%)")
        st.bar_chart(perf.set_index("Subject")["Percentage"])
    upcoming = [r for r in exams.list_exams_for_student(user) if r["my_status"] in ("not_started", "in_progress", "reopened") and r["window"] != "closed"]
    if upcoming:
        st.subheader("Examinations waiting for you")
        st.dataframe(_exam_table(upcoming), hide_index=True)


def page_exams(user):
    ui.page_header("My Examinations", "Examinations assigned to your class and subjects.")
    rows = exams.list_exams_for_student(user)
    if not rows:
        ui.empty_state("No examinations have been assigned to you yet.")
        return
    ui.searchable_table(_exam_table(rows), "se_tbl", filter_columns=["Subject", "My status"])


def page_submit(user):
    ui.page_header("Submit Answers", "Write your answers, save a draft, then submit when finished.")
    rows = [r for r in exams.list_exams_for_student(user) if r["my_status"] != "submitted" and (r["window"] == "open" or r["my_status"] == "reopened")]
    if not rows:
        ui.empty_state("No examination is open for you right now. Check 'My Examinations' for the schedule.", "⏳")
        return
    labels = {f"{r['title']} — {r['subject_name']}": r["id"] for r in rows}
    exam_id = labels[st.selectbox("Examination", list(labels), key="sa_exam")]
    ok, data = ui.run(subs.open_exam_for_student, user, exam_id)
    if not ok:
        return
    exam = data["exam"]
    st.info(f"{exam['title']} — total {exam['total_marks']:g} marks. "
            + ("Your teacher has allowed you to resubmit." if data["submission"] and data["submission"]["status"] == "reopened"
               else f"Closes at {data['ends_at']}."))
    if not data["can_edit"]:
        st.warning(data["reason"])
        return
    with st.form(f"answer_form_{exam_id}"):
        answers = {}
        for q in data["questions"]:
            st.markdown(f"**Q{q['question_number']}. {q['question_text']}** ({q['maximum_marks']:g} marks)")
            answers[q["id"]] = st.text_area("Your answer", value=data["answers"].get(q["id"], ""), height=160,
                                            key=f"sa_{exam_id}_{q['id']}", label_visibility="collapsed")
        confirm = st.checkbox("I confirm I want to submit my final answers (cannot be changed afterwards)")
        b1, b2 = st.columns(2)
        draft, final = b1.form_submit_button("💾 Save draft"), b2.form_submit_button("📤 Submit final answers")
        if draft:
            ok, _ = ui.run(subs.save_answers, user, exam_id, answers, False)
            if ok:
                st.success("Draft saved. You can continue editing until the deadline.")
        if final:
            if not confirm:
                st.error("Please tick the confirmation box before submitting.")
            else:
                ok, sub = ui.run(subs.save_answers, user, exam_id, answers, True)
                if ok:
                    ui.flash(f"Submitted successfully at {sub['submitted_at']}. You will see your marks after your teacher approves them.")
                    st.rerun()


def page_history(user):
    ui.page_header("Submission History", "Everything you have saved or submitted.")
    rows = subs.list_student_submissions(user)
    if not rows:
        ui.empty_state("You have not started any examination yet.")
        return
    df = pd.DataFrame(rows)
    st.dataframe(df[["title", "subject_name", "status", "started_at", "submitted_at"]].rename(columns={
        "title": "Examination", "subject_name": "Subject", "status": "Status", "started_at": "Started", "submitted_at": "Submitted"}), hide_index=True)
    labels = {f"{r['title']} ({r['status']})": r["submission_id"] for r in rows}
    pick = st.selectbox("View my answers", list(labels), key="sh_pick")
    ok, detail = ui.run(subs.get_submission_detail, user, labels[pick])
    if ok:
        for a in detail["answers"]:
            ui.show_text(f"Q{a['question_number']}. {a['question_text']}", a["answer_text"], f"sh_{a['answer_id']}")


def page_results(user):
    ui.page_header("My Results", "Marks and feedback appear only after your teacher approves them.")
    results = evals.get_student_results(user)
    totals = {t["exam_id"]: t for t in reports.student_dashboard(user)["totals"]}
    if not results:
        ui.empty_state("No approved results yet. Your teacher is still reviewing your answers.", "🕐")
        return
    exam_ids = list(dict.fromkeys(r["exam_id"] for r in results))
    summary = []
    for eid in exam_ids:
        t = totals.get(eid)
        if t:
            summary.append({"Examination": t["exam"], "Subject": t["subject"], "Marks": f"{t['total']:g} / {t['total_marks']:g}" if t["complete"] else "Partially published",
                            "Percentage": t["percentage"] if t["complete"] else None, "Result": t["result"]})
    st.dataframe(pd.DataFrame(summary), hide_index=True)
    labels = {next(r["title"] for r in results if r["exam_id"] == e): e for e in exam_ids}
    pick = labels[st.selectbox("Examination", list(labels), key="mr_pick")]
    t = totals.get(pick)
    if t and not t["complete"]:
        st.info("Some answers are still awaiting approval. The total will appear when all are approved.")
    for r in [x for x in results if x["exam_id"] == pick]:
        with st.expander(f"Q{r['question_number']} — {r['final_marks']:g} / {r['maximum_marks']:g} marks", expanded=True):
            st.write(r["question_text"])
            ui.show_text("Your answer", r["answer_text"], f"mr_ans_{pick}_{r['question_number']}", 110)
            if r["teacher_feedback"]:
                st.success("Teacher feedback: " + r["teacher_feedback"])
            if r["missing_concepts"]:
                st.warning("Concepts to revise: " + "; ".join(r["missing_concepts"]))
            if r["missing_keywords"]:
                st.write("Missing keywords: " + ", ".join(r["missing_keywords"]))
            st.text(r["ai_feedback"])
            st.caption("How the suggestion was calculated: " + ", ".join(f"{k} {v:.0f}%" for k, v in r["breakdown"].items() if v is not None)
                       + ". Final marks are decided by your teacher.")


def page_analytics(user):
    ui.page_header("Performance Analytics", "Trends from your approved results.")
    prog, perf = reports.student_progress(user), reports.student_subject_performance(user)
    if prog.empty:
        ui.empty_state("Analytics appear once at least one examination is fully approved.", "📈")
        return
    a, b = st.columns(2)
    with a:
        st.subheader("Progress across examinations (%)")
        st.line_chart(prog.set_index("Examination")["Percentage"])
    with b:
        st.subheader("Average by subject (%)")
        st.bar_chart(perf.set_index("Subject")["Percentage"])
    missed = Counter(c for r in evals.get_student_results(user) for c in r["missing_concepts"])
    if missed:
        st.subheader("Topics to revise")
        st.dataframe(pd.DataFrame(missed.most_common(10), columns=["Concept", "Times missed"]), hide_index=True)


def page_profile(user):
    ui.page_header("My Profile", "Your account details.")
    st.write(f"**Name:** {user['name']}  \n**Email:** {user['email']}  \n**Registration number:** {user['registration_number']}")
    with st.form("sp_pw"):
        old, new = st.text_input("Current password", type="password"), st.text_input("New password", type="password")
        if st.form_submit_button("Change password"):
            ok, _ = ui.run(auth_service.change_password, user, old, new)
            if ok:
                st.success("Password changed.")


PAGES = {"My Dashboard": page_dashboard, "My Examinations": page_exams, "Submit Answers": page_submit,
         "Submission History": page_history, "My Results": page_results, "Performance Analytics": page_analytics,
         "My Profile": page_profile}


def render(user):
    choice = st.sidebar.radio("Navigation", list(PAGES), key="nav_student")
    ui.show_flash()
    PAGES[choice](user)
