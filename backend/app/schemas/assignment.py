from pydantic import model_validator

from app.common.exceptions import BusinessException

from app.schemas.learning_item import (
    CourseId, DueAt, ItemRequest, ItemResponse, ItemType, Note, Status, Title,
)


class AssignmentCreateRequest(ItemRequest):
    course_id: CourseId
    type: ItemType
    title: Title
    due_at: DueAt
    status: Status = "pending"
    note: Note = None

    @model_validator(mode="before")
    @classmethod
    def require_fields(cls, value):
        if not isinstance(value, dict):
            raise BusinessException("请求体必须为 JSON 对象", code=400)
        for field in ("course_id", "type", "title", "due_at"):
            if field not in value:
                raise BusinessException(f"{field} 必填", code=400)
        return value


class AssignmentUpdateRequest(ItemRequest):
    # Defaults are intentionally not validated; omitted fields mean no change.
    course_id: CourseId = None
    type: ItemType = None
    title: Title = None
    due_at: DueAt = None
    status: Status = None
    note: Note = None


class AssignmentResponse(ItemResponse):
    course_id: int
    due_at: DueAt
    type: str
    note: str | None
