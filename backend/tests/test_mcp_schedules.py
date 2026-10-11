"""MCP 课表回归测试：内存数据库和进程内客户端，不读取 .env 或启动服务器。"""

import json
import os
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import jwt
from pydantic_settings import BaseSettings
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


_original_settings_init = BaseSettings.__init__
_TEST_SECRET = "mcp-schedule-tests-only-secret-never-use-in-production"


def _test_settings_init(self, *args, **kwargs):
    kwargs["_env_file"] = None
    _original_settings_init(self, *args, **kwargs)


with patch.dict(
    os.environ,
    {
        "DATABASE_URL": "sqlite://",
        "JWT_SECRET_KEY": _TEST_SECRET,
        "JWT_ALGORITHM": "HS256",
        "JWT_EXPIRE_HOURS": "1",
    },
), patch.object(BaseSettings, "__init__", _test_settings_init):
    from fastmcp import Client

    import mcp_server
    from app.config import settings
    from app.database import Base
    from app.dependencies import mcp_auth
    from app.models.coures import Course
    from app.models.schedule import Schedule
    from app.models.user import User
    from app.services.schedule_service import list_schedules_as_dict
    from app.utils.jwt import create_access_token, decode_access_token


_DEFAULT_AUTHORIZATION = object()


class _TrackedSession(Session):
    """确认成功和异常分支都释放了为工具创建的数据库会话。"""

    was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


class MCPScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(settings, "JWT_SECRET_KEY", _TEST_SECRET))
        self.patches.enter_context(patch.object(settings, "JWT_ALGORITHM", "HS256"))
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(
            bind=self.engine, class_=_TrackedSession, autoflush=False
        )
        self.opened_sessions = []

        def test_session():
            session = self.sessions()
            self.opened_sessions.append(session)
            return session

        self.patches.enter_context(patch.object(mcp_auth, "SessionLocal", test_session))
        self.patches.enter_context(patch.object(mcp_server, "SessionLocal", test_session))
        with self.sessions() as db:
            aaa = User(username="aaa", password="test-only", name="学生甲", role="student")
            bbb = User(username="bbb", password="test-only", name="学生乙", role="student")
            courses = [
                Course(name="大学英语", code="ENG101"),
                Course(name="数据结构", code="DS101"),
                Course(name="高等数学", code="MATH101"),
            ]
            db.add_all([aaa, bbb, *courses])
            db.flush()
            self.aaa_id = aaa.id
            self.bbb_id = bbb.id
            self.course_ids = [course.id for course in courses]
            db.add_all(
                [
                    Schedule(
                        user_id=aaa.id,
                        class_date=date(2026, 10, 9 + index),
                        period=f"{index * 2 + 1}-{index * 2 + 2}节",
                        course_id=course.id,
                        location=["教学楼 A101", "教学楼 B305", "教学楼 C201"][index],
                    )
                    for index, course in enumerate(courses)
                ]
            )
            db.commit()
        self.tokens = {
            "aaa": create_access_token(self.aaa_id, "test-only"),
            "bbb": create_access_token(self.bbb_id, "test-only"),
        }

    def tearDown(self):
        try:
            self.assertTrue(
                all(session.was_closed for session in self.opened_sessions),
                "MCP 鉴权或课表查询留下了未关闭的数据库会话",
            )
        finally:
            self.patches.close()
            self.engine.dispose()

    @staticmethod
    def tool_data(result):
        """兼容 FastMCP 2/3 的结构化结果，必要时读取 JSON 文本内容。"""
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            return structured
        return json.loads("".join(item.text for item in result.content if item.type == "text"))

    async def call_tool(self, arguments=None, *, authorization=_DEFAULT_AUTHORIZATION):
        if authorization is _DEFAULT_AUTHORIZATION:
            authorization = f"Bearer {self.tokens['aaa']}"
        headers = {} if authorization is None else {"authorization": authorization}
        with patch.object(mcp_server, "get_http_headers", return_value=headers) as get_headers:
            async with Client(mcp_server.mcp) as client:
                result = await client.call_tool("get_my_schedules", arguments or {})
        get_headers.assert_called_once_with(include_all=True)
        return self.tool_data(result)

    def assert_error(self, result):
        self.assertIsInstance(result.get("error"), str, result)
        self.assertTrue(result["error"], result)
        self.assertEqual(result["items"], [])
        self.assertEqual(result.get("count", 0), 0)

    async def test_tool_schema_has_only_optional_date(self):
        async with Client(mcp_server.mcp) as client:
            tools = await client.list_tools()
        by_name = {tool.name: tool for tool in tools}
        self.assertEqual(set(by_name), {"get_my_schedules", "save_study_plan", "search_course_docs"})
        schema = by_name["get_my_schedules"].inputSchema
        self.assertEqual(set(schema.get("properties", {})), {"date"})
        self.assertNotIn("date", schema.get("required", []))
        self.assertIsNone(schema["properties"]["date"].get("default"))

    async def test_all_schedules_use_exact_output_fields_and_string_dates(self):
        result = await self.call_tool()
        self.assertEqual(set(result), {"items", "count"})
        self.assertEqual(result["count"], 3)
        by_date = {item["date"]: item for item in result["items"]}
        self.assertEqual(set(by_date), {"2026-10-09", "2026-10-10", "2026-10-11"})
        for item in result["items"]:
            self.assertEqual(
                set(item), {"date", "period", "course_id", "course_name", "location"}
            )
            self.assertIsInstance(item["date"], str)
            self.assertEqual(date.fromisoformat(item["date"]).isoformat(), item["date"])
        self.assertEqual(
            by_date["2026-10-10"],
            {
                "date": "2026-10-10",
                "period": "3-4节",
                "course_id": self.course_ids[1],
                "course_name": "数据结构",
                "location": "教学楼 B305",
            },
        )

    async def test_query_is_not_limited_to_rest_page_size(self):
        with self.sessions() as db:
            db.add_all(
                [
                    Schedule(
                        user_id=self.aaa_id,
                        class_date=date(2026, 11, index + 1),
                        period="1-2节",
                        course_id=self.course_ids[0],
                        location="教学楼 A101",
                    )
                    for index in range(12)
                ]
            )
            db.commit()
        result = await self.call_tool()
        self.assertEqual(result["count"], 15)
        self.assertEqual(len(result["items"]), 15)

    async def test_date_filter_and_no_class_day(self):
        result = await self.call_tool({"date": "2026-10-10"})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["course_name"], "数据结构")
        self.assertEqual(result["items"][0]["date"], "2026-10-10")
        self.assertEqual(
            await self.call_tool({"date": "2026-10-20"}), {"items": [], "count": 0}
        )

    async def test_blank_or_null_dates_mean_all_schedules(self):
        for value in (None, "", " \t "):
            with self.subTest(date=value):
                result = await self.call_tool({"date": value})
                self.assertEqual(result["count"], 3)
        result = await self.call_tool({"date": " 2026-10-10 "})
        self.assertEqual(result["count"], 1)

    async def test_invalid_dates_return_error_without_crashing(self):
        for value in (
            "tomorrow", "2026-1-9", "20261009", "2026-W41-5",
            "2026-02-30", "2026-13-01", "2026-10-09T00:00:00", "0000-01-01",
        ):
            with self.subTest(date=value):
                result = await self.call_tool({"date": value})
                self.assert_error(result)
                self.assertIn("YYYY-MM-DD", result["error"])
        self.assertEqual((await self.call_tool())["count"], 3)

    async def test_users_see_only_their_own_schedules(self):
        bbb_authorization = f"Bearer {self.tokens['bbb']}"
        self.assertEqual(
            await self.call_tool(authorization=bbb_authorization),
            {"items": [], "count": 0},
        )
        with self.sessions() as db:
            db.add(
                Schedule(
                    user_id=self.bbb_id,
                    class_date=date(2026, 10, 10),
                    period="7-8节",
                    course_id=self.course_ids[0],
                    location="乙的教室",
                )
            )
            db.commit()
        aaa = await self.call_tool()
        bbb = await self.call_tool(authorization=bbb_authorization)
        self.assertEqual(aaa["count"], 3)
        self.assertEqual(bbb["count"], 1)
        self.assertEqual(bbb["items"][0]["location"], "乙的教室")
        self.assertNotIn("乙的教室", [item["location"] for item in aaa["items"]])

    async def test_extra_user_id_cannot_select_another_users_schedules(self):
        # FastMCP 版本可拒绝未知参数或忽略它；任何情况下不能改变 JWT 所属用户。
        headers = {"authorization": f"Bearer {self.tokens['bbb']}"}
        with patch.object(mcp_server, "get_http_headers", return_value=headers):
            async with Client(mcp_server.mcp) as client:
                result = await client.call_tool(
                    "get_my_schedules", {"user_id": self.aaa_id}, raise_on_error=False
                )
        if not result.is_error:
            self.assertEqual(self.tool_data(result), {"items": [], "count": 0})

    async def test_missing_empty_or_wrong_authorization_scheme_is_rejected(self):
        for authorization in (
            None, "", " \t ", "Bearer", "Bearer ",
            f"Basic {self.tokens['aaa']}", self.tokens["aaa"],
            f"Bearer {self.tokens['aaa']} extra",
        ):
            with self.subTest(authorization=authorization):
                self.assert_error(await self.call_tool(authorization=authorization))
        result = await self.call_tool(authorization=f"bearer {self.tokens['aaa']}")
        self.assertEqual(result["count"], 3)

    async def test_invalid_signature_malformed_and_expired_tokens_are_rejected(self):
        expiration = datetime.now(timezone.utc) + timedelta(hours=1)
        version = decode_access_token(self.tokens["aaa"])["pwdv"]
        wrong_signature = jwt.encode(
            {"user_id": self.aaa_id, "exp": expiration, "pwdv": version},
            "different-test-signing-secret-that-is-never-production",
            algorithm="HS256",
        )
        expired = jwt.encode(
            {"user_id": self.aaa_id, "exp": datetime.now(timezone.utc) - timedelta(hours=1), "pwdv": version},
            _TEST_SECRET,
            algorithm="HS256",
        )
        for token in ("not-a-jwt", "invalid.jwt.token", wrong_signature, expired):
            with self.subTest(token_type=token[:12]):
                self.assert_error(await self.call_tool(authorization=f"Bearer {token}"))

    async def test_invalid_jwt_user_ids_are_rejected(self):
        version = decode_access_token(self.tokens["aaa"])["pwdv"]
        for user_id in (None, 0, -1, True, 1.0, "1", "abc", [], {}):
            with self.subTest(user_id=user_id):
                token = jwt.encode(
                    {"user_id": user_id, "exp": datetime.now(timezone.utc) + timedelta(hours=1), "pwdv": version},
                    _TEST_SECRET,
                    algorithm="HS256",
                )
                self.assert_error(await self.call_tool(authorization=f"Bearer {token}"))

    async def test_missing_or_disabled_users_are_rejected(self):
        with self.sessions() as db:
            db.get(User, self.bbb_id).status = 0
            db.commit()
        for token in (create_access_token(999999, "missing-user"), self.tokens["bbb"]):
            self.assert_error(await self.call_tool(authorization=f"Bearer {token}"))

    async def test_internal_query_error_does_not_expose_sql_or_credentials(self):
        sensitive_error = "SELECT password FROM users; mysql://demo:secret@db/campus"
        with patch.object(
            mcp_server, "list_schedules_as_dict", side_effect=RuntimeError(sensitive_error)
        ), self.assertLogs("mcp_server", level="ERROR") as logged:
            result = await self.call_tool()
        self.assert_error(result)
        self.assertEqual(result["error"], "查询课表失败，请稍后重试")
        self.assertNotIn(sensitive_error, json.dumps(result))
        self.assertNotIn(sensitive_error, "\n".join(logged.output))
        self.assertEqual(len(self.opened_sessions), 2)

    async def test_database_session_creation_error_is_safe(self):
        sensitive_error = "mysql://demo:secret@db/campus"
        with patch.object(mcp_server, "SessionLocal", side_effect=RuntimeError(sensitive_error)), \
                self.assertLogs("mcp_server", level="ERROR"):
            result = await self.call_tool()
        self.assert_error(result)
        self.assertNotIn(sensitive_error, json.dumps(result))
        self.assertEqual(len(self.opened_sessions), 1)

    async def test_authentication_query_failure_closes_session_and_hides_details(self):
        session = self.sessions()
        self.opened_sessions.append(session)
        sensitive_error = "SELECT id FROM users; mysql://demo:secret@db/campus"
        with patch.object(mcp_auth, "SessionLocal", return_value=session), \
                patch.object(session, "query", side_effect=RuntimeError(sensitive_error)), \
                self.assertLogs("mcp_server", level="ERROR") as logged:
            result = await self.call_tool()
        self.assert_error(result)
        self.assertEqual(result["error"], "查询课表失败，请稍后重试")
        self.assertTrue(session.was_closed)
        self.assertNotIn(sensitive_error, json.dumps(result))
        self.assertNotIn(sensitive_error, "\n".join(logged.output))

    def test_dictionary_adapter_reuses_full_query_and_has_no_owner_fields(self):
        with self.sessions() as db:
            items = list_schedules_as_dict(db, self.aaa_id, "2026-10-10")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["date"], "2026-10-10")
        self.assertNotIn("user_id", items[0])
        self.assertNotIn("username", items[0])


if __name__ == "__main__":
    unittest.main()
