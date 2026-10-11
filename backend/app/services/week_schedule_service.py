"""周课表视图：读取实际日期记录，不生成、迁移或修改课表。"""

from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.common.db_errors import database_business_errors
from app.common.exceptions import BusinessException
from app.common.schedule_config import SCHEDULE_PERIODS
from app.config import settings
from app.models.coures import Course
from app.models.schedule import Schedule
from app.models.user import User
from app.utils.semester import (
    date_to_week,
    is_class_day,
    week_date_range,
    week_holiday_info,
)


def shanghai_today() -> date:
    """课表标记按上海时区计算，避免执行环境的时区改变当天。"""
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _parse_week(value: int | str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        try:
            return int(value)
        except ValueError:
            pass
    raise BusinessException("周次格式错误，请输入整数周次", code=400)


def parse_student_id(value: str) -> int:
    """仅管理员选择学生时解析参数，学生请求中的该字段不会执行解析。"""
    if re.fullmatch(r"[0-9]+", value):
        try:
            identifier = int(value)
            if identifier > 0:
                return identifier
        except ValueError:
            pass
    raise BusinessException("学生 ID 格式错误，请输入正整数", code=400)


@database_business_errors
def get_week_schedules(
    db: Session,
    user_id: int,
    week: int | str | None = None,
    *,
    today: date | None = None,
    require_student: bool = False,
) -> dict:
    """返回已授权账号的视图；显式管理员目标必须是存在的学生账号。"""
    target = db.query(User).filter(User.id == user_id).first()
    if target is None or (require_student and target.role != "student"):
        raise BusinessException("学生不存在", code=404)
    today = today if today is not None else shanghai_today()
    semester_start, _ = week_date_range(1)
    _, semester_end = week_date_range(settings.TOTAL_WEEKS)
    current_week = (
        date_to_week(today) if semester_start <= today <= semester_end else None
    )
    if week is None:
        selected_week = current_week or (1 if today < semester_start else settings.TOTAL_WEEKS)
    else:
        selected_week = _parse_week(week)
    monday, sunday = week_date_range(selected_week)
    holiday_info = week_holiday_info(selected_week)

    days = []
    days_by_date = {}
    for offset in range(7):
        day = monday + timedelta(days=offset)
        holiday = next(
            (
                item
                for item in holiday_info["holidays"]
                if item["start_date"] <= day.isoformat() <= item["end_date"]
            ),
            None,
        )
        item = {
            "date": day.isoformat(),
            "weekday": day.isoweekday(),
            "is_class_day": is_class_day(day),
            "is_today": day == today,
            "is_holiday": holiday is not None,
            "holiday_name": holiday["name"] if holiday else None,
            "courses": [],
        }
        days.append(item)
        days_by_date[day] = item

    rows = (
        db.query(Schedule, Course.name)
        .join(Course, Schedule.course_id == Course.id)
        .filter(
            Schedule.user_id == user_id,
            Schedule.class_date >= monday,
            Schedule.class_date <= sunday,
            Schedule.start_week <= selected_week,
            Schedule.end_week >= selected_week,
        )
        .order_by(Schedule.class_date, Schedule.id)
        .all()
    )
    occupied = set()
    for row, course_name in rows:
        day = days_by_date[row.class_date]
        # 调休日也由统一日历判定；普通周末和节假日不进入网格。
        if not day["is_class_day"]:
            continue
        if row.period not in SCHEDULE_PERIODS:
            raise BusinessException(
                f"{row.class_date.isoformat()} 的课表节次不属于四档标准节次，请先调整课表",
                code=400,
            )
        key = (row.class_date, row.period)
        if key in occupied:
            raise BusinessException(
                f"{row.class_date.isoformat()} {row.period} 存在多条课表安排，请先调整冲突课表",
                code=409,
            )
        occupied.add(key)
        day["courses"].append(
            {
                "id": row.id,
                "user_id": row.user_id,
                "period": row.period,
                "course_id": row.course_id,
                "course_name": course_name,
                "teacher": row.teacher,
                "location": row.location,
                "start_week": row.start_week,
                "end_week": row.end_week,
            }
        )
    for day in days:
        day["courses"].sort(key=lambda item: SCHEDULE_PERIODS.index(item["period"]))

    return {
        "user_id": target.id,
        "username": target.username,
        "name": target.name,
        "week": selected_week,
        "start_date": monday.isoformat(),
        "end_date": sunday.isoformat(),
        "is_current_week": selected_week == current_week,
        "total_weeks": settings.TOTAL_WEEKS,
        "current_week": current_week,
        "today": today.isoformat(),
        "semester_start_date": semester_start.isoformat(),
        "semester_end_date": semester_end.isoformat(),
        "periods": list(SCHEDULE_PERIODS),
        "days": days,
        "has_holidays": holiday_info["has_holidays"],
        "holiday_notice": holiday_info["holiday_notice"],
    }
