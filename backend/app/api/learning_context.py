"""学习页面的只读桥接；所有入口必须先验证当前 Authorization。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.services import learning_context_service

router = APIRouter(prefix="/learning-context", tags=["学习页面只读上下文"])


@router.get("")
def get_learning_context(
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return Response.success(data=learning_context_service.get_learning_context(db))


@router.get("/courses/{course_id}")
def get_course_context(
    course_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return Response.success(data=learning_context_service.get_course_context(db, course_id, current_user.id))


@router.get("/courses/{course_id}/docs")
async def get_course_docs(
    course_id: int,
    request: Request,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = learning_context_service.course_search_query(db, course_id)
    data = await learning_context_service.search_course_documents(
        request.headers.get("Authorization") or "", query,
    )
    return Response.success(data=data)
