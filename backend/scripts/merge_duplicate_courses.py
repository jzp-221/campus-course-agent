"""事务合并重名课程，保留最小 ID；课表行及日期一律保留。

    .venv/bin/python scripts/merge_duplicate_courses.py --check
    .venv/bin/python scripts/merge_duplicate_courses.py

修改前将课程完整行、受影响课表完整行及 ID 落盘到已忽略的
backups/course_merges/。备份写入失败则不执行合并；任何数据库失败整体回滚。
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

from sqlalchemy import func, update

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.models.coures import Course  # noqa: E402
from app.models.schedule import Schedule  # noqa: E402


def _row(row):
    return {
        column.name: (value.isoformat() if isinstance(value := getattr(row, column.name),
                                                     (date, datetime)) else value)
        for column in row.__table__.columns
    }


def duplicate_groups(db, *, lock=False):
    """使用数据库自身的 name 比较规则，与业务接口的重名判断一致。"""
    names = db.query(Course.name).group_by(Course.name).having(func.count(Course.id) > 1).all()
    groups = []
    for (name,) in names:
        query = db.query(Course).filter(Course.name == name).order_by(Course.id)
        courses = (query.with_for_update() if lock else query).all()
        if len(courses) < 2:
            continue
        repeated = [course.id for course in courses[1:]]
        query = db.query(Schedule).filter(Schedule.course_id.in_(repeated)).order_by(Schedule.id)
        schedules = (query.with_for_update() if lock else query).all()
        groups.append({
            "name": courses[0].name, "keep": _row(courses[0]),
            "duplicates": [_row(course) for course in courses[1:]],
            "schedules": [_row(row) for row in schedules],
        })
    return sorted(groups, key=lambda group: group["keep"]["id"])


def print_plan(groups, *, output=print):
    if not groups:
        output("无重复课程")
        return
    output(f"重名课程 {len(groups)} 组；将改指 {sum(len(g['schedules']) for g in groups)} 条课表")
    for group in groups:
        keep = group["keep"]
        output(f"{group['name']}：保留 id={keep['id']} code={keep['code']}")
        for course in group["duplicates"]:
            count = sum(row["course_id"] == course["id"] for row in group["schedules"])
            output(f"  删除 id={course['id']} code={course['code']}；改指 {count} 条课表")
        output("  受影响课表 ID：" + ", ".join(str(row["id"]) for row in group["schedules"]))


def write_backup(groups, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        + "-" + uuid.uuid4().hex[:8] + ".json")
    payload = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
               "rule": "keep_minimum_course_id", "groups": groups}
    # 排他创建并确保内容写入磁盘，成功后才允许数据库修改。
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def merge_duplicate_courses(session_factory, *, check=False,
                            backup_dir=BACKEND / "backups/course_merges", output=print):
    """独立会话的单一事务；可注入 SQLite 会话工厂作离线回滚验证。"""
    result = {"groups": 0, "affected_schedules": 0, "deleted_courses": 0, "backup": None}
    with session_factory.begin() as db:
        groups = duplicate_groups(db, lock=not check)
        print_plan(groups, output=output)
        if check:
            output("--check：只报告，未修改数据库、未生成备份")
            return result | {"groups": len(groups), "affected_schedules": sum(len(g["schedules"]) for g in groups)}
        if not groups:
            return result
        # 除 course_id 外，全字段核对，避免出现课表行丢失或隐式更新时间变化。
        before = [_row(row) for row in db.query(Schedule).order_by(Schedule.id)]
        backup = write_backup(groups, backup_dir)
        output(f"修改前备份：{backup}")
        replacement = {}
        affected = 0
        for group in groups:
            keep_id = group["keep"]["id"]
            repeated = [row["id"] for row in group["duplicates"]]
            replacement.update({identifier: keep_id for identifier in repeated})
            statement = update(Schedule).where(Schedule.course_id.in_(repeated)).values(
                course_id=keep_id, update_time=Schedule.update_time
            ).execution_options(synchronize_session=False)
            affected += db.execute(statement).rowcount
            # 外键顺序：先改引用并 flush，随后才删除父课程。
            db.flush()
            for identifier in repeated:
                db.delete(db.get(Course, identifier))
            db.flush()
        db.expire_all()
        after = [_row(row) for row in db.query(Schedule).order_by(Schedule.id)]
        expected = [row | {"course_id": replacement.get(row["course_id"], row["course_id"])}
                    for row in before]
        if after != expected or affected != sum(len(g["schedules"]) for g in groups):
            raise RuntimeError("课表回查不一致，已取消合并")
        if duplicate_groups(db):
            raise RuntimeError("重名课程回查未通过，已取消合并")
        total_courses = db.query(Course).count()
        result = {"groups": len(groups), "affected_schedules": affected,
                  "deleted_courses": len(replacement), "backup": str(backup),
                  "schedule_count_before": len(before), "schedule_count_after": len(after),
                  "course_count_after": total_courses}
    output(f"合并已提交：改指 {affected} 条课表，删除 {len(replacement)} 条重复课程")
    output(f"回查：课程 {total_courses} 条；课表 {len(before)} → {len(after)} 条；"
           "原课表 ID、日期分布及其余字段不变；无重复课程")
    return result


def main():
    parser = argparse.ArgumentParser(description="重名课程事务合并（保留最小 ID）")
    parser.add_argument("--check", action="store_true", help="只读检查")
    parser.add_argument("--backup-dir", type=Path, default=BACKEND / "backups/course_merges")
    args = parser.parse_args()
    from app.database import SessionLocal
    try:
        merge_duplicate_courses(SessionLocal, check=args.check, backup_dir=args.backup_dir)
        return 0
    except Exception as error:
        # Driver 异常可能含 SQL/连接串，不打印异常正文。
        print(f"合并失败（{type(error).__name__}）：未提交，本次事务已回滚；请核对备份及数据库状态", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
