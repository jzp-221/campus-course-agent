"""独立的 FastMCP HTTP 服务：在 backend 目录执行 python mcp_server.py。"""

import asyncio
import logging
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from pydantic import Field

from app.common.exceptions import BusinessException
from app.config import settings
from app.database import SessionLocal
from app.dependencies.mcp_auth import user_id_from_authorization
from app.models.user import User
from app.schemas.study_plan import StudyPlanCreateRequest
from app.services.schedule_service import list_schedules_as_dict
from app.services.study_plan_service import save_plan
from app.services import rag_service

logger = logging.getLogger(__name__)
mcp = FastMCP("campus-course-agent")


def _current_user_id() -> int:
    """身份只取自当前 HTTP 请求头，不接受工具参数传入用户 ID。"""
    # FastMCP 2.x 使用 include_all；取得请求头后仅使用 Authorization。
    headers = get_http_headers(include_all=True) or {}
    authorization = headers.get("authorization") or headers.get("Authorization")
    return user_id_from_authorization(authorization)


@mcp.tool()
def get_my_schedules(
    date: Annotated[
        str | None,
        Field(description="可选上课日期，格式 YYYY-MM-DD；不传则返回本人的全部课表"),
    ] = None,
) -> dict:
    """查询当前登录用户本人的课表，可按上课日期筛选。"""
    try:
        user_id = _current_user_id()
        with SessionLocal() as db:
            items = list_schedules_as_dict(db, user_id, date)
        return {"items": items, "count": len(items)}
    except BusinessException as exc:
        return {"error": exc.message, "items": [], "count": 0}
    except Exception as exc:
        # 数据库错误等内部细节不传给工具调用方，也不记录 JWT 或 SQL 参数。
        logger.error("课表查询发生内部错误：%s", type(exc).__name__)
        return {"error": "查询课表失败，请稍后重试", "items": [], "count": 0}


@mcp.tool()
def save_study_plan(
    content: Annotated[
        str,
        Field(
            strict=True,
            description="学习计划正文，最多2000字符；仅当用户明确要求保存或记录时调用",
        ),
    ],
) -> dict:
    """保存当前登录用户本人的学习计划；用户身份仅来自 Authorization。"""
    try:
        user_id = _current_user_id()
        with SessionLocal() as db:
            current_user = (
                db.query(User).filter(User.id == user_id, User.status == 1).first()
            )
            if current_user is None:
                raise BusinessException(message="登录已失效或用户已被禁用", code=401)
            saved = save_plan(
                db, current_user, StudyPlanCreateRequest(content=content)
            )
            return saved.model_dump(mode="json")
    except BusinessException as exc:
        return {"error": exc.message, "code": exc.code}
    except Exception as exc:
        # 不返回数据库、连接配置或 JWT 等内部信息，也不声称保存成功。
        logger.error("学习计划保存发生内部错误：%s", type(exc).__name__)
        return {"error": "保存学习计划失败，请稍后重试", "code": 500}


# FastMCP 2.x 会压缩函数 schema；显式公布它实际执行的额外参数拒绝规则。
save_study_plan.parameters["additionalProperties"] = False


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def search_course_docs(
    query: Annotated[
        str,
        Field(strict=True, min_length=1, max_length=2000,
              description="课程资料检索问题；返回最多3个原文片段及来源文件名"),
    ],
) -> dict:
    """检索全站共享课程知识库；身份仅来自当次 Authorization，禁止传入用户ID。"""
    try:
        _current_user_id()
        # 文本 embedding 与持久化 Chroma 都为同步 I/O，避免阻塞 MCP 事件循环。
        return await asyncio.to_thread(rag_service.search_course_docs, query)
    except BusinessException as exc:
        message = exc.message if exc.message.startswith("检索失败") else f"检索失败：{exc.message}"
        return {"error": message, "code": exc.code}
    except Exception as exc:
        logger.error("课程资料检索发生内部错误：%s", type(exc).__name__)
        return {"error": "检索失败：课程知识库暂时不可用，请稍后重试", "code": 500}


search_course_docs.parameters["additionalProperties"] = False


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=settings.MCP_HOST,
        port=settings.MCP_PORT,
        path=settings.MCP_PATH,
        uvicorn_config={"ws": "websockets"},
    )
