"""离线 SSE：真实 Agent 图、ASGI disconnect、可控模型/MCP。"""

import asyncio
from datetime import date
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import StructuredTool
from pydantic import PrivateAttr, SecretStr

# 该模块关闭 .env 读取并提供离线模型，避免触及真实 Key。
from tests.test_agent import FakeToolChatModel, ScheduleArgs, test_config, tool_message
from app.api.agent import router
from app.common.exceptions import BusinessException, business_exception_handler, http_excpetion_hadler
from app.config import settings
from app.dependencies.auth import get_current_user
from app.services import agent_stream_service, mcp_client_service


class ChunkModel(FakeToolChatModel):
    _started: object = PrivateAttr(default=None)
    _released: object = PrivateAttr(default=None)
    _closed: object = PrivateAttr(default=None)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        if self._started is not None:
            self._started.set()
        try:
            if self._released is not None:
                await self._released.wait()
            response = self.responses[min(self.calls, len(self.responses) - 1)]
            self.calls += 1
            if response.tool_calls:
                # 中间轮先输出一段文字，随后才请求工具；不能泄漏为最终 token。
                yield ChatGenerationChunk(message=AIMessageChunk(content="我先查询一下。"))
                chunks = [{"name": item["name"], "args": json.dumps(item["args"]),
                           "id": item["id"], "index": index}
                          for index, item in enumerate(response.tool_calls)]
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=chunks))
            else:
                for text in ("明天", "有测试课", "。"):
                    yield ChatGenerationChunk(message=AIMessageChunk(content=text))
                    await asyncio.sleep(0)
        finally:
            if self._closed is not None:
                self._closed.set()


def decoded(items):
    return [(item["event"], json.loads(item["data"])) for item in items]


class AgentStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = test_config()
        self.model = ChunkModel(responses=[tool_message(), AIMessage(content="明天有测试课。")])
        self.data = {"items": [], "count": 0}
        self.tool_started = asyncio.Event()
        self.tool_closed = asyncio.Event()
        self.tool_release = None

        async def query(date=None):
            self.tool_started.set()
            try:
                if self.tool_release is not None:
                    await self.tool_release.wait()
                return json.dumps(self.data)
            finally:
                self.tool_closed.set()

        tool = StructuredTool.from_function(coroutine=query, name="get_my_schedules",
                                           description="查询本人课表", args_schema=ScheduleArgs)
        self.loader = AsyncMock(return_value=[tool])

    def prepared(self):
        return agent_stream_service.prepare_stream(
            "Bearer current-user", "明天有什么课", config=self.config,
            model_factory=lambda config: self.model, tool_loader=self.loader,
            today_provider=lambda: date(2026, 10, 9),
        )

    async def collect(self):
        return decoded([item async for item in self.prepared().events()])

    async def test_normal_sequence_and_exact_payloads(self):
        events = await self.collect()
        self.assertEqual([name for name, _ in events],
                         ["status", "status", "tool_start", "tool_end", "token", "token", "token", "done"])
        self.assertEqual(events[0][1], {"stage": "thinking"})
        self.assertEqual(events[1][1], {"stage": "querying"})
        self.assertEqual(events[2][1], {"name": "get_my_schedules", "args": {"date": "2026-10-10"}})
        self.assertEqual(events[3][1], {"name": "get_my_schedules", "ok": True})
        tokens = "".join(data["text"] for name, data in events if name == "token")
        self.assertEqual(tokens, events[-1][1]["answer"])
        self.assertNotIn("我先查询", tokens)
        self.assertEqual(events[-1][1]["tool_calls"], [events[2][1]])
        self.assertEqual(set(events[-1][1]), {"answer", "tool_calls"})
        self.loader.assert_awaited_once_with("Bearer current-user")

    async def test_tool_error_end_false_then_error_no_tokens(self):
        self.data = {"error": "日期格式错误，请使用 YYYY-MM-DD", "items": [], "count": 0}
        events = await self.collect()
        self.assertEqual([name for name, _ in events], ["status", "status", "tool_start", "tool_end", "error"])
        self.assertFalse(events[-2][1]["ok"])
        self.assertEqual(events[-1][1]["code"], 500)
        self.assertIn("YYYY-MM-DD", events[-1][1]["message"])

    async def test_limit_returns_429_error(self):
        self.config.AGENT_MAX_ITERATIONS = 2
        self.model = ChunkModel(responses=[tool_message()])
        events = await self.collect()
        self.assertEqual(events[-1][0], "error")
        self.assertEqual(events[-1][1]["code"], 429)
        self.assertEqual(sum(name == "tool_start" for name, _ in events), 2)
        self.assertFalse(any(name in {"token", "done"} for name, _ in events))

    async def test_missing_key_fails_before_stream_and_mcp(self):
        self.config.LLM_API_KEY = SecretStr("")
        with self.assertRaisesRegex(BusinessException, "LLM_API_KEY"):
            self.prepared()
        self.loader.assert_not_awaited()

    async def test_model_clients_are_request_owned_and_close_independently(self):
        # 使用真实 SDK 构造但不请求网络，防止一个流关闭另一请求的连接池。
        first = await agent_stream_service.build_stream_model(self.config)
        second = await agent_stream_service.build_stream_model(self.config)
        try:
            self.assertIsNot(first.http_client, second.http_client)
            self.assertIsNot(first.http_async_client, second.http_async_client)
            await agent_stream_service.close_model(first)
            self.assertTrue(first.http_client.is_closed)
            self.assertTrue(first.http_async_client.is_closed)
            self.assertFalse(second.http_client.is_closed)
            self.assertFalse(second.http_async_client.is_closed)
        finally:
            await agent_stream_service.close_model(second)

    async def test_forbidden_user_id_emits_error_without_tool_execution(self):
        self.model = ChunkModel(responses=[tool_message({"user_id": 5})])
        events = await self.collect()
        self.assertEqual([name for name, _ in events], ["status", "error"])
        self.assertEqual(events[-1][1]["code"], 400)
        self.assertFalse(self.tool_started.is_set())

    async def test_closing_generator_cancels_pending_model(self):
        self.model._started = asyncio.Event()
        self.model._released = asyncio.Event()
        self.model._closed = asyncio.Event()
        stream = self.prepared().events()
        await anext(stream)
        await asyncio.wait_for(self.model._started.wait(), 2)
        await asyncio.wait_for(stream.aclose(), 2)
        self.assertTrue(self.model._closed.is_set())
        self.assertFalse(any(task.get_name() == "agent-sse-producer" and not task.done()
                             for task in asyncio.all_tasks()))

    async def test_closing_generator_cancels_pending_mcp(self):
        self.tool_release = asyncio.Event()
        stream = self.prepared().events()
        await anext(stream)
        await asyncio.wait_for(self.tool_started.wait(), 2)
        await asyncio.wait_for(stream.aclose(), 2)
        self.assertTrue(self.tool_closed.is_set())


class StreamAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        self.app.add_exception_handler(BusinessException, business_exception_handler)
        from fastapi import HTTPException
        self.app.add_exception_handler(HTTPException, http_excpetion_hadler)

    async def test_unauthenticated_rejected_before_sse(self):
        with patch.object(agent_stream_service, "prepare_stream") as prepare:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://offline") as client:
                response = await client.post("/api/agent/chat/stream", json={"message": "课表"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertNotIn("text/event-stream", response.headers["content-type"])
        prepare.assert_not_called()

    async def test_missing_key_is_json_before_response_start(self):
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
        with patch.object(settings, "LLM_API_KEY", SecretStr("")), \
             patch.object(mcp_client_service, "get_schedule_tools", new_callable=AsyncMock) as loader:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://offline") as client:
                response = await client.post("/api/agent/chat/stream", headers={"Authorization": "Bearer test"},
                                             json={"message": "课表"})
        self.assertEqual(response.json()["code"], 503)
        self.assertIn("application/json", response.headers["content-type"])
        loader.assert_not_awaited()

    async def test_asgi_disconnect_cancels_running_producer(self):
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
        started, closed, disconnect = asyncio.Event(), asyncio.Event(), asyncio.Event()
        model = ChunkModel(responses=[AIMessage(content="回答")])
        model._started, model._released, model._closed = started, asyncio.Event(), closed
        async def query(date=None):
            return json.dumps({"items": [], "count": 0})
        tool = StructuredTool.from_function(coroutine=query, name="get_my_schedules",
                                           description="query", args_schema=ScheduleArgs)
        prepared = agent_stream_service.prepare_stream("Bearer test", "课表", config=test_config(),
            model_factory=lambda config: model, tool_loader=AsyncMock(return_value=[tool]))
        sent = []
        body = json.dumps({"message": "课表"}).encode()
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
                 "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": "/api/agent/chat/stream", "raw_path": b"/api/agent/chat/stream",
                 "query_string": b"", "headers": [(b"content-type", b"application/json"),
                 (b"authorization", b"Bearer test")], "server": ("offline", 80), "client": ("test", 1)}
        with patch.object(agent_stream_service, "prepare_stream", return_value=prepared):
            task = asyncio.create_task(self.app(scope, receive, send))
            started_task = asyncio.create_task(started.wait())
            completed, _ = await asyncio.wait([task, started_task], timeout=2,
                                               return_when=asyncio.FIRST_COMPLETED)
            if not started.is_set():
                started_task.cancel()
                disconnect.set()
                await task
                self.fail(f"模型未启动，ASGI 消息：{sent}")
            disconnect.set()
            await asyncio.wait_for(task, 2)
        self.assertTrue(closed.is_set())
        self.assertEqual(sent[0]["status"], 200)
        self.assertFalse(any(task.get_name() == "agent-sse-producer" and not task.done()
                             for task in asyncio.all_tasks()))


if __name__ == "__main__":
    unittest.main()
