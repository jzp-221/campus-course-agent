from app.common.exceptions import BusinessException
from app.common.db_errors import database_business_errors
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.models.coures import Course
from app.models.schedule import Schedule
from app.models.assignment import Assignment
from app.models.plan_task import PlanTask
from app.schemas.course import CourseCreateRequest, CourseResponse, CourseUpdateRequest


def list_courses(db: Session, keywords: str | None = None) -> list[CourseResponse]:
    """课程列表"""
    query = db.query(Course)
    if keywords and keywords.strip():
        kw = f"%{keywords.strip()}%"
        query = query.filter(
            or_(
                Course.name.ilike(kw),
                Course.code.ilike(kw),
                Course.description.ilike(kw),
            )
        )
    rows = query.order_by(Course.id.desc()).all()
    return [CourseResponse.model_validate(row) for row in rows]


@database_business_errors
def create_course(db: Session, data: CourseCreateRequest) -> CourseResponse:
    """新增课程"""
    if not data.code.strip():
        raise BusinessException("课程编码不能为空", code=400)
    if not data.name.strip():
        raise BusinessException("课程名称不能为空", code=400)
    exist = db.query(Course).filter(Course.code == data.code.strip()).first()
    if exist:
        raise BusinessException("课程编码已存在", code=409)
    if db.query(Course).filter(Course.name == data.name.strip()).first():
        raise BusinessException("课程名称已存在", code=409)
    course = Course(
        name=data.name.strip(),
        code=data.code.strip(),
        description=(data.description or "").strip() or None,
        status=data.status if data.status in (0, 1) else 1,
    )
    db.add(course)
    db.commit()
    db.refresh(course)
    return CourseResponse.model_validate(course)


@database_business_errors
def update_course(
    db: Session, course_id: int, data: CourseUpdateRequest
) -> CourseResponse:
    """更新课程"""
    row = db.query(Course).filter(Course.id == course_id).first()
    if not row:
        raise BusinessException(message="课程不存在", code=404)
    payload = data.model_dump(exclude_none=True, exclude_unset=True)
    if "name" in payload:
        payload["name"] = payload["name"].strip()
        if not payload["name"]:
            raise BusinessException("课程名称不能为空", code=400)
        conflict = db.query(Course).filter(
            Course.name == payload["name"], Course.id != course_id
        ).first()
        if conflict:
            raise BusinessException("课程名称已存在", code=409)
    if "code" in payload:
        code = payload["code"] = payload["code"].strip()
        if not code:
            raise BusinessException("课程编码不能为空", code=400)
        conflict = (
            db.query(Course).filter(Course.code == code, Course.id != course_id).first()
        )
        if conflict:
            raise BusinessException("课程编码已存在", code=409)
        payload["code"] = code
    if "description" in payload:
        payload["description"] = payload["description"].strip()

    for field, value in payload.items():
        setattr(row, field, value)

    db.commit()
    db.refresh(row)
    return CourseResponse.model_validate(row)


@database_business_errors
def delete_course(db: Session, course_id: int) -> None:
    """删除课程"""
    row = db.query(Course).filter(Course.id == course_id).with_for_update().first()
    if not row:
        raise BusinessException(message="课程不存在", code=404)
    # Use a current locking read rather than the authentication transaction's
    # earlier MySQL REPEATABLE READ snapshot; keep FK constraints as the guard.
    schedules = len(db.query(Schedule.id).filter(
        Schedule.course_id == course_id
    ).with_for_update().all())
    references = []
    if schedules:
        references.append(f"{schedules} 条课表安排")
    for model, label in ((Assignment, "作业/考试"), (PlanTask, "计划任务")):
        count = len(db.query(model.id).filter(model.course_id == course_id).with_for_update().all())
        if count:
            references.append(f"{count} 条{label}")
    if references:
        if schedules and len(references) == 1:
            raise BusinessException(
                f"该课程已有 {schedules} 条课表安排，请先删除或调整这些课表", code=409
            )
        raise BusinessException(
            f"该课程已有 {'、'.join(references)}，请先删除或调整这些关联数据", code=409
        )
    db.delete(row)
    db.commit()
