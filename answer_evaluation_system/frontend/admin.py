"""Administrator interface: users, classes, subjects, assignments, examinations, analytics and settings."""
import pandas as pd
import streamlit as st

import config
from backend import (auth_service, examination_service as exams, report_service as reports, user_service as users)
from database import connection
from database.models import TABLES
from frontend import components as ui
from ml import model_loader
from ml.evaluator import ENGINE_NAME


def _users_df(rows):
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["is_active"] = df["is_active"].map({1: "Active", 0: "Inactive"})
    return df.rename(columns={"id": "ID", "name": "Name", "email": "Email", "role": "Role", "registration_number": "Reg. No",
                              "class_id": "Class ID", "is_active": "Status", "created_at": "Created", "last_login": "Last login"})


def _pick(label, items, key, fmt=lambda x: x):
    if not items:
        return None
    return st.selectbox(label, items, format_func=fmt, key=key)


def page_dashboard(admin):
    ui.page_header("Administrator Dashboard", "Platform-wide live statistics.")
    s = reports.admin_dashboard(admin)
    ui.stat_cards([("Teachers", s["teachers"], f"{s['pending_teachers']} awaiting approval"), ("Students", s["students"], ""),
                   ("Classes", s["classes"], ""), ("Subjects", s["subjects"], "")])
    ui.stat_cards([("Examinations", s["exams"], ""), ("Submissions", s["submissions"], ""),
                   ("AI evaluations", s["evaluations"], ""), ("Approved", s["approved"], f"{s['pending']} pending")])
    if s["pending_teachers"]:
        st.warning(f"{s['pending_teachers']} teacher account(s) are waiting for approval in 'Manage Teachers'.")


def page_teachers(admin):
    ui.page_header("Manage Teachers", "Create, approve and assign teachers.")
    t1, t2, t3 = st.tabs(["Teachers", "Create teacher", "Assign classes & subjects"])
    teachers = users.list_users(admin, "teacher")
    with t1:
        pending = [t for t in teachers if not t["is_active"]]
        for t in pending:
            c1, c2 = st.columns([4, 1])
            c1.write(f"⏳ **{t['name']}** ({t['email']}) is awaiting approval")
            if c2.button("Approve", key=f"apr_{t['id']}"):
                ok, _ = ui.run(users.set_user_active, admin, t["id"], True)
                if ok:
                    ui.flash(f"{t['name']} approved."); st.rerun()
        ui.searchable_table(_users_df(teachers), "adm_teachers", ["Status"], "No teachers yet.")
    with t2:
        with st.form("create_teacher", clear_on_submit=True):
            name, email, pw = st.text_input("Name"), st.text_input("Email"), st.text_input("Temporary password", type="password")
            if st.form_submit_button("Create teacher"):
                ok, _ = ui.run(users.create_user, admin, name, email, pw, "teacher")
                if ok:
                    ui.flash("Teacher created."); st.rerun()
    with t3:
        classes, subjects = users.list_classes(), users.list_subjects()
        active = [t for t in teachers if t["is_active"]]
        if not (active and classes and subjects):
            ui.empty_state("Create an active teacher, a class and a subject first.")
        else:
            with st.form("assign"):
                t = _pick("Teacher", active, "as_t", lambda x: x["name"])
                c = _pick("Class", classes, "as_c", lambda x: x["label"])
                s = _pick("Subject", subjects, "as_s", lambda x: x["label"])
                if st.form_submit_button("Assign"):
                    ok, _ = ui.run(users.assign_teacher, admin, t["id"], c["id"], s["id"])
                    if ok:
                        ui.flash("Assignment saved. Students of that class were enrolled automatically."); st.rerun()
        assigns = users.list_assignments()
        if assigns:
            st.dataframe(pd.DataFrame(assigns)[["id", "teacher", "class_label", "subject_label", "students"]], hide_index=True)
            pick = _pick("Remove assignment", assigns, "as_rm", lambda a: f"{a['teacher']} — {a['class_label']} — {a['subject_label']}")
            if st.button("Remove selected assignment", key="as_rm_btn"):
                ok, _ = ui.run(users.remove_assignment, admin, pick["id"])
                if ok:
                    ui.flash("Assignment removed."); st.rerun()


def page_students(admin):
    ui.page_header("Manage Students", "Create students, change classes and enrol into subjects.")
    classes, subjects = users.list_classes(), users.list_subjects()
    students = users.list_users(admin, "student")
    t1, t2, t3 = st.tabs(["Students", "Create student", "Class & enrolment"])
    with t1:
        ui.searchable_table(_users_df(students), "adm_students", ["Status"], "No students registered yet.")
    with t2:
        with st.form("create_student", clear_on_submit=True):
            name, email = st.text_input("Name"), st.text_input("Email")
            reg, pw = st.text_input("Registration number"), st.text_input("Temporary password", type="password")
            cls = _pick("Class", [{"id": None, "label": "(none)"}] + classes, "cs_c", lambda x: x["label"])
            if st.form_submit_button("Create student"):
                ok, _ = ui.run(users.create_user, admin, name, email, pw, "student", reg, cls["id"] if cls else None)
                if ok:
                    ui.flash("Student created."); st.rerun()
    with t3:
        if not students or not classes:
            ui.empty_state("Need at least one student and one class.")
        else:
            st.write("**Change class** (student is auto-enrolled in all subjects assigned to the new class)")
            with st.form("chg_class"):
                s = _pick("Student", students, "cc_s", lambda x: f"{x['name']} ({x['registration_number']})")
                c = _pick("New class", classes, "cc_c", lambda x: x["label"])
                if st.form_submit_button("Update class"):
                    ok, _ = ui.run(users.update_profile, admin, s["id"], None, c["id"])
                    if ok:
                        ui.flash("Class updated."); st.rerun()
            if subjects:
                st.write("**Enrol in an additional class/subject**")
                with st.form("enrol"):
                    s = _pick("Student", students, "en_s", lambda x: f"{x['name']} ({x['registration_number']})")
                    c = _pick("Class", classes, "en_c", lambda x: x["label"])
                    sub = _pick("Subject", subjects, "en_sub", lambda x: x["label"])
                    if st.form_submit_button("Enrol"):
                        ok, _ = ui.run(users.enroll_student, admin, s["id"], c["id"], sub["id"])
                        if ok:
                            ui.flash("Enrolled."); st.rerun()


def _simple_manager(admin, kind):
    is_class = kind == "class"
    ui.page_header("Manage Classes" if is_class else "Manage Subjects", "Create and remove records.")
    rows = users.list_classes() if is_class else users.list_subjects()
    with st.form(f"new_{kind}", clear_on_submit=True):
        a = st.text_input("Class name (e.g. Class 10)" if is_class else "Subject name")
        b = st.text_input("Section (e.g. A)" if is_class else "Subject code (e.g. CS101)")
        if st.form_submit_button("Create"):
            fn = users.create_class if is_class else users.create_subject
            ok, _ = ui.run(fn, admin, a, b)
            if ok:
                ui.flash("Created."); st.rerun()
    if not rows:
        ui.empty_state("Nothing here yet.")
        return
    st.dataframe(pd.DataFrame(rows).drop(columns=["label"]), hide_index=True)
    pick = _pick("Delete", rows, f"del_{kind}", lambda x: x["label"])
    if st.button("Delete selected", key=f"del_{kind}_btn"):
        ok, _ = ui.run(users.delete_class if is_class else users.delete_subject, admin, pick["id"])
        if ok:
            ui.flash("Deleted."); st.rerun()


def page_classes(admin):
    _simple_manager(admin, "class")


def page_subjects(admin):
    _simple_manager(admin, "subject")


def page_exams(admin):
    ui.page_header("Manage Examinations", "All examinations on the platform.")
    rows = exams.list_exams(admin)
    if not rows:
        ui.empty_state("No examinations have been created yet.")
        return
    df = pd.DataFrame(rows)[["id", "title", "teacher", "class_label", "subject_name", "exam_date", "duration", "total_marks", "status", "submitted_count"]]
    ui.searchable_table(df, "adm_exams", ["status", "teacher"])
    pick = _pick("Select examination", rows, "adm_ex_pick", lambda r: f"{r['title']} ({r['teacher']}) [{r['status']}]")
    cols = st.columns(4)
    for col, (label, status) in zip(cols[:3], (("Publish", "published"), ("Close", "closed"), ("Back to draft", "draft"))):
        if col.button(label, key=f"adm_st_{status}"):
            ok, _ = ui.run(exams.set_status, admin, pick["id"], status)
            if ok:
                ui.flash(f"Examination is now {status}."); st.rerun()
    if cols[3].button("Delete (no submissions)", key="adm_ex_del"):
        ok, _ = ui.run(exams.delete_exam, admin, pick["id"])
        if ok:
            ui.flash("Examination deleted."); st.rerun()


def page_analytics(admin):
    ui.page_header("System Analytics", "Usage and evaluation statistics.")
    s = reports.admin_dashboard(admin)
    a, b = st.columns(2)
    with a:
        st.subheader("Users")
        st.bar_chart(pd.DataFrame({"Count": [s["teachers"], s["students"]]}, index=["Teachers", "Students"]))
    with b:
        st.subheader("Evaluations")
        st.bar_chart(pd.DataFrame({"Count": [s["pending"], s["approved"], s["evaluations"] - s["pending"] - s["approved"]]},
                                  index=["Pending", "Approved", "Rejected / outdated"]))
    rows = exams.list_exams(admin)
    if rows:
        st.subheader("Examinations per subject")
        st.bar_chart(pd.DataFrame(rows).groupby("subject_name").size().rename("Examinations"))
        st.subheader("Submissions per examination")
        st.bar_chart(pd.DataFrame(rows).set_index("title")["submitted_count"])


def page_database(admin):
    ui.page_header("Database Reports", "Read-only view of stored records (password hashes are never shown).")
    st.dataframe(reports.database_table_counts(admin), hide_index=True)
    table = st.selectbox("Browse table", TABLES, key="db_table")
    df = reports.read_table(admin, table)
    ui.searchable_table(df, "db_browse", empty_message="This table is empty.")
    ui.csv_button(df, f"{table}.csv", "db_csv")


def page_users(admin):
    ui.page_header("User Management", "Activate, deactivate, change roles and reset passwords.")
    rows = users.list_users(admin)
    ui.searchable_table(_users_df(rows), "um_tbl", ["Role", "Status"])
    pick = _pick("Select user", rows, "um_pick", lambda u: f"{u['name']} ({u['email']}) — {u['role']}")
    if not pick:
        return
    c1, c2, c3 = st.columns(3)
    if pick["is_active"] and c1.button("Deactivate account", key="um_deact"):
        ok, _ = ui.run(users.set_user_active, admin, pick["id"], False)
        if ok:
            ui.flash("Account deactivated."); st.rerun()
    if not pick["is_active"] and c1.button("Activate account", key="um_act"):
        ok, _ = ui.run(users.set_user_active, admin, pick["id"], True)
        if ok:
            ui.flash("Account activated."); st.rerun()
    if c2.button("Reset password", key="um_reset"):
        ok, temp = ui.run(users.reset_password, admin, pick["id"])
        if ok:
            st.success("Temporary password (shown once - share it securely):")
            st.code(temp)
    if pick["role"] in ("admin", "teacher") and pick["id"] != admin["id"]:
        new_role = c3.selectbox("Role", ["teacher", "admin"], index=0 if pick["role"] == "teacher" else 1, key="um_role")
        if new_role != pick["role"] and st.button(f"Change role to {new_role}", key="um_role_btn"):
            ok, _ = ui.run(users.set_user_role, admin, pick["id"], new_role)
            if ok:
                ui.flash("Role changed."); st.rerun()


def page_settings(admin):
    ui.page_header("Settings", "Security, configuration and backups.")
    with st.form("ad_pw"):
        old, new = st.text_input("Current password", type="password"), st.text_input("New password", type="password")
        if st.form_submit_button("Change my password"):
            ok, _ = ui.run(auth_service.change_password, admin, old, new)
            if ok:
                st.success("Password changed.")
    st.subheader("Configuration (read-only; set via environment variables)")
    st.dataframe(pd.DataFrame([
        ("Database file", str(connection.get_db_path())), ("Evaluation engine", ENGINE_NAME),
        ("Sentence-transformers", "enabled" if model_loader.semantic_available() else "not enabled"),
        ("Session timeout (min)", config.SESSION_TIMEOUT_MINUTES), ("Max login attempts", config.MAX_LOGIN_ATTEMPTS),
        ("Lockout (min)", config.LOCKOUT_MINUTES), ("Pass percentage", config.PASS_PERCENTAGE),
        ("Copy-detection threshold (%)", config.PLAGIARISM_THRESHOLD), ("Low-confidence threshold", config.LOW_CONFIDENCE_THRESHOLD)],
        columns=["Setting", "Value"]).astype(str), hide_index=True)
    st.subheader("Backup")
    st.download_button("⬇ Download database backup", reports.export_database(admin), file_name="evaluation_backup.db",
                       mime="application/octet-stream", key="ad_backup")


PAGES = {"Dashboard": page_dashboard, "Manage Teachers": page_teachers, "Manage Students": page_students,
         "Manage Classes": page_classes, "Manage Subjects": page_subjects, "Manage Examinations": page_exams,
         "System Analytics": page_analytics, "Database Reports": page_database, "User Management": page_users,
         "Settings": page_settings}


def render(user):
    choice = st.sidebar.radio("Navigation", list(PAGES), key="nav_admin")
    ui.show_flash()
    PAGES[choice](user)
