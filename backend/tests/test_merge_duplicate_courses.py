"""重名课程合并离线回归：真实事务、外键与备份，绝不读取业务库。"""

from contextlib import redirect_stdout
from datetime import date, datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests import test_agent  # 禁止 dotenv，必须先于 app / scripts 导入。
from app import database
from app.database import Base
from app.models.coures import Course
from app.models.schedule import Schedule
from app.models.user import User
from scripts import merge_duplicate_courses as merge


class MergeDuplicateCoursesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        event.listen(self.engine, "connect", lambda connection, record: connection.execute("PRAGMA foreign_keys=ON"))
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, autoflush=False)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.backup_dir = Path(self.temporary.name) / "backups"
        created = datetime(2026, 8, 1, 9, 20, 30, 123456)
        updated = datetime(2026, 8, 2, 10, 21, 31, 654321)
        with self.sessions() as db:
            db.add_all([
                User(id=1, username="aaa", name="学生甲", role="student", password="unused"),
                User(id=2, username="bbb", name="学生乙", role="student", password="unused"),
            ])
            db.add_all([
                Course(id=2, name="数据结构", code="DS101", description="保留简介", status=1, create_time=created, update_time=updated),
                Course(id=50, name="数据结构", code="CS201", description="重复简介", status=0, create_time=created, update_time=updated),
                Course(id=51, name="数据结构", code="DS103", description=None, status=1, create_time=created, update_time=updated),
                Course(id=7, name="操作系统", code="OS101", description="系统课程", status=1, create_time=created, update_time=updated),
                Course(id=70, name="操作系统", code="CS202", description="系统重复", status=1, create_time=created, update_time=updated),
                Course(id=90, name="大学英语", code="ENG101", description="不参与合并", status=1, create_time=created, update_time=updated),
            ])
            db.flush()
            for identifier, user_id, course_id, class_date in (
                (10, 1, 2, "2026-09-23"),
                (11, 1, 50, "2026-09-24"),
                (12, 2, 50, "2026-10-15"),
                (13, 1, 51, "2026-10-14"),
                (14, 2, 70, "2026-10-15"),
                (15, 2, 90, "2026-10-10"),
            ):
                db.add(Schedule(
                    id=identifier, user_id=user_id, course_id=course_id,
                    class_date=date.fromisoformat(class_date), period="1-2节",
                    teacher="王老师" if identifier % 2 else None,
                    location="教学楼 A101" if identifier % 2 else None,
                    start_week=3, end_week=16, create_time=created, update_time=updated,
                ))
            db.commit()

    def snapshot(self):
        with self.sessions() as db:
            return {
                "courses": [merge._row(row) for row in db.query(Course).order_by(Course.id)],
                "schedules": [merge._row(row) for row in db.query(Schedule).order_by(Schedule.id)],
            }

    def run_merge(self, **kwargs):
        lines = []
        result = merge.merge_duplicate_courses(
            self.sessions, backup_dir=self.backup_dir, output=lines.append, **kwargs
        )
        return result, lines

    def test_multiple_groups_keep_smallest_id_and_preserve_all_schedule_fields(self):
        before = self.snapshot()
        result, lines = self.run_merge()
        after = self.snapshot()
        self.assertEqual((result["groups"], result["deleted_courses"], result["affected_schedules"]), (2, 3, 4))
        self.assertEqual([row["id"] for row in after["courses"]], [2, 7, 90])
        self.assertEqual(after["courses"], [row for row in before["courses"] if row["id"] in {2, 7, 90}])
        replacements = {50: 2, 51: 2, 70: 7}
        expected = [row | {"course_id": replacements.get(row["course_id"], row["course_id"])} for row in before["schedules"]]
        # 对完整行比较：ID、学生、日期、节次、教师、地点、周次、两种时间戳均保持。
        self.assertEqual(after["schedules"], expected)
        self.assertEqual((result["schedule_count_before"], result["schedule_count_after"]), (6, 6))
        self.assertIn("重名课程 2 组；将改指 4 条课表", lines)
        self.assertTrue(any("课表 6 → 6 条" in line for line in lines))
        with self.sessions() as db:
            self.assertEqual(merge.duplicate_groups(db), [])

    def test_repeated_execution_makes_no_changes_or_new_backup(self):
        self.run_merge()
        before = self.snapshot()
        files = sorted(self.backup_dir.iterdir())
        with patch.object(merge, "write_backup", side_effect=AssertionError("不应新增备份")):
            result, lines = self.run_merge()
        self.assertEqual(result, {"groups": 0, "affected_schedules": 0, "deleted_courses": 0, "backup": None})
        self.assertEqual(lines, ["无重复课程"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(self.backup_dir.iterdir()), files)

    def test_check_reports_affected_rows_without_database_or_backup_writes(self):
        before = self.snapshot()
        with patch.object(merge, "write_backup", side_effect=AssertionError("check 不允许备份")):
            result, lines = self.run_merge(check=True)
        self.assertEqual((result["groups"], result["affected_schedules"]), (2, 4))
        self.assertIsNone(result["backup"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup_dir.exists())
        self.assertTrue(any("保留 id=2 code=DS101" in line for line in lines))
        self.assertTrue(any("受影响课表 ID：11, 12, 13" in line for line in lines))
        self.assertIn("--check：只报告，未修改数据库、未生成备份", lines)

    def test_cli_check_exits_zero_and_only_reports(self):
        before = self.snapshot()
        output = io.StringIO()
        with patch.object(database, "SessionLocal", self.sessions), \
                patch("sys.argv", ["merge_duplicate_courses.py", "--check", "--backup-dir", str(self.backup_dir)]), \
                redirect_stdout(output):
            self.assertEqual(merge.main(), 0)
        self.assertIn("--check：只报告", output.getvalue())
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup_dir.exists())

    def test_backup_contains_full_original_course_and_affected_schedule_rows(self):
        before = self.snapshot()
        result, _ = self.run_merge()
        backup = Path(result["backup"])
        payload = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["rule"], "keep_minimum_course_id")
        self.assertTrue(payload["created_at"])
        self.assertEqual([group["keep"]["id"] for group in payload["groups"]], [2, 7])
        courses = {row["id"]: row for row in before["courses"]}
        schedules = {row["id"]: row for row in before["schedules"]}
        for group in payload["groups"]:
            self.assertEqual(group["keep"], courses[group["keep"]["id"]])
            for row in group["duplicates"]:
                self.assertEqual(row, courses[row["id"]])
            for row in group["schedules"]:
                self.assertEqual(row, schedules[row["id"]])
        self.assertEqual([row["id"] for group in payload["groups"] for row in group["schedules"]], [11, 12, 13, 14])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_backup_failure_leaves_database_unchanged(self):
        before = self.snapshot()
        with patch.object(merge, "write_backup", side_effect=PermissionError("不可写")):
            with self.assertRaises(PermissionError):
                self.run_merge()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup_dir.exists())

    def test_backup_file_open_failure_also_prevents_changes(self):
        before = self.snapshot()
        with patch.object(merge.os, "open", side_effect=PermissionError("不可写")):
            with self.assertRaises(PermissionError):
                self.run_merge()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.backup_dir.iterdir()), [])

    def test_delete_failure_after_updates_rolls_back_all_groups(self):
        before = self.snapshot()
        observed = []

        def fail_second_group(mapper, connection, target):
            # 第二组删除前，第一组已完成；当前组引用也已改指。
            if target.id == 70:
                rows = connection.execute(Schedule.__table__.select().order_by(Schedule.id)).mappings().all()
                observed.extend(row["course_id"] for row in rows)
                raise RuntimeError("模拟删除失败")

        event.listen(Course, "before_delete", fail_second_group)
        try:
            with self.assertRaisesRegex(RuntimeError, "模拟删除失败"):
                self.run_merge()
        finally:
            event.remove(Course, "before_delete", fail_second_group)
        self.assertEqual(observed, [2, 2, 2, 2, 7, 90])
        self.assertEqual(self.snapshot(), before)
        # 整体回滚后，修改前的完整备份仍可人工核对。
        self.assertEqual(len(list(self.backup_dir.glob("*.json"))), 1)

    def test_duplicates_without_schedules_are_removed_without_touching_other_rows(self):
        with self.sessions() as db:
            db.add(Course(id=91, name="大学英语", code="ENG102", description="无引用重复"))
            db.commit()
        before = self.snapshot()["schedules"]
        result, _ = self.run_merge()
        self.assertEqual((result["groups"], result["deleted_courses"], result["affected_schedules"]), (3, 4, 4))
        expected = [row | {"course_id": {50: 2, 51: 2, 70: 7}.get(row["course_id"], row["course_id"])} for row in before]
        self.assertEqual(self.snapshot()["schedules"], expected)


if __name__ == "__main__":
    unittest.main()
