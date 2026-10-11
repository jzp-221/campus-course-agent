"""P3 保存意图、工具白名单及单轮成功次数防护；无网络。"""

import asyncio
from datetime import date
import json
import unittest
from unittest.mock import AsyncMock
import uuid

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict

from tests.test_agent import FakeToolChatModel, ScheduleArgs, test_config
from app.common.exceptions import BusinessException
from app.services import agent_service, agent_stream_service


class PlanArgs(BaseModel):
    content: str
    model_config = ConfigDict(extra="forbid")


class SavingStreamModel(FakeToolChatModel):
    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        for text in ("计划", "已保存"):
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))
            await asyncio.sleep(0)


def save_message(args=None, times=1):
    return AIMessage(content="", tool_calls=[{
        "name": "save_study_plan", "args": {"content": "课前预习，课后复习。"} if args is None else args,
        "id": uuid.uuid4().hex, "type": "tool_call",
    } for _ in range(times)])


class AgentStudyPlanTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved = []
        self.error = None

        async def query(date=None):
            return json.dumps({"items": [], "count": 0})

        async def save(content):
            await asyncio.sleep(0)
            if self.error:
                return json.dumps(self.error, ensure_ascii=False)
            self.saved.append(content)
            return json.dumps({"id": len(self.saved), "content": content, "create_time": None})

        self.tools = [
            StructuredTool.from_function(coroutine=query, name="get_my_schedules",
                                        description="查本人课表", args_schema=ScheduleArgs),
            StructuredTool.from_function(coroutine=save, name="save_study_plan",
                                        description="保存本人计划", args_schema=PlanArgs),
        ]
        self.loader = AsyncMock(return_value=self.tools)

    async def run_case(self, model, message="帮我生成一份学习计划并保存", history=None):
        return await agent_service.run_agent("Bearer student", message, history,
            config=test_config(), model_factory=lambda config: model, tool_loader=self.loader,
            today_provider=lambda: date(2026, 10, 9))

    async def test_explicit_save_succeeds_once(self):
        model = FakeToolChatModel(responses=[save_message(), AIMessage(content="已保存计划")])
        result = await self.run_case(model)
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(result["tool_calls"], [{"name": "save_study_plan", "args": {"content": self.saved[0]}}])

    async def test_claiming_saved_without_actual_save_is_rejected(self):
        model = FakeToolChatModel(responses=[AIMessage(content="假的：计划已保存")])
        with self.assertRaisesRegex(BusinessException, "未确认学习计划已保存"):
            await self.run_case(model)
        self.assertEqual(self.saved, [])

    async def test_stream_claim_without_save_is_rejected_before_any_token(self):
        model = FakeToolChatModel(responses=[AIMessage(content="假的已保存")])
        stream = agent_stream_service.prepare_stream("Bearer student", "帮我生成学习计划并保存",
            config=test_config(), model_factory=lambda config: model, tool_loader=self.loader)
        events = [(item["event"], json.loads(item["data"])) async for item in stream.events()]
        self.assertEqual([name for name, _ in events], ["status", "error"])
        self.assertEqual(events[-1][1]["code"], 502)
        self.assertEqual(self.saved, [])

    async def test_missing_save_tool_returns_explicit_error_before_model(self):
        self.loader.return_value = [self.tools[0]]
        model = FakeToolChatModel(responses=[AIMessage(content="假的已保存")])
        with self.assertRaises(BusinessException) as caught:
            await self.run_case(model)
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(model.calls, 0)

    async def test_stream_save_success_has_saving_stage_and_actual_record(self):
        model = SavingStreamModel(responses=[save_message(), AIMessage(content="计划已保存")])
        stream = agent_stream_service.prepare_stream("Bearer student", "帮我生成学习计划并保存",
            config=test_config(), model_factory=lambda config: model, tool_loader=self.loader)
        events = [(item["event"], json.loads(item["data"])) async for item in stream.events()]
        self.assertEqual([name for name, _ in events], ["status", "status", "tool_start", "tool_end", "token", "token", "done"])
        self.assertEqual(events[1][1], {"stage": "saving"})
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(events[-1][1]["tool_calls"], [events[2][1]])

    async def test_planning_alone_rejects_attempted_save(self):
        for message in ("帮我规划一下明天的学习安排", "不要保存，只规划一下", "请解释如何保存计划",
                        "帮我写一句‘请保存学习计划’作为示例", "以后我说请保存时，再保存"):
            with self.subTest(message=message):
                with self.assertRaises(BusinessException) as caught:
                    await self.run_case(FakeToolChatModel(responses=[save_message()]), message)
                self.assertEqual(caught.exception.code, 403)
        self.assertEqual(self.saved, [])

    async def test_history_cannot_authorize_current_save(self):
        with self.assertRaises(BusinessException) as caught:
            await self.run_case(FakeToolChatModel(responses=[save_message()]), "再规划一下",
                [{"role": "user", "content": "请保存计划"}, {"role": "assistant", "content": "已保存"}])
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(self.saved, [])

    async def test_forbidden_identity_args_rejected_before_io(self):
        for extra in ({"user_id": 99}, {"plan_id": 1}):
            with self.subTest(extra=extra):
                with self.assertRaises(BusinessException) as caught:
                    await self.run_case(FakeToolChatModel(responses=[save_message({"content": "计划", **extra})]))
                self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.saved, [])

    async def test_duplicate_sequential_save_rejected(self):
        model = FakeToolChatModel(responses=[save_message(), save_message(), AIMessage(content="假的成功")])
        with self.assertRaises(BusinessException) as caught:
            await self.run_case(model)
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(model.calls, 2)

    async def test_parallel_duplicate_save_rejected(self):
        model = FakeToolChatModel(responses=[save_message(times=2), AIMessage(content="假的成功")])
        with self.assertRaises(BusinessException) as caught:
            await self.run_case(model)
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(len(self.saved), 1)

    async def test_save_failure_stops_before_claiming_success(self):
        self.error = {"error": "计划保存失败", "code": 503}
        model = FakeToolChatModel(responses=[save_message(), AIMessage(content="假的已保存")])
        with self.assertRaisesRegex(BusinessException, "计划保存失败") as caught:
            await self.run_case(model)
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(model.calls, 1)
        self.assertEqual(self.saved, [])

    async def test_parallel_retry_after_committed_but_malformed_reply_cannot_write_twice(self):
        async def committed_but_uncertain(content):
            self.saved.append(content)
            await asyncio.sleep(0)
            return "response was lost after database commit"

        self.tools[1] = StructuredTool.from_function(coroutine=committed_but_uncertain,
            name="save_study_plan", description="uncertain response", args_schema=PlanArgs)
        model = FakeToolChatModel(responses=[save_message(times=2)])
        with self.assertRaises(BusinessException):
            await self.run_case(model)
        self.assertEqual(len(self.saved), 1, "提交后响应损坏时，排队的重复保存仍不能再落库")

    async def test_stream_duplicate_save_emits_error_without_second_start(self):
        model = FakeToolChatModel(responses=[save_message(), save_message()])
        stream = agent_stream_service.prepare_stream("Bearer student", "帮我生成学习计划并保存",
            config=test_config(), model_factory=lambda config: model, tool_loader=self.loader)
        events = [(item["event"], json.loads(item["data"])) async for item in stream.events()]
        self.assertEqual([name for name, _ in events], ["status", "status", "tool_start", "tool_end", "error"])
        self.assertEqual(events[1][1], {"stage": "saving"})
        self.assertTrue(events[-2][1]["ok"])
        self.assertEqual(events[-1][1]["code"], 409)
        self.assertEqual(len(self.saved), 1)

    async def test_content_limit_is_enforced_before_io(self):
        for content in ("", "  ", "课" * 2001):
            with self.subTest(length=len(content)):
                with self.assertRaises(BusinessException) as caught:
                    await self.run_case(FakeToolChatModel(responses=[save_message({"content": content})]))
                self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.saved, [])

    def test_save_intent_uses_explicit_current_instructions(self):
        for message in ("保存", "记录一下", "帮我生成一份学习计划并保存", "把这份计划存下来", "请保存上面的计划"):
            self.assertTrue(agent_service.has_save_intent(message), message)
        for message in ("帮我规划一下", "不要保存", "如何保存", "保存是什么意思", "这个计划很详细",
                        "帮我写一句‘请保存学习计划’作为示例", "以后我说请保存时，再保存"):
            self.assertFalse(agent_service.has_save_intent(message), message)


if __name__ == "__main__":
    unittest.main()
