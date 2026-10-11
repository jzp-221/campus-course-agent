from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.common.exceptions import BusinessException
from app.common.schedule_config import SCHEDULE_PERIODS
from app.config import settings
from app.utils.semester import week_date_range


class _ScheduleFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("period", mode="before", check_fields=False)
    @classmethod
    def standard_period(cls, value):
        if value is None:
            return value
        if not isinstance(value, str) or value.strip() not in SCHEDULE_PERIODS:
            raise BusinessException("节次必须为 1-2节 / 3-4节 / 5-6节 / 7-8节", code=400)
        return value.strip()

    @field_validator("start_week", "end_week", mode="before", check_fields=False)
    @classmethod
    def semester_week(cls, value):
        # Explicit null is not an omission: both database columns are NOT NULL.
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            try:
                value = int(value)
            except ValueError:
                pass
        week_date_range(value)
        return value

    @field_validator("teacher", check_fields=False)
    @classmethod
    def valid_teacher(cls, value):
        if value is not None and len(value) > 100:
            raise BusinessException("教师姓名不能超过 100 字", code=400)
        return value


class ScheduleCreateRequest(_ScheduleFields):
    """管理员新增课表。"""

    user_id: int
    class_date: date
    period: str
    course_id: int
    location: str
    start_week: int = 1
    end_week: int = Field(default_factory=lambda: settings.TOTAL_WEEKS)
    teacher: str | None = None

    @model_validator(mode="after")
    def ordered_weeks(self):
        if self.start_week > self.end_week:
            raise BusinessException("起始周次不能晚于结束周次", code=400)
        return self


class ScheduleUpdateRequest(_ScheduleFields):
    """管理员更新课表。"""

    user_id: int | None = None
    class_date: date | None = None
    period: str | None = None
    course_id: int | None = None
    location: str | None = None
    start_week: int | None = None
    end_week: int | None = None
    teacher: str | None = None


class ScheduleResponse(BaseModel):
    """课表返回（联表带出账号、姓名、课程名，方便页面直接展示）。"""

    id: int
    user_id: int
    username: str
    user_name: str
    class_date: date
    period: str
    course_id: int
    course_name: str
    location: str
    start_week: int = 1
    end_week: int = 20
    teacher: str | None = None

    model_config = ConfigDict(from_attributes=True)
