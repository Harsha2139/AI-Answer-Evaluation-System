import sqlite3
import unittest

from tests.base import DBTestCase
from database import connection, models, repository as repo


class TestDatabase(DBTestCase):
    def test_schema_complete(self):
        conn = connection.connect()
        try:
            self.assertEqual(models.verify_schema(conn), [])
        finally:
            conn.close()

    def test_migrations_idempotent(self):
        self.assertEqual(connection.run_migrations(), [])

    def test_foreign_keys_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with connection.transaction() as conn:
                repo.insert("student_enrollments", {"student_id": 999, "class_id": 999, "subject_id": 999}, conn)

    def test_transaction_rolls_back(self):
        try:
            with connection.transaction() as conn:
                repo.insert("classes", {"class_name": "Temp", "section": "Z"}, conn)
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertIsNone(repo.fetch_one("SELECT id FROM classes WHERE class_name='Temp'"))

    def test_check_constraints(self):
        w = self.make_world()
        with self.assertRaises(sqlite3.IntegrityError):
            with connection.transaction() as conn:
                repo.insert("examinations", {"title": "x", "class_id": w["class"], "subject_id": w["subject"],
                                             "teacher_id": w["teacher"]["id"], "exam_date": "2030-01-01 10:00:00",
                                             "duration": 0, "status": "draft"}, conn)

    def test_unsafe_identifier_rejected(self):
        with self.assertRaises(ValueError):
            with connection.transaction() as conn:
                repo.insert("classes; DROP TABLE users", {"a": 1}, conn)


if __name__ == "__main__":
    unittest.main()
