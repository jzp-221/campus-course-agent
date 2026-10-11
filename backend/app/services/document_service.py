"""文档记录、私有原文与向量的协调事务。

SQL、文件系统和 Chroma 没有共同事务：批量只在全部入库后提交 SQL，
其余失败用文档 ID 做补偿；补偿失败会明确返回，绝不宣称已完全回滚。
"""

from pathlib import Path
from functools import wraps
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.db_errors import (
    database_business_errors, database_business_exception, rollback_safely,
)
from app.common.response import PageResponse
from app.config import DOCUMENT_DIR, MAX_FILE_SIZE
from app.models.document import Document
from app.models.user import User
from app.schemas.document import DocumentPreviewResponse, DocumentResponse
from app.services import document_text_service, rag_service, rag_vector_service

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}
MAX_BATCH_FILES = 10


def _filename(upload: UploadFile) -> str:
    # Windows 浏览器也可能传入路径；仅保留展示名，落盘始终使用随机名。
    name = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
        raise BusinessException("文件名不能为空、过长或含控制字符", code=400)
    if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise BusinessException("仅支持 .txt / .md / .pdf / .docx 文档", code=400)
    return name


def _reason(error: Exception) -> str:
    # 业务异常允许给出抽取、embedding、向量的已净化说明。
    if isinstance(error, BusinessException):
        return error.message
    if isinstance(error, SQLAlchemyError):
        return database_business_exception(error).message
    if isinstance(error, OSError):
        return "私有文档文件读写失败"
    return "文档处理或数据库写入失败"


def _error_code(error: Exception) -> int:
    if isinstance(error, BusinessException):
        return error.code
    if isinstance(error, SQLAlchemyError):
        return database_business_exception(error).code
    return 500


def _get_document(db: Session, document_id: int) -> Document:
    row = db.get(Document, document_id)
    if row is None:
        raise BusinessException("文档不存在", code=404)
    return row


def _private_path(row: Document) -> Path:
    path = Path(row.storage_path).resolve()
    # 防止损坏的数据库记录使管理员接口删除知识库目录之外的文件。
    if path.parent != Path(DOCUMENT_DIR).resolve():
        raise BusinessException("文档存储路径异常", code=500)
    return path


def _locked_document_operation(operation):
    """相同文档的重建/删除及失败补偿不可交叉；先文档锁，再内部向量锁。"""
    @wraps(operation)
    def locked(db: Session, document_id: int, *args, **kwargs):
        with rag_vector_service.document_operation_lock(document_id):
            # 鉴权或调用方可能已开启 REPEATABLE READ；等待后必须开启新快照。
            db.rollback()
            db.expire_all()
            return operation(db, document_id, *args, **kwargs)
    return locked


@database_business_errors
def upload_documents(
    db: Session, current_admin: User, files: list[UploadFile]
) -> list[DocumentResponse]:
    """在 FastAPI 同步路由的工作线程执行；一个 Session 串行处理整批。"""
    if not files:
        raise BusinessException("请至少上传一份文档", code=400)
    if len(files) > MAX_BATCH_FILES:
        raise BusinessException(f"每批最多上传 {MAX_BATCH_FILES} 份文档", code=400)

    rows: list[Document] = []
    attempted_ids: list[int] = []
    written_paths: list[Path] = []
    current_filename = "未知文件"
    try:
        Path(DOCUMENT_DIR).mkdir(parents=True, exist_ok=True)
        for upload in files:
            current_filename = (upload.filename or "未知文件").replace("\\", "/").rsplit("/", 1)[-1]
            filename = _filename(upload)
            if upload.size is not None and upload.size > MAX_FILE_SIZE:
                raise BusinessException(
                    f"文件不能超过 {MAX_FILE_SIZE // 1024 // 1024}MB", code=400
                )
            content = upload.file.read(MAX_FILE_SIZE + 1)
            if len(content) > MAX_FILE_SIZE:
                raise BusinessException(
                    f"文件不能超过 {MAX_FILE_SIZE // 1024 // 1024}MB", code=400
                )
            if not content:
                raise BusinessException("文档内容为空", code=400)

            path = Path(DOCUMENT_DIR) / f"{uuid4().hex}{Path(filename).suffix.lower()}"
            row = Document(
                filename=filename,
                storage_path=str(path.resolve()),
                uploader_id=current_admin.id,
                status="processing",
                extracted_text="",
                chunk_count=0,
            )
            db.add(row)
            db.flush()
            rows.append(row)
            # 在写文件和调用向量服务前登记，失败到一半同样可补偿。
            written_paths.append(path)
            path.write_bytes(content)
            text = document_text_service.extract_text(filename, content)
            chunks = document_text_service.chunk_text(text, filename=filename)
            if not text.strip() or not chunks:
                raise BusinessException("文档没有可入库的文本内容", code=400)
            row.extracted_text = text
            attempted_ids.append(row.id)
            rag_service.index_document(row.id, filename, chunks)
            row.chunk_count = len(chunks)
            row.status = "indexed"
            row.error_message = None

        # DTO 验证也必须在提交之前；提交成功后不再做可能失败的 ORM 读取。
        db.flush()
        result = [DocumentResponse.model_validate(row) for row in rows]
        db.commit()
        return result
    except Exception as error:
        rolled_back = rollback_safely(db)
        cleanup_errors = [] if rolled_back else ["数据库回滚未确认，请回查记录"]
        for document_id in reversed(attempted_ids):
            try:
                rag_service.delete_document(document_id)
            except Exception:
                cleanup_errors.append(f"文档 {document_id} 的向量清理失败")
        for path in reversed(written_paths):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                cleanup_errors.append("私有原文文件清理失败")
        message = f"文件「{current_filename}」入库失败：{_reason(error)}"
        if cleanup_errors:
            message += "；补偿清理未完成：" + "、".join(cleanup_errors) + "，请管理员重试清理"
        elif isinstance(error, SQLAlchemyError) and _error_code(error) == 503:
            # A lost connection during COMMIT cannot confirm the SQL outcome.
            # Files/vectors are compensated, but do not claim the SQL batch vanished.
            message += "；已执行文件和向量补偿清理，请回查数据库记录"
        else:
            message += "；整批已回滚"
        raise BusinessException(message, code=_error_code(error)) from None


def list_documents(db: Session, page: int, page_size: int) -> PageResponse:
    query = db.query(Document)
    total = query.count()
    rows = query.order_by(Document.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return PageResponse(
        list=[DocumentResponse.model_validate(row) for row in rows], total=total
    )


def preview_document(db: Session, document_id: int) -> DocumentPreviewResponse:
    return DocumentPreviewResponse.model_validate(_get_document(db, document_id))


@database_business_errors
@_locked_document_operation
def reindex_document(db: Session, document_id: int) -> DocumentResponse:
    row = _get_document(db, document_id)
    try:
        # MCP 只检索已提交且 indexed 的记录；先持久化此状态，重建中不可见。
        row.status = "processing"
        row.error_message = None
        db.commit()
        content = _private_path(row).read_bytes()
        if len(content) > MAX_FILE_SIZE:
            raise BusinessException("文档超过大小限制", code=400)
        text = document_text_service.extract_text(row.filename, content)
        chunks = document_text_service.chunk_text(text, filename=row.filename)
        if not text.strip() or not chunks:
            raise BusinessException("文档没有可入库的文本内容", code=400)
        rag_service.index_document(row.id, row.filename, chunks)
        row.extracted_text = text
        row.chunk_count = len(chunks)
        row.status = "indexed"
        row.error_message = None
        db.flush()
        result = DocumentResponse.model_validate(row)
        db.commit()
        return result
    except Exception as error:
        rollback_safely(db)
        cleanup_error = ""
        try:
            rag_service.delete_document(document_id)
        except Exception:
            cleanup_error = "；残余向量清理失败，请管理员重试"
        row = _get_document(db, document_id)
        row.status = "failed"
        row.chunk_count = 0
        row.error_message = _reason(error) + cleanup_error
        db.commit()
        raise BusinessException(
            f"文件「{row.filename}」重新入库失败：{row.error_message}",
            code=_error_code(error),
        ) from None


@database_business_errors
@_locked_document_operation
def delete_document(db: Session, document_id: int) -> None:
    row = _get_document(db, document_id)
    filename = row.filename
    try:
        # 向量删除成功之前不删记录或文件，失败可通过相同 ID 重试。
        rag_service.delete_document(document_id)
        _private_path(row).unlink(missing_ok=True)
        db.delete(row)
        db.commit()
    except Exception as error:
        rollback_safely(db)
        row = db.get(Document, document_id)
        if row is None:
            # A COMMIT may succeed on the server before its acknowledgement is lost.
            # Do not replace that uncertain SQL result with an unrelated 404.
            raise BusinessException(
                f"文件「{filename}」删除结果未确认，请回查文档列表：{_reason(error)}",
                code=_error_code(error),
            ) from None
        row.status = "failed"
        row.error_message = "删除失败：" + _reason(error)
        db.commit()
        raise BusinessException(
            f"文件「{filename}」删除失败，记录保留供重试：{_reason(error)}",
            code=_error_code(error),
        ) from None
