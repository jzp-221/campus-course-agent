"""保存 MCP 工具安全回归：内存数据库/进程内 MCP，不读取 .env 或访问网络。"""

import inspect
import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# 先复用既有测试的配置隔离；它在导入应用前显式关闭 dotenv。
from tests import test_mcp_schedules as _offline

from fastmcp import Client
from fastmcp.exceptions import ToolError

import mcp_server
from app.common.exceptions import BusinessException
from app.config import settings
from app.database import Base
from app.dependencies import mcp_auth
from app.models.study_plan import StudyPlan
from app.models.user import User
from app.services import mcp_client_service
from app.utils.jwt import create_access_token


_DEFAULT_AUTHORIZATION = object()


def content_blocks(data):
    return [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]


class MCPStudyPlanToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.patches.enter_context(
            patch.object(settings, "JWT_SECRET_KEY", _offline._TEST_SECRET)
        )
        self.patches.enter_context(patch.object(settings, "JWT_ALGORITHM", "HS256"))
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(
            bind=self.engine, class_=_offline._TrackedSession, autoflush=False
        )
        self.opened_sessions = []

        def session_factory():
            session = self.sessions()
            self.opened_sessions.append(session)
            return session

        self.patches.enter_context(patch.object(mcp_auth, "SessionLocal", session_factory))
        self.patches.enter_context(patch.object(mcp_server, "SessionLocal", session_factory))
        with self.sessions() as db:
            aaa = User(username="aaa", password="test-only", name="甲", role="student")
            bbb = User(username="bbb", password="test-only", name="乙", role="student")
            db.add_all([aaa, bbb])
            db.commit()
            self.aaa_id, self.bbb_id = aaa.id, bbb.id
        self.tokens = {
            "aaa": create_access_token(self.aaa_id, "test-only"),
            "bbb": create_access_token(self.bbb_id, "test-only"),
        }

    def tearDown(self):
        try:
            self.assertTrue(all(session.was_closed for session in self.opened_sessions))
        finally:
            self.patches.close()
            self.engine.dispose()

    async def save(self, arguments=None, *, authorization=_DEFAULT_AUTHORIZATION):
        if authorization is _DEFAULT_AUTHORIZATION:
            authorization = f"Bearer {self.tokens['aaa']}"
        headers = {} if authorization is None else {"authorization": authorization}
        with patch.object(mcp_server, "get_http_headers", return_value=headers):
            async with Client(mcp_server.mcp) as client:
                result = await client.call_tool(
                    "save_study_plan", arguments or {"content": "测试学习计划"}
                )
        return _offline.MCPScheduleTests.tool_data(result)

    def rows(self):
        with self.sessions() as db:
            return [(row.id, row.user_id, row.content) for row in db.query(StudyPlan).all()]

    async def test_signature_and_schema_expose_only_required_content(self):
        self.assertEqual(set(inspect.signature(mcp_server.save_study_plan.fn).parameters), {"content"})
        async with Client(mcp_server.mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        self.assertEqual(set(tools), {"get_my_schedules", "save_study_plan", "search_course_docs"})
        schema = tools["save_study_plan"].inputSchema
        self.assertEqual(set(schema["properties"]), {"content"})
        self.assertEqual(schema["required"], ["content"])
        self.assertFalse(schema["additionalProperties"])

    async def test_saved_result_is_real_row_owned_by_jwt_user(self):
        result = await self.save({"content": "  明天复习数据结构  "})
        self.assertEqual(set(result), {"id", "content", "create_time"})
        self.assertGreater(result["id"], 0)
        self.assertEqual(result["content"], "明天复习数据结构")
        self.assertIsInstance(result["create_time"], str)
        self.assertEqual(self.rows(), [(result["id"], self.aaa_id, result["content"])])

    async def test_two_authorizations_save_only_their_own_plans(self):
        aaa = await self.save({"content": "甲的计划"})
        bbb = await self.save(
            {"content": "乙的计划"}, authorization=f"Bearer {self.tokens['bbb']}"
        )
        self.assertEqual(
            self.rows(),
            [(aaa["id"], self.aaa_id, "甲的计划"), (bbb["id"], self.bbb_id, "乙的计划")],
        )

    async def test_missing_forged_and_disabled_auth_cannot_save(self):
        for authorization in (None, "Bearer forged-token"):
            result = await self.save(authorization=authorization)
            self.assertEqual(result["code"], 401)
            self.assertTrue(result["error"])
        with self.sessions() as db:
            db.get(User, self.aaa_id).status = 0
            db.commit()
        result = await self.save()
        self.assertEqual(result["code"], 401)
        self.assertEqual(self.rows(), [])

    async def test_extra_identity_and_plan_id_are_rejected_before_any_write(self):
        for field in ("user_id", "plan_id"):
            with self.subTest(field=field), self.assertRaises(ToolError):
                await self.save({"content": "不能越权", field: self.bbb_id})
        self.assertEqual(self.rows(), [])

    async def test_blank_and_over_limit_use_existing_business_validation(self):
        for content in (" \n\t", "字" * 2001):
            result = await self.save({"content": content})
            # 参数类校验统一为 400（原实现返回 500，已按 REST 语义修正）
            self.assertEqual(result["code"], 400)
            self.assertTrue(result["error"])
        self.assertEqual(self.rows(), [])

    async def test_exactly_2000_characters_are_saved(self):
        result = await self.save({"content": "学" * 2000})
        self.assertEqual(len(result["content"]), 2000)
        self.assertEqual(len(self.rows()), 1)

    async def test_save_plan_business_failure_retains_code_without_fake_success(self):
        with patch.object(
            mcp_server, "save_plan",
            side_effect=BusinessException(message="本次不允许保存", code=403),
        ):
            result = await self.save()
        self.assertEqual(result, {"error": "本次不允许保存", "code": 403})
        self.assertEqual(self.rows(), [])

    async def test_database_failure_is_sanitized_and_session_released(self):
        with patch.object(
            mcp_server, "save_plan", side_effect=RuntimeError("private database password")
        ):
            result = await self.save()
        self.assertEqual(result, {"error": "保存学习计划失败，请稍后重试", "code": 500})
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(self.rows(), [])


class MCPAgentToolDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def discover(self, tools):
        client = SimpleNamespace(get_tools=AsyncMock(return_value=tools))
        with patch.object(mcp_client_service, "MultiServerMCPClient", return_value=client):
            return await mcp_client_service.get_schedule_tools("Bearer offline-test")

    async def test_only_whitelisted_tools_are_exposed(self):
        schedule = SimpleNamespace(name="get_my_schedules")
        save = SimpleNamespace(name="save_study_plan")
        tools = await self.discover([schedule, SimpleNamespace(name="delete_all"), save])
        self.assertEqual(tools, [schedule, save])

    async def test_old_schedule_only_transport_remains_compatible(self):
        schedule = SimpleNamespace(name="get_my_schedules")
        self.assertEqual(await self.discover([schedule]), [schedule])

    async def test_missing_schedule_or_duplicate_authorized_tool_is_rejected(self):
        for names in (
            ["save_study_plan"],
            ["get_my_schedules", "get_my_schedules"],
            ["get_my_schedules", "save_study_plan", "save_study_plan"],
        ):
            with self.subTest(names=names), self.assertRaises(BusinessException):
                await self.discover([SimpleNamespace(name=name) for name in names])

    async def test_each_user_gets_fresh_clients_and_both_tool_instances(self):
        calls = []

        def factory(connection):
            authorization = connection["campus"]["headers"]["Authorization"]
            tools = [
                SimpleNamespace(name=name, authorization=authorization)
                for name in ("get_my_schedules", "save_study_plan")
            ]
            calls.append(connection)
            return SimpleNamespace(get_tools=AsyncMock(return_value=tools))

        with patch.object(mcp_client_service, "MultiServerMCPClient", side_effect=factory):
            aaa = await mcp_client_service.get_schedule_tools("Bearer aaa-offline")
            bbb = await mcp_client_service.get_schedule_tools("Bearer bbb-offline")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["campus"]["transport"], "streamable_http")
        for index in (0, 1):
            self.assertIsNot(aaa[index], bbb[index])
            self.assertEqual(aaa[index].authorization, "Bearer aaa-offline")
            self.assertEqual(bbb[index].authorization, "Bearer bbb-offline")


class MCPSaveResultTests(unittest.TestCase):
    def test_save_result_supports_text_and_blocks(self):
        data = {"id": 1, "content": "复习英语", "create_time": "2026-10-09T10:00:00"}
        for result in (json.dumps(data), content_blocks(data)):
            self.assertEqual(mcp_client_service._parse_save_result(result), data)

    def test_save_error_preserves_valid_business_code(self):
        with self.assertRaises(BusinessException) as raised:
            mcp_client_service._parse_save_result(content_blocks({"error": "登录失效", "code": 401}))
        self.assertEqual(raised.exception.code, 401)
        self.assertEqual(raised.exception.message, "登录失效")
        for code in (True, "401", 200, 999, None):
            with self.assertRaises(BusinessException) as raised:
                mcp_client_service._parse_save_result(content_blocks({"error": "保存失败", "code": code}))
            self.assertEqual(raised.exception.code, 500)

    def test_malformed_save_results_cannot_be_reported_as_saved(self):
        for result in (
            None, "private invalid JSON", content_blocks([]), content_blocks({}),
            content_blocks({"id": True, "content": "计划"}),
            content_blocks({"id": 0, "content": "计划"}),
            content_blocks({"id": 1, "content": " "}),
            content_blocks({"id": 1, "content": "学" * 2001}),
            content_blocks({"id": 1, "content": "计划", "user_id": 2}),
            content_blocks({"id": 1, "content": "计划", "create_time": 123}),
        ):
            with self.subTest(result=result), self.assertRaises(BusinessException) as raised:
                mcp_client_service._parse_save_result(result)
            self.assertNotIn("private", raised.exception.message)


if __name__ == "__main__":
    unittest.main()
