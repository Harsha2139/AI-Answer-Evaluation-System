"""Create the first administrator (and optionally demo structure).

Usage (PowerShell):
    python -m database.seed --admin-email admin@school.edu --admin-name "Admin"
    python -m database.seed --demo
Passwords are read from the ADMIN_PASSWORD / DEMO_PASSWORD environment variables or prompted for,
so no credential is ever stored in source code.
"""
import argparse
import getpass
import os
import sys

from backend import auth_service, user_service
from database.connection import run_migrations
from utils.helpers import AppError


def _password(env_name: str, prompt: str) -> str:
    return os.getenv(env_name) or getpass.getpass(prompt)


def seed_admin(name, email, password):
    return user_service.create_first_admin(name, email, password)


def seed_demo(admin, teacher_password, student_password):
    """Demo classes/subjects/accounts only (no marks, answers or results are fabricated)."""
    c = user_service.create_class(admin, "Class 10", "A")
    s1 = user_service.create_subject(admin, "Computer Science", "CS101")
    t = user_service.create_user(admin, "Demo Teacher", "teacher@demo.edu", teacher_password, "teacher")
    user_service.assign_teacher(admin, t, c, s1)
    for i, name in enumerate(["Anitha Kumar", "Bala Murugan", "Charles Daniel"], start=1):
        user_service.create_user(admin, name, f"student{i}@demo.edu", student_password, "student", f"DEMO{i:03d}", c)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-name", default="Administrator")
    parser.add_argument("--admin-email")
    parser.add_argument("--demo", action="store_true", help="also create demo class, subject, teacher and students")
    args = parser.parse_args(argv)
    run_migrations()
    try:
        admin = None
        if args.admin_email:
            seed_admin(args.admin_name, args.admin_email, _password("ADMIN_PASSWORD", "Admin password: "))
            print(f"Administrator {args.admin_email} created.")
        if args.demo:
            from database.repository import fetch_one
            row = fetch_one("SELECT id, role FROM users WHERE role='admin' ORDER BY id LIMIT 1")
            if not row:
                sys.exit("Create an administrator first (--admin-email).")
            admin = {"id": row["id"], "role": "admin"}
            demo_pw = _password("DEMO_PASSWORD", "Password for demo teacher/students: ")
            seed_demo(admin, demo_pw, demo_pw)
            print("Demo data created: teacher@demo.edu, student1@demo.edu ... student3@demo.edu")
    except AppError as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
