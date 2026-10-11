from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.plan_task import PlanTaskCreateRequest, PlanTaskUpdateRequest, PlanTaskDeferRequest
from app.services import plan_task_service

router = APIRouter(prefix="/plan-tasks", tags=["学习计划任务"])


@router.get("")
def list_plan_tasks(page: str = "1", page_size: str = "10", status: str | None = None,
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return Response.success(data=plan_task_service.list_plan_tasks(db, current_user, page, page_size, status=status))


@router.post("")
def create_plan_task(data: PlanTaskCreateRequest, current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    return Response.success(data=plan_task_service.create_plan_task(db, current_user, data))


@router.put("/{item_id}")
def update_plan_task(item_id: int, data: PlanTaskUpdateRequest,
                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return Response.success(data=plan_task_service.update_plan_task(db, current_user, item_id, data))


@router.post("/{item_id}/defer")
def defer_plan_task(item_id: int, data: PlanTaskDeferRequest | None = None,
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return Response.success(data=plan_task_service.defer_plan_task(
        db, current_user, item_id, data.days if data is not None else 1))


@router.delete("/{item_id}")
def delete_plan_task(item_id: int, current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    plan_task_service.delete_plan_task(db, current_user, item_id)
    return Response.success()
