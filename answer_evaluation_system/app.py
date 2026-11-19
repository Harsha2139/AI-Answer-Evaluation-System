"""Entry point:  streamlit run app.py"""
import time

import streamlit as st

import config
from backend import user_service
from database.connection import run_migrations
from frontend import admin, authentication, student, teacher
from frontend import components as ui

st.set_page_config(page_title=config.APP_NAME, page_icon="🎓", layout="wide")

ROLE_MODULES = {"admin": admin, "teacher": teacher, "student": student}


@st.cache_resource
def _init_database():
    run_migrations()
    return True


def _logout(message=None):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    if message:
        ui.flash(message, "warning")
    st.rerun()


def _current_user():
    """Return the signed-in user, enforcing session timeout and live account status."""
    user = st.session_state.get("user")
    if not user:
        return None
    if time.time() - st.session_state.get("last_active", 0) > config.SESSION_TIMEOUT_MINUTES * 60:
        _logout("Your session expired. Please sign in again.")
    fresh = user_service.get_user(user["id"])
    if not fresh or not fresh["is_active"]:
        _logout("Your account is no longer active.")
    st.session_state["user"], st.session_state["last_active"] = fresh, time.time()
    return fresh


def main():
    _init_database()
    ui.inject_css()
    user = _current_user()
    if not user:
        authentication.render_auth()
        return
    with st.sidebar:
        st.markdown(f"### 🎓 {config.APP_NAME}")
        st.markdown(f"**{user['name']}**  \n{user['role'].title()}")
        st.divider()
    ROLE_MODULES[user["role"]].render(user)
    with st.sidebar:
        st.divider()
        if st.button("Sign out", key="logout"):
            _logout()


main()
