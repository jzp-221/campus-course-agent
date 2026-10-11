from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.study_plan import StudyPlanCreateRequest
from app.services import study_plan_service

router = APIRouter(prefix="/study-plans", tags=["学习计划相关的API接口"])


@router.get("")
def get_my_page_plans(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = study_plan_service.list_page_plans(db, current_user, page, page_size)
    return Response.success(data=data)


@router.post("")
def create_plan(
    data: StudyPlanCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = study_plan_service.create_study_plan(db, current_user, data)
    return Response.success(data=row)


@router.put("/{plan_id}")
def update_plan(
    plan_id: int,
    data: StudyPlanCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = study_plan_service.update_study_plan(db, current_user, plan_id, data)
    return Response.success(data=row)


@router.delete("/{plan_id}")
def delete_plan(
    plan_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    study_plan_service.delete_study_plan(db, current_user, plan_id)
    return Response.success()
