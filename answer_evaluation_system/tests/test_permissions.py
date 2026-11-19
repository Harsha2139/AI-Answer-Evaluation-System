import unittest

from backend import (evaluation_service as ev, examination_service as exams, report_service as reports,
                     submission_service as subs, user_service as users)
from tests.base import DBTestCase, GOOD, PW
from utils.helpers import ConflictError, PermissionDenied, ValidationError


class TestPermissions(DBTestCase):
    def setUp(self):
        super().setUp()
        self.w = self.make_world()
        self.exam = self.make_exam(self.w)
        self.s1, self.s2, self.s3 = self.w["students"]
        self.other_teacher = {"id": users.create_user(self.w["admin"], "Teacher Two", "t2@x.edu", PW, "teacher"), "role": "teacher"}

    def test_only_admin_manages_users(self):
        for actor in (self.w["teacher"], self.s1):
            with self.assertRaises(PermissionDenied):
                users.create_user(actor, "X Person", "x@x.edu", PW, "teacher")
            with self.assertRaises(PermissionDenied):
                users.list_users(actor)
            with self.assertRaises(PermissionDenied):
                users.create_class(actor, "Hack", "Z")

    def test_other_teacher_cannot_touch_exam(self):
        t2 = self.other_teacher
        with self.assertRaises(PermissionDenied):
            exams.get_exam_with_questions(t2, self.exam)
        with self.assertRaises(PermissionDenied):
            exams.set_status(t2, self.exam, "closed")
        with self.assertRaises(PermissionDenied):
            ev.evaluate_exam(t2, self.exam)
        with self.assertRaises(PermissionDenied):
            reports.export_excel(t2, [self.exam])

    def test_unassigned_teacher_cannot_create_exam(self):
        with self.assertRaises(PermissionDenied):
            exams.create_exam(self.other_teacher, "Nope", self.w["class"], self.w["subject"], "2030-01-01 10:00", 30,
                              [{"question_text": "Q", "reference_answer": "A", "maximum_marks": 1}])

    def test_students_cannot_create_exam_or_evaluate(self):
        with self.assertRaises(PermissionDenied):
            exams.create_exam(self.s1, "Nope", self.w["class"], self.w["subject"], "2030-01-01 10:00", 30, [])
        with self.assertRaises(PermissionDenied):
            ev.evaluate_exam(self.s1, self.exam)
        with self.assertRaises(PermissionDenied):
            ev.review_evaluation(self.s1, 1, "approve")

    def test_student_not_enrolled_cannot_open_exam(self):
        c2 = users.create_class(self.w["admin"], "Class 11", "B")
        outsider = {"id": users.create_user(self.w["admin"], "Out Sider", "o@x.edu", PW, "student", "OUT001", c2), "role": "student"}
        with self.assertRaises(PermissionDenied):
            subs.open_exam_for_student(outsider, self.exam)
        with self.assertRaises(PermissionDenied):
            subs.save_answers(outsider, self.exam, {1: "x"})
        self.assertEqual(exams.list_exams_for_student(outsider), [])

    def test_draft_exam_invisible_to_students(self):
        draft = self.make_exam(self.w, publish=False)
        self.assertNotIn(draft, [e["id"] for e in exams.list_exams_for_student(self.s1)])
        with self.assertRaises(PermissionDenied):
            subs.open_exam_for_student(self.s1, draft)

    def test_student_cannot_see_reference_answers(self):
        data = subs.open_exam_for_student(self.s1, self.exam)
        for q in data["questions"]:
            self.assertNotIn("reference_answer", q)
            self.assertNotIn("expected_keywords", q)

    def test_student_cannot_view_another_students_submission(self):
        qid = subs.open_exam_for_student(self.s1, self.exam)["questions"][0]["id"]
        sub = subs.save_answers(self.s1, self.exam, {qid: GOOD}, submit=True)
        with self.assertRaises(PermissionDenied):
            subs.get_submission_detail(self.s2, sub["id"])
        self.assertEqual(subs.get_submission_detail(self.s1, sub["id"])["student_id"], self.s1["id"])

    def test_answer_for_foreign_question_rejected(self):
        other_exam = self.make_exam(self.w)
        foreign_q = subs.open_exam_for_student(self.s1, other_exam)["questions"][0]["id"]
        with self.assertRaises(ValidationError):
            subs.save_answers(self.s1, self.exam, {foreign_q: "sneaky"})

    def test_deadline_and_duplicate_submission(self):
        past = self.make_exam(self.w, start_offset_min=-200, duration=30)
        qid = subs.open_exam_for_student(self.s1, past)["questions"][0]["id"]
        with self.assertRaises(ConflictError):
            subs.save_answers(self.s1, past, {qid: GOOD})        # deadline passed
        future = self.make_exam(self.w, start_offset_min=500)
        qid2 = subs.open_exam_for_student(self.s1, future)["questions"][0]["id"]
        with self.assertRaises(ConflictError):
            subs.save_answers(self.s1, future, {qid2: GOOD})     # not open yet
        qid3 = subs.open_exam_for_student(self.s1, self.exam)["questions"][0]["id"]
        subs.save_answers(self.s1, self.exam, {qid3: GOOD}, submit=True)
        with self.assertRaises(ConflictError):
            subs.save_answers(self.s1, self.exam, {qid3: "changed"})  # already submitted

    def test_exam_validation(self):
        base = dict(title="T", class_id=self.w["class"], subject_id=self.w["subject"], exam_date="2030-01-01 10:00", duration=30)
        good_q = {"question_text": "Q", "reference_answer": "A", "maximum_marks": 2}
        t = self.w["teacher"]
        for bad in ({**good_q, "maximum_marks": 0}, {**good_q, "maximum_marks": -1}, {**good_q, "question_text": " "},
                    {**good_q, "reference_answer": ""}):
            with self.assertRaises(ValidationError):
                exams.create_exam(t, questions=[bad], **base)
        with self.assertRaises(ValidationError):
            exams.create_exam(t, questions=[good_q], total_marks=99, **base)   # total != sum
        with self.assertRaises(ValidationError):
            exams.create_exam(t, questions=[], **base)
        self.assertTrue(exams.create_exam(t, questions=[good_q, good_q], total_marks=4, **base))

    def test_questions_locked_after_submissions(self):
        qid = subs.open_exam_for_student(self.s1, self.exam)["questions"][0]["id"]
        subs.save_answers(self.s1, self.exam, {qid: GOOD})
        with self.assertRaises(ConflictError):
            exams.update_question(self.w["teacher"], qid, {"question_text": "New", "reference_answer": "x", "maximum_marks": 1})
        with self.assertRaises(ConflictError):
            exams.delete_exam(self.w["teacher"], self.exam)

    def test_admin_cannot_deactivate_self(self):
        with self.assertRaises(ValidationError):
            users.set_user_active(self.w["admin"], self.w["admin"]["id"], False)

    def test_inactive_user_flag_blocks_login_only_via_auth(self):
        users.set_user_active(self.w["admin"], self.s1["id"], False)
        from backend import auth_service
        from utils.helpers import AuthenticationError
        with self.assertRaises(AuthenticationError):
            auth_service.login("s1@x.edu", PW)


if __name__ == "__main__":
    unittest.main()
