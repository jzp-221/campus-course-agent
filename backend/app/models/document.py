from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Document(Base):
    """管理员共享知识库文档；原始文件与文本均不公开挂载。"""

    __tablename__ = "documents"
    __table_args__ = {"comment": "课程知识库文档"}

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    extracted_text: Mapped[str] = mapped_column(
        Text().with_variant(LONGTEXT(), "mysql"), nullable=False, default=""
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="processing")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploader_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
