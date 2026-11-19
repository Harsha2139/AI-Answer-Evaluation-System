"""Teacher interface: exams, questions, submissions, AI evaluation, approval, reports and export."""
from datetime import date, datetime, time

import pandas as pd
import streamlit as st

import config
from backend import (auth_service, evaluation_service as evals, examination_service as exams,
                     report_service as reports, submission_service as subs, user_service as users)
from frontend import components as ui
from ml import model_loader
from ml.evaluator import ENGINE_NAME
from utils.helpers import fmt_dt, loads

WEIGHT_LABELS = {"similarity": "Similarity (TF-IDF cosine)", "keyword": "Keyword match",
                 "concept": "Concept coverage", "completeness": "Completeness"}


# ------------------------------------------------------------------ helpers
def _exam_picker(user, key, label="Examination"):
    rows = exams.list_exams(user)
    if not rows:
        ui.empty_state("No examinations yet. Create one from 'Create Examination'.")
        return None
    labels = {f"{r['title']} — {r['class_label']} — {r['subject_name']} [{r['status']}]": r for r in rows}
    return labels[st.selectbox(label, list(labels), key=key)]


def _weight_inputs(prefix, current=None):
    current = current or config.DEFAULT_WEIGHTS
    cols = st.columns(4)
    return {k: cols[i].slider(WEIGHT_LABELS[k], 0.0, 1.0, float(current.get(k, config.DEFAULT_WEIGHTS[k])), 0.05,
                              key=f"{prefix}_w_{k}") for i, k in enumerate(WEIGHT_LABELS)}


def _question_fields(prefix, q=None):
    q = q or {}
    text = st.text_area("Question", value=q.get("question_text", ""), key=f"{prefix}_text")
    ref = st.text_area("Reference (model) answer", value=q.get("reference_answer", ""), key=f"{prefix}_ref", height=120)
    c1, c2 = st.columns([1, 3])
    marks = c1.number_input("Maximum marks", 0.5, 100.0, float(q.get("maximum_marks", 5.0)), 0.5, key=f"{prefix}_marks")
    kws = c2.text_input("Expected keywords (comma separated; use 'term|synonym' for synonyms)",
                        value=", ".join(q.get("expected_keywords", [])), key=f"{prefix}_kw")
    concepts = st.text_area("Expected concepts (one per line)", value="\n".join(q.get("expected_concepts", [])),
                            key=f"{prefix}_con", height=80)
    alts = st.text_area("Alternate acceptable answers (optional; separate several with a line containing only ---)",
                        value="\n---\n".join(q.get("alternate_answers", [])), key=f"{prefix}_alt", height=80)
    return {"question_text": text, "reference_answer": ref, "maximum_marks": marks, "expected_keywords": kws,
            "expected_concepts": concepts, "alternate_answers": alts}


# ------------------------------------------------------------------ pages
def page_dashboard(user):
    ui.page_header("Dashboard", "Live overview of your examinations and evaluations.")
    s = reports.teacher_dashboard(user)
    ui.stat_cards([("Students", s["total_students"], "in your classes"), ("Examinations", s["total_exams"], ""),
                   ("Submitted answers", s["submitted_answers"], ""), ("Evaluations completed", s["evaluations_done"], "AI suggestions made")])
    ui.stat_cards([("Pending approvals", s["pending_approvals"], "need your review"),
                   ("Average marks", f"{s['average_marks_pct']:.1f}%", "fully approved results only")])
    rows = exams.list_exams(user)
    if not rows:
        ui.empty_state("You have not created any examinations yet.")
        return
    df = pd.DataFrame(rows)[["title", "class_label", "subject_name", "exam_date", "duration", "total_marks", "status", "submitted_count"]]
    df.columns = ["Examination", "Class", "Subject", "Date", "Minutes", "Total marks", "Status", "Submitted"]
    st.subheader("My examinations")
    st.dataframe(df, hide_index=True)
    summary = reports.class_summary_df(user, [r["id"] for r in rows])
    summary = summary.dropna(subset=["Class Average %"])
    if not summary.empty:
        st.subheader("Class average by examination (%)")
        st.bar_chart(summary.set_index("Examination")["Class Average %"])


def page_classes(user):
    ui.page_header("My Classes", "Classes assigned to you by the administrator.")
    assigns = users.list_assignments(user["id"])
    if not assigns:
        ui.empty_state("No classes assigned yet. Please contact the administrator.")
        return
    classes = {}
    for a in assigns:
        classes.setdefault(a["class_id"], {"label": a["class_label"], "subjects": [], "students": a["students"]})["subjects"].append(a["subject_label"])
    st.dataframe(pd.DataFrame([{"Class": c["label"], "Subjects": ", ".join(c["subjects"]), "Students": c["students"]} for c in classes.values()]),
                 hide_index=True)
    choice = st.selectbox("View students of", [c["label"] for c in classes.values()], key="cls_pick")
    class_id = [i for i, c in classes.items() if c["label"] == choice][0]
    ok, students = ui.run(users.list_class_students, user, class_id)
    if ok:
        ui.searchable_table(pd.DataFrame(students).rename(columns={"name": "Name", "email": "Email", "registration_number": "Registration No"}).drop(columns=["id"], errors="ignore"),
                            "cls_students", empty_message="No students in this class yet.")


def page_subjects(user):
    ui.page_header("My Subjects", "Subjects you teach and their examinations.")
    assigns = users.list_assignments(user["id"])
    if not assigns:
        ui.empty_state("No subjects assigned yet.")
        return
    ex = pd.DataFrame(exams.list_exams(user))
    rows = []
    for a in assigns:
        n = 0 if ex.empty else int(((ex["subject_id"] == a["subject_id"]) & (ex["class_id"] == a["class_id"])).sum())
        rows.append({"Subject": a["subject_label"], "Class": a["class_label"], "Students": a["students"], "Examinations": n})
    st.dataframe(pd.DataFrame(rows), hide_index=True)


def page_create_exam(user):
    ui.page_header("Create Examination", "Add questions with reference answers, keywords and concepts.")
    assigns = users.list_assignments(user["id"])
    if not assigns:
        ui.empty_state("You are not assigned to any class/subject yet.")
        return
    targets = {f"{a['class_label']} — {a['subject_label']}": a for a in assigns}
    n_q = int(st.number_input("Number of questions", 1, 30, 2, key="ce_nq"))
    with st.form("create_exam"):
        title = st.text_input("Examination title")
        target = st.selectbox("Class and subject", list(targets))
        c1, c2, c3 = st.columns(3)
        d = c1.date_input("Date", value=date.today())
        t = c2.time_input("Start time", value=time(9, 0))
        duration = c3.number_input("Duration (minutes)", 5, 600, 60, 5)
        st.caption("Students can write and submit only between the start time and the end of the duration.")
        with st.expander("Scoring weights (advanced - normalised automatically)"):
            weights = _weight_inputs("ce")
        questions = []
        for i in range(n_q):
            st.markdown(f"#### Question {i + 1}")
            questions.append(_question_fields(f"ce_q{i}"))
        publish = st.checkbox("Publish now (make visible to students)")
        if st.form_submit_button("Save examination"):
            a = targets[target]
            ok, exam_id = ui.run(exams.create_exam, user, title, a["class_id"], a["subject_id"],
                                 fmt_dt(datetime.combine(d, t)), duration, questions, weights, None, publish)
            if ok:
                total = sum(q["maximum_marks"] for q in questions)
                ui.flash(f"Examination saved (ID {exam_id}) - {n_q} questions, total {total:g} marks"
                         f"{' - published' if publish else ' - saved as draft. Publish it from Manage Questions.'}")
                st.rerun()


def page_manage_questions(user):
    ui.page_header("Manage Questions", "Edit questions, publish or close an examination.")
    exam = _exam_picker(user, "mq_exam")
    if not exam:
        return
    ok, detail = ui.run(exams.get_exam_with_questions, user, exam["id"])
    if not ok:
        return
    st.markdown(f"Status: {ui.badge(detail['status'])} &nbsp; Total marks: **{detail['total_marks']:g}** &nbsp; "
                f"Window: **{detail['exam_date']}** for **{detail['duration']} min**", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    for col, (label, status) in zip((c1, c2, c3), (("Publish", "published"), ("Close", "closed"), ("Back to draft", "draft"))):
        if col.button(label, key=f"mq_status_{status}"):
            ok, _ = ui.run(exams.set_status, user, exam["id"], status)
            if ok:
                ui.flash(f"Examination is now {status}.")
                st.rerun()
    with st.expander("Edit title, schedule and scoring weights"):
        with st.form("mq_edit_exam"):
            title = st.text_input("Title", value=detail["title"])
            e1, e2, e3 = st.columns(3)
            start = datetime.strptime(detail["exam_date"], "%Y-%m-%d %H:%M:%S")
            d = e1.date_input("Date", value=start.date(), key="mq_date")
            t = e2.time_input("Start time", value=start.time(), key="mq_time")
            dur = e3.number_input("Duration (minutes)", 5, 600, int(detail["duration"]), 5, key="mq_dur")
            weights = _weight_inputs("mq", detail["scoring_weights"])
            use_default = st.checkbox("Use system default weights instead", value=detail["scoring_weights"] is None)
            if st.form_submit_button("Save changes"):
                ok, _ = ui.run(exams.update_exam, user, exam["id"], title, fmt_dt(datetime.combine(d, t)), dur,
                               None if use_default else weights, use_default)
                if ok:
                    ui.flash("Examination updated. Re-evaluate answers to apply new weights.")
                    st.rerun()
    locked = detail["has_submissions"]
    if locked:
        st.info("Students have started this examination, so questions are locked to keep evaluation consistent.")
    for q in detail["questions"]:
        with st.expander(f"Q{q['question_number']}. {q['question_text'][:90]} ({q['maximum_marks']:g} marks)"):
            if locked:
                st.write(q["question_text"])
                ui.show_text("Reference answer", q["reference_answer"], f"mq_ref_{q['id']}")
                st.caption(f"Keywords: {', '.join(q['expected_keywords']) or '(auto-extracted)'}  |  "
                           f"Concepts: {'; '.join(q['expected_concepts']) or '(auto-extracted)'}")
                continue
            with st.form(f"mq_q_{q['id']}"):
                data = _question_fields(f"mq_{q['id']}", q)
                s1, s2 = st.columns(2)
                save, delete = s1.form_submit_button("Save question"), s2.form_submit_button("Delete question")
                if save:
                    ok, _ = ui.run(exams.update_question, user, q["id"], data)
                    if ok:
                        ui.flash("Question updated."); st.rerun()
                if delete:
                    ok, _ = ui.run(exams.delete_question, user, q["id"])
                    if ok:
                        ui.flash("Question deleted."); st.rerun()
    if not locked:
        with st.expander("➕ Add a question"):
            with st.form("mq_add"):
                data = _question_fields("mq_new")
                if st.form_submit_button("Add question"):
                    ok, _ = ui.run(exams.add_question, user, exam["id"], data)
                    if ok:
                        ui.flash("Question added."); st.rerun()
        with st.expander("🗑 Delete this examination"):
            confirm = st.checkbox("I understand this permanently deletes the examination and its questions", key="mq_del_ok")
            if st.button("Delete examination", disabled=not confirm, key="mq_del"):
                ok, _ = ui.run(exams.delete_exam, user, exam["id"])
                if ok:
                    ui.flash("Examination deleted."); st.rerun()


def page_submissions(user):
    ui.page_header("Student Submissions", "See who has submitted and read their answers.")
    exam = _exam_picker(user, "sub_exam")
    if not exam:
        return
    rows = subs.list_exam_submissions(user, exam["id"])
    if not rows:
        ui.empty_state("No students are enrolled for this class and subject yet.")
        return
    df = pd.DataFrame(rows)
    done = int((df["status"] == "submitted").sum())
    ui.stat_cards([("Enrolled", len(df), ""), ("Submitted", done, ""), ("Not submitted", len(df) - done, "")])
    view = df[["name", "registration_number", "status", "submitted_at", "evaluated", "approved", "total_questions"]].copy()
    view.columns = ["Student", "Reg. No", "Status", "Submitted at", "Evaluated", "Approved", "Questions"]
    ui.searchable_table(view, "sub_tbl", filter_columns=["Status"])
    have = df[df["submission_id"].notna()]
    if have.empty:
        return
    labels = {f"{r['name']} ({r['registration_number']}) — {r['status']}": int(r["submission_id"]) for _, r in have.iterrows()}
    pick = st.selectbox("Open a submission", list(labels), key="sub_pick")
    ok, detail = ui.run(subs.get_submission_detail, user, labels[pick])
    if not ok:
        return
    for a in detail["answers"]:
        ui.show_text(f"Q{a['question_number']}. {a['question_text']} ({a['maximum_marks']:g} marks)", a["answer_text"], f"sub_ans_{a['answer_id']}")
    if detail["status"] == "submitted":
        reason = st.text_input("Reason for allowing resubmission (optional)", key="sub_reason")
        if st.button("Allow resubmission", key="sub_reopen"):
            ok, _ = ui.run(subs.reopen_submission, user, detail["id"], reason)
            if ok:
                ui.flash("Student may now edit and resubmit. Earlier marks for changed answers become outdated.")
                st.rerun()


def _eval_card(user, e):
    flag = " ⚠ review" if e["needs_review"] else ""
    with st.expander(f"{e['student_name']} — Q{e['question_number']} — AI {e['suggested_marks']:g}/{e['maximum_marks']:g} — "
                     f"{e['approval_status']}{flag}"):
        a, b = st.columns(2)
        with a:
            ui.show_text("Student answer", e["answer_text"], f"ev_ans_{e['evaluation_id']}")
        with b:
            ui.show_text("Reference answer", e["reference_answer"], f"ev_ref_{e['evaluation_id']}")
        m = st.columns(5)
        m[0].metric("Similarity", f"{e['similarity_score']:.0f}%")
        m[1].metric("Keywords", f"{e['keyword_coverage']:.0f}%")
        m[2].metric("Concepts", f"{e['concept_coverage']:.0f}%")
        m[3].metric("Confidence", f"{e['confidence']:.0%}")
        m[4].metric("Copy check", f"{e['plagiarism_score']:.0f}%")
        d = e["details"]
        for flag_text in d.get("flags", []):
            st.warning(flag_text)
        if e["plagiarism_score"] >= config.PLAGIARISM_THRESHOLD:
            st.warning("This answer is very similar to another student's answer to the same question.")
        if d.get("missing_concepts") or d.get("partial_concepts"):
            st.write("**Missing / weak concepts:** " + "; ".join(d.get("missing_concepts", []) + d.get("partial_concepts", [])))
        if d.get("missing_keywords"):
            st.write("**Missing keywords:** " + ", ".join(d["missing_keywords"]))
        st.caption(f"Engine: {d.get('engine', ENGINE_NAME)} (baseline NLP suggestion, not a trained model). "
                   f"Breakdown: {d.get('breakdown', {})}")
        st.text(e["ai_feedback"])
        with st.form(f"rv_{e['evaluation_id']}"):
            default = e["final_marks"] if e["final_marks"] is not None else e["suggested_marks"]
            marks = st.number_input("Final marks", 0.0, float(e["maximum_marks"]), float(default), 0.5, key=f"rv_m_{e['evaluation_id']}")
            fb = st.text_area("Your feedback to the student (optional)", value=e["teacher_feedback"], key=f"rv_f_{e['evaluation_id']}", height=70)
            reason = st.text_input("Reason (required if you change the AI marks without feedback)", key=f"rv_r_{e['evaluation_id']}")
            b1, b2, b3, b4 = st.columns(4)
            action = None
            if b1.form_submit_button("✅ Approve AI marks"):
                action = ("approve", None)
            if b2.form_submit_button("✏ Save edited marks"):
                action = ("edit", marks)
            if b3.form_submit_button("🚫 Reject AI evaluation"):
                action = ("reject", None)
            if b4.form_submit_button("🔄 Re-evaluate"):
                action = ("re", None)
            if action:
                if action[0] == "re":
                    ok, _ = ui.run(evals.reevaluate_answer, user, e["answer_id"])
                else:
                    ok, _ = ui.run(evals.review_evaluation, user, e["evaluation_id"], action[0], action[1], fb, reason)
                if ok:
                    ui.flash(f"{e['student_name']} Q{e['question_number']}: {action[0]} done."); st.rerun()
        hist = evals.get_audit_history(user, e["evaluation_id"])
        if hist:
            st.caption("Audit history")
            st.dataframe(pd.DataFrame(hist), hide_index=True)


def page_evaluation(user):
    ui.page_header("AI Evaluation", "Run the NLP engine, then review, edit and approve - students see nothing until you approve.")
    exam = _exam_picker(user, "ev_exam")
    if not exam:
        return
    c1, c2 = st.columns(2)
    if c1.button("▶ Run AI evaluation on new submissions", key="ev_run"):
        with st.spinner("Running NLP evaluation on submitted answers…"):
            ok, res = ui.run(evals.evaluate_exam, user, exam["id"])
        if ok:
            ui.flash(f"Evaluated {res['evaluated']} answer(s); {res['skipped']} already evaluated; "
                     f"{res['plagiarism_flagged']} flagged for similarity to another student.")
            st.rerun()
    if c2.button("✅ Approve all unflagged suggestions", key="ev_bulk"):
        ok, n = ui.run(evals.bulk_approve, user, exam["id"])
        if ok:
            ui.flash(f"Approved {n} unflagged suggestion(s). Flagged answers still need your individual review.")
            st.rerun()
    allrows = evals.list_evaluations(user, exam["id"])
    if not allrows:
        ui.empty_state("No evaluations yet. Click 'Run AI evaluation' once students have submitted.")
        return
    df = pd.DataFrame(allrows)
    ui.stat_cards([("Pending", int((df.approval_status == "pending").sum()), ""), ("Approved", int((df.approval_status == "approved").sum()), ""),
                   ("Rejected", int((df.approval_status == "rejected").sum()), ""), ("Outdated", int((df.approval_status == "outdated").sum()), "student edited answer"),
                   ("Flagged for review", int(df.needs_review.sum()), "low confidence / copy / negation")])
    f1, f2, f3 = st.columns(3)
    students = ["All"] + sorted(df.student_name.unique())
    who = f1.selectbox("Student", students, key="ev_f_student")
    status = f2.selectbox("Status", ["All", "pending", "approved", "rejected", "outdated"], key="ev_f_status")
    flagged = f3.checkbox("Only flagged for review", key="ev_f_flag")
    shown = [r for r in allrows if (who == "All" or r["student_name"] == who) and (status == "All" or r["approval_status"] == status)
             and (not flagged or r["needs_review"])]
    limit = 25
    st.caption(f"Showing {min(len(shown), limit)} of {len(shown)} answers - use the filters to narrow down.")
    for e in shown[:limit]:
        _eval_card(user, e)
    pairs = evals.get_plagiarism_pairs(user, exam["id"])
    with st.expander(f"Possible copying between students ({len(pairs)})"):
        if pairs:
            st.dataframe(pd.DataFrame(pairs), hide_index=True)
        else:
            st.write("No suspiciously similar answers found.")


def page_reports(user):
    ui.page_header("Evaluation Reports", "Analytics from fully approved results.")
    exam = _exam_picker(user, "rp_exam")
    if not exam:
        return
    summary = reports.class_summary_df(user, [exam["id"]]).iloc[0]
    ui.stat_cards([("Submitted", int(summary["Submitted"]), ""), ("Fully approved", int(summary["Fully Approved"]), ""),
                   ("Class average", ui.fmt_pct(summary["Class Average %"]), ""), ("Highest", ui.fmt_pct(summary["Highest %"]), ""),
                   ("Lowest", ui.fmt_pct(summary["Lowest %"]), ""), ("Pass rate", ui.fmt_pct(summary["Pass Rate %"]), f"pass ≥ {config.PASS_PERCENTAGE:g}%")])
    perf = reports.class_performance_df(user, [exam["id"]])
    qa = reports.question_analysis_df(user, [exam["id"]])
    left, right = st.columns(2)
    with left:
        st.subheader("Score distribution")
        if perf.empty:
            ui.empty_state("Approve all answers of at least one student to see the distribution.")
        else:
            st.bar_chart(reports.score_distribution_df(user, exam["id"]))
    with right:
        st.subheader("Question difficulty (average %)")
        shown = qa.dropna(subset=["Avg %"]) if not qa.empty else qa
        if shown.empty:
            ui.empty_state("No evaluated answers yet.")
        else:
            st.bar_chart(shown.assign(Q="Q" + shown["Question No"].astype(str)).set_index("Q")["Avg %"])
    st.subheader("Question-wise analysis")
    ui.searchable_table(qa, "rp_qa", filter_columns=["Difficulty"], empty_message="No questions.")
    st.subheader("Most commonly missed keywords")
    missed = reports.missed_keywords_df(user, exam["id"])
    ui.searchable_table(missed, "rp_missed", empty_message="No missed keywords recorded yet.") if not missed.empty else ui.empty_state("No missed keywords recorded yet.")
    st.subheader("Student ranking")
    ui.searchable_table(perf, "rp_rank", empty_message="No fully approved results yet.")
    st.caption("Percentages count only teacher-approved marks. Students with unapproved answers are excluded from the ranking.")


def page_export(user):
    ui.page_header("Excel Export", "Download real examination records as .xlsx (3 sheets) or CSV.")
    rows = exams.list_exams(user)
    if not rows:
        ui.empty_state("No examinations to export.")
        return
    labels = {f"{r['title']} — {r['class_label']} — {r['subject_name']}": r["id"] for r in rows}
    picked = st.multiselect("Examinations", list(labels), default=list(labels)[:1], key="ex_pick")
    ids = [labels[p] for p in picked]
    if not ids:
        st.info("Select at least one examination.")
        return
    if st.button("Prepare reports", key="ex_prepare"):
        with st.spinner("Building workbook from database records…"):
            ok, data = ui.run(reports.export_excel, user, ids)
        if ok:
            st.session_state["xlsx_bytes"], st.session_state["xlsx_ids"] = data, ids
    if st.session_state.get("xlsx_ids") == ids and st.session_state.get("xlsx_bytes"):
        st.success("Report ready.")
        st.download_button("⬇ Download Excel", st.session_state["xlsx_bytes"], file_name="evaluation_report.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="ex_dl")
        ui.csv_button(reports.student_results_df(user, ids), "student_results.csv", "ex_csv", "⬇ Download student results (CSV)")


def page_profile(user):
    ui.page_header("Profile and Settings", "Account, password and information about the evaluation engine.")
    st.write(f"**Name:** {user['name']}  \n**Email:** {user['email']}")
    with st.form("tp_pw"):
        old, new = st.text_input("Current password", type="password"), st.text_input("New password", type="password")
        if st.form_submit_button("Change password"):
            ok, _ = ui.run(auth_service.change_password, user, old, new)
            if ok:
                st.success("Password changed.")
    st.subheader("Evaluation engine")
    st.write(f"Engine: `{ENGINE_NAME}` - a transparent NLP **baseline** (TF-IDF cosine similarity, fuzzy keyword and concept "
             "matching, completeness). It is **not** a trained supervised model and has not been validated against human grading.")
    st.write(f"Sentence-transformers semantic model: {'enabled' if model_loader.semantic_available() else 'not enabled'}")
    st.write("Default weights: " + ", ".join(f"{WEIGHT_LABELS[k]} {v:.0%}" for k, v in config.DEFAULT_WEIGHTS.items()))
    with st.expander("Limitations - please read"):
        st.markdown("- Similarity is not correctness: a wrong answer reusing the question's words can score well.\n"
                    "- Correct answers phrased very differently may score low (enable sentence embeddings to help).\n"
                    "- Negations and numeric facts are only checked heuristically.\n"
                    "- Confidence and flags are heuristics; always review before approving.\n"
                    "- English text only; handwritten answers are not supported.")


PAGES = {"Dashboard": page_dashboard, "My Classes": page_classes, "My Subjects": page_subjects,
         "Create Examination": page_create_exam, "Manage Questions": page_manage_questions,
         "Student Submissions": page_submissions, "AI Evaluation": page_evaluation,
         "Evaluation Reports": page_reports, "Excel Export": page_export, "Profile and Settings": page_profile}


def render(user):
    choice = st.sidebar.radio("Navigation", list(PAGES), key="nav_teacher")
    ui.show_flash()
    PAGES[choice](user)
