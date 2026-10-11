from sqlalchemy.orm import Session
from datetime import date
import re

from app.common.exceptions import BusinessException
from app.common.db_errors import database_business_errors
from app.common.response import PageResponse
from app.common.schedule_config import SCHEDULE_PERIODS
from app.models.coures import Course
from app.models.schedule import Schedule
from app.models.user import User
from app.utils.semester import week_date_range
from app.schemas.schedule import (
    ScheduleCreateRequest,
    ScheduleResponse,
    ScheduleUpdateRequest,
)


def _to_response(
    row: Schedule,
    username: str,
    user_name: str,
    course_name: str,
) -> ScheduleResponse:
    """把课表行 + 联表字段组装成响应。"""
    return ScheduleResponse(
        id=row.id,
        user_id=row.user_id,
        username=username,
        user_name=user_name,
        class_date=row.class_date,
        period=row.period,
        course_id=row.course_id,
        course_name=course_name,
        location=row.location,
        start_week=row.start_week,
        end_week=row.end_week,
        teacher=row.teacher,
    )


def _base_query(
    db: Session, user_id: int | None = None, class_date: date | None = None
):
    query = (
        db.query(Schedule, User.username, User.name, Course.name)
        .join(User, Schedule.user_id == User.id)
        .join(Course, Schedule.course_id == Course.id)
    )
    if user_id is not None:
        query = query.filter(Schedule.user_id == user_id)
    if class_date is not None:
        query = query.filter(Schedule.class_date == class_date)
    return query


def list_page_schedules(
    db: Session,
    user_id: int | None = None,
    class_date: date | None = None,
    page: int = 1,
    page_size: int = 10,
) -> PageResponse:
    """分页查询课表列表"""
    query = _base_query(db, user_id, class_date)
    total = query.count()
    rows = (
        query.order_by(Schedule.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PageResponse(
        list=[
            _to_response(
                row=row, username=username, user_name=user_name, course_name=course_name
            )
            for row, username, user_name, course_name in rows
        ],
        total=total,
    )


def list_schedules(
    db: Session,
    user_id: int | None = None,
    class_date: date | None = None,
) -> list[ScheduleResponse]:
    """查询全部课表列表  MCP工具使用"""
    rows = _base_query(db, user_id, class_date).order_by(Schedule.id.desc()).all()
    return [
        _to_response(
            row=row, username=username, user_name=user_name, course_name=course_name
        )
        for row, username, user_name, course_name in rows
    ]


def _lock_student(db: Session, user_id: int) -> User:
    # Every schedule writer locks its student first, serializing even an empty
    # destination slot without depending on a new database unique constraint.
    user = (
        db.query(User).filter(User.id == user_id)
        .with_for_update().populate_existing().first()
    )
    if not user:
        raise BusinessException(message="用户不存在", code=404)
    if user.role != "student":
        raise BusinessException(message="只能给学生安排课表", code=400)
    return user


def _validate_slot_and_weeks(period: str, start_week: int, end_week: int) -> None:
    if period not in SCHEDULE_PERIODS:
        raise BusinessException("节次必须为 1-2节 / 3-4节 / 5-6节 / 7-8节", code=400)
    week_date_range(start_week)
    week_date_range(end_week)
    if start_week > end_week:
        raise BusinessException("起始周次不能晚于结束周次", code=400)


def _check_slot(
    db: Session, user_id: int, class_date: date, period: str,
    *, exclude_id: int | None = None,
) -> None:
    # Authentication may already have opened a REPEATABLE READ snapshot.
    # A locking read sees the prior writer's committed row after the user lock.
    query = db.query(Schedule.id).filter(
        Schedule.user_id == user_id, Schedule.class_date == class_date, Schedule.period == period,
    )
    if exclude_id is not None:
        query = query.filter(Schedule.id != exclude_id)
    if query.with_for_update().first() is not None:
        raise BusinessException(
            f"该学生 {class_date.isoformat()} {period} 已有课表安排，请调整日期或节次", code=409,
        )


@database_business_errors
def create_schedule(db: Session, data: ScheduleCreateRequest) -> ScheduleResponse:
    """新增课表；写入前拒绝非法节次、周次和同人同格冲突。"""
    user = _lock_student(db, data.user_id)
    _validate_slot_and_weeks(data.period, data.start_week, data.end_week)

    course = db.query(Course).filter(Course.id == data.course_id).first()
    if not course:
        raise BusinessException(message="课程不存在", code=404)

    _check_slot(db, data.user_id, data.class_date, data.period)
    row = Schedule(**data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_response(row, user.username, user.name, course.name)


@database_business_errors
def update_schedule(
    db: Session, schedule_id: int, data: ScheduleUpdateRequest
) -> ScheduleResponse:
    """更新课表"""
    row = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not row:
        raise BusinessException(message="课表不存在", code=404)
    original_user_id = row.user_id
    # Retain the old null-as-omission behavior for existing fields, while the
    # newly nullable teacher explicitly supports clearing with null.
    payload = {
        key: value for key, value in data.model_dump(exclude_unset=True).items()
        if value is not None or key == "teacher"
    }
    user_id = payload.get("user_id", row.user_id)
    # Stable parent lock order also covers moves between students. Lock and
    # refresh the schedule only afterwards, matching the creation lock order.
    users = {
        identifier: _lock_student(db, identifier)
        for identifier in sorted({original_user_id, user_id})
    }
    row = (
        db.query(Schedule).filter(Schedule.id == schedule_id)
        .with_for_update().populate_existing().first()
    )
    if row is None:
        raise BusinessException(message="课表不存在", code=404)
    if row.user_id != original_user_id:
        raise BusinessException("该课表已被其他操作修改，请刷新后重试", code=409)
    user = users[user_id]
    course_id = payload.get("course_id", row.course_id)
    period = payload.get("period", row.period)
    class_date = payload.get("class_date", row.class_date)
    _validate_slot_and_weeks(
        period, payload.get("start_week", row.start_week), payload.get("end_week", row.end_week),
    )

    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise BusinessException(message="课程不存在", code=404)

    _check_slot(db, user_id, class_date, period, exclude_id=schedule_id)

    for field, value in payload.items():
        setattr(row, field, value)

    db.commit()
    db.refresh(row)
    return _to_response(row, user.username, user.name, course.name)


@database_business_errors
def delete_schedule(db: Session, schedule_id: int) -> None:
    """删除课表"""
    row = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not row:
        raise BusinessException(message="课表不存在", code=404)
    db.delete(row)
    db.commit()


def list_schedules_as_dict(
    db: Session,
    user_id: int | None = None,
    date_str: str | None = None,
) -> list[dict]:
    """复用全量课表查询，为 MCP 返回日期为字符串的字典列表。"""
    date_text = (date_str or "").strip()
    class_date = None
    if date_text:
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date_text):
                raise ValueError("invalid date format")
            class_date = date.fromisoformat(date_text)
        except ValueError:
            raise BusinessException(
                message="日期格式错误，请使用 YYYY-MM-DD", code=400
            ) from None

    items = list_schedules(db, user_id, class_date)
    return [
        {
            "date": item.class_date.isoformat(),
            "period": item.period,
            "course_id": item.course_id,
            "course_name": item.course_name,
            "location": item.location,
        }
        for item in items
    ]
