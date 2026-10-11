"""Translate expected database failures after rollback, never into success."""

from functools import wraps
import logging

from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError

from app.common.exceptions import BusinessException

logger = logging.getLogger(__name__)


def database_business_exception(error: SQLAlchemyError) -> BusinessException:
    # Never expose str(error): it can contain SQL, parameters and connection data.
    if isinstance(error, IntegrityError):
        return BusinessException("数据存在关联或唯一字段冲突，请先清理关联或核对数据", code=409)
    if isinstance(error, DataError):
        return BusinessException("提交的数据格式或长度不符合要求", code=400)
    return BusinessException("数据库暂时不可用，操作结果未确认，请稍后回查", code=503)


def rollback_safely(db):
    try:
        db.rollback()
        return True
    except SQLAlchemyError as error:
        # Preserve the original failure instead of replacing it with raw SQL.
        logger.warning("数据库回滚失败：%s", type(error).__name__)
        return False


def database_business_errors(operation):
    """Service boundary: domain prechecks first, constraint races safely fail."""
    @wraps(operation)
    def guarded(*args, **kwargs):
        db = args[0] if args else kwargs["db"]
        try:
            return operation(*args, **kwargs)
        except BusinessException:
            rollback_safely(db)
            raise
        except SQLAlchemyError as error:
            rollback_safely(db)
            logger.warning("数据库操作失败（%s）：%s", operation.__name__, type(error).__name__)
            raise database_business_exception(error) from None
    return guarded
