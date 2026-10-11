"""Initialize only the isolated Compose demo DB with newly invented records.

CLI guards run before app imports (and therefore before reading backend/.env).
`seed_records` takes an injected Session so its behavior can be tested offline.
No existing passwords or records are replaced, and there is no clean/delete mode.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from io import BytesIO
import os
from pathlib import Path
import sys
from typing import Mapping

from sqlalchemy.engine import make_url


DEMO_VERSION = "public-demo-v1"
DEMO_ADMIN = "demo_admin"
DEMO_STUDENTS = ("demo_aaa", "demo_bbb")
SAMPLE_DOCS_DIR = Path(__file__).resolve().parent / "sample_docs"
COURSES = (
    ("PUB-DEMO-01", "算法探索（虚构演示）", "林知远"),
    ("PUB-DEMO-02", "数据库工坊（虚构演示）", "周星禾"),
    ("PUB-DEMO-03", "计算思维（虚构演示）", "许书言"),
    ("PUB-DEMO-04", "软件协作（虚构演示）", "陈沐青"),
    ("PUB-DEMO-05", "网页创作（虚构演示）", "沈亦宁"),
    ("PUB-DEMO-06", "数据观察（虚构演示）", "林知远"),
    ("PUB-DEMO-07", "创新实践（虚构演示）", "周星禾"),
    ("PUB-DEMO-08", "逻辑练习（虚构演示）", "许书言"),
    ("PUB-DEMO-09", "表达训练（虚构演示）", "陈沐青"),
)


class DemoSeedError(RuntimeError):
    """A sanitized error suitable for deployment logs."""


def assert_isolated_target(
    environ: Mapping[str, str], *, docker_marker: bool | None = None
) -> None:
    """Reject local databases before opening a connection; never echo the URL."""
    if environ.get("DEMO_DEPLOYMENT", "").lower() != "true":
        raise DemoSeedError("拒绝初始化：需要 DEMO_DEPLOYMENT=true")
    if environ.get("DEMO_SEED_TARGET") != "isolated-compose":
        raise DemoSeedError("拒绝初始化：需要隔离 Compose 标识")
    in_container = Path("/.dockerenv").is_file() if docker_marker is None else docker_marker
    if not in_container:
        raise DemoSeedError("拒绝初始化：仅允许在演示 Compose 容器中执行")
    try:
        target = make_url(environ.get("DATABASE_URL", ""))
    except Exception:
        raise DemoSeedError("拒绝初始化：演示数据库配置无效") from None
    if (target.drivername != "mysql+pymysql" or target.host != "db"
            or target.database != "campus_demo" or target.port not in (None, 3306)
            or target.query):
        raise DemoSeedError("拒绝初始化：只能写入 Compose 的 db/campus_demo")


def require_demo_target() -> None:
    """Reusable guard for the isolated API, MCP and seed entry points."""
    assert_isolated_target(os.environ)


def validate_passwords(student_password: str, admin_password: str) -> None:
    """Require fresh deployment-only passwords without printing their contents."""
    values = (student_password, admin_password)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise DemoSeedError("缺少演示学生或私有管理员密码")
    try:
        valid_lengths = all(12 <= len(value) and len(value.encode("utf-8")) <= 72
                            for value in values)
    except UnicodeError:
        valid_lengths = False
    if not valid_lengths:
        raise DemoSeedError("演示密码至少 12 个字符且不能超过 72 字节")
    if student_password == admin_password:
        raise DemoSeedError("演示学生与私有管理员必须使用不同密码")


def _ensure_users(db, student_password: str, admin_password: str):
    from app.models.user import User
    from app.utils.password import hash_password

    users = {}
    for username in (DEMO_ADMIN, *DEMO_STUDENTS):
        role = "admin" if username == DEMO_ADMIN else "student"
        row = db.query(User).filter(User.username == username).one_or_none()
        if row is not None and row.role != role:
            raise DemoSeedError("演示账号角色冲突，未改动原记录")
        if row is None:
            row = User(username=username, name={
                DEMO_ADMIN: "演示管理员", DEMO_STUDENTS[0]: "演示同学 A",
                DEMO_STUDENTS[1]: "演示同学 B",
            }[username], role=role, status=1, password=hash_password(
                admin_password if role == "admin" else student_password))
            db.add(row)
        users[username] = row
    db.flush()
    return users


def _ensure_courses(db):
    from app.models.coures import Course

    courses = []
    for code, name, _teacher in COURSES:
        matches = db.query(Course).filter((Course.code == code) | (Course.name == name)).all()
        if len(matches) > 1 or (matches and (matches[0].code != code or matches[0].name != name)):
            raise DemoSeedError("虚构课程名称或编码冲突，未改动原课程")
        row = matches[0] if matches else Course(
            code=code, name=name, description="公网体验专用的虚构课程，不代表真实教学安排。",
            status=1)
        if not matches:
            db.add(row)
        courses.append(row)
    db.flush()
    return courses


def build_schedule(users, courses) -> list[dict]:
    """Separate fixed cells for each student; ranges, holidays and makeup apply."""
    from app.common.schedule_config import SCHEDULE_PERIODS
    from app.config import settings
    from app.utils import semester

    # course index, actual weekday (Mon=0), period index, week range
    grids = {
        DEMO_STUDENTS[0]: (
            (0, 0, 0, 1, 20), (1, 1, 1, 1, 20), (2, 2, 2, 1, 20),
            (3, 3, 3, 1, 20), (4, 4, 0, 1, 20), (5, 5, 1, 1, 20),
            (6, 6, 2, 1, 20), (7, 1, 0, 3, 8), (8, 4, 2, 9, 16),
        ),
        DEMO_STUDENTS[1]: (
            (2, 0, 1, 1, 20), (4, 3, 0, 1, 20),
            (5, 5, 3, 1, 20), (6, 6, 0, 1, 20),
        ),
    }
    rows = []
    for week in range(1, settings.TOTAL_WEEKS + 1):
        monday, _ = semester.week_date_range(week)
        for username, grid in grids.items():
            for index, weekday, period, start_week, end_week in grid:
                actual_end = min(end_week, settings.TOTAL_WEEKS)
                day = monday + timedelta(days=weekday)
                if not start_week <= week <= actual_end or not semester.is_class_day(day):
                    continue
                rows.append({"user_id": users[username].id, "course_id": courses[index].id,
                             "class_date": day, "period": SCHEDULE_PERIODS[period],
                             "teacher": COURSES[index][2], "location": f"虚构教学楼 {201 + index}",
                             "start_week": start_week, "end_week": actual_end})
    return rows


def _ensure_schedules(db, rows):
    from app.models.schedule import Schedule

    for fields in rows:
        row = db.query(Schedule).filter(
            Schedule.user_id == fields["user_id"], Schedule.class_date == fields["class_date"],
            Schedule.period == fields["period"]).one_or_none()
        if row is not None and row.course_id != fields["course_id"]:
            raise DemoSeedError("演示课表格子冲突，未替换现有排课")
        if row is None:
            db.add(Schedule(**fields))
    db.flush()


def _ensure_learning_items(db, users, courses, today: date):
    from app.common.holiday_config import HOLIDAY_PERIODS
    from app.models.assignment import Assignment
    from app.models.plan_task import PlanTask

    assignments = [
        (DEMO_STUDENTS[0], 0, "assignment", "算法探索：路线比较练习", -2, "pending"),
        (DEMO_STUDENTS[0], 1, "assignment", "数据库工坊：查询练习", 1, "pending"),
        (DEMO_STUDENTS[0], 2, "assignment", "计算思维：小组讨论记录", 3, "pending"),
        (DEMO_STUDENTS[0], 3, "assignment", "软件协作：迭代回顾", 7, "done"),
        (DEMO_STUDENTS[0], 4, "exam", "网页创作：阶段测验", 10, "pending"),
        (DEMO_STUDENTS[1], 2, "assignment", "计算思维：阅读笔记", 2, "pending"),
        (DEMO_STUDENTS[1], 4, "exam", "网页创作：基础测验", 14, "pending"),
    ]
    for username, course, kind, title, offset, status in assignments:
        _assignment(db, Assignment, users[username].id, courses[course].id,
                    kind, title, datetime.combine(today + timedelta(days=offset), time(18)), status)
    future_holidays = [holiday for holiday in HOLIDAY_PERIODS if holiday.end_date >= today]
    if future_holidays:
        holiday = future_holidays[0]
        _assignment(db, Assignment, users[DEMO_STUDENTS[0]].id, courses[0].id,
                    "assignment", "算法探索：假期阅读记录", datetime.combine(
                        max(today, holiday.start_date), time(18)), "pending")
    tasks = (
        ("复习算法探索课堂笔记", 0, 1, "pending", 0),
        ("整理数据库查询练习", 1, 3, "pending", 2),
        ("完成计算思维自测", 2, None, "done", 0),
    )
    for title, course, offset, status, deferred in tasks:
        user_id = users[DEMO_STUDENTS[0]].id
        exists = db.query(PlanTask).filter(PlanTask.user_id == user_id, PlanTask.title == title).first()
        if exists is None:
            db.add(PlanTask(user_id=user_id, course_id=courses[course].id, title=title,
                            due_at=None if offset is None else datetime.combine(
                                today + timedelta(days=offset), time(20)),
                            status=status, defer_count=deferred))
    db.flush()


def _assignment(db, model, user_id, course_id, kind, title, due_at, status):
    if db.query(model).filter(model.user_id == user_id, model.title == title).first() is None:
        db.add(model(user_id=user_id, course_id=course_id, type=kind, title=title,
                     due_at=due_at, status=status, note="虚构公网演示事项，不代表真实截止日。"))


def seed_records(db, *, student_password: str, admin_password: str,
                 today: date | None = None) -> dict:
    """Called inside a transaction; Session injection is for isolated offline tests."""
    from app.models import Assignment, Course, PlanTask, Schedule, User
    from app.utils.learning_time import local_now

    validate_passwords(student_password, admin_password)
    users = _ensure_users(db, student_password, admin_password)
    courses = _ensure_courses(db)
    _ensure_schedules(db, build_schedule(users, courses))
    _ensure_learning_items(db, users, courses, today or local_now().date())
    # ORM fills all timestamps; no raw INSERT, deletion or text-plan write.
    return {"users": db.query(User).count(), "courses": db.query(Course).count(),
            "schedules": db.query(Schedule).count(), "assignments": db.query(Assignment).count(),
            "plan_tasks": db.query(PlanTask).count()}


def index_sample_documents(db, *, enabled: bool, embedding_key_present: bool,
                           upload=None) -> dict:
    """Use the real upload transaction only on an explicit deployment opt-in."""
    from app.models.document import Document
    from app.models.user import User

    if not enabled:
        return {"indexed": 0, "skipped": True, "reason": "未启用 DEMO_INDEX_DOCUMENTS，未调用 embedding"}
    if not embedding_key_present:
        return {"indexed": 0, "skipped": True, "reason": "缺少 embedding Key，资料未入库；检索仍会明确报失败"}
    admin = db.query(User).filter(User.username == DEMO_ADMIN).one()
    files = sorted(SAMPLE_DOCS_DIR.glob("*.md"))
    missing = []
    for path in files:
        existing = db.query(Document).filter(Document.filename == path.name).all()
        if existing:
            if len(existing) != 1 or existing[0].uploader_id != admin.id or existing[0].status != "indexed":
                raise DemoSeedError("演示资料记录异常，请在管理页检查；未覆盖原文档")
        else:
            missing.append(path)
    if missing:
        from fastapi import UploadFile
        from app.services.document_service import upload_documents

        operation = upload or upload_documents
        uploads = [UploadFile(filename=path.name, file=BytesIO(path.read_bytes()),
                              size=path.stat().st_size) for path in missing]
        try:
            operation(db, admin, uploads)
        finally:
            for item in uploads:
                item.file.close()
    return {"indexed": len(files), "skipped": False}


def main() -> int:
    try:
        require_demo_target()
        validate_passwords(os.environ.get("DEMO_STUDENT_PASSWORD", ""),
                           os.environ.get("DEMO_ADMIN_PASSWORD", ""))
        # Imports below this line may load settings; the independent URL is already required.
        from app.config import settings
        from app.database import Base, SessionLocal, engine
        from app import models  # noqa: F401 — register all tables for a new DB

        assert_isolated_target({**os.environ, "DATABASE_URL": str(engine.url)}, docker_marker=True)
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            with db.begin():
                counts = seed_records(db, student_password=os.environ["DEMO_STUDENT_PASSWORD"],
                                      admin_password=os.environ["DEMO_ADMIN_PASSWORD"])
            docs = index_sample_documents(db,
                enabled=os.environ.get("DEMO_INDEX_DOCUMENTS", "").lower() == "true",
                embedding_key_present=bool(settings.EMBEDDING_API_KEY.get_secret_value().strip()))
        print(f"演示版本：{DEMO_VERSION}（独立 campus_demo，未读取本机业务数据）")
        print(" / ".join(f"{name}={count}" for name, count in counts.items()))
        print(f"虚构资料 indexed={docs['indexed']}" + (f"；{docs['reason']}" if docs["skipped"] else ""))
        return 0
    except DemoSeedError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        # Do not print connection strings, model provider keys or raw SQL exceptions.
        print("演示初始化失败；未完成的事务已回滚，请检查部署服务配置", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
