"""Public seed guard + ORM behavior: in-memory SQLite only, no provider calls."""

from datetime import date, timedelta
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from tests import test_agent  # Establish offline settings without reading backend/.env.
from app.config import settings
from app.database import Base
from app.models import Assignment, Course, Document, PlanTask, Schedule, StudyPlan, User
from app.common.holiday_config import HOLIDAY_PERIODS, MAKEUP_WORKDAYS
from app.utils import semester
from app.utils.password import verify_password

SCAFFOLD = Path(__file__).resolve().parents[2]
if str(SCAFFOLD) not in sys.path:
    sys.path.insert(0, str(SCAFFOLD))
from deploy.demo import seed


STUDENT_PASSWORD = "offline-student-password"
ADMIN_PASSWORD = "offline-private-admin-password"


class PublicDemoTargetGuardTests(unittest.TestCase):
    def setUp(self):
        self.environ = {"DEMO_DEPLOYMENT": "true", "DEMO_SEED_TARGET": "isolated-compose",
                        "DATABASE_URL": "mysql+pymysql://demo:offline@db:3306/campus_demo"}

    def test_only_expected_compose_target_is_accepted(self):
        seed.assert_isolated_target(self.environ, docker_marker=True)

    def test_container_marker_and_both_opt_in_flags_are_required(self):
        for change in ({"DEMO_DEPLOYMENT": "false"}, {"DEMO_SEED_TARGET": "local"}):
            with self.subTest(change=change), self.assertRaises(seed.DemoSeedError):
                seed.assert_isolated_target({**self.environ, **change}, docker_marker=True)
        with self.assertRaises(seed.DemoSeedError):
            seed.assert_isolated_target(self.environ, docker_marker=False)

    def test_local_or_unexpected_database_is_rejected_without_url_leak(self):
        for url in ("mysql+pymysql://root:private@127.0.0.1/campus_demo",
                    "mysql+pymysql://root:private@db/campus_course",
                    "mysql://root:private@db/campus_demo",
                    "mysql+pymysql://root:private@db:8000/campus_demo",
                    "sqlite://", "invalid-secret-private"):
            with self.subTest(url=url), self.assertRaises(seed.DemoSeedError) as result:
                seed.assert_isolated_target({**self.environ, "DATABASE_URL": url}, docker_marker=True)
            self.assertNotIn("private", str(result.exception))

    def test_query_options_cannot_override_the_connection_target(self):
        for query in ("host=127.0.0.1", "database=campus_course",
                      "unix_socket=/tmp/mysql.sock", "charset=utf8mb4"):
            with self.subTest(query=query), self.assertRaises(seed.DemoSeedError):
                seed.assert_isolated_target(
                    {**self.environ, "DATABASE_URL": self.environ["DATABASE_URL"] + "?" + query},
                    docker_marker=True)

    def test_shared_entry_guard_reads_current_process_environment(self):
        with patch.dict("os.environ", self.environ, clear=True), \
                patch.object(seed.Path, "is_file", return_value=True):
            seed.require_demo_target()

    def test_main_rejects_before_import_or_connect(self):
        with patch.dict("os.environ", {"DEMO_DEPLOYMENT": "false"}, clear=True), \
                patch("builtins.print") as output, patch("app.database.Base.metadata.create_all") as create:
            self.assertEqual(seed.main(), 1)
        create.assert_not_called()
        self.assertIn("拒绝初始化", output.call_args.args[0])

    def test_short_identical_and_invalid_passwords_are_rejected(self):
        for student, admin in (("123", ADMIN_PASSWORD), (STUDENT_PASSWORD, "admin"),
                               (STUDENT_PASSWORD, STUDENT_PASSWORD),
                               ("字" * 25, ADMIN_PASSWORD), ("\ud800" * 12, ADMIN_PASSWORD)):
            with self.subTest(student_length=len(student)), self.assertRaises(seed.DemoSeedError):
                seed.validate_passwords(student, admin)


class PublicDemoSeedTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        event.listen(self.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, autoflush=False)
        self.addCleanup(self.engine.dispose)
        self.today = date(2026, 10, 11)

    def run_seed(self, *, today=None, student_password=STUDENT_PASSWORD, admin_password=ADMIN_PASSWORD):
        with self.sessions() as db, db.begin():
            return seed.seed_records(db, student_password=student_password,
                                     admin_password=admin_password, today=today or self.today)

    def test_seed_creates_only_virtual_accounts_and_courses_with_orm_timestamps(self):
        counts = self.run_seed()
        self.assertEqual(counts["users"], 3)
        self.assertEqual(counts["courses"], 9)
        with self.sessions() as db:
            self.assertEqual({row.username for row in db.query(User)},
                             {seed.DEMO_ADMIN, *seed.DEMO_STUDENTS})
            self.assertTrue(all("虚构演示" in row.name for row in db.query(Course)))
            for model in (User, Course, Schedule, Assignment, PlanTask):
                self.assertTrue(all(row.create_time is not None and row.update_time is not None
                                    for row in db.query(model)))
            self.assertEqual(db.query(StudyPlan).count(), 0)
            self.assertEqual(db.query(Document).count(), 0)

    def test_seed_is_idempotent_and_never_changes_existing_passwords_or_due_dates(self):
        first = self.run_seed()
        with self.sessions() as db:
            hashes = {row.username: row.password for row in db.query(User)}
            due = {row.id: row.due_at for row in db.query(Assignment)}
            ids = {row.id for row in db.query(Schedule)}
        second = self.run_seed(today=self.today + timedelta(days=45),
                               student_password="another-student-password",
                               admin_password="another-private-admin-password")
        self.assertEqual(first, second)
        with self.sessions() as db:
            self.assertEqual(hashes, {row.username: row.password for row in db.query(User)})
            self.assertEqual(due, {row.id: row.due_at for row in db.query(Assignment)})
            self.assertEqual(ids, {row.id for row in db.query(Schedule)})
            self.assertTrue(verify_password(STUDENT_PASSWORD, hashes[seed.DEMO_STUDENTS[0]]))

    def test_no_existing_text_plan_or_unrelated_account_is_modified(self):
        with self.sessions() as db, db.begin():
            other = User(username="untouched", name="untouched", role="student", password="fixture")
            db.add(other)
            db.flush()
            db.add(StudyPlan(user_id=other.id, content="Keep the original text plan."))
        self.run_seed()
        with self.sessions() as db:
            self.assertEqual(db.query(StudyPlan).one().content, "Keep the original text plan.")
            self.assertEqual(db.query(User).filter(User.username == "untouched").one().password, "fixture")

    def test_conflicting_account_role_rolls_back_whole_seed(self):
        with self.sessions() as db, db.begin():
            db.add(User(username=seed.DEMO_STUDENTS[0], name="Existing", role="admin", password="fixture"))
        with self.assertRaises(seed.DemoSeedError):
            self.run_seed()
        with self.sessions() as db:
            self.assertEqual(db.query(User).count(), 1)
            self.assertEqual(db.query(Course).count(), 0)
            self.assertEqual(db.query(Schedule).count(), 0)

    def test_course_name_conflict_rolls_back_without_replacing_course(self):
        with self.sessions() as db, db.begin():
            db.add(Course(name=seed.COURSES[0][1], code="existing-code"))
        with self.assertRaises(seed.DemoSeedError):
            self.run_seed()
        with self.sessions() as db:
            self.assertEqual(db.query(User).count(), 0)
            self.assertEqual(db.query(Course).one().code, "existing-code")

    def test_all_twenty_weeks_have_data_for_both_students(self):
        self.run_seed()
        with self.sessions() as db:
            for student in db.query(User).filter(User.role == "student"):
                rows = db.query(Schedule).filter(Schedule.user_id == student.id).all()
                self.assertEqual({semester.date_to_week(row.class_date) for row in rows},
                                 set(range(1, settings.TOTAL_WEEKS + 1)))
                self.assertTrue(all(row.start_week <= semester.date_to_week(row.class_date) <= row.end_week
                                    for row in rows))

    def test_holiday_skip_and_actual_weekend_makeup_days_are_observed(self):
        self.run_seed()
        with self.sessions() as db:
            all_rows = db.query(Schedule).all()
            self.assertFalse(any(holiday.start_date <= row.class_date <= holiday.end_date
                                 for holiday in HOLIDAY_PERIODS for row in all_rows))
            for student in db.query(User).filter(User.role == "student"):
                dates = {row.class_date for row in all_rows if row.user_id == student.id}
                self.assertTrue(MAKEUP_WORKDAYS <= dates)
                self.assertFalse(any(day.weekday() > 4 and day not in MAKEUP_WORKDAYS for day in dates))

    def test_independent_fixed_grids_have_no_conflicts_or_shared_cells(self):
        self.run_seed()
        with self.sessions() as db:
            grids = {}
            for student in db.query(User).filter(User.role == "student"):
                rows = db.query(Schedule).filter(Schedule.user_id == student.id).all()
                cells = {(row.class_date, row.period) for row in rows}
                self.assertEqual(len(cells), len(rows))
                assignments = {}
                for row in rows:
                    assignments.setdefault(row.course_id, set()).add((row.class_date.weekday(), row.period))
                self.assertTrue(all(len(slots) == 1 for slots in assignments.values()))
                grids[student.username] = cells
            self.assertEqual(grids[seed.DEMO_STUDENTS[0]] & grids[seed.DEMO_STUDENTS[1]], set())
            self.assertLess(len(grids[seed.DEMO_STUDENTS[1]]), len(grids[seed.DEMO_STUDENTS[0]]))

    def test_learning_items_include_overdue_upcoming_completed_and_holiday(self):
        self.run_seed()
        with self.sessions() as db:
            assignments = db.query(Assignment).all()
            self.assertTrue(any(row.status == "pending" and row.due_at.date() < self.today for row in assignments))
            self.assertTrue(any(row.status == "pending" and 0 <= (row.due_at.date() - self.today).days <= 3
                                for row in assignments))
            self.assertTrue(any(row.status == "done" for row in assignments))
            self.assertTrue(any(holiday.start_date <= row.due_at.date() <= holiday.end_date
                                for row in assignments for holiday in HOLIDAY_PERIODS))
            tasks = db.query(PlanTask).all()
            self.assertTrue(any(row.defer_count == 2 for row in tasks))
            self.assertTrue(any(row.status == "done" and row.due_at is None for row in tasks))

    def test_document_upload_disabled_or_missing_key_never_calls_provider_or_adds_record(self):
        self.run_seed()
        upload = Mock(side_effect=AssertionError("must not call provider"))
        with self.sessions() as db:
            for enabled, key in ((False, True), (True, False), (False, False)):
                result = seed.index_sample_documents(db, enabled=enabled,
                                                     embedding_key_present=key, upload=upload)
                self.assertTrue(result["skipped"])
                self.assertEqual(result["indexed"], 0)
            self.assertEqual(db.query(Document).count(), 0)
        upload.assert_not_called()

    def test_sample_docs_are_batched_through_upload_service_and_second_run_skips(self):
        self.run_seed()
        uploads_seen = []
        def upload(db, admin, files):
            uploads_seen.append([item.filename for item in files])
            for item in files:
                content = item.file.read().decode("utf-8")
                self.assertIn("虚构", content)
                db.add(Document(filename=item.filename, storage_path="offline", extracted_text=content,
                                chunk_count=1, status="indexed", uploader_id=admin.id))
            db.commit()
        with self.sessions() as db:
            result = seed.index_sample_documents(db, enabled=True, embedding_key_present=True, upload=upload)
            again = seed.index_sample_documents(db, enabled=True, embedding_key_present=True, upload=upload)
            self.assertEqual(result["indexed"], 3)
            self.assertEqual(again["indexed"], 3)
            self.assertEqual(len(uploads_seen), 1)
            self.assertEqual(len(uploads_seen[0]), 3)
            self.assertEqual(db.query(Document).count(), 3)

    def test_existing_failed_document_is_not_overwritten(self):
        self.run_seed()
        with self.sessions() as db:
            admin = db.query(User).filter(User.username == seed.DEMO_ADMIN).one()
            name = sorted(seed.SAMPLE_DOCS_DIR.glob("*.md"))[0].name
            db.add(Document(filename=name, storage_path="keep", extracted_text="Keep", chunk_count=0,
                            status="failed", uploader_id=admin.id))
            db.commit()
            upload = Mock()
            with self.assertRaises(seed.DemoSeedError):
                seed.index_sample_documents(db, enabled=True, embedding_key_present=True, upload=upload)
            upload.assert_not_called()
            self.assertEqual(db.query(Document).one().extracted_text, "Keep")


if __name__ == "__main__":
    unittest.main()
