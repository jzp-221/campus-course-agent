"""学期周次和上课日换算；不访问数据库，也不生成或修改课表。"""

from datetime import date, datetime, timedelta
import re

from app.common.exceptions import BusinessException
from app.common.holiday_config import HOLIDAY_PERIODS, MAKEUP_WORKDAYS
from app.config import settings


def _parse_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise BusinessException("日期格式错误，请使用有效的 YYYY-MM-DD 日期", code=400)


def _semester_config(
    semester_start_date: date | str | None, total_weeks: int | None
) -> tuple[date, int]:
    start = _parse_date(
        settings.SEMESTER_START_DATE
        if semester_start_date is None
        else semester_start_date
    )
    weeks = settings.TOTAL_WEEKS if total_weeks is None else total_weeks
    if isinstance(weeks, bool) or not isinstance(weeks, int) or weeks < 1:
        raise BusinessException("学期总周数必须为正整数", code=400)
    if start.weekday() != 0:
        raise BusinessException("学期开始日期必须为周一", code=400)
    try:
        start + timedelta(days=weeks * 7 - 1)
    except OverflowError as exc:
        raise BusinessException("学期日期范围超出支持范围", code=400) from exc
    return start, weeks


def week_date_range(
    week: int,
    *,
    semester_start_date: date | str | None = None,
    total_weeks: int | None = None,
) -> tuple[date, date]:
    """第 N 周 -> (周一, 周日)，越界以业务码 400 拒绝。"""
    start, weeks = _semester_config(semester_start_date, total_weeks)
    if isinstance(week, bool) or not isinstance(week, int) or not 1 <= week <= weeks:
        raise BusinessException(f"周次超出范围，请输入 1 至 {weeks} 的整数", code=400)
    monday = start + timedelta(days=(week - 1) * 7)
    return monday, monday + timedelta(days=6)


def date_to_week(
    value: date | str,
    *,
    semester_start_date: date | str | None = None,
    total_weeks: int | None = None,
) -> int:
    """学期内日期 -> 周次；每周以周一为起点。"""
    day = _parse_date(value)
    start, weeks = _semester_config(semester_start_date, total_weeks)
    last = start + timedelta(days=weeks * 7 - 1)
    if not start <= day <= last:
        raise BusinessException(
            f"日期不在学期范围内（{start.isoformat()} 至 {last.isoformat()}）",
            code=400,
        )
    return (day - start).days // 7 + 1


def is_class_day(value: date | str) -> bool:
    """放假为 False，指定调休上班日为 True，其余按周一至周五。"""
    day = _parse_date(value)
    if any(period.start_date <= day <= period.end_date for period in HOLIDAY_PERIODS):
        return False
    if day in MAKEUP_WORKDAYS:
        return True
    return day.weekday() < 5


def week_holiday_info(
    week: int,
    *,
    semester_start_date: date | str | None = None,
    total_weeks: int | None = None,
) -> dict:
    """当周放假说明，同时保留完整假期与该周的交集日期。"""
    monday, sunday = week_date_range(
        week,
        semester_start_date=semester_start_date,
        total_weeks=total_weeks,
    )
    holidays = []
    notices = []
    for period in HOLIDAY_PERIODS:
        if period.start_date > sunday or period.end_date < monday:
            continue
        holidays.append(
            {
                "name": period.name,
                "start_date": period.start_date.isoformat(),
                "end_date": period.end_date.isoformat(),
                "week_start_date": max(monday, period.start_date).isoformat(),
                "week_end_date": min(sunday, period.end_date).isoformat(),
                "note": period.note,
            }
        )
        notice = (
            f"{period.start_date.month}/{period.start_date.day}"
            f"-{period.end_date.month}/{period.end_date.day} {period.name}放假"
        )
        if period.note:
            notice += f"（{period.note}）"
        notices.append(notice)
    return {
        "has_holidays": bool(holidays),
        "holidays": holidays,
        "holiday_notice": "；".join(notices),
    }
