from datetime import timedelta

from sqlalchemy import update

from app.common.db_errors import database_business_errors
from app.common.exceptions import BusinessException
from app.common.response import PageResponse
from app.models.coures import Course
from app.models.plan_task import PlanTask
from app.schemas.learning_item import valid_defer_days, valid_positive_int, valid_status
from app.schemas.plan_task import PlanTaskResponse
from app.services.learning_item_service import check_course, item_response, owned_row, response_with_course
from app.utils.learning_time import local_now


@database_business_errors
def list_plan_tasks(db, user, page=1, page_size=10, *, status=None):
    page = valid_positive_int(page, "page")
    page_size = valid_positive_int(page_size, "page_size", 100)
    query = db.query(PlanTask, Course.name).outerjoin(Course, Course.id == PlanTask.course_id).filter(
        PlanTask.user_id == user.id)
    if status is not None:
        query = query.filter(PlanTask.status == valid_status(status))
    total = query.count()
    rows = query.order_by(PlanTask.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return PageResponse(list=[item_response(row, PlanTaskResponse, name) for row, name in rows], total=total)


@database_business_errors
def create_plan_task(db, user, data):
    check_course(db, data.course_id)
    row = PlanTask(user_id=user.id, **data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return response_with_course(db, row, PlanTaskResponse)


@database_business_errors
def update_plan_task(db, user, item_id, data):
    row = owned_row(db, PlanTask, user, item_id, "计划任务", lock=True)
    payload = data.model_dump(exclude_unset=True)
    if "course_id" in payload:
        check_course(db, payload["course_id"])
    for field, value in payload.items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return response_with_course(db, row, PlanTaskResponse)


@database_business_errors
def defer_plan_task(db, user, item_id, days=1):
    days = valid_defer_days(days)
    # Locking read serializes both deadline calculation and counter increments.
    row = owned_row(db, PlanTask, user, item_id, "计划任务", lock=True)
    try:
        due_at = (row.due_at or local_now()) + timedelta(days=days)
    except OverflowError:
        raise BusinessException("延期后的截止时间超出支持范围", code=400) from None
    db.execute(update(PlanTask).where(PlanTask.id == item_id, PlanTask.user_id == user.id).values(
        due_at=due_at, defer_count=PlanTask.defer_count + 1, update_time=local_now()))
    db.commit()
    db.refresh(row)
    return response_with_course(db, row, PlanTaskResponse)


@database_business_errors
def delete_plan_task(db, user, item_id):
    row = owned_row(db, PlanTask, user, item_id, "计划任务", lock=True)
    db.delete(row)
    db.commit()
