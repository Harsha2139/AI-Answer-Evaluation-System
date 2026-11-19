import unittest

import config
from backend import auth_service, user_service
from database import repository as repo
from tests.base import DBTestCase, PW
from utils import security
from utils.helpers import AuthenticationError, ConflictError, PermissionDenied, ValidationError


class TestAuth(DBTestCase):
    def test_hashing_never_plaintext_and_verifies(self):
        h = security.hash_password(PW)
        self.assertNotIn(PW, h)
        self.assertTrue(security.verify_password(PW, h))
        self.assertFalse(security.verify_password("wrong", h))
        self.assertNotEqual(h, security.hash_password(PW))  # unique salt

    def test_password_stored_hashed(self):
        sid = auth_service.register_student("Hash Test", "hash@x.edu", "HASH001", PW)
        row = repo.fetch_one("SELECT password_hash FROM users WHERE id=?", (sid,))
        self.assertTrue(row["password_hash"].startswith("scrypt$"))

    def test_student_registration_and_login(self):
        auth_service.register_student("Reg Student", "reg@x.edu", "REG900", PW)
        user = auth_service.login("REG@x.edu", PW)
        self.assertEqual(user["role"], "student")
        self.assertNotIn("password_hash", user)

    def test_duplicate_registration_rejected(self):
        auth_service.register_student("Dup One", "dup@x.edu", "DUP001", PW)
        with self.assertRaises(ConflictError):
            auth_service.register_student("Dup Two", "dup@x.edu", "DUP002", PW)
        with self.assertRaises(ConflictError):
            auth_service.register_student("Dup Three", "dup3@x.edu", "DUP001", PW)

    def test_validation(self):
        with self.assertRaises(ValidationError):
            auth_service.register_student("A", "bad-email", "X1", "short")
        with self.assertRaises(ValidationError):
            auth_service.register_student("Valid Name", "ok1@x.edu", "OK0001", "onlyletters")

    def test_wrong_password_generic_error(self):
        auth_service.register_student("Gen Err", "generr@x.edu", "GEN001", PW)
        for email in ("generr@x.edu", "nobody@x.edu"):
            with self.assertRaises(AuthenticationError) as cm:
                auth_service.login(email, "wrong-pass1")
            self.assertEqual(str(cm.exception), auth_service.GENERIC_ERROR)

    def test_lockout_after_repeated_failures(self):
        auth_service.register_student("Lock Me", "lock@x.edu", "LOCK01", PW)
        for _ in range(config.MAX_LOGIN_ATTEMPTS):
            with self.assertRaises(AuthenticationError):
                auth_service.login("lock@x.edu", "bad-pass1")
        with self.assertRaises(AuthenticationError) as cm:
            auth_service.login("lock@x.edu", PW)  # correct password but locked
        self.assertIn("Too many", str(cm.exception))

    def test_teacher_registration_needs_admin_approval(self):
        w = self.make_world()
        auth_service.register_teacher("New Teacher", "newt@x.edu", PW)
        with self.assertRaises(AuthenticationError):
            auth_service.login("newt@x.edu", PW)
        tid = repo.fetch_one("SELECT id FROM users WHERE email='newt@x.edu'")["id"]
        user_service.set_user_active(w["admin"], tid, True)
        self.assertEqual(auth_service.login("newt@x.edu", PW)["role"], "teacher")

    def test_first_admin_only_once(self):
        self.make_world()
        with self.assertRaises(PermissionDenied):
            user_service.create_first_admin("Evil", "evil@x.edu", PW)

    def test_change_password(self):
        sid = auth_service.register_student("Chg Pw", "chg@x.edu", "CHG001", PW)
        actor = {"id": sid, "role": "student"}
        with self.assertRaises(AuthenticationError):
            auth_service.change_password(actor, "wrong", "NewPassw0rd")
        auth_service.change_password(actor, PW, "NewPassw0rd")
        self.assertEqual(auth_service.login("chg@x.edu", "NewPassw0rd")["id"], sid)


if __name__ == "__main__":
    unittest.main()
