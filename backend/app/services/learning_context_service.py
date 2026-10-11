"""学生学习页面的只读上下文；不修改课表、课程或文本学习计划。"""

import asyncio

from sqlalchemy.orm import Session

from app.common.db_errors import database_business_errors
from app.common.exceptions import BusinessException
from app.common.holiday_config import HOLIDAY_PERIODS, MAKEUP_WORKDAYS
from app.config import settings
from app.models.coures import Course
from app.models.schedule import Schedule
from app.services import mcp_client_service
from app.services.week_schedule_service import shanghai_today
from app.utils.semester import date_to_week, week_date_range


def semester_info() -> dict:
    """日历只从既有配置生成；学期之外 current_week 为 null。"""
    start, _ = week_date_range(1)
    _, end = week_date_range(settings.TOTAL_WEEKS)
    today = shanghai_today()
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_weeks": settings.TOTAL_WEEKS,
        "current_week": date_to_week(today) if start <= today <= end else None,
        "today": today.isoformat(),
    }


def _course_data(row: Course) -> dict:
    return {
        "id": row.id, "name": row.name, "code": row.code,
        "description": row.description, "status": row.status,
    }


def _find_course(db: Session, course_id: int) -> Course:
    row = db.query(Course).filter(Course.id == course_id).first()
    if row is None:
        raise BusinessException("课程不存在", code=404)
    return row


@database_business_errors
def get_learning_context(db: Session) -> dict:
    return {
        "courses": [_course_data(row) for row in db.query(Course).order_by(Course.id).all()],
        "semester": semester_info(),
        "holidays": [
            {"name": item.name, "start_date": item.start_date.isoformat(),
             "end_date": item.end_date.isoformat(), "note": item.note}
            for item in HOLIDAY_PERIODS
        ],
        "makeup_workdays": [day.isoformat() for day in sorted(MAKEUP_WORKDAYS)],
    }


@database_business_errors
def get_course_context(db: Session, course_id: int, user_id: int) -> dict:
    course = _find_course(db, course_id)
    # 同时约束两个字段，任何角色都只看到自己的上课安排。
    rows = db.query(Schedule).filter(
        Schedule.course_id == course_id, Schedule.user_id == user_id,
    ).order_by(Schedule.class_date, Schedule.id).all()
    return {
        "course": _course_data(course),
        "semester": semester_info(),
        "schedules": [
            {"id": row.id, "class_date": row.class_date.isoformat(),
             "weekday": row.class_date.isoweekday(), "period": row.period,
             "teacher": row.teacher, "location": row.location,
             "start_week": row.start_week, "end_week": row.end_week}
            for row in rows
        ],
        "start_week": min((row.start_week for row in rows), default=None),
        "end_week": max((row.end_week for row in rows), default=None),
    }


@database_business_errors
def course_search_query(db: Session, course_id: int) -> str:
    """检索输入由真实课程名确定，接口不接受任意 query/user_id。"""
    return _find_course(db, course_id).name


async def search_course_documents(authorization: str, course_name: str) -> dict:
    """每次请求重新发现工具并转发 JWT，经真实 Streamable-HTTP 检索。"""
    if not settings.EMBEDDING_API_KEY.get_secret_value().strip():
        raise BusinessException("当前无法查询资料（检索失败：未配置 EMBEDDING_API_KEY）", code=503)
    try:
        tools = await mcp_client_service.get_schedule_tools(authorization)
        selected = [tool for tool in tools if tool.name == "search_course_docs"]
        if len(selected) != 1:
            raise BusinessException("当前无法查询资料（检索失败：MCP 未提供唯一的 search_course_docs 工具）", code=502)
        result = await asyncio.wait_for(selected[0].ainvoke({"query": course_name}), timeout=45)
    except BusinessException as error:
        if "检索失败" in error.message:
            raise
        # 不转发运输异常细节（可能包含内部地址、请求头）。
        raise BusinessException("当前无法查询资料（检索失败：MCP 服务不可用）", code=502) from None
    except Exception:
        raise BusinessException("当前无法查询资料（检索失败：MCP 服务不可用或调用超时）", code=502) from None
    return mcp_client_service._parse_search_result(result)
