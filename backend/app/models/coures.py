from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String
from app.database import Base


class Course(Base):
    __tablename__ = "coruses"
    __table_args__ = {"comment": "课程信息表"}

    name: Mapped[str] = mapped_column(String(50), comment="课程名称", nullable=False)
    code: Mapped[str] = mapped_column(
        String(50), comment="课程编码", unique=True, nullable=False
    )
    description: Mapped[str] = mapped_column(String(255), comment="简介", nullable=True)
    status: Mapped[int] = mapped_column(default=1, comment="状态：0-停用，1-正常")
