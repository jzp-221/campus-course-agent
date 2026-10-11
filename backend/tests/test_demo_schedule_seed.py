"""P10 种子离线验证：内存 SQLite，不连接真实数据库、不修改演示资产。"""

from contextlib import redirect_stdout
from datetime import date, timedelta
from io import StringIO
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.common.exceptions import BusinessException
from app.database import Base
from app.models.coures import Course
from app.models.schedule import Schedule
from app.models.document import Document
from app.models.study_plan import StudyPlan
from app.models.user import User
from tests.manual import seed_demo_schedules as seed


def courses_for_planning():
    rows = []
    for index, (code, name, _) in enumerate(seed.CS_COURSES, 1):
        if not any(row.name == name for row in rows):
            rows.append(SimpleNamespace(id=index, code=code, name=name))
    return rows


class DemoSchedulePlanningTests(unittest.TestCase):
    def setUp(self):
        self.students = [SimpleNamespace(id=10, username="aaa", name="学生甲"),
                         SimpleNamespace(id=11, username="bbb", name="学生乙")]
        self.courses = courses_for_planning()

    def test_fixed_seed_and_course_order_are_reproducible(self):
        first = seed.build_schedule(self.students, self.courses)
        second = seed.build_schedule(list(reversed(self.students)), list(reversed(self.courses)))
        self.assertEqual(first, second)

    def test_twenty_two_unique_courses_use_distinct_cells(self):
        assignments = seed.assign_courses(self.courses)
        self.assertEqual(len(assignments), 22)
        self.assertEqual(len({row.course_id for row in assignments}), 22)
        self.assertEqual(len({(row.weekday, row.period) for row in assignments}), 22)
        self.assertEqual(sum(row.weekday < 5 for row in assignments), 20)
        self.assertEqual(sum(row.weekday == 5 for row in assignments), 1)
        self.assertEqual(sum(row.weekday == 6 for row in assignments), 1)
        self.assertEqual({row.period for row in assignments}, set(seed.PERIODS))

    def test_each_student_has_courses_in_all_twenty_weeks(self):
        rows = seed.build_schedule(self.students, self.courses)
        for student in self.students:
            weeks = {seed.semester.date_to_week(row["class_date"])
                     for row in rows if row["user_id"] == student.id}
            self.assertEqual(weeks, set(range(1, 21)))
        self.assertEqual(len({(row["user_id"], row["class_date"], row["period"])
                              for row in rows}), len(rows))

    def test_every_course_keeps_its_actual_weekday_and_period(self):
        rows = seed.build_schedule(self.students, self.courses)
        for student in self.students:
            for course in self.courses:
                cells = {(row["class_date"].weekday(), row["period"])
                         for row in rows if row["course_id"] == course.id
                         and row["user_id"] == student.id}
                self.assertLessEqual(len(cells), 1, (student.username, course.code))

    def test_ranges_are_varied_and_always_effective(self):
        rows = seed.build_schedule(self.students, self.courses)
        ranges = {(row["start_week"], row["end_week"]) for row in rows}
        self.assertTrue({(1, 20), (1, 16), (3, 8), (9, 16), (1, 8)} <= ranges)
        self.assertTrue(all(row["teacher"] and row["location"] for row in rows))
        self.assertTrue(all(row["start_week"] <= seed.semester.date_to_week(row["class_date"])
                            <= row["end_week"] for row in rows))

    def test_holidays_have_no_rows_and_makeup_days_have_special_courses(self):
        rows = seed.build_schedule(self.students, self.courses)
        holiday_ranges = [(date(2026, 9, 25), date(2026, 9, 27)),
                          (date(2026, 10, 1), date(2026, 10, 7)),
                          (date(2027, 1, 1), date(2027, 1, 3))]
        for first, last in holiday_ranges:
            self.assertFalse(any(first <= row["class_date"] <= last for row in rows))
        for student in self.students:
            for day, expected_count in [(date(2026, 9, 20), 1), (date(2026, 10, 10), 1)]:
                selected = [row for row in rows
                            if row["user_id"] == student.id and row["class_date"] == day]
                self.assertEqual(len(selected), expected_count)
        other_weekends = [row for row in rows if row["class_date"].weekday() >= 5
                          and row["class_date"] not in {date(2026, 9, 20), date(2026, 10, 10)}]
        self.assertEqual(other_weekends, [])

    def test_weekly_text_lists_twenty_weeks_for_each_student(self):
        output = StringIO()
        with redirect_stdout(output):
            seed.print_weekly_summary(self.students, self.courses,
                                      seed.build_schedule(self.students, self.courses))
        text = output.getvalue()
        self.assertEqual(text.count("2026-09-07 ~ 2026-09-13"), 2)
        self.assertEqual(text.count("2027-01-18 ~ 2027-01-24"), 2)
        self.assertIn("国庆节放假", text)
        self.assertIn("上课天数", text)
        self.assertIn("课节数", text)

    def test_missing_template_course_is_rejected_before_writing(self):
        with self.assertRaises(BusinessException) as caught:
            seed.build_schedule(self.students, [course for course in self.courses if course.code != "MATH101"])
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("22 门", caught.exception.message)

    def test_students_have_at_least_eighty_percent_different_cells_each_week(self):
        rows = seed.build_schedule(self.students, self.courses)
        summary = seed.weekly_difference_summary(self.students, rows)
        self.assertEqual(len(summary), 20)
        self.assertTrue(all(row["different_ratio"] >= 0.8 for row in summary))
        different = sum(row["different_cells"] for row in summary)
        compared = sum(row["compared_cells"] for row in summary)
        self.assertGreaterEqual(different / compared, 0.8)

    def test_sparse_student_has_five_courses_and_fewer_days(self):
        rows = seed.build_schedule(self.students, self.courses)
        by_name = {student.username: [row for row in rows if row["user_id"] == student.id]
                   for student in self.students}
        self.assertEqual(len({row["course_id"] for row in by_name["bbb"]}), 5)
        self.assertLess(len(by_name["bbb"]), len(by_name["aaa"]) / 2)
        for week in range(1, 21):
            selected = [row for row in by_name["bbb"]
                        if seed.semester.date_to_week(row["class_date"]) == week]
            self.assertLessEqual(len({row["class_date"] for row in selected
                                     if row["class_date"].weekday() < 5}), 3)

    def test_personal_seed_uses_student_id_and_preserves_input_rng_state(self):
        self.assertNotEqual(seed.student_random_seed(self.students[0]),
                            seed.student_random_seed(self.students[1]))
        rng = random.Random(42)
        state = rng.getstate()
        first = seed.build_schedule(self.students, self.courses, rng=rng)
        second = seed.build_schedule(list(reversed(self.students)), self.courses, rng=rng)
        self.assertEqual(first, second)
        self.assertEqual(rng.getstate(), state)

    def test_core_courses_are_available_for_same_color_cross_week_verification(self):
        student = SimpleNamespace(id=2, username="aaa", name="学生甲")
        assignments = seed.assign_courses(self.courses,
                                         random.Random(seed.student_random_seed(student)))
        for code, weekday in [("CS201", 3), ("CS303", 4)]:
            assignment = next(row for row in assignments if row.code == code)
            self.assertEqual((assignment.weekday, assignment.start_week, assignment.end_week),
                             (weekday, 1, 20))

    def test_difference_summary_counts_same_course_and_unequal_occupied_cells(self):
        monday = date(2026, 9, 7)
        rows = [{"user_id": student, "class_date": monday, "period": period, "course_id": course}
                for student, period, course in [(10, "1-2节", 1), (11, "1-2节", 1),
                                                (10, "3-4节", 2), (11, "3-4节", 3),
                                                (10, "5-6节", 4), (11, "7-8节", 5)]]
        summary = seed.weekly_difference_summary(self.students, rows)
        self.assertEqual(summary[0], {"week": 1, "start_date": monday,
                                     "end_date": date(2026, 9, 13), "aaa_count": 3,
                                     "bbb_count": 3, "same_cells": 1, "different_cells": 3,
                                     "compared_cells": 4, "different_ratio": 0.75})
        self.assertEqual(summary[1]["compared_cells"], 0)
        self.assertEqual(summary[1]["different_ratio"], 0)

    def test_difference_summary_rejects_missing_student(self):
        with self.assertRaises(BusinessException) as caught:
            seed.weekly_difference_summary(self.students[:1], [])
        self.assertEqual(caught.exception.code, 400)

    def test_summary_prints_data_version_and_difference_contract(self):
        output = StringIO()
        with redirect_stdout(output):
            seed.print_weekly_summary(self.students, self.courses,
                                      seed.build_schedule(self.students, self.courses))
        text = output.getvalue()
        self.assertIn(seed.DATA_VERSION, text)
        self.assertIn("双空格不参与计算", text)
        self.assertIn("相同格子", text)
        self.assertIn("全学期：", text)


class DemoScheduleDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def enforce_foreign_keys(dbapi_connection, _):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False)
        self.backup_folder = TemporaryDirectory()
        self.backup_root = Path(self.backup_folder.name)
        self.backup_patch = patch.object(seed, "BACKEND", self.backup_root)
        self.backup_patch.start()
        self.db = self.session_factory()
        for index, username in enumerate(seed.DEMO_USERNAMES, 1):
            self.db.add(User(id=index, username=username, name=username,
                             role="student", status=1, password="offline-placeholder"))
        self.db.commit()
        self.students = seed._students(self.db)
        self.courses, self.created = seed.ensure_courses(self.db)
        self.db.commit()
        self.wanted = seed.build_schedule(self.students, self.courses)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.backup_patch.stop()
        self.backup_folder.cleanup()

    def test_existing_courses_are_preserved_and_ensure_is_idempotent(self):
        self.assertEqual(self.created, 22)
        original = [(course.id, course.code, course.name) for course in self.courses]
        self.courses[0].description = "管理员原有简介"
        self.db.commit()
        courses, created = seed.ensure_courses(self.db)
        self.assertEqual(created, 0)
        self.assertEqual([(course.id, course.code, course.name) for course in courses], original)
        self.assertEqual(courses[0].description, "管理员原有简介")

    def test_repeated_sync_keeps_ids_and_timestamps_and_inserts_no_duplicates(self):
        first = seed.sync_schedules(self.db, self.students, self.courses, self.wanted)
        self.db.commit()
        original = [(row.id, row.create_time, row.update_time)
                    for row in self.db.query(Schedule).order_by(Schedule.id).all()]
        second = seed.sync_schedules(self.db, self.students, self.courses, self.wanted)
        self.db.commit()
        current = [(row.id, row.create_time, row.update_time)
                   for row in self.db.query(Schedule).order_by(Schedule.id).all()]
        self.assertEqual(first["created"], len(self.wanted))
        self.assertEqual(second, {"created": 0, "updated": 0, "deleted": 0,
                                  "unchanged": len(self.wanted)})
        self.assertEqual(original, current)

    def test_other_semesters_courses_and_students_are_preserved(self):
        outside = Course(code="PERSONAL101", name="非演示课程", status=1)
        third = User(username="other", name="其他学生", role="student", status=1,
                     password="offline-placeholder")
        self.db.add_all([outside, third])
        self.db.flush()
        old = Schedule(user_id=self.students[0].id, course_id=self.courses[0].id,
                       class_date=date(2026, 8, 31), period=seed.PERIODS[0])
        untouched = Schedule(user_id=self.students[0].id, course_id=outside.id,
                             class_date=date(2026, 8, 31), period=seed.PERIODS[0])
        third_row = Schedule(user_id=third.id, course_id=self.courses[0].id,
                             class_date=date(2026, 10, 8), period=seed.PERIODS[0])
        self.db.add_all([old, untouched, third_row])
        self.db.commit()
        old_id, untouched_id, third_id = old.id, untouched.id, third_row.id
        result = seed.sync_schedules(self.db, self.students, self.courses, self.wanted)
        self.db.commit()
        self.assertEqual(result["deleted"], 0)
        self.assertIsNotNone(self.db.get(Schedule, old_id))
        self.assertIsNotNone(self.db.get(Schedule, untouched_id))
        self.assertIsNotNone(self.db.get(Schedule, third_id))

    def test_unmanaged_cell_conflict_rejects_before_deleting_anything(self):
        outside = Course(code="PERSONAL101", name="非演示课程", status=1)
        self.db.add(outside)
        self.db.flush()
        desired = self.wanted[0]
        blocker = Schedule(user_id=desired["user_id"], course_id=outside.id,
                           class_date=desired["class_date"], period=desired["period"])
        self.db.add(blocker)
        self.db.commit()
        with self.assertRaises(BusinessException) as caught:
            seed.sync_schedules(self.db, self.students, self.courses, self.wanted)
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(self.db.query(Schedule).count(), 1)

    def test_clean_only_removes_current_demo_schedules_and_preserves_all_assets(self):
        seed.sync_schedules(self.db, self.students, self.courses, self.wanted)
        extra = Course(code="PERSONAL101", name="非演示课程", status=1)
        self.db.add(extra)
        self.db.flush()
        row = Schedule(user_id=self.students[0].id, course_id=extra.id,
                       class_date=date(2026, 8, 31), period=seed.PERIODS[0])
        other_semester = Schedule(user_id=self.students[0].id, course_id=self.courses[0].id,
                                  class_date=date(2026, 8, 31), period=seed.PERIODS[1])
        plan = StudyPlan(user_id=self.students[0].id, content="保留的学习计划")
        document = Document(filename="保留.md", storage_path="private_documents/offline.md",
                            extracted_text="保留的资料", status="indexed", chunk_count=1,
                            uploader_id=self.students[0].id)
        self.db.add_all([row, other_semester, plan, document])
        self.db.commit()
        with patch.object(seed, "SessionLocal", self.session_factory), redirect_stdout(StringIO()):
            self.assertEqual(seed.clean(), 0)
        self.db.expire_all()
        self.assertEqual(self.db.query(Course).count(), 23)
        self.assertEqual(self.db.query(Schedule).count(), 2)
        self.assertEqual(self.db.query(StudyPlan).one().content, "保留的学习计划")
        self.assertEqual(self.db.query(Document).one().filename, "保留.md")

    def test_clean_preserves_other_student_references_without_deleting_courses(self):
        seed.sync_schedules(self.db, self.students, self.courses, self.wanted)
        third = User(username="other", name="其他学生", role="student", status=1,
                     password="offline-placeholder")
        self.db.add(third)
        self.db.flush()
        self.db.add(Schedule(user_id=third.id, course_id=self.courses[0].id,
                             class_date=date(2026, 10, 8), period=seed.PERIODS[0]))
        self.db.commit()
        with patch.object(seed, "SessionLocal", self.session_factory), redirect_stdout(StringIO()):
            self.assertEqual(seed.clean(), 0)
        self.db.expire_all()
        self.assertEqual(self.db.query(Course).count(), 22)
        self.assertEqual(self.db.query(Schedule).count(), 1)
        self.assertEqual(self.db.query(Schedule).one().user_id, third.id)

    def test_same_name_different_code_reuses_original_id_and_preserves_fields(self):
        # 模拟历史原课程 DS101 已在库里，模板 CS201 必须复用它。
        course = self.db.query(Course).filter(Course.name == "数据结构").one()
        course.code = "DS101"
        course.description = "原课程简介"
        self.db.commit()
        courses, created = seed.ensure_courses(self.db)
        self.assertEqual(created, 0)
        structures = [row for row in courses if row.name == "数据结构"]
        self.assertEqual(len(structures), 2)
        self.assertEqual({row.id for row in structures}, {course.id})
        self.assertEqual(course.description, "原课程简介")
        self.assertEqual(self.db.query(Course).count(), 22)

    def test_merged_alias_course_keeps_one_slot_and_new_data_is_idempotent(self):
        course = self.db.query(Course).filter(Course.name == "数据结构").one()
        course.code = "DS101"
        self.db.commit()
        courses, created = seed.ensure_courses(self.db)
        wanted = seed.build_schedule(self.students, courses)
        seed.sync_schedules(self.db, self.students, courses, wanted)
        self.db.commit()
        before = [(row.id, row.course_id, row.class_date, row.period, row.teacher,
                   row.location, row.start_week, row.end_week, row.create_time, row.update_time)
                  for row in self.db.query(Schedule).order_by(Schedule.id)]
        stats = seed.sync_schedules(self.db, self.students, courses, wanted)
        self.db.commit()
        after = [(row.id, row.course_id, row.class_date, row.period, row.teacher,
                  row.location, row.start_week, row.end_week, row.create_time, row.update_time)
                 for row in self.db.query(Schedule).order_by(Schedule.id)]
        self.assertEqual(len(after), len(wanted))
        self.assertEqual(before, after)
        self.assertEqual(stats, {"created": 0, "updated": 0, "deleted": 0, "unchanged": len(wanted)})
        self.assertEqual(created, 0)
        for student in self.students:
            structure_rows = [row for row in wanted if row["course_id"] == course.id
                              and row["user_id"] == student.id]
            self.assertEqual(len({(row["class_date"].weekday(), row["period"])
                                  for row in structure_rows}), 1)

    def test_code_match_does_not_insert_even_if_name_is_different(self):
        original = self.db.query(Course).filter(Course.code == "CS101").one()
        original.name = "原有课程名称"
        self.db.commit()
        courses, created = seed.ensure_courses(self.db)
        self.assertEqual(created, 0)
        self.assertEqual(courses[0].id, original.id)
        self.assertEqual(self.db.query(Course).count(), 22)

    def test_aliases_resolve_to_one_course_id_and_one_assignment(self):
        assignments = [row for row in seed.assign_courses(self.courses)
                       if row.code in {"CS201", "DS101"}]
        self.assertEqual(len(assignments), 1)
        self.assertEqual(len({row.course_id for row in assignments}), 1)
        self.assertEqual(assignments[0].code, "CS201")

    def test_reseed_failure_rolls_back_clean_and_leaves_prechange_backup(self):
        seed.sync_schedules(self.db, self.students, self.courses, self.wanted)
        self.db.commit()
        original = [(row.id, row.course_id, row.class_date, row.period)
                    for row in self.db.query(Schedule).order_by(Schedule.id)]
        with patch.object(seed, "SessionLocal", self.session_factory), \
             patch.object(seed, "sync_schedules", side_effect=BusinessException("模拟写入失败", code=409)), \
             redirect_stdout(StringIO()):
            with self.assertRaises(BusinessException):
                seed.seed(with_courses=False, reseed=True)
        self.db.expire_all()
        after = [(row.id, row.course_id, row.class_date, row.period)
                 for row in self.db.query(Schedule).order_by(Schedule.id)]
        self.assertEqual(original, after)
        backup = json.loads(next((self.backup_root / "backups/demo_schedules").glob("*.json")).read_text())
        self.assertEqual(backup["count"], len(original))
        self.assertEqual({row["id"] for row in backup["schedules"]}, {row[0] for row in original})

    def test_backup_contains_exact_managed_rows_with_restore_fields(self):
        seed.sync_schedules(self.db, self.students, self.courses, self.wanted)
        outside = Schedule(user_id=self.students[0].id, course_id=self.courses[0].id,
                           class_date=date(2026, 8, 31), period=seed.PERIODS[0])
        self.db.add(outside)
        self.db.commit()
        with redirect_stdout(StringIO()):
            path = seed.backup_demo_schedules(self.db, self.students, self.courses)
        snapshot = json.loads(path.read_text())
        self.assertEqual(snapshot["data_version"], seed.DATA_VERSION)
        self.assertEqual(snapshot["count"], len(self.wanted))
        self.assertNotIn(outside.id, {row["id"] for row in snapshot["schedules"]})
        self.assertTrue(all({"id", "course_id", "user_id", "class_date", "period", "teacher",
                             "location", "start_week", "end_week", "create_time", "update_time"}
                            == row.keys() for row in snapshot["schedules"]))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_preview_is_read_only_and_does_not_create_backup(self):
        before = self.db.query(Course).count(), self.db.query(Schedule).count()
        with patch.object(seed, "SessionLocal", self.session_factory), redirect_stdout(StringIO()):
            self.assertEqual(seed.seed(preview=True), 0)
        self.db.expire_all()
        self.assertEqual(before, (self.db.query(Course).count(), self.db.query(Schedule).count()))
        self.assertFalse((self.backup_root / "backups").exists())

    def test_reseed_rebuilds_only_demo_rows_and_retains_twenty_two_courses(self):
        seed.sync_schedules(self.db, self.students, self.courses, self.wanted)
        self.db.commit()
        with patch.object(seed, "SessionLocal", self.session_factory), redirect_stdout(StringIO()):
            self.assertEqual(seed.seed(with_courses=False, reseed=True), 0)
        self.db.expire_all()
        self.assertEqual(self.db.query(Schedule).count(), len(self.wanted))
        self.assertEqual(self.db.query(Course).count(), 22)


if __name__ == "__main__":
    unittest.main()
