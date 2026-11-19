-- Initial schema for the AI Answer Evaluation System
CREATE TABLE classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_name TEXT NOT NULL,
    section TEXT NOT NULL DEFAULT '',
    UNIQUE (class_name, section)
);

CREATE TABLE subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_name TEXT NOT NULL,
    subject_code TEXT NOT NULL UNIQUE
);

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'teacher', 'student')),
    registration_number TEXT UNIQUE,
    class_id INTEGER REFERENCES classes(id) ON DELETE SET NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    last_login TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (role <> 'student' OR registration_number IS NOT NULL)
);
CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_class ON users(class_id);

CREATE TABLE student_enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    UNIQUE (student_id, class_id, subject_id)
);
CREATE INDEX idx_enroll_student ON student_enrollments(student_id);

CREATE TABLE teacher_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    UNIQUE (teacher_id, class_id, subject_id)
);
CREATE INDEX idx_assign_teacher ON teacher_assignments(teacher_id);

CREATE TABLE examinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    teacher_id INTEGER NOT NULL REFERENCES users(id),
    exam_date TEXT NOT NULL,
    duration INTEGER NOT NULL CHECK (duration > 0),
    total_marks REAL NOT NULL DEFAULT 0 CHECK (total_marks >= 0),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'closed')),
    scoring_weights TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX idx_exam_teacher ON examinations(teacher_id);
CREATE INDEX idx_exam_class_subject ON examinations(class_id, subject_id);

CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    examination_id INTEGER NOT NULL REFERENCES examinations(id) ON DELETE CASCADE,
    question_number INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    reference_answer TEXT NOT NULL,
    maximum_marks REAL NOT NULL CHECK (maximum_marks > 0),
    expected_keywords TEXT NOT NULL DEFAULT '[]',
    expected_concepts TEXT NOT NULL DEFAULT '[]',
    alternate_answers TEXT NOT NULL DEFAULT '[]',
    UNIQUE (examination_id, question_number)
);

CREATE TABLE submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    examination_id INTEGER NOT NULL REFERENCES examinations(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    submitted_at TEXT,
    status TEXT NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress', 'submitted', 'reopened')),
    UNIQUE (examination_id, student_id)
);
CREATE INDEX idx_sub_student ON submissions(student_id);

CREATE TABLE student_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    answer_text TEXT NOT NULL DEFAULT '',
    saved_at TEXT NOT NULL,
    UNIQUE (submission_id, question_id)
);

CREATE TABLE evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_answer_id INTEGER NOT NULL UNIQUE REFERENCES student_answers(id) ON DELETE CASCADE,
    similarity_score REAL NOT NULL DEFAULT 0,
    keyword_coverage REAL NOT NULL DEFAULT 0,
    concept_coverage REAL NOT NULL DEFAULT 0,
    completeness_score REAL NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    needs_review INTEGER NOT NULL DEFAULT 0 CHECK (needs_review IN (0, 1)),
    plagiarism_score REAL NOT NULL DEFAULT 0,
    suggested_marks REAL NOT NULL CHECK (suggested_marks >= 0),
    final_marks REAL CHECK (final_marks IS NULL OR final_marks >= 0),
    ai_feedback TEXT NOT NULL DEFAULT '',
    teacher_feedback TEXT NOT NULL DEFAULT '',
    approval_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (approval_status IN ('pending', 'approved', 'rejected', 'outdated')),
    approved_by INTEGER REFERENCES users(id),
    approved_at TEXT,
    evaluated_by INTEGER REFERENCES users(id),
    evaluated_at TEXT NOT NULL,
    engine TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_eval_status ON evaluations(approval_status);

CREATE TABLE evaluation_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    old_marks REAL,
    new_marks REAL,
    changed_by INTEGER REFERENCES users(id),
    reason TEXT NOT NULL DEFAULT '',
    changed_at TEXT NOT NULL
);
CREATE INDEX idx_audit_eval ON evaluation_audit(evaluation_id);
