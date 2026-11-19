"""Users, classes, subjects, teacher assignments and enrolments (admin-managed)."""
import sqlite3

from database import repository as repo
from database.connection import transaction
from utils.helpers import ConflictError, NotFound, PermissionDenied, ValidationError, now_str
from utils.security import generate_password, hash_password
from utils.validators import (clean_text, require_text, validate_email, validate_name, validate_password,
                              validate_registration_number)

PUBLIC_COLUMNS = ("id, name, email, role, registration_number, class_id, is_active, created_at, last_login")


def require_role(actor, *roles):
    if not actor or actor.get("role") not in roles:
        raise PermissionDenied("You do not have permission to perform this action.")


def public_user(row):
    return {k: v for k, v in row.items() if k not in ("password_hash", "failed_attempts", "locked_until")} if row else None


# ----------------------------------------------------------------- user records
def create_user_record(conn, name, email, password, role, registration_number=None, class_id=None, is_active=True):
    name, email, password = validate_name(name), validate_email(email), validate_password(password)
    if role == "student":
        registration_number = validate_registration_number(registration_number)
    else:
        registration_number = None
    try:
        uid = repo.insert("users", {
            "name": name, "email": email, "password_hash": hash_password(password), "role": role,
            "registration_number": registration_number, "class_id": class_id if role == "student" else None,
            "is_active": 1 if is_active else 0, "created_at": now_str()}, conn)
    except sqlite3.IntegrityError as exc:
        text = str(exc).lower()
        if "registration_number" in text:
            raise ConflictError("That registration number is already registered.")
        if "email" in text:
            raise ConflictError("That email address is already registered.")
        raise ValidationError("Invalid class or user data.")
    if role == "student" and class_id:
        sync_student_enrollments(conn, uid)
    return uid


def create_user(actor, name, email, password, role, registration_number=None, class_id=None):
    require_role(actor, "admin")
    if role not in ("admin", "teacher", "student"):
        raise ValidationError("Invalid role.")
    with transaction() as conn:
        return create_user_record(conn, name, email, password, role, registration_number, class_id)


def list_users(actor, role=None, active=None):
    require_role(actor, "admin")
    sql, params = f"SELECT {PUBLIC_COLUMNS} FROM users WHERE 1=1", []
    if role:
        sql += " AND role = ?"; params.append(role)
    if active is not None:
        sql += " AND is_active = ?"; params.append(1 if active else 0)
    return repo.fetch_all(sql + " ORDER BY name", params)


def get_user(user_id):
    return repo.fetch_one(f"SELECT {PUBLIC_COLUMNS} FROM users WHERE id = ?", (user_id,))


def set_user_active(actor, user_id, active: bool):
    require_role(actor, "admin")
    if user_id == actor["id"] and not active:
        raise ValidationError("You cannot deactivate your own account.")
    with transaction() as conn:
        if not repo.fetch_one("SELECT id FROM users WHERE id=?", (user_id,), conn):
            raise NotFound("User not found.")
        repo.update("users", user_id, {"is_active": 1 if active else 0, "failed_attempts": 0, "locked_until": None}, conn)


def set_user_role(actor, user_id, role):
    require_role(actor, "admin")
    if role not in ("admin", "teacher"):
        raise ValidationError("Role can only be changed between admin and teacher. Create a student account instead.")
    if user_id == actor["id"]:
        raise ValidationError("You cannot change your own role.")
    with transaction() as conn:
        user = repo.fetch_one("SELECT role FROM users WHERE id=?", (user_id,), conn)
        if not user:
            raise NotFound("User not found.")
        if user["role"] == "student":
            raise ValidationError("Student accounts cannot be converted; create a new account.")
        repo.update("users", user_id, {"role": role}, conn)


def update_profile(actor, user_id, name=None, class_id=None):
    if actor["id"] != user_id and actor["role"] != "admin":
        raise PermissionDenied("You can only edit your own profile.")
    data = {}
    if name is not None:
        data["name"] = validate_name(name)
    with transaction() as conn:
        user = repo.fetch_one("SELECT role FROM users WHERE id=?", (user_id,), conn)
        if not user:
            raise NotFound("User not found.")
        if class_id is not None and actor["role"] == "admin" and user["role"] == "student":
            data["class_id"] = class_id or None
        repo.update("users", user_id, data, conn)
        if "class_id" in data and data["class_id"]:
            sync_student_enrollments(conn, user_id)


def reset_password(actor, user_id):
    """Admin resets a password; returns the one-time temporary password."""
    require_role(actor, "admin")
    temp = generate_password()
    with transaction() as conn:
        if not repo.fetch_one("SELECT id FROM users WHERE id=?", (user_id,), conn):
            raise NotFound("User not found.")
        repo.update("users", user_id, {"password_hash": hash_password(temp), "failed_attempts": 0, "locked_until": None}, conn)
    return temp


def admin_exists() -> bool:
    return repo.fetch_one("SELECT id FROM users WHERE role='admin' AND is_active=1 LIMIT 1") is not None


def create_first_admin(name, email, password):
    """Bootstrap: only allowed while no admin exists."""
    with transaction() as conn:
        if repo.fetch_one("SELECT id FROM users WHERE role='admin' LIMIT 1", (), conn):
            raise PermissionDenied("An administrator already exists.")
        return create_user_record(conn, name, email, password, "admin")


# ----------------------------------------------------------------- classes / subjects
def create_class(actor, class_name, section=""):
    require_role(actor, "admin")
    class_name, section = require_text(class_name, "Class name", 60), clean_text(section, 20)
    with transaction() as conn:
        try:
            return repo.insert("classes", {"class_name": class_name, "section": section}, conn)
        except sqlite3.IntegrityError:
            raise ConflictError("That class and section already exist.")


def list_classes():
    return repo.fetch_all("""SELECT c.id, c.class_name, c.section,
        TRIM(c.class_name || ' ' || c.section) AS label,
        (SELECT COUNT(*) FROM users u WHERE u.class_id = c.id AND u.role='student') AS students
        FROM classes c ORDER BY c.class_name, c.section""")


def delete_class(actor, class_id):
    require_role(actor, "admin")
    with transaction() as conn:
        if repo.fetch_one("SELECT id FROM examinations WHERE class_id=? LIMIT 1", (class_id,), conn):
            raise ConflictError("This class has examinations and cannot be deleted.")
        repo.delete("classes", class_id, conn)


def create_subject(actor, subject_name, subject_code):
    require_role(actor, "admin")
    subject_name, subject_code = require_text(subject_name, "Subject name", 100), require_text(subject_code, "Subject code", 20).upper()
    with transaction() as conn:
        try:
            return repo.insert("subjects", {"subject_name": subject_name, "subject_code": subject_code}, conn)
        except sqlite3.IntegrityError:
            raise ConflictError("That subject code already exists.")


def list_subjects():
    return repo.fetch_all("SELECT id, subject_name, subject_code, subject_name || ' (' || subject_code || ')' AS label "
                          "FROM subjects ORDER BY subject_name")


def delete_subject(actor, subject_id):
    require_role(actor, "admin")
    with transaction() as conn:
        if repo.fetch_one("SELECT id FROM examinations WHERE subject_id=? LIMIT 1", (subject_id,), conn):
            raise ConflictError("This subject has examinations and cannot be deleted.")
        repo.delete("subjects", subject_id, conn)


# ----------------------------------------------------------------- assignments & enrolments
def sync_student_enrollments(conn, student_id):
    """Enrol the student in every subject currently assigned to their class."""
    student = repo.fetch_one("SELECT class_id FROM users WHERE id=? AND role='student'", (student_id,), conn)
    if not student or not student["class_id"]:
        return
    conn.execute("""INSERT OR IGNORE INTO student_enrollments(student_id, class_id, subject_id)
                    SELECT ?, class_id, subject_id FROM teacher_assignments WHERE class_id = ?""",
                 (student_id, student["class_id"]))


def assign_teacher(actor, teacher_id, class_id, subject_id):
    require_role(actor, "admin")
    with transaction() as conn:
        teacher = repo.fetch_one("SELECT role FROM users WHERE id=?", (teacher_id,), conn)
        if not teacher or teacher["role"] != "teacher":
            raise ValidationError("Selected user is not a teacher.")
        try:
            repo.insert("teacher_assignments", {"teacher_id": teacher_id, "class_id": class_id, "subject_id": subject_id}, conn)
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise ConflictError("This assignment already exists.")
            raise ValidationError("Invalid class or subject.")
        for s in repo.fetch_all("SELECT id FROM users WHERE role='student' AND class_id=?", (class_id,), conn):
            sync_student_enrollments(conn, s["id"])


def remove_assignment(actor, assignment_id):
    require_role(actor, "admin")
    with transaction() as conn:
        a = repo.fetch_one("SELECT * FROM teacher_assignments WHERE id=?", (assignment_id,), conn)
        if not a:
            raise NotFound("Assignment not found.")
        if repo.fetch_one("SELECT id FROM examinations WHERE teacher_id=? AND class_id=? AND subject_id=? LIMIT 1",
                          (a["teacher_id"], a["class_id"], a["subject_id"]), conn):
            raise ConflictError("Examinations exist for this assignment; it cannot be removed.")
        repo.delete("teacher_assignments", assignment_id, conn)


def list_assignments(teacher_id=None):
    sql = """SELECT ta.id, ta.teacher_id, t.name AS teacher, ta.class_id, TRIM(c.class_name||' '||c.section) AS class_label,
                    ta.subject_id, s.subject_name || ' (' || s.subject_code || ')' AS subject_label,
                    (SELECT COUNT(*) FROM users u WHERE u.class_id = ta.class_id AND u.role='student') AS students
             FROM teacher_assignments ta JOIN users t ON t.id=ta.teacher_id JOIN classes c ON c.id=ta.class_id
             JOIN subjects s ON s.id=ta.subject_id"""
    if teacher_id:
        return repo.fetch_all(sql + " WHERE ta.teacher_id=? ORDER BY c.class_name, s.subject_name", (teacher_id,))
    return repo.fetch_all(sql + " ORDER BY t.name, c.class_name, s.subject_name")


def enroll_student(actor, student_id, class_id, subject_id):
    require_role(actor, "admin")
    with transaction() as conn:
        try:
            conn.execute("INSERT OR IGNORE INTO student_enrollments(student_id, class_id, subject_id) VALUES (?,?,?)",
                         (student_id, class_id, subject_id))
        except sqlite3.IntegrityError:
            raise ValidationError("Invalid student, class or subject.")


def list_class_students(actor, class_id, subject_id=None):
    if actor["role"] == "teacher":
        if not repo.fetch_one("SELECT id FROM teacher_assignments WHERE teacher_id=? AND class_id=?", (actor["id"], class_id)):
            raise PermissionDenied("This class is not assigned to you.")
    elif actor["role"] != "admin":
        raise PermissionDenied("Not allowed.")
    if subject_id:
        return repo.fetch_all("""SELECT u.id, u.name, u.email, u.registration_number FROM users u
            JOIN student_enrollments se ON se.student_id=u.id WHERE se.class_id=? AND se.subject_id=? ORDER BY u.name""",
                              (class_id, subject_id))
    return repo.fetch_all("SELECT id, name, email, registration_number FROM users WHERE role='student' AND class_id=? ORDER BY name",
                          (class_id,))
