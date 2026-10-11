from datetime import date

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Date, ForeignKey, Integer, String, text
from app.database import Base


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = {"comment": "课程信息"}

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), comment="学生ID", nullable=False
    )
    class_date: Mapped[date] = mapped_column(Date, comment="上课日期", nullable=False)
    period: Mapped[str] = mapped_column(String(50), comment="节次", nullable=True)
    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("coruses.id"), comment="课程ID", nullable=False
    )
    location: Mapped[str] = mapped_column(
        String(100), comment="上课地点", nullable=True
    )
    start_week: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False,
        comment="起始周次",
    )
    end_week: Mapped[int] = mapped_column(
        Integer, default=20, server_default=text("20"), nullable=False,
        comment="结束周次",
    )
    teacher: Mapped[str | None] = mapped_column(
        String(100), default=None, server_default=text("NULL"), nullable=True,
        comment="授课教师",
    )
