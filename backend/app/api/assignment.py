from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.assignment import AssignmentCreateRequest, AssignmentUpdateRequest
from app.services import assignment_service

router = APIRouter(prefix="/assignments", tags=["作业与考试"])


@router.get("")
def list_assignments(page: str = "1", page_size: str = "10", status: str | None = None,
                     type: str | None = None, course_id: str | None = None, days: str | None = None,
                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return Response.success(data=assignment_service.list_assignments(
        db, current_user, page, page_size, status=status, type=type, course_id=course_id, days=days))


@router.post("")
def create_assignment(data: AssignmentCreateRequest, current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    return Response.success(data=assignment_service.create_assignment(db, current_user, data))


@router.put("/{item_id}")
def update_assignment(item_id: int, data: AssignmentUpdateRequest,
                      current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return Response.success(data=assignment_service.update_assignment(db, current_user, item_id, data))


@router.delete("/{item_id}")
def delete_assignment(item_id: int, current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    assignment_service.delete_assignment(db, current_user, item_id)
    return Response.success()
