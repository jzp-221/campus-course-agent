"""调用方持有文字历史；不接受系统消息、工具消息或用户身份参数。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10000)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=50)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
