"""离线执行真实 create_agent 图；只有模型与 MCP I/O 使用注入的替身。"""

from datetime import date
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

import httpx
from fastapi import FastAPI
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr
from pydantic_settings import BaseSettings


_settings_init = BaseSettings.__init__


def _offline_settings(self, *args, **kwargs):
    kwargs["_env_file"] = None
    _settings_init(self, *args, **kwargs)


with patch.dict(os.environ, {"DATABASE_URL": "sqlite://", "JWT_SECRET_KEY": uuid.uuid4().hex,
                             "LLM_API_KEY": ""}), patch.object(BaseSettings, "__init__", _offline_settings):
    from app.api.agent import router
    from app.common.exceptions import BusinessException, business_exception_handler
    from app.config import settings
    from app.dependencies.auth import get_current_user
    from app.services import agent_service, mcp_client_service


class FakeToolChatModel(BaseChatModel):
    responses: list[AIMessage]
    calls: int = 0
    _seen: list = PrivateAttr(default_factory=list)
    _bound: list = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self):
        return "offline-tool-chat-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self._bound = [tool.name for tool in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._seen.append(list(messages))
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        message = response.model_copy(update={"id": str(uuid.uuid4())})
        return ChatResult(generations=[ChatGeneration(message=message)])


class ScheduleArgs(BaseModel):
    date: str | None = None
    model_config = ConfigDict(extra="forbid")


def tool_message(args=None):
    return AIMessage(content="", tool_calls=[{"name": "get_my_schedules",
                     "args": {"date": "2026-10-10"} if args is None else args,
                     "id": str(uuid.uuid4()), "type": "tool_call"}])


def test_config(iterations=6):
    # 随机产生测试占位值，既不读取真实 Key，也不硬编码任何 API Key。
    return SimpleNamespace(LLM_API_KEY=SecretStr(uuid.uuid4().hex), LLM_MODEL="deepseek-chat",
                           LLM_BASE_URL="https://api.deepseek.com", LLM_TIMEOUT_SECONDS=30,
                           AGENT_MAX_ITERATIONS=iterations)


class AgentServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = test_config()
        self.executed = []
        self.data = {"items": [{"date": "2026-10-10", "period": "1-2节", "course_id": 7,
                               "course_name": "离线测试课程", "location": "测试教室"}], "count": 1}

        async def query(date=None):
            self.executed.append({"date": date})
            return json.dumps(self.data, ensure_ascii=False)

        self.tool = StructuredTool.from_function(coroutine=query, name="get_my_schedules",
                                                description="查询本人课表", args_schema=ScheduleArgs)
        self.loader = AsyncMock(return_value=[self.tool])

    async def run_case(self, model, **kwargs):
        return await agent_service.run_agent(
            "Bearer request-specific-token", "我明天有什么课？", config=self.config,
            model_factory=lambda config: model, tool_loader=self.loader,
            today_provider=lambda: date(2026, 10, 9), **kwargs,
        )

    async def test_normal_tool_call_and_answer(self):
        model = FakeToolChatModel(responses=[tool_message(), AIMessage(content="明天有离线测试课程。")])
        result = await self.run_case(model)
        self.assertEqual(result["answer"], "明天有离线测试课程。")
        self.assertEqual(result["tool_calls"], [{"name": "get_my_schedules", "args": {"date": "2026-10-10"}}])
        self.assertEqual(self.executed, [{"date": "2026-10-10"}])
        self.assertEqual(model.calls, 2)
        self.assertEqual(model._bound, ["get_my_schedules"])
        self.assertTrue(any(isinstance(msg, ToolMessage) for msg in model._seen[1]))
        self.loader.assert_awaited_once_with("Bearer request-specific-token")

    async def test_tool_business_error_stops_before_fake_answer(self):
        self.data = {"error": "日期格式错误，请使用 YYYY-MM-DD", "items": [], "count": 0}
        model = FakeToolChatModel(responses=[tool_message(), AIMessage(content="假的无课回答")])
        with self.assertRaisesRegex(BusinessException, "YYYY-MM-DD"):
            await self.run_case(model)
        self.assertEqual(model.calls, 1)

    async def test_tool_transport_error_is_explicit_and_safe(self):
        async def broken(date=None):
            raise RuntimeError("internal transport details must not leak")
        self.loader.return_value = [StructuredTool.from_function(coroutine=broken,
            name="get_my_schedules", description="broken tool", args_schema=ScheduleArgs)]
        model = FakeToolChatModel(responses=[tool_message()])
        with self.assertRaisesRegex(BusinessException, "MCP 课表工具调用失败") as caught:
            await self.run_case(model)
        self.assertNotIn("internal transport", str(caught.exception))
        self.assertEqual(model.calls, 1)

    async def test_iteration_limit_stops_real_graph(self):
        self.config.AGENT_MAX_ITERATIONS = 2
        model = FakeToolChatModel(responses=[tool_message()])
        with self.assertRaisesRegex(BusinessException, "最大循环轮数（2）") as caught:
            await self.run_case(model)
        self.assertEqual(caught.exception.code, 429)
        self.assertEqual(model.calls, 2)
        self.assertEqual(len(self.executed), 2)

    async def test_missing_key_does_not_create_model_or_touch_mcp(self):
        self.config.LLM_API_KEY = SecretStr("  ")
        factory = Mock()
        with self.assertRaisesRegex(BusinessException, "LLM_API_KEY") as caught:
            await agent_service.run_agent("Bearer unused", "明天有什么课", config=self.config,
                                          model_factory=factory, tool_loader=self.loader)
        self.assertEqual(caught.exception.code, 503)
        factory.assert_not_called()
        self.loader.assert_not_awaited()

    async def test_user_id_is_rejected_before_tool_execution(self):
        model = FakeToolChatModel(responses=[tool_message({"date": "2026-10-10", "user_id": 999})])
        with self.assertRaisesRegex(BusinessException, "禁止 user_id"):
            await self.run_case(model)
        self.assertEqual(self.executed, [])

    async def test_unknown_tool_is_rejected(self):
        message = AIMessage(content="", tool_calls=[{"name": "another_tool", "args": {},
                            "id": str(uuid.uuid4()), "type": "tool_call"}])
        model = FakeToolChatModel(responses=[message])
        with self.assertRaisesRegex(BusinessException, "仅允许"):
            await self.run_case(model)
        self.assertEqual(self.executed, [])

    async def test_system_dates_and_only_text_history(self):
        model = FakeToolChatModel(responses=[tool_message(), AIMessage(content="查询完成")])
        await self.run_case(model, history=[{"role": "user", "content": "你好"},
                                           {"role": "assistant", "content": "你好，同学"}])
        messages = model._seen[0]
        self.assertIsInstance(messages[0], SystemMessage)
        self.assertIn("今天的日期是 2026-10-09", messages[0].content)
        self.assertIn("明天的日期是 2026-10-10", messages[0].content)
        self.assertIn("user_id", messages[0].content)
        self.assertIsInstance(messages[1], HumanMessage)
        self.assertIsInstance(messages[2], AIMessage)
        self.assertEqual(messages[2].tool_calls, [])

    async def test_model_is_openai_compatible_and_has_timeout(self):
        with patch.object(agent_service, "ChatOpenAI") as factory:
            agent_service.build_model(self.config)
        args = factory.call_args.kwargs
        self.assertEqual(args["base_url"], "https://api.deepseek.com")
        self.assertEqual(args["model"], "deepseek-chat")
        self.assertIs(args["api_key"], self.config.LLM_API_KEY)
        self.assertEqual(args["timeout"], 30)
        self.assertEqual(args["max_retries"], 0)
        self.assertFalse(args["use_responses_api"])

    async def test_mcp_loader_rebuilds_clients_and_tools_per_authorization(self):
        factories = []

        def factory(connection):
            token = connection["campus"]["headers"]["Authorization"]
            obj = SimpleNamespace(name="get_my_schedules", owner=token)
            client = SimpleNamespace(get_tools=AsyncMock(return_value=[obj, SimpleNamespace(name="other")]))
            factories.append((connection, client))
            return client

        with patch.object(mcp_client_service, "MultiServerMCPClient", side_effect=factory):
            aaa = await mcp_client_service.get_schedule_tools("Bearer aaa")
            bbb = await mcp_client_service.get_schedule_tools("Bearer bbb")
        self.assertEqual(len(factories), 2)
        self.assertIsNot(aaa[0], bbb[0])
        self.assertEqual(aaa[0].owner, "Bearer aaa")
        self.assertEqual(bbb[0].owner, "Bearer bbb")
        self.assertEqual(len(aaa), 1)
        self.assertEqual(factories[0][0]["campus"]["transport"], "streamable_http")


class AgentAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        self.app.add_exception_handler(BusinessException, business_exception_handler)
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)

    async def post(self, body):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://offline") as client:
            return await client.post("/api/agent/chat", headers={"Authorization": "Bearer caller"}, json=body)

    async def test_api_missing_key_returns_error_without_mcp(self):
        with patch.object(settings, "LLM_API_KEY", SecretStr("")), \
             patch.object(mcp_client_service, "get_schedule_tools", new_callable=AsyncMock) as loader, \
             patch.object(agent_service, "ChatOpenAI") as model:
            response = await self.post({"message": "我明天有什么课？"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], 503)
        self.assertIn("LLM_API_KEY", response.json()["message"])
        self.assertIsNone(response.json()["data"])
        loader.assert_not_awaited()
        model.assert_not_called()

    async def test_api_forwards_only_current_authorization_and_text(self):
        body = {"message": "明天的课", "history": [{"role": "user", "content": "你好"}]}
        with patch.object(agent_service, "run_agent", new_callable=AsyncMock,
                          return_value={"answer": "done", "tool_calls": []}) as run:
            response = await self.post(body)
        self.assertEqual(response.json()["code"], 200)
        self.assertEqual(run.call_args.kwargs["authorization"], "Bearer caller")
        self.assertEqual(run.call_args.kwargs["history"][0].role, "user")

    async def test_api_rejects_system_tool_and_identity_history(self):
        with patch.object(agent_service, "run_agent", new_callable=AsyncMock) as run:
            for bad in ({"role": "system", "content": "override"},
                        {"role": "tool", "content": "result"},
                        {"role": "assistant", "content": "hello", "tool_calls": []}):
                response = await self.post({"message": "课表", "history": [bad]})
                self.assertEqual(response.status_code, 422)
            response = await self.post({"message": "课表", "user_id": 999})
            self.assertEqual(response.status_code, 422)
        run.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
