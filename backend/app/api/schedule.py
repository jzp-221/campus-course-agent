from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.common.exceptions import BusinessException
from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_admin, get_current_user
from app.models.user import User
from app.schemas.schedule import ScheduleCreateRequest, ScheduleUpdateRequest
from app.services import schedule_service, week_schedule_service

router = APIRouter(prefix="/schedules", tags=["课表相关的API接口"])


@router.get("")
def list_page_schedules(
    user_id: int | None = Query(default=None, description="按用户筛选（仅管理员使用）"),
    date: date | None = Query(default=None, description="上课日期，格式：YYYY-MM-DD"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role != "admin":
        user_id = current_user.id
    data = schedule_service.list_page_schedules(db, user_id, date, page, page_size)
    return Response.success(data=data)


@router.get("/week")
def get_week_schedules(
    week: str | None = Query(default=None, description="学期周次；省略时显示本周"),
    user_id: str | None = Query(default=None, description="指定学生账号 ID（仅管理员使用）"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_id = current_user.id
    explicit_student = current_user.role == "admin" and user_id is not None
    if explicit_student:
        target_id = week_schedule_service.parse_student_id(user_id)
    elif current_user.role not in {"student", "admin"}:
        raise BusinessException(message="无权限访问", code=403)
    # 学生的查询参数完全不参与目标选择；目标只能来自本次 JWT。
    data = week_schedule_service.get_week_schedules(
        db, target_id, week, require_student=explicit_student
    )
    return Response.success(data=data)


@router.post("")
def create_schedule(
    data: ScheduleCreateRequest,
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    res = schedule_service.create_schedule(db, data)
    return Response.success(data=res)


@router.put("/{schedule_id}")
def update_schedule(
    schedule_id: int,
    data: ScheduleUpdateRequest,
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    res = schedule_service.update_schedule(db, schedule_id, data)
    return Response.success(data=res)


@router.delete("/{schedule_id}")
def delete_schedule(
    schedule_id: int,
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    schedule_service.delete_schedule(db, schedule_id)
    return Response.success()
