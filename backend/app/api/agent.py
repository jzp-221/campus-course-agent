from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.common.response import Response
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.agent import AgentChatRequest
from app.services import agent_service
from app.services import agent_stream_service

router = APIRouter(prefix="/agent", tags=["课程智能助手"])


@router.post("/chat")
async def chat(
    data: AgentChatRequest,
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
):
    result = await agent_service.run_agent(
        authorization=request.headers.get("Authorization") or "",
        message=data.message,
        history=data.history,
    )
    return Response.success(data=result)


@router.post("/chat/stream")
async def chat_stream(
    data: AgentChatRequest,
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
):
    prepared = agent_stream_service.prepare_stream(
        authorization=request.headers.get("Authorization") or "",
        message=data.message,
        history=data.history,
    )
    return EventSourceResponse(
        prepared.events(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        send_timeout=15,
    )
