"""Shared test fixtures: every test class gets its own throw-away database file."""
import os
import sys
import tempfile
import unittest
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from backend import auth_service, evaluation_service, examination_service, submission_service, user_service  # noqa: E402
from database import connection  # noqa: E402
from utils.helpers import fmt_dt, now  # noqa: E402

PW = "Passw0rd!"
REF = ("Machine Learning is a branch of artificial intelligence that allows computers to learn patterns "
       "from data and make predictions.")
GOOD = "Machine Learning helps computers learn from data and predict results without manually programming every rule."
IRRELEVANT = "The capital of France is Paris and it has the Eiffel Tower."


class DBTestCase(unittest.TestCase):
    def setUp(self):  # a fresh database per test keeps tests independent
        self._tmp = tempfile.TemporaryDirectory()
        connection.configure(os.path.join(self._tmp.name, "test.db"))
        config.MODEL_DIR = type(config.MODEL_DIR)(self._tmp.name) / "models"
        connection.run_migrations()

    def tearDown(self):
        self._tmp.cleanup()

    # -- helpers ------------------------------------------------------------
    def make_world(self):
        """admin, class, subject, teacher (assigned), 3 students; returns a dict of actors/ids."""
        admin_id = user_service.create_first_admin("Admin", "admin@x.edu", PW)
        admin = {"id": admin_id, "role": "admin"}
        w = {"admin": admin}
        w["class"] = user_service.create_class(admin, "Class 10", "A")
        w["subject"] = user_service.create_subject(admin, "Computer Science", "CS101")
        tid = user_service.create_user(admin, "Teacher One", "t1@x.edu", PW, "teacher")
        w["teacher"] = {"id": tid, "role": "teacher"}
        user_service.assign_teacher(admin, tid, w["class"], w["subject"])
        w["students"] = []
        for i, nm in enumerate(["Anu", "Bala", "Chitra"], start=1):
            sid = user_service.create_user(admin, nm, f"s{i}@x.edu", PW, "student", f"REG{i:03d}", w["class"])
            w["students"].append({"id": sid, "role": "student", "name": nm})
        return w

    def make_exam(self, w, publish=True, start_offset_min=-5, duration=60, qs=None):
        qs = qs or [
            {"question_text": "What is Machine Learning?", "reference_answer": REF, "maximum_marks": 5,
             "expected_keywords": "machine learning, artificial intelligence|AI, data, patterns, predictions",
             "expected_concepts": "branch of artificial intelligence\ncomputers learn patterns from data\nmake predictions"},
            {"question_text": "What is supervised learning?", "maximum_marks": 5,
             "reference_answer": "Supervised learning trains a model on labelled data so it can predict outputs for new inputs."},
        ]
        start = now() + timedelta(minutes=start_offset_min)
        return examination_service.create_exam(w["teacher"], "Unit Test 1", w["class"], w["subject"], fmt_dt(start),
                                               duration, qs, publish=publish)
