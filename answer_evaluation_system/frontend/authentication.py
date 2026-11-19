"""Sign-in, registration and first-run administrator setup."""
import time

import streamlit as st

from backend import auth_service, user_service
from frontend import components as ui


def _login_user(user):
    st.session_state["user"] = user
    st.session_state["last_active"] = time.time()
    st.rerun()


def render_setup():
    ui.page_header("First-time setup", "Create the administrator account for this installation.")
    with st.form("setup_admin"):
        name = st.text_input("Administrator name")
        email = st.text_input("Email")
        pw = st.text_input("Password", type="password", help="At least 8 characters, letters and digits.")
        pw2 = st.text_input("Confirm password", type="password")
        if st.form_submit_button("Create administrator"):
            if pw != pw2:
                st.error("Passwords do not match.")
            else:
                ok, _ = ui.run(user_service.create_first_admin, name, email, pw)
                if ok:
                    ui.flash("Administrator created. Please sign in.")
                    st.rerun()


def render_auth():
    ui.show_flash()
    if not user_service.admin_exists():
        render_setup()
        return
    ui.page_header("AI Answer Evaluation System", "NLP-assisted evaluation of descriptive answers - teacher approved.")
    tab_in, tab_student, tab_teacher = st.tabs(["Sign in", "Student registration", "Teacher registration"])
    with tab_in:
        with st.form("login"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Sign in"):
                with st.spinner("Signing in…"):
                    ok, user = ui.run(auth_service.login, email, password)
                if ok:
                    _login_user(user)
    with tab_student:
        classes = user_service.list_classes()
        labels = {c["label"]: c["id"] for c in classes}
        with st.form("register_student"):
            name = st.text_input("Full name")
            email = st.text_input("Email", key="rs_email")
            reg = st.text_input("Registration number")
            class_label = st.selectbox("Class", list(labels) or ["(no classes available yet)"])
            pw = st.text_input("Password", type="password", key="rs_pw", help="At least 8 characters, letters and digits.")
            if st.form_submit_button("Register"):
                ok, _ = ui.run(auth_service.register_student, name, email, reg, pw, labels.get(class_label))
                if ok:
                    st.success("Registration successful. You can now sign in.")
    with tab_teacher:
        st.info("Teacher accounts must be approved by an administrator before you can sign in.")
        with st.form("register_teacher"):
            name = st.text_input("Full name", key="rt_name")
            email = st.text_input("Email", key="rt_email")
            pw = st.text_input("Password", type="password", key="rt_pw")
            if st.form_submit_button("Request teacher account"):
                ok, _ = ui.run(auth_service.register_teacher, name, email, pw)
                if ok:
                    st.success("Request submitted. An administrator will review it.")
