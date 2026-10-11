"""通过 MCP 调用课表工具；每次请求单独携带本次用户的 Authorization。"""

import asyncio
import json
import logging
import math

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.common.exceptions import BusinessException
from app.config import STUDY_PLAN_MAX_LENGTH, settings

logger = logging.getLogger(__name__)
_CALL_TIMEOUT = 10
ALLOWED_AGENT_TOOLS = frozenset({"get_my_schedules", "save_study_plan", "search_course_docs"})


async def get_schedule_tools(authorization: str):
    """每次请求重建客户端，只发现白名单工具；保留原函数名以兼容调用方。"""
    try:
        client = MultiServerMCPClient(
            {
                "campus": {
                    "transport": "streamable_http",
                    "url": f"http://{settings.MCP_HOST}:{settings.MCP_PORT}{settings.MCP_PATH}",
                    "headers": {"Authorization": authorization},
                }
            }
        )
        tools = await asyncio.wait_for(
            client.get_tools(server_name="campus"), timeout=_CALL_TIMEOUT
        )
    except Exception:
        raise BusinessException(
            message="MCP 服务不可用，请确认已启动 mcp_server.py"
        ) from None
    selected = [tool for tool in tools if tool.name in ALLOWED_AGENT_TOOLS]
    names = [tool.name for tool in selected]
    if names.count("get_my_schedules") != 1:
        raise BusinessException(message="MCP 必须提供唯一的 get_my_schedules 工具")
    for name in ALLOWED_AGENT_TOOLS:
        if names.count(name) > 1:
            raise BusinessException(message=f"MCP 必须提供唯一的 {name} 工具")
    return selected


def _parse_tool_result(result) -> dict:
    """将 MCP 文本内容中的 JSON 转成接口可以返回的字典。"""
    try:
        if isinstance(result, str):
            text = result
        else:
            text = next(
                block["text"]
                for block in result
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
        data = json.loads(text)
    except (TypeError, ValueError, StopIteration):
        raise BusinessException(message="MCP 工具返回的数据格式不正确") from None

    if not isinstance(data, dict):
        raise BusinessException(message="MCP 工具返回的数据格式不正确")
    if data.get("error"):
        message = data["error"]
        raise BusinessException(
            message=message if isinstance(message, str) else "MCP 课表查询失败"
        )
    if (
        not isinstance(data.get("items"), list)
        or type(data.get("count")) is not int
        or data["count"] != len(data["items"])
    ):
        raise BusinessException(message="MCP 工具返回的数据格式不正确")
    return data


def _parse_save_result(result) -> dict:
    """校验保存工具的真实结果；错误结果绝不能转成保存成功。"""
    try:
        if isinstance(result, str):
            text = result
        else:
            text = next(
                block["text"]
                for block in result
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
        data = json.loads(text)
    except (TypeError, ValueError, StopIteration):
        raise BusinessException(message="MCP 保存工具返回的数据格式不正确") from None

    if not isinstance(data, dict):
        raise BusinessException(message="MCP 保存工具返回的数据格式不正确")
    if data.get("error"):
        code = data.get("code")
        raise BusinessException(
            message=(
                data["error"] if isinstance(data["error"], str)
                else "MCP 学习计划保存失败"
            ),
            code=code if type(code) is int and 400 <= code <= 599 else 500,
        )
    if (
        set(data) - {"id", "content", "create_time"}
        or type(data.get("id")) is not int
        or data["id"] <= 0
        or not isinstance(data.get("content"), str)
        or not data["content"].strip()
        or len(data["content"]) > STUDY_PLAN_MAX_LENGTH
        or (
            data.get("create_time") is not None
            and not isinstance(data["create_time"], str)
        )
    ):
        raise BusinessException(message="MCP 保存工具返回的数据格式不正确")
    return data


def _parse_search_result(result) -> dict:
    """失败与正常空结果严格区分，任何无原文/来源的片段均不可交给模型。"""
    invalid = "当前无法查询资料（检索失败：MCP 检索工具返回的数据格式不正确）"
    try:
        if isinstance(result, str):
            text = result
        else:
            text = next(block["text"] for block in result if isinstance(block, dict)
                        and block.get("type") == "text" and isinstance(block.get("text"), str))
        data = json.loads(text)
    except (TypeError, ValueError, StopIteration):
        raise BusinessException(message=invalid, code=502) from None
    if not isinstance(data, dict):
        raise BusinessException(message=invalid, code=502)
    if data.get("error"):
        detail = data["error"] if isinstance(data["error"], str) else "检索失败：课程知识库暂时不可用"
        if not detail.startswith("检索失败"):
            detail = f"检索失败：{detail}"
        code = data.get("code")
        raise BusinessException(message=f"当前无法查询资料（{detail}）",
                                code=code if type(code) is int and 400 <= code <= 599 else 500)
    if (set(data) != {"items", "count"} or not isinstance(data.get("items"), list)
            or type(data.get("count")) is not int or data["count"] != len(data["items"])
            or not 0 <= data["count"] <= 3):
        raise BusinessException(message=invalid, code=502)
    for item in data["items"]:
        if (not isinstance(item, dict)
                or set(item) - {"text", "source", "document_id", "chunk_index", "distance"}
                or not isinstance(item.get("text"), str) or not item["text"].strip()
                or not isinstance(item.get("source"), str) or not item["source"].strip()
                or type(item.get("document_id")) is not int or item["document_id"] <= 0
                or type(item.get("chunk_index")) is not int or item["chunk_index"] < 0
                or ("distance" in item and (type(item["distance"]) not in (int, float)
                    or not math.isfinite(item["distance"]) or item["distance"] < 0))):
            raise BusinessException(message=invalid, code=502)
    return data


async def call_get_my_schedules(
    authorization: str, date: str | None = None
) -> dict:
    """转发当前登录凭证，通过 MCP 查询本人的课表。"""
    try:
        client = MultiServerMCPClient(
            {
                "campus": {
                    "transport": "streamable_http",
                    "url": f"http://{settings.MCP_HOST}:{settings.MCP_PORT}{settings.MCP_PATH}",
                    "headers": {"Authorization": authorization},
                }
            }
        )
        tools = await asyncio.wait_for(
            client.get_tools(server_name="campus"), timeout=_CALL_TIMEOUT
        )
        tool = next((tool for tool in tools if tool.name == "get_my_schedules"), None)
        if tool is None:
            raise BusinessException(
                message="未找到工具 get_my_schedules，请确认 MCP 服务已正确启动"
            )
        payload = {"date": date} if date else {}
        result = await asyncio.wait_for(tool.ainvoke(payload), timeout=_CALL_TIMEOUT)
    except BusinessException:
        raise
    except Exception as exc:
        logger.warning("MCP 调用失败：%s", type(exc).__name__)
        raise BusinessException(
            message="MCP 服务不可用，请确认已启动 mcp_server.py"
        ) from None
    return _parse_tool_result(result)
