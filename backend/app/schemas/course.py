from pydantic import BaseModel, ConfigDict


class CourseCreateRequest(BaseModel):
    """新增课程请求对象"""

    name: str
    code: str
    description: str | None = None
    status: int = 1


class CourseUpdateRequest(BaseModel):
    """更新课程请求对象"""

    name: str | None = None
    code: str | None = None
    description: str | None = None
    status: int | None = None


class CourseResponse(BaseModel):
    """课程返回的数据"""

    id: int
    name: str
    code: str
    description: str | None = None
    status: int

    model_config = ConfigDict(
        from_attributes=True,
    )
