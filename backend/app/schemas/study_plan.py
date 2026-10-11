from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StudyPlanCreateRequest(BaseModel):
    """保存学习计划。"""

    content: str

    model_config = ConfigDict(str_strip_whitespace=True)


class StudyPlanResponse(BaseModel):
    """学习计划返回。"""

    id: int
    content: str
    create_time: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
