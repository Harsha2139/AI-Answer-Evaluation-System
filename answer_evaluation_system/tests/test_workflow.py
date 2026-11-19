"""End-to-end test of the complete workflow described in the project brief."""
import io
import unittest

from openpyxl import load_workbook

from backend import (auth_service, evaluation_service as ev, examination_service as exams, report_service as reports,
                     submission_service as subs, user_service as users)
from database import repository as repo
from tests.base import DBTestCase, GOOD, IRRELEVANT, PW, REF
from utils.helpers import ConflictError


class TestFullWorkflow(DBTestCase):
    def test_complete_workflow(self):
        # 1-2. admin creates accounts and assigns the teacher
        w = self.make_world()
        teacher = auth_service.login("t1@x.edu", PW)
        anu, bala, chitra = [auth_service.login(f"s{i}@x.edu", PW) for i in (1, 2, 3)]
        self.assertEqual(len(users.list_assignments(teacher["id"])), 1)
        # 3-4. teacher creates exam with questions
        exam_id = self.make_exam(w)
        detail = exams.get_exam_with_questions(teacher, exam_id)
        self.assertEqual(detail["total_marks"], 10)
        q1, q2 = [q["id"] for q in detail["questions"]]
        # 5-6. students see the assigned exam
        self.assertEqual([e["id"] for e in exams.list_exams_for_student(anu)], [exam_id])
        # 7-8. students answer; answers persisted
        subs.save_answers(anu, exam_id, {q1: GOOD}, submit=False)                    # draft
        self.assertEqual(subs.open_exam_for_student(anu, exam_id)["answers"][q1], GOOD)
        subs.save_answers(anu, exam_id, {q1: GOOD, q2: "It trains a model with labelled data to predict outputs."}, submit=True)
        subs.save_answers(bala, exam_id, {q1: IRRELEVANT}, submit=True)               # q2 left blank
        # chitra copies anu's answer word for word (plagiarism check) -- but only after submitting
        subs.save_answers(chitra, exam_id, {q1: GOOD, q2: "It trains a model with labelled data to predict outputs."}, submit=True)
        self.assertEqual(repo.fetch_one("SELECT COUNT(*) n FROM student_answers")["n"], 6)  # blank rows created on submit
        # 9. teacher opens submissions
        rows = {r["name"]: r for r in subs.list_exam_submissions(teacher, exam_id)}
        self.assertEqual({r["status"] for r in rows.values()}, {"submitted"})
        # nothing visible to students before evaluation/approval
        self.assertEqual(ev.get_student_results(anu), [])
        # 10-11. teacher runs AI evaluation
        summary = ev.evaluate_exam(teacher, exam_id)
        self.assertEqual(summary["evaluated"], 6)
        self.assertGreaterEqual(summary["plagiarism_flagged"], 2)   # Anu & Chitra identical
        self.assertEqual(ev.evaluate_exam(teacher, exam_id)["evaluated"], 0)  # idempotent
        evals = ev.list_evaluations(teacher, exam_id)
        self.assertTrue(all(e["approval_status"] == "pending" and e["final_marks"] is None for e in evals))
        self.assertEqual(ev.get_student_results(anu), [])           # AI never publishes
        by = {(e["student_name"], e["question_number"]): e for e in evals}
        self.assertEqual(by[("Bala", 1)]["suggested_marks"], 0.0)
        self.assertEqual(by[("Bala", 2)]["suggested_marks"], 0.0)   # blank answer
        self.assertGreater(by[("Anu", 1)]["suggested_marks"], 2.0)
        self.assertGreater(by[("Anu", 1)]["suggested_marks"], by[("Bala", 1)]["suggested_marks"])
        self.assertGreaterEqual(by[("Anu", 1)]["plagiarism_score"], 85)
        self.assertTrue(by[("Chitra", 1)]["needs_review"])
        self.assertEqual(len(ev.get_plagiarism_pairs(teacher, exam_id)), 1)
        # 12-13. teacher reviews: approve, edit with reason, reject
        ev.review_evaluation(teacher, by[("Anu", 1)]["evaluation_id"], "approve")
        with self.assertRaises(Exception):
            ev.review_evaluation(teacher, by[("Anu", 2)]["evaluation_id"], "edit", marks=99, reason="x")
        with self.assertRaises(Exception):                                  # change without reason
            ev.review_evaluation(teacher, by[("Anu", 2)]["evaluation_id"], "edit", marks=5)
        ev.review_evaluation(teacher, by[("Anu", 2)]["evaluation_id"], "edit", marks=5,
                             teacher_feedback="Excellent, full marks.", reason="Fully correct")
        ev.review_evaluation(teacher, by[("Chitra", 1)]["evaluation_id"], "reject", reason="Copied")
        ev.bulk_approve(teacher, exam_id)
        audit = ev.get_audit_history(teacher, by[("Anu", 2)]["evaluation_id"])
        self.assertEqual([a["action"] for a in audit], ["ai_suggested", "edited"])
        self.assertEqual(audit[-1]["new_marks"], 5.0)
        stored = repo.fetch_one("SELECT * FROM evaluations WHERE id=?", (by[("Anu", 2)]["evaluation_id"],))
        self.assertEqual(stored["final_marks"], 5.0)
        self.assertEqual(stored["approved_by"], teacher["id"])
        self.assertLess(stored["suggested_marks"], 5.0)                     # teacher override differs from the AI
        # 14. student sees only approved results
        anu_results = ev.get_student_results(anu)
        self.assertEqual(len(anu_results), 2)
        self.assertEqual({r["question_number"]: r["final_marks"] for r in anu_results}[2], 5.0)
        # Chitra's Q1 was rejected (hidden). Her short Q2 is below the 8-word copy-check minimum, so bulk-approved.
        self.assertEqual([r["question_number"] for r in ev.get_student_results(chitra)], [2])
        bala_results = ev.get_student_results(bala)
        self.assertEqual([r["final_marks"] for r in bala_results], [0.0, 0.0])
        dash = reports.student_dashboard(anu)
        self.assertEqual((dash["total_exams"], dash["completed"], dash["pending"]), (1, 1, 0))
        self.assertGreater(dash["average_pct"], 0)
        # dashboards come from live data
        td = reports.teacher_dashboard(teacher)
        self.assertEqual((td["total_students"], td["total_exams"], td["submitted_answers"]), (3, 1, 6))
        self.assertEqual(td["pending_approvals"], 0)
        # 15. Excel contains real database rows
        data = reports.export_excel(teacher, [exam_id])
        wb = load_workbook(io.BytesIO(data))
        self.assertEqual(wb.sheetnames, ["Student Results", "Question-wise Analysis", "Class Performance"])
        sheet = wb["Student Results"]
        header = [c.value for c in sheet[1]]
        self.assertIn("Teacher Approved Marks", header)
        names = {r[0] for r in sheet.iter_rows(min_row=2, values_only=True)}
        self.assertEqual(names, {"Anu", "Bala", "Chitra"})
        self.assertEqual(sheet.max_row, 7)                                  # header + 6 answer rows
        anu_rows = [r for r in sheet.iter_rows(min_row=2, values_only=True) if r[0] == "Anu"]
        self.assertEqual(sorted(r[7] for r in anu_rows if r[7] is not None)[-1], 5.0)
        self.assertEqual(wb["Question-wise Analysis"].max_row, 3)

    def test_resubmission_invalidates_old_evaluation(self):
        w = self.make_world()
        t, s = w["teacher"], w["students"][0]
        exam_id = self.make_exam(w)
        q1 = subs.open_exam_for_student(s, exam_id)["questions"][0]["id"]
        sub = subs.save_answers(s, exam_id, {q1: IRRELEVANT}, submit=True)
        ev.evaluate_exam(t, exam_id)
        ev.bulk_approve(t, exam_id, include_flagged=True)
        with self.assertRaises(ConflictError):
            subs.save_answers(s, exam_id, {q1: GOOD})
        subs.reopen_submission(t, sub["id"], "Wrong file")
        subs.save_answers(s, exam_id, {q1: GOOD}, submit=True)
        e = ev.list_evaluations(t, exam_id)
        self.assertIn("outdated", {x["approval_status"] for x in e})
        visible = [r["question_number"] for r in ev.get_student_results(s)]
        self.assertEqual(visible, [2])                                      # changed answer's old marks are hidden
        with self.assertRaises(ConflictError):
            ev.review_evaluation(t, e[0]["evaluation_id"], "approve")
        self.assertEqual(ev.evaluate_exam(t, exam_id)["evaluated"], 1)      # only the changed answer is re-evaluated
        new = {x["question_number"]: x for x in ev.list_evaluations(t, exam_id)}
        self.assertGreater(new[1]["suggested_marks"], 0)

    def test_reevaluate_single_answer_resets_approval(self):
        w = self.make_world()
        t, s = w["teacher"], w["students"][0]
        exam_id = self.make_exam(w)
        q1 = subs.open_exam_for_student(s, exam_id)["questions"][0]["id"]
        subs.save_answers(s, exam_id, {q1: GOOD}, submit=True)
        ev.evaluate_exam(t, exam_id)
        e = ev.list_evaluations(t, exam_id)[0]
        ev.review_evaluation(t, e["evaluation_id"], "approve")
        ev.reevaluate_answer(t, e["answer_id"])
        again = [x for x in ev.list_evaluations(t, exam_id) if x["answer_id"] == e["answer_id"]][0]
        self.assertEqual((again["approval_status"], again["final_marks"]), ("pending", None))
        actions = [a["action"] for a in ev.get_audit_history(t, e["evaluation_id"])]
        self.assertEqual(actions, ["ai_suggested", "approved", "re-evaluated"])

    def test_teacher_exam_weights_change_scores(self):
        w = self.make_world()
        t, s = w["teacher"], w["students"][0]
        exam_id = self.make_exam(w)
        q1 = subs.open_exam_for_student(s, exam_id)["questions"][0]["id"]
        subs.save_answers(s, exam_id, {q1: GOOD}, submit=True)
        ev.evaluate_exam(t, exam_id)
        first = ev.list_evaluations(t, exam_id)[0]
        exams.update_exam(t, exam_id, weights={"similarity": 0, "keyword": 0, "concept": 0, "completeness": 1})
        ev.reevaluate_answer(t, first["answer_id"])
        second = [x for x in ev.list_evaluations(t, exam_id) if x["answer_id"] == first["answer_id"]][0]
        self.assertEqual(second["suggested_marks"], 5.0)   # completeness-only weighting
        self.assertNotEqual(first["suggested_marks"], second["suggested_marks"])


if __name__ == "__main__":
    unittest.main()
