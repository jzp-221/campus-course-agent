from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_admin
from app.models.user import User
from app.services import document_service

router = APIRouter(prefix="/documents", tags=["管理员知识库文档"])


@router.post("/upload")
def upload_documents(
    files: list[UploadFile] = File(...),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return Response.success(data=document_service.upload_documents(db, current_admin, files))


@router.get("")
def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    _current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return Response.success(data=document_service.list_documents(db, page, page_size))


@router.get("/{document_id}/preview")
def preview_document(
    document_id: int,
    _current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return Response.success(data=document_service.preview_document(db, document_id))


@router.post("/{document_id}/reindex")
def reindex_document(
    document_id: int,
    _current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return Response.success(data=document_service.reindex_document(db, document_id))


@router.delete("/{document_id}")
def delete_document(
    document_id: int,
    _current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    document_service.delete_document(db, document_id)
    return Response.success()
