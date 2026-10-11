"""相对星期与检索依据规则；仅使用离线模型和 MCP 替身。"""

from datetime import date
import json
import unittest
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import StructuredTool

from tests.test_agent import (
    FakeToolChatModel, ScheduleArgs, agent_service, test_config, tool_message,
)


class AgentPromptDateTests(unittest.TestCase):
    def test_saturday_maps_elapsed_wednesday_to_next_week(self):
        prompt = agent_service.build_system_prompt(date(2026, 10, 10))
        self.assertIn("本周一=2026-10-05", prompt)
        self.assertIn("本周三=2026-10-07", prompt)
        self.assertIn("本周日=2026-10-11", prompt)
        self.assertIn("下周三=2026-10-14", prompt)
        self.assertIn("星期三=2026-10-14", prompt)
        self.assertIn("若该星期在本周已经过去，应理解为下周同一天", prompt)
        self.assertIn("这周三（10-14）", prompt)
        self.assertIn("必须说明实际查询日期", prompt)
        self.assertIn("尊重其历史查询意图，不滚动到下周", prompt)

    def test_same_day_and_remaining_weekdays_do_not_roll_forward(self):
        prompt = agent_service.build_system_prompt(date(2026, 10, 10))
        self.assertIn("星期六=2026-10-10", prompt)
        self.assertIn("星期日=2026-10-11", prompt)
        self.assertIn("下周六=2026-10-17", prompt)
        self.assertIn("明确说‘下周’时使用下周的自然日历映射", prompt)

    def test_weekday_mapping_crosses_year_boundary(self):
        prompt = agent_service.build_system_prompt(date(2026, 1, 3))
        self.assertIn("本周一=2025-12-29", prompt)
        self.assertIn("下周三=2026-01-07", prompt)
        self.assertIn("星期三=2026-01-07", prompt)
        self.assertIn("星期日=2026-01-04", prompt)

    def test_retrieved_text_without_answer_has_distinct_response(self):
        prompt = agent_service.build_system_prompt(date(2026, 10, 10))
        self.assertIn("检索到的片段没有正面回答问题时，必须明说", prompt)
        self.assertIn("检索到的资料未包含该问题的答案", prompt)
        self.assertIn("query 必须逐字使用当次用户问题原文", prompt)
        self.assertIn("不得改写、扩词或追加关键词", prompt)
        self.assertIn("不得用片段里的其它内容作答或编造", prompt)
        self.assertIn("知识库中没有检索到相关内容", prompt)
        self.assertIn("当前无法查询资料", prompt)


class AgentPromptInjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_saturday_wednesday_question_receives_date_mapping_in_system_message(self):
        async def query(date=None):
            return json.dumps({"items": [{"date": date, "period": "1-2节", "course_id": 7,
                "course_name": "离线测试课程", "location": "测试教室"}], "count": 1})

        tool = StructuredTool.from_function(coroutine=query, name="get_my_schedules",
                                            description="查询本人课表", args_schema=ScheduleArgs)
        loader = AsyncMock(return_value=[tool])
        model = FakeToolChatModel(responses=[
            tool_message({"date": "2026-10-14"}),
            AIMessage(content="这周三（10-14）有离线测试课程。"),
        ])
        result = await agent_service.run_agent(
            "Bearer offline-caller", "我这周三有几门课？", config=test_config(),
            model_factory=lambda config: model, tool_loader=loader,
            today_provider=lambda: date(2026, 10, 10),
        )
        system_message = model._seen[0][0]
        self.assertIsInstance(system_message, SystemMessage)
        self.assertIn("下周三=2026-10-14", system_message.content)
        self.assertIn("星期三=2026-10-14", system_message.content)
        self.assertEqual(model._seen[0][-1].content, "我这周三有几门课？")
        self.assertEqual(result["tool_calls"], [
            {"name": "get_my_schedules", "args": {"date": "2026-10-14"}},
        ])


if __name__ == "__main__":
    unittest.main()
