from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column
from sqlalchemy import create_engine, DateTime
from app.config import settings
from datetime import datetime
from sqlalchemy.exc import SQLAlchemyError
from app.common.db_errors import database_business_exception, rollback_safely

engine = create_engine(settings.DATABASE_URL)

# 数据库session会话的连接工厂
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    except SQLAlchemyError as error:
        # Includes authorization queries before a mutation service is reached.
        rollback_safely(db)
        raise database_business_exception(error) from None
    finally:
        try:
            db.close()
        except SQLAlchemyError as error:
            raise database_business_exception(error) from None


class Base(DeclarativeBase):
    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="主键ID", sort_order=-1
    )
    create_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, comment="创建时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
