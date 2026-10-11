"""以门闩阻止模型结束，证明 token 在模型生成中已经发出。"""

import asyncio
from datetime import date
import json
import unittest
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import StructuredTool
from pydantic import PrivateAttr

from tests.test_agent import FakeToolChatModel, ScheduleArgs, test_config, tool_message
from app.services import agent_stream_service


class GatedModel(FakeToolChatModel):
    _release: object = PrivateAttr(default=None)
    _finished: object = PrivateAttr(default=None)
    _closed: object = PrivateAttr(default=None)
    _fail: bool = PrivateAttr(default=False)
    _choice: str = PrivateAttr(default="")
    _decision_started: object = PrivateAttr(default=None)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if self._decision_started is not None:
            self._decision_started.set()
            try:
                await self._release.wait()
            finally:
                self._closed.set()
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        self._seen.append(list(messages))
        self._choice = kwargs.get("tool_choice", "")
        try:
            yield ChatGenerationChunk(message=AIMessageChunk(content="第一段已到达。"))
            await self._release.wait()
            if self._fail:
                raise RuntimeError("offline failure must not leak")
            yield ChatGenerationChunk(message=AIMessageChunk(content="第二段继续生成。"))
            self._finished.set()
        finally:
            self._closed.set()


class ProgressiveStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        decision = tool_message()
        decision.content = "我先推测一下，稍后才请求工具。"
        self.model = GatedModel(responses=[decision, AIMessage(content="决策草稿不可展示")])
        self.model._release, self.model._finished, self.model._closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.queried = False

        async def query(date=None):
            self.queried = True
            return json.dumps({"items": [], "count": 0})

        self.tools = [StructuredTool.from_function(coroutine=query, name="get_my_schedules",
            description="query", args_schema=ScheduleArgs)]

    def prepared(self, config=None):
        return agent_stream_service.prepare_stream("Bearer student", "我明天有什么课",
            config=config or test_config(), model_factory=lambda config: self.model,
            tool_loader=AsyncMock(return_value=self.tools), today_provider=lambda: date(2026, 10, 9))

    async def first_token(self, stream):
        seen = []
        while True:
            item = await asyncio.wait_for(anext(stream), 2)
            seen.append((item["event"], json.loads(item["data"])))
            if item["event"] == "token":
                return seen

    async def test_token_arrives_before_model_round_finishes_and_after_tools(self):
        stream = self.prepared().events()
        try:
            first = await self.first_token(stream)
            self.assertEqual([name for name, _ in first], ["status", "status", "tool_start", "tool_end", "token"])
            self.assertFalse(self.model._finished.is_set(), "token 不得等待模型轮次结束")
            self.assertTrue(self.queried)
            self.assertEqual(self.model._choice, "none")
            self.assertTrue(all(not item.content for item in self.model._seen[-1]
                if isinstance(item, AIMessage) and item.tool_calls))
            self.assertEqual(first[-1][1]["text"], "第一段已到达。")
            self.model._release.set()
            remaining = [(item["event"], json.loads(item["data"])) async for item in stream]
            self.assertEqual([name for name, _ in remaining], ["token", "done"])
            self.assertEqual(remaining[-1][1]["answer"], "第一段已到达。第二段继续生成。")
            self.assertNotIn("推测", remaining[-1][1]["answer"])
            self.assertNotIn("草稿", remaining[-1][1]["answer"])
        finally:
            await stream.aclose()

    async def test_disconnect_after_first_token_closes_still_generating_model(self):
        stream = self.prepared().events()
        await self.first_token(stream)
        await asyncio.wait_for(stream.aclose(), 2)
        self.assertFalse(self.model._finished.is_set())
        self.assertTrue(self.model._closed.is_set())
        self.assertFalse(any(task.get_name() == "agent-sse-producer" and not task.done()
                             for task in asyncio.all_tasks()))

    async def test_disconnect_during_tool_decision_also_cancels_model(self):
        self.model._decision_started = asyncio.Event()
        stream = self.prepared().events()
        await anext(stream)
        await asyncio.wait_for(self.model._decision_started.wait(), 2)
        await asyncio.wait_for(stream.aclose(), 2)
        self.assertTrue(self.model._closed.is_set())
        self.assertFalse(self.queried)

    async def test_failure_after_first_token_terminates_with_error_not_done(self):
        self.model._fail = True
        stream = self.prepared().events()
        await self.first_token(stream)
        self.model._release.set()
        remaining = [(item["event"], json.loads(item["data"])) async for item in stream]
        self.assertEqual([name for name, _ in remaining], ["error"])
        self.assertEqual(remaining[0][1]["code"], 502)
        self.assertNotIn("offline failure", remaining[0][1]["message"])

    async def test_final_generation_also_counts_toward_model_limit(self):
        stream = self.prepared(test_config(iterations=2)).events()
        events = [(item["event"], json.loads(item["data"])) async for item in stream]
        self.assertEqual(events[-1][0], "error")
        self.assertEqual(events[-1][1]["code"], 429)
        self.assertFalse(any(name == "token" for name, _ in events))
        self.assertEqual(self.model.calls, 2)


if __name__ == "__main__":
    unittest.main()
