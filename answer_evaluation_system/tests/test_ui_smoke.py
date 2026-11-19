"""Executes every page of every dashboard against a real (temporary) database using a stub Streamlit.

This does not test visual rendering, but it proves that all UI code paths call valid backend functions with valid
arguments, handle empty states, and complete the main click-through actions.
"""
import importlib
import sys
import unittest
from datetime import date, time

from tests import streamlit_stub
from tests.base import DBTestCase, GOOD, IRRELEVANT, PW

STUB = streamlit_stub.install()
for name in [m for m in sys.modules if m == "app" or m.startswith("frontend")]:
    del sys.modules[name]
from frontend import admin, authentication, components, student, teacher  # noqa: E402
from backend import (evaluation_service as ev, report_service as reports, submission_service as subs,  # noqa: E402
                     user_service as users)
from database import repository as repo  # noqa: E402


def call(fn, *args):
    STUB.seen.clear()
    try:
        fn(*args)
    except streamlit_stub.StopApp:
        pass
    assert not STUB.duplicates, f"Duplicate Streamlit widget IDs in {getattr(fn, '__name__', fn)}: {STUB.duplicates}"


class UISmoke(DBTestCase):
    def setUp(self):
        super().setUp()
        STUB.reset()
        self.w = self.make_world()
        self.admin_u = users.get_user(self.w["admin"]["id"])
        self.teacher_u = users.get_user(self.w["teacher"]["id"])
        self.student_u = users.get_user(self.w["students"][0]["id"])

    def populate(self):
        exam_id = self.make_exam(self.w)
        qs = [q["id"] for q in subs.open_exam_for_student(self.w["students"][0], exam_id)["questions"]]
        subs.save_answers(self.w["students"][0], exam_id, {qs[0]: GOOD, qs[1]: "Learning from labelled data."}, submit=True)
        subs.save_answers(self.w["students"][1], exam_id, {qs[0]: IRRELEVANT}, submit=True)
        self.w["teacher"]["exam"] = exam_id
        return exam_id

    def render_all(self, module, user):
        for title, page in module.PAGES.items():
            STUB.errors.clear()
            call(page, user)
            self.assertEqual(STUB.errors, [], f"{module.__name__}:{title} showed errors {STUB.errors}")

    # ------------------------------------------------------------------ empty states
    def test_all_pages_render_with_no_exam_data(self):
        for module, user in ((admin, self.admin_u), (teacher, self.teacher_u), (student, self.student_u)):
            self.render_all(module, user)

    # ------------------------------------------------------------------ populated, every stage
    def test_all_pages_render_with_live_data(self):
        exam_id = self.populate()
        self.render_all(student, self.student_u)
        self.render_all(teacher, self.teacher_u)
        ev.evaluate_exam(self.w["teacher"], exam_id)
        self.render_all(teacher, self.teacher_u)             # evaluation cards, reports, export
        ev.bulk_approve(self.w["teacher"], exam_id, include_flagged=True)
        self.render_all(teacher, self.teacher_u)
        self.render_all(student, self.student_u)             # approved results & analytics
        self.render_all(admin, self.admin_u)

    # ------------------------------------------------------------------ click-through flows
    def test_teacher_creates_exam_through_ui(self):
        STUB.values = {"Examination title": "UI Exam", "Question": "Define AI?", "Reference (model) answer": "AI simulates human intelligence.",
                       "Maximum marks": 4.0, "Date": date.today(), "Start time": time(0, 0), "Duration (minutes)": 1440}
        STUB.pressed = {"Save examination"}
        call(teacher.page_create_exam, self.teacher_u)
        row = repo.fetch_one("SELECT * FROM examinations WHERE title='UI Exam'")
        self.assertIsNotNone(row)
        self.assertEqual(row["total_marks"], 8.0)            # default 2 questions x 4 marks
        self.assertEqual(STUB.errors, [])

    def test_student_saves_and_submits_through_ui(self):
        exam_id = self.populate()
        STUB.session_state.clear()
        user = users.get_user(self.w["students"][2]["id"])
        STUB.values = {"Your answer": GOOD}
        STUB.pressed = {"Save draft"}
        call(student.page_submit, user)
        self.assertEqual(repo.fetch_one("SELECT status FROM submissions WHERE student_id=?", (user["id"],))["status"], "in_progress")
        STUB.pressed, STUB.checked = {"Submit final answers"}, True
        call(student.page_submit, user)
        self.assertEqual(repo.fetch_one("SELECT status FROM submissions WHERE student_id=?", (user["id"],))["status"], "submitted")
        self.assertEqual(STUB.errors, [])

    def test_submit_without_confirmation_is_blocked(self):
        self.populate()
        user = users.get_user(self.w["students"][2]["id"])
        STUB.values, STUB.pressed = {"Your answer": GOOD}, {"Submit final answers"}
        call(student.page_submit, user)
        self.assertTrue(any("confirmation" in e for e in STUB.errors))
        self.assertIsNone(repo.fetch_one("SELECT id FROM submissions WHERE student_id=?", (user["id"],)))

    def test_teacher_evaluates_and_approves_through_ui(self):
        exam_id = self.populate()
        STUB.pressed = {"Run AI evaluation"}
        call(teacher.page_evaluation, self.teacher_u)
        self.assertEqual(repo.fetch_one("SELECT COUNT(*) n FROM evaluations")["n"], 4)
        self.assertEqual(repo.fetch_one("SELECT COUNT(*) n FROM evaluations WHERE approval_status='approved'")["n"], 0)
        STUB.pressed = {"Approve AI marks"}
        call(teacher.page_evaluation, self.teacher_u)
        self.assertGreater(repo.fetch_one("SELECT COUNT(*) n FROM evaluations WHERE approval_status='approved'")["n"], 0)
        self.assertEqual(STUB.errors, [])
        call(student.page_results, self.student_u)
        self.assertEqual(STUB.errors, [])

    def test_admin_actions_through_ui(self):
        STUB.values = {"Name": "UI Teacher", "Email": "uit@x.edu", "Temporary password": "Secret123"}
        STUB.pressed = {"Create teacher"}
        call(admin.page_teachers, self.admin_u)
        self.assertEqual(repo.fetch_one("SELECT role FROM users WHERE email='uit@x.edu'")["role"], "teacher")
        STUB.pressed = {"Reset password"}
        call(admin.page_users, self.admin_u)
        self.assertEqual(STUB.errors, [])

    def test_registration_and_login_screens(self):
        STUB.values = {"Full name": "New Kid", "Email": "kid@x.edu", "Registration number": "KID001", "Password": "KidPass123"}
        STUB.pressed = {"Register"}
        call(authentication.render_auth)
        self.assertIsNotNone(repo.fetch_one("SELECT id FROM users WHERE email='kid@x.edu'"))
        STUB.pressed, STUB.values = {"Sign in"}, {"Email": "kid@x.edu", "Password": "KidPass123"}
        call(authentication.render_auth)
        self.assertEqual(STUB.session_state["user"]["email"], "kid@x.edu")

    def test_wrong_login_shows_error_not_crash(self):
        STUB.pressed, STUB.values = {"Sign in"}, {"Email": "s1@x.edu", "Password": "nope-nope1"}
        call(authentication.render_auth)
        self.assertEqual(STUB.errors, ["Invalid email or password."])
        self.assertNotIn("user", STUB.session_state)

    def test_app_routes_by_role_and_timeout(self):
        import app  # noqa: F401  (runs main with the stub)
        for role_user in (self.admin_u, self.teacher_u, self.student_u):
            STUB.session_state.clear()
            STUB.session_state.update({"user": role_user, "last_active": __import__("time").time()})
            call(app.main)
            self.assertEqual(STUB.errors, [])
        STUB.session_state.update({"user": self.student_u, "last_active": 0})
        call(app.main)
        self.assertNotIn("user", STUB.session_state)           # expired session logged out

    def test_first_run_setup_screen(self):
        repo_conn = __import__("database.connection", fromlist=["x"])
        with repo_conn.transaction() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DELETE FROM users WHERE role='admin'")
        STUB.values = {"Administrator name": "Root", "Email": "root@x.edu", "Password": "RootPass123", "Confirm password": "RootPass123"}
        STUB.pressed = {"Create administrator"}
        call(authentication.render_auth)
        self.assertTrue(users.admin_exists())

    def test_excel_download_data_is_real(self):
        exam_id = self.populate()
        ev.evaluate_exam(self.w["teacher"], exam_id)
        ev.bulk_approve(self.w["teacher"], exam_id, include_flagged=True)
        data = reports.export_excel(self.w["teacher"], [exam_id])
        self.assertTrue(data.startswith(b"PK"))               # a genuine xlsx (zip) container
        self.assertGreater(len(reports.export_database(self.w["admin"])), 1000)


if __name__ == "__main__":
    unittest.main()
