from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StudyPlan(Base):
    __tablename__ = "study_plans"
    __table_args__ = {"comment": "学习计划"}

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, comment="用户ID"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="计划内容")
