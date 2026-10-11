"""P4 MCP/Agent 资料工具离线契约与安全测试，不联网、不读取真实 Key。"""

import asyncio
from datetime import date
import inspect
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict

from tests.test_agent import FakeToolChatModel, ScheduleArgs, test_config
from tests import test_mcp_study_plan_tool as mcp_plan_tests
from tests import test_mcp_schedules as schedule_tests
from fastmcp import Client
from fastmcp.exceptions import ToolError

import mcp_server
from app.common.exceptions import BusinessException
from app.services import agent_service, agent_stream_service, mcp_client_service


class QueryArgs(BaseModel):
    query: str
    model_config = ConfigDict(extra="forbid")


def search_data():
    return {"items": [{"text": "实验课须提前10分钟到达，并携带实验记录本。",
                       "source": "实验课规定.txt", "document_id": 7,
                       "chunk_index": 0, "distance": 0.12}], "count": 1}


def search_message(args=None):
    return AIMessage(content="", tool_calls=[{
        "name": "search_course_docs", "args": {"query": "实验课提前多久到达？"} if args is None else args,
        "id": uuid.uuid4().hex, "type": "tool_call",
    }])


class RagStreamModel(FakeToolChatModel):
    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        self._seen.append(list(messages))
        for text in ("实验课须提前10分钟到达。", "来源：实验课规定.txt"):
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))
            await asyncio.sleep(0)


class AgentRagTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.queries = []
        self.data = search_data()

        async def schedules(date=None):
            return json.dumps({"items": [], "count": 0})

        async def search(query):
            self.queries.append(query)
            return json.dumps(self.data, ensure_ascii=False)

        self.tools = [
            StructuredTool.from_function(coroutine=schedules, name="get_my_schedules",
                description="查询本人课表", args_schema=ScheduleArgs),
            StructuredTool.from_function(coroutine=search, name="search_course_docs",
                description="课程资料检索", args_schema=QueryArgs),
        ]
        self.loader = AsyncMock(return_value=self.tools)

    async def run_case(self, model):
        return await agent_service.run_agent("Bearer current-student", "实验课规定如何要求到达时间？",
            config=test_config(), model_factory=lambda _: model, tool_loader=self.loader,
            today_provider=lambda: date(2026, 10, 10))

    async def test_retrieval_result_with_source_reaches_model_and_real_call_record(self):
        model = FakeToolChatModel(responses=[search_message(), AIMessage(
            content="实验课须提前10分钟到达。来源：实验课规定.txt")])
        result = await self.run_case(model)
        self.assertIn("实验课规定.txt", result["answer"])
        self.assertEqual(result["tool_calls"], [{"name": "search_course_docs", "args": {"query": self.queries[0]}}])
        self.loader.assert_awaited_once_with("Bearer current-student")
        prompt = model._seen[0][0].content
        for required in ("资料类问题必须先调用", "来源文件名", "知识库中没有检索到相关内容", "当前无法查询资料"):
            self.assertIn(required, prompt)

    async def test_normal_empty_is_a_result_not_retrieval_error(self):
        self.data = {"items": [], "count": 0}
        model = FakeToolChatModel(responses=[search_message(), AIMessage(content="知识库中没有检索到相关内容")])
        result = await self.run_case(model)
        self.assertEqual(result["answer"], "知识库中没有检索到相关内容")
        self.assertEqual(model.calls, 2)

    async def test_missing_embedding_key_error_never_becomes_no_hits_or_fake_answer(self):
        self.data = {"error": "检索失败：未配置 EMBEDDING_API_KEY", "code": 503}
        model = FakeToolChatModel(responses=[search_message(), AIMessage(content="假的：没有相关内容")])
        with self.assertRaises(BusinessException) as caught:
            await self.run_case(model)
        self.assertEqual(caught.exception.code, 503)
        self.assertIn("当前无法查询资料", caught.exception.message)
        self.assertIn("检索失败", caught.exception.message)
        self.assertNotIn("没有相关内容", caught.exception.message)
        self.assertEqual(model.calls, 1)

    async def test_malformed_or_source_less_result_stops_before_model_answer(self):
        self.data["items"][0].pop("source")
        model = FakeToolChatModel(responses=[search_message(), AIMessage(content="不能编造的回答")])
        with self.assertRaisesRegex(BusinessException, "当前无法查询资料"):
            await self.run_case(model)
        self.assertEqual(model.calls, 1)

    async def test_query_only_and_bounds_reject_before_io(self):
        for args in ({}, {"query": ""}, {"query": "  "}, {"query": 9},
                     {"query": "问" * 2001}, {"query": "资料", "user_id": 2},
                     {"query": "资料", "document_id": 1}, {"query": "资料", "plan_id": 4}):
            with self.subTest(args=args), self.assertRaises(BusinessException) as caught:
                await self.run_case(FakeToolChatModel(responses=[search_message(args)]))
            self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.queries, [])

    async def test_transport_failure_is_retrieval_failure_and_sanitized(self):
        async def broken(query):
            raise RuntimeError("private embedding key must never leak")
        self.tools[1] = StructuredTool.from_function(coroutine=broken, name="search_course_docs",
            description="broken", args_schema=QueryArgs)
        with self.assertRaises(BusinessException) as caught:
            await self.run_case(FakeToolChatModel(responses=[search_message()]))
        self.assertEqual(caught.exception.code, 502)
        self.assertIn("当前无法查询资料", caught.exception.message)
        self.assertNotIn("private", caught.exception.message)

    async def test_stream_retrieving_tool_pair_precedes_progressive_answer(self):
        model = RagStreamModel(responses=[search_message(), AIMessage(content="READY")])
        stream = agent_stream_service.prepare_stream("Bearer current-student", "实验课的到达规定？",
            config=test_config(), model_factory=lambda _: model, tool_loader=self.loader)
        events = [(item["event"], json.loads(item["data"])) async for item in stream.events()]
        self.assertEqual([name for name, _ in events],
                         ["status", "status", "tool_start", "tool_end", "token", "token", "done"])
        self.assertEqual(events[1][1], {"stage": "retrieving"})
        self.assertEqual(events[2][1]["name"], "search_course_docs")
        self.assertTrue(events[3][1]["ok"])
        self.assertEqual(events[-1][1]["tool_calls"], [events[2][1]])
        self.assertIn("实验课规定.txt", events[-1][1]["answer"])

    async def test_stream_retrieval_error_is_not_empty_done(self):
        self.data = {"error": "检索失败：未配置 EMBEDDING_API_KEY", "code": 503}
        model = RagStreamModel(responses=[search_message(), AIMessage(content="假的无资料")])
        stream = agent_stream_service.prepare_stream("Bearer current-student", "请检索课程资料",
            config=test_config(), model_factory=lambda _: model, tool_loader=self.loader)
        events = [(item["event"], json.loads(item["data"])) async for item in stream.events()]
        self.assertEqual([name for name, _ in events], ["status", "status", "tool_start", "tool_end", "error"])
        self.assertFalse(events[-2][1]["ok"])
        self.assertEqual(events[-1][1]["code"], 503)
        self.assertIn("当前无法查询资料", events[-1][1]["message"])
        self.assertEqual(model.calls, 1)


class RagResultTests(unittest.TestCase):
    def test_success_and_normal_empty_are_valid_for_text_or_blocks(self):
        optional_distance = search_data()
        optional_distance["items"][0].pop("distance")
        for data in (search_data(), optional_distance, {"items": [], "count": 0}):
            text = json.dumps(data, ensure_ascii=False)
            for payload in (text, [{"type": "text", "text": text}]):
                self.assertEqual(mcp_client_service._parse_search_result(payload), data)

    def test_error_preserves_code_and_never_returns_items(self):
        for code in (401, 500, 502, 503):
            with self.subTest(code=code), self.assertRaises(BusinessException) as caught:
                mcp_client_service._parse_search_result(json.dumps(
                    {"error": "检索失败：测试错误", "code": code, "items": [], "count": 0}))
            self.assertEqual(caught.exception.code, code)
            self.assertIn("当前无法查询资料", caught.exception.message)

    def test_invalid_result_shape_and_nonfinite_distance_fail_closed(self):
        bad = [None, "private invalid json", "[]", "{}", json.dumps({"items": [], "count": True}),
               json.dumps({"items": [], "count": 1}), json.dumps({"items": [search_data()["items"][0]] * 4, "count": 4})]
        for field, value in (("source", ""), ("text", " "), ("document_id", True),
                             ("chunk_index", -1), ("distance", float("nan")), ("distance", float("inf"))):
            data = search_data()
            data["items"][0][field] = value
            bad.append(json.dumps(data))
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(BusinessException) as caught:
                mcp_client_service._parse_search_result(payload)
            self.assertEqual(caught.exception.code, 502)
            self.assertNotIn("private", caught.exception.message)


class MCPSearchToolTests(unittest.IsolatedAsyncioTestCase):
    setUp = mcp_plan_tests.MCPStudyPlanToolTests.setUp
    tearDown = mcp_plan_tests.MCPStudyPlanToolTests.tearDown

    async def search(self, args=None, *, authorization="default"):
        if authorization == "default":
            authorization = f"Bearer {self.tokens['aaa']}"
        headers = {} if authorization is None else {"authorization": authorization}
        with patch.object(mcp_server, "get_http_headers", return_value=headers):
            async with Client(mcp_server.mcp) as client:
                result = await client.call_tool("search_course_docs", args or {"query": "实验课规定"})
        return schedule_tests.MCPScheduleTests.tool_data(result)

    async def test_readonly_schema_exposes_only_query_never_user_id(self):
        self.assertEqual(set(inspect.signature(mcp_server.search_course_docs.fn).parameters), {"query"})
        async with Client(mcp_server.mcp) as client:
            tool = next(tool for tool in await client.list_tools() if tool.name == "search_course_docs")
        self.assertEqual(set(tool.inputSchema["properties"]), {"query"})
        self.assertEqual(tool.inputSchema["required"], ["query"])
        self.assertFalse(tool.inputSchema["additionalProperties"])
        self.assertTrue(tool.annotations.readOnlyHint)

    async def test_identity_is_checked_before_embedding_or_chroma(self):
        with patch.object(mcp_server.rag_service, "search_course_docs") as search:
            for authorization in (None, "Bearer forged-token"):
                data = await self.search(authorization=authorization)
                self.assertEqual(data["code"], 401)
                self.assertIn("检索失败", data["error"])
                self.assertNotIn("items", data)
            search.assert_not_called()

    async def test_valid_identity_success_and_business_failure_are_distinct(self):
        with patch.object(mcp_server.rag_service, "search_course_docs", return_value=search_data()) as search:
            data = await self.search()
            self.assertEqual(data, search_data())
            search.assert_called_once_with("实验课规定")
        with patch.object(mcp_server.rag_service, "search_course_docs",
                          side_effect=BusinessException("检索失败：未配置 EMBEDDING_API_KEY", code=503)):
            data = await self.search()
            self.assertEqual(data["code"], 503)
            self.assertNotIn("items", data)

    async def test_extra_identity_params_are_rejected_before_search(self):
        with patch.object(mcp_server.rag_service, "search_course_docs") as search:
            for extra in ("user_id", "document_id", "plan_id"):
                with self.subTest(extra=extra), self.assertRaises(ToolError):
                    await self.search({"query": "资料", extra: 5})
            search.assert_not_called()


class RagDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_rebuilds_all_three_tools_per_current_authorization(self):
        connections = []

        def factory(connection):
            connections.append(connection)
            authorization = connection["campus"]["headers"]["Authorization"]
            tools = [SimpleNamespace(name=name, authorization=authorization)
                     for name in mcp_client_service.ALLOWED_AGENT_TOOLS]
            return SimpleNamespace(get_tools=AsyncMock(return_value=tools))

        with patch.object(mcp_client_service, "MultiServerMCPClient", side_effect=factory):
            aaa = await mcp_client_service.get_schedule_tools("Bearer aaa")
            bbb = await mcp_client_service.get_schedule_tools("Bearer bbb")
        self.assertEqual(len(connections), 2)
        self.assertEqual({tool.name for tool in aaa}, mcp_client_service.ALLOWED_AGENT_TOOLS)
        self.assertTrue(all(tool.authorization == "Bearer aaa" for tool in aaa))
        self.assertTrue(all(tool.authorization == "Bearer bbb" for tool in bbb))
        self.assertFalse(any(a is b for a in aaa for b in bbb))

    async def test_duplicate_search_and_unknown_agent_tool_are_rejected(self):
        tools = [SimpleNamespace(name=name) for name in
                 ("get_my_schedules", "search_course_docs", "search_course_docs")]
        with patch.object(mcp_client_service, "MultiServerMCPClient", return_value=SimpleNamespace(
                          get_tools=AsyncMock(return_value=tools))), self.assertRaises(BusinessException):
            await mcp_client_service.get_schedule_tools("Bearer aaa")
        with self.assertRaises(BusinessException):
            agent_service.validate_tools([SimpleNamespace(name="get_my_schedules"), SimpleNamespace(name="delete_all")])


if __name__ == "__main__":
    unittest.main()
