from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.learning_time import local_now


class PlanTask(Base):
    __tablename__ = "plan_tasks"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'done')", name="ck_plan_tasks_status"),
        CheckConstraint("defer_count >= 0", name="ck_plan_tasks_defer_count"),
        {"comment": "学习计划任务（与文本计划并存）"},
    )

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    course_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("coruses.id"), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    defer_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=local_now)
    update_time: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now)
