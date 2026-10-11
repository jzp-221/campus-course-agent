from datetime import timedelta

from app.common.db_errors import database_business_errors
from app.common.exceptions import BusinessException
from app.common.response import PageResponse
from app.models.assignment import Assignment
from app.models.coures import Course
from app.schemas.assignment import AssignmentResponse
from app.schemas.learning_item import valid_positive_int, valid_status, valid_type
from app.services.learning_item_service import check_course, item_response, owned_row, response_with_course
from app.utils.learning_time import local_now


@database_business_errors
def list_assignments(db, user, page=1, page_size=10, *, status=None, type=None, course_id=None, days=None):
    page = valid_positive_int(page, "page")
    page_size = valid_positive_int(page_size, "page_size", 100)
    query = db.query(Assignment, Course.name).join(Course, Course.id == Assignment.course_id).filter(
        Assignment.user_id == user.id)
    if status is not None:
        query = query.filter(Assignment.status == valid_status(status))
    if type is not None:
        query = query.filter(Assignment.type == valid_type(type))
    if course_id is not None:
        course_id = valid_positive_int(course_id, "course_id")
        check_course(db, course_id)
        query = query.filter(Assignment.course_id == course_id)
    if days is not None:
        days = valid_positive_int(days)
        now = local_now()
        try:
            end = now + timedelta(days=days)
        except OverflowError:
            raise BusinessException("days 超出支持的时间范围", code=400) from None
        query = query.filter(Assignment.due_at >= now, Assignment.due_at <= end)
    total = query.count()
    rows = query.order_by(Assignment.due_at, Assignment.id).offset((page - 1) * page_size).limit(page_size).all()
    return PageResponse(list=[item_response(row, AssignmentResponse, name) for row, name in rows], total=total)


@database_business_errors
def create_assignment(db, user, data):
    check_course(db, data.course_id)
    row = Assignment(user_id=user.id, **data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return response_with_course(db, row, AssignmentResponse)


@database_business_errors
def update_assignment(db, user, item_id, data):
    row = owned_row(db, Assignment, user, item_id, "作业/考试", lock=True)
    payload = data.model_dump(exclude_unset=True)
    if "course_id" in payload:
        check_course(db, payload["course_id"])
    for field, value in payload.items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return response_with_course(db, row, AssignmentResponse)


@database_business_errors
def delete_assignment(db, user, item_id):
    row = owned_row(db, Assignment, user, item_id, "作业/考试", lock=True)
    db.delete(row)
    db.commit()
