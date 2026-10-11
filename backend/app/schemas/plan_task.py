from typing import Annotated
from pydantic import BeforeValidator, model_validator

from app.common.exceptions import BusinessException

from app.schemas.learning_item import (
    ItemRequest, ItemResponse, OptionalCourseId, OptionalDueAt, Status, Title, valid_defer_days,
)


class PlanTaskCreateRequest(ItemRequest):
    title: Title
    course_id: OptionalCourseId = None
    due_at: OptionalDueAt = None
    status: Status = "pending"

    @model_validator(mode="before")
    @classmethod
    def require_title(cls, value):
        if not isinstance(value, dict):
            raise BusinessException("请求体必须为 JSON 对象", code=400)
        if "title" not in value:
            raise BusinessException("title 必填", code=400)
        return value


class PlanTaskUpdateRequest(ItemRequest):
    title: Title = None
    course_id: OptionalCourseId = None
    due_at: OptionalDueAt = None
    status: Status = None


class PlanTaskDeferRequest(ItemRequest):
    days: Annotated[int, BeforeValidator(valid_defer_days)] = 1


class PlanTaskResponse(ItemResponse):
    defer_count: int
