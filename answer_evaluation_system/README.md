# AI-Based Automatic Student Answer Evaluation System

A multi-role (Admin / Teacher / Student) web application that suggests marks and feedback for descriptive answers using
NLP, while **keeping the teacher in control**: nothing is visible to a student until a teacher approves it.

## Honest description of the "AI"

The scoring engine (`ml/evaluator.py`, engine id `baseline-nlp-tfidf-v1`) is a **transparent NLP baseline**, not a trained
supervised model, and it has **not** been validated against human grading. It computes real signals from the text:

| Signal | How it is computed |
|---|---|
| Similarity | TF-IDF cosine on stemmed words (+ character n-gram cosine for spelling tolerance), best over reference + alternate answers; calibrated so a raw similarity of `SIMILARITY_FULL_CREDIT` (0.65) earns full credit. Optional sentence-transformers embeddings are blended in when enabled. |
| Keyword coverage | Teacher keywords (`term\|synonym` syntax), fuzzy stem matching; auto-extracted from the reference if none given |
| Concept coverage | Each expected concept is matched by stem coverage, with a best-sentence TF-IDF fallback for paraphrases |
| Completeness | Answer length relative to the reference (a weak signal, 10% weight) |

`Final = w1*Similarity + w2*Keyword + w3*Concept + w4*Completeness` (defaults 0.35 / 0.25 / 0.30 / 0.10, normalised; override per exam).

**Safeguards:** empty answers → 0; irrelevant answers → 0; negation mismatch → capped at 50% and flagged; keyword-list answers flagged;
a confidence score (agreement between signals) flags low-confidence answers; copy detection between students (≥85% similar, ≥8 content words).

**Limitations:** similarity is not correctness; a wrong answer reusing the question's words can score well and a correct answer phrased very
differently can score low; negation/number facts are checked only heuristically; English only. Always review before approving.

## Features

* **Admin:** manage teachers (with approval of self-registrations), students, classes, subjects, teacher assignments, enrolments, all exams, analytics, read-only database browser, user activation / role / password reset, backup download.
* **Teacher:** create exams (questions, reference answers, marks, keywords, concepts, alternate answers, scoring weights), publish/close, view submissions, allow resubmission, bulk AI evaluation, review cards (approve / edit / reject / re-evaluate / feedback), bulk-approve unflagged, copy-detection report, analytics (distribution, difficulty, missed keywords, ranking), Excel (3 sheets) and CSV export.
* **Student:** registration, see only exams for their class + subject, draft saving, final submission with deadline enforcement, history, **approved** results only, feedback, analytics.
* **Integrity & security:** scrypt password hashing, role-based authorisation in every service, ownership checks, parameterised SQL, login lockout, session timeout, live account-status check, audit trail of every mark change, atomic transactions, students never receive reference answers.

## Project layout

```
app.py  config.py  requirements.txt  .env.example
database/   connection.py (transactions, migrations)  repository.py  models.py  seed.py  migrations/001_initial_schema.sql
backend/    auth_service  user_service  examination_service  submission_service  evaluation_service  report_service
ml/         preprocessing  similarity  evaluator  feedback  model_loader
frontend/   authentication  admin  teacher  student  components
utils/      security  validators  helpers  excel_export
tests/      test_auth  test_database  test_evaluation  test_permissions  test_workflow  test_ui_smoke
```

## Installation (Windows PowerShell)

```powershell
cd answer_evaluation_system
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process RemoteSigned
pip install -r requirements.txt
Copy-Item .env.example .env           # optional: edit settings
streamlit run app.py
```

Open http://localhost:8501. On first start the app shows a **First-time setup** page where you create the administrator
(no default credentials exist). Alternatively from the command line:

```powershell
$env:ADMIN_PASSWORD = "ChooseAStrongPassw0rd"
python -m database.seed --admin-email admin@school.edu --admin-name "Administrator"
# Optional demo structure (class, subject, teacher, 3 students - no answers or marks are fabricated):
$env:DEMO_PASSWORD = "DemoPassw0rd"; python -m database.seed --demo
```

## Typical workflow

1. Admin: create a class and a subject, create/approve a teacher, assign the teacher to class + subject. Students register (choosing a class) and are enrolled automatically in that class's assigned subjects.
2. Teacher: *Create Examination* → *Manage Questions* → **Publish**.
3. Student: *Submit Answers* inside the exam window → *Submit final answers*.
4. Teacher: *AI Evaluation* → **Run AI evaluation** → review each answer → approve / edit / reject → *Evaluation Reports* → *Excel Export*.
5. Student: *My Results* shows only approved marks and feedback.

## Testing

```powershell
python -m unittest discover -s tests -t . -v
```
(`pip install pytest` and `pytest` also works.) The suite covers authentication and lockout, the database schema and constraints, the NLP engine
(paraphrase, spelling, empty, irrelevant, negation, weights), role permissions, deadlines and duplicate submissions, the full 15-step workflow
(admin → teacher → student → AI → approval → student results → Excel with real rows), and an execution smoke test of every dashboard page
(`tests/test_ui_smoke.py` uses a stub Streamlit so it runs without a browser; it does not check visual layout).

## Deployment

* **Single server / PC (recommended for schools):** run `streamlit run app.py --server.port 8501 --server.address 0.0.0.0` behind a reverse proxy (nginx/IIS) with HTTPS. Set `DATABASE_PATH` to a persistent folder and back it up (Admin → Settings → backup, or copy the file while the app is idle).
* **Streamlit Community Cloud or containers:** the filesystem may be ephemeral - mount a persistent volume for `DATABASE_PATH` and `MODEL_DIR`, otherwise data is lost on restart.
* **Production hardening:** always use HTTPS; set strong admin password; keep `.env` out of version control; restrict network access; restart policy and regular backups.
* **PostgreSQL:** not implemented. All SQL is isolated in the services and `database/repository.py` (SQLite dialect), so a port would mean swapping the connection layer and migrating `001_initial_schema.sql`.

## Optional: semantic embeddings

`pip install sentence-transformers`, set `USE_SENTENCE_TRANSFORMERS=true`. The model loads once and is reused; if it cannot load the app silently falls back to TF-IDF only.
Embeddings improve paraphrase handling but are still not proof of correctness.

## Not implemented

OCR / handwritten answers, multilingual evaluation, LMS integration, PDF export, SQLAlchemy/PostgreSQL, bcrypt (scrypt from the standard library is used instead).
