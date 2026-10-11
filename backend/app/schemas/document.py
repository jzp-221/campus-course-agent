from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    id: int
    filename: str
    chunk_count: int
    status: Literal["processing", "indexed", "failed"]
    error_message: str | None = None
    uploader_id: int
    create_time: datetime
    update_time: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentPreviewResponse(DocumentResponse):
    extracted_text: str
