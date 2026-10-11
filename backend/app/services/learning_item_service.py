"""Small helpers; all ownership checks are independent of role."""

from app.common.exceptions import BusinessException
from app.models.coures import Course


def owned_row(db, model, user, item_id, label, *, lock=False):
    query = db.query(model).filter(model.id == item_id, model.user_id == user.id)
    row = (query.with_for_update() if lock else query).first()
    if row is None:
        raise BusinessException(f"{label}不存在", code=404)
    return row


def check_course(db, course_id):
    if course_id is not None and db.query(Course.id).filter(Course.id == course_id).first() is None:
        raise BusinessException("课程不存在", code=404)


def item_response(row, response_class, course_name=None):
    result = response_class.model_validate(row)
    return result.model_copy(update={"course_name": course_name})


def response_with_course(db, row, response_class):
    name = db.query(Course.name).filter(Course.id == row.course_id).scalar() if row.course_id else None
    return item_response(row, response_class, name)
