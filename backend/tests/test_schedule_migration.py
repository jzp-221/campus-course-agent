"""P8 新增列和迁移兼容性：只使用内存 SQLite，不读取真实 .env。"""

from datetime import date
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic_settings import BaseSettings
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scripts.migrate_schedule_weeks import migrate_schedule_weeks, schedule_structure


_original_settings_init = BaseSettings.__init__


def _test_settings_init(self, *args, **kwargs):
    kwargs["_env_file"] = None
    _original_settings_init(self, *args, **kwargs)


with patch.dict(
    os.environ,
    {
        "DATABASE_URL": "sqlite://",
        "JWT_SECRET_KEY": "schedule-migration-tests-only-secret-never-use-in-production",
    },
), patch.object(BaseSettings, "__init__", _test_settings_init):
    from app.database import Base
    from app.models.schedule import Schedule


LEGACY_DDL = """
CREATE TABLE schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    class_date DATE NOT NULL,
    period VARCHAR(50),
    course_id INTEGER NOT NULL REFERENCES coruses(id),
    location VARCHAR(100),
    create_time DATETIME NOT NULL,
    update_time DATETIME NOT NULL
)
"""


class ScheduleMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE coruses (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO users VALUES (1)"))
            connection.execute(text("INSERT INTO coruses VALUES (1)"))
            connection.execute(text(LEGACY_DDL))
            connection.execute(text("""
                INSERT INTO schedules
                (id, user_id, class_date, period, course_id, location, create_time, update_time)
                VALUES (77, 1, '2026-10-14', '1-2节', 1, '教学楼 201',
                        '2026-10-10 12:00:00', '2026-10-10 13:00:00')
            """))

    def test_adds_all_columns_with_explicit_database_defaults(self):
        self.assertEqual(
            migrate_schedule_weeks(self.engine), ["start_week", "end_week", "teacher"]
        )
        columns = {column["name"]: column for column in schedule_structure(self.engine)}
        self.assertFalse(columns["start_week"]["nullable"])
        self.assertFalse(columns["end_week"]["nullable"])
        self.assertTrue(columns["teacher"]["nullable"])
        self.assertEqual(columns["start_week"]["default"], "1")
        self.assertEqual(columns["end_week"]["default"], "20")
        self.assertEqual(columns["teacher"]["default"], "NULL")

    def test_old_row_and_timestamps_survive_with_week_defaults(self):
        with self.engine.connect() as connection:
            before = tuple(connection.execute(text("SELECT * FROM schedules")).one())
        migrate_schedule_weeks(self.engine)
        with self.engine.connect() as connection:
            after = tuple(connection.execute(text("SELECT * FROM schedules")).one())
        self.assertEqual(after[:-3], before)
        self.assertEqual(after[-3:], (1, 20, None))

    def test_second_execution_is_a_noop_and_keeps_explicit_values(self):
        migrate_schedule_weeks(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("""
                UPDATE schedules SET start_week=3, end_week=8, teacher='李明'
                WHERE id=77
            """))
        self.assertEqual(migrate_schedule_weeks(self.engine), [])
        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT start_week, end_week, teacher FROM schedules")
            ).one()
        self.assertEqual(tuple(row), (3, 8, "李明"))

    def test_partial_migration_resumes_without_overwriting_existing_column(self):
        with self.engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE schedules ADD COLUMN start_week INTEGER NOT NULL DEFAULT 1"
            ))
            connection.execute(text("UPDATE schedules SET start_week=9 WHERE id=77"))
        self.assertEqual(migrate_schedule_weeks(self.engine), ["end_week", "teacher"])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("""
                SELECT start_week, end_week, teacher FROM schedules WHERE id=77
            """)).one(), (9, 20, None))

    def test_raw_insert_without_new_columns_uses_database_defaults(self):
        migrate_schedule_weeks(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO schedules
                (user_id, course_id, class_date, period, create_time, update_time)
                VALUES (1, 1, '2026-10-15', '3-4节',
                        '2026-10-10 12:00:00', '2026-10-10 12:00:00')
            """))
            row = connection.execute(text("""
                SELECT start_week, end_week, teacher FROM schedules WHERE id<>77
            """)).one()
        self.assertEqual(tuple(row), (1, 20, None))

    def test_foreign_keys_are_preserved_and_still_enforced(self):
        before = inspect(self.engine).get_foreign_keys("schedules")
        migrate_schedule_weeks(self.engine)
        self.assertEqual(inspect(self.engine).get_foreign_keys("schedules"), before)
        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO schedules
                (user_id, course_id, class_date, create_time, update_time)
                VALUES (999, 1, '2026-10-15',
                        '2026-10-10 12:00:00', '2026-10-10 12:00:00')
            """))

    def test_missing_table_fails_explicitly(self):
        missing = create_engine("sqlite://")
        self.addCleanup(missing.dispose)
        with self.assertRaisesRegex(RuntimeError, "schedules 表不存在"):
            migrate_schedule_weeks(missing)

    def test_unsupported_dialect_fails_before_running_ddl(self):
        unsupported = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        with self.assertRaisesRegex(RuntimeError, "MySQL 和 SQLite"):
            migrate_schedule_weeks(unsupported)


class ScheduleModelDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)

    def test_orm_defaults_keep_legacy_schedule_construction_usable(self):
        with Session(self.engine) as db:
            schedule = Schedule(
                user_id=1, course_id=1, class_date=date(2026, 10, 14), period="1-2节"
            )
            db.add(schedule)
            db.commit()
            self.assertEqual(schedule.start_week, 1)
            self.assertEqual(schedule.end_week, 20)
            self.assertIsNone(schedule.teacher)

    def test_new_database_schema_has_matching_server_defaults(self):
        self.assertEqual(migrate_schedule_weeks(self.engine), [])
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO schedules
                (user_id, course_id, class_date, create_time, update_time)
                VALUES (1, 1, '2026-10-14',
                        '2026-10-10 12:00:00', '2026-10-10 12:00:00')
            """))
            row = connection.execute(
                text("SELECT start_week, end_week, teacher FROM schedules")
            ).one()
        self.assertEqual(tuple(row), (1, 20, None))


if __name__ == "__main__":
    unittest.main()
