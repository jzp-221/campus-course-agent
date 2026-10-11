from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.learning_time import local_now


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        CheckConstraint("type IN ('assignment', 'exam')", name="ck_assignments_type"),
        CheckConstraint("status IN ('pending', 'done')", name="ck_assignments_status"),
        {"comment": "作业与考试"},
    )

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    course_id: Mapped[int] = mapped_column(Integer, ForeignKey("coruses.id"))
    type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    due_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Only Python/ORM defaults: no CURRENT_TIMESTAMP or other server defaults.
    create_time: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now)
