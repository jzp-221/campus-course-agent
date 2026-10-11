from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.common.response import Response
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.services import mcp_client_service

router = APIRouter(prefix="/mcp", tags=["MCP服务调试"])


@router.get("/my-schedule")
async def get_my_schedule_by_mcp(
    _current_user: Annotated[User, Depends(get_current_user)],
    request: Request,
    date: Annotated[
        str | None, Query(description="可选上课日期 YYYY-MM-DD；不传返回本人全部课表")
    ] = None,
):
    authorization = request.headers.get("Authorization") or ""
    data = await mcp_client_service.call_get_my_schedules(authorization, date)
    return Response.success(data=data)
