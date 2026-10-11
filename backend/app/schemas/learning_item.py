"""Business-code validation shared by the two new resources (no global changes)."""

from datetime import datetime
import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict

from app.common.exceptions import BusinessException


def valid_title(value):
    if not isinstance(value, str) or not value.strip():
        raise BusinessException("标题不能为空", code=400)
    value = value.strip()
    if len(value) > 200:
        raise BusinessException("标题不能超过200字", code=400)
    return value


def valid_status(value):
    if value not in ("pending", "done"):
        raise BusinessException("状态只允许 pending 或 done", code=400)
    return value


def valid_type(value):
    if value not in ("assignment", "exam"):
        raise BusinessException("类型只允许 assignment 或 exam", code=400)
    return value


def valid_course_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BusinessException("course_id 必须为正整数", code=400)
    return value


def valid_optional_course_id(value):
    return None if value is None else valid_course_id(value)


def valid_due_at(value):
    # Interpret supplied wall-clock fields literally. No UTC/local conversion.
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            value = None
    if not isinstance(value, datetime):
        raise BusinessException("due_at 必须为合法 ISO 日期时间，如 2026-10-12T18:00:00", code=400)
    return value.replace(tzinfo=None)


def valid_optional_due_at(value):
    return None if value is None else valid_due_at(value)


def valid_note(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 500:
        raise BusinessException("备注必须为不超过500字的文本", code=400)
    return value


def valid_positive_int(value, label="days", maximum=None):
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or (maximum and value > maximum):
        limit = f"1～{maximum}" if maximum else "正"
        raise BusinessException(f"{label} 必须为{limit}整数", code=400)
    return value


def valid_defer_days(value):
    return valid_positive_int(value, maximum=30)


Title = Annotated[str, BeforeValidator(valid_title)]
Status = Annotated[str, BeforeValidator(valid_status)]
ItemType = Annotated[str, BeforeValidator(valid_type)]
CourseId = Annotated[int, BeforeValidator(valid_course_id)]
OptionalCourseId = Annotated[int | None, BeforeValidator(valid_optional_course_id)]
DueAt = Annotated[datetime, BeforeValidator(valid_due_at)]
OptionalDueAt = Annotated[datetime | None, BeforeValidator(valid_optional_due_at)]
Note = Annotated[str | None, BeforeValidator(valid_note)]


class ItemRequest(BaseModel):
    # Owner always comes from JWT. Ignore any user_id supplied by the caller.
    model_config = ConfigDict(extra="ignore")


class ItemResponse(BaseModel):
    id: int
    user_id: int
    title: str
    status: str
    course_id: int | None
    course_name: str | None = None
    due_at: datetime | None
    create_time: datetime
    update_time: datetime
    model_config = ConfigDict(from_attributes=True)
