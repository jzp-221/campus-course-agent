"""P13-Step2 只读桥接离线回归：内存 SQL/JWT，MCP 运输使用替身。"""

from contextlib import ExitStack
from datetime import date
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


_settings_init = BaseSettings.__init__
_TEST_SECRET = "learning-context-offline-jwt-never-use-in-production"


def _offline_settings(self, *args, **kwargs):
    kwargs["_env_file"] = None
    _settings_init(self, *args, **kwargs)


with patch.dict(os.environ, {"DATABASE_URL": "sqlite://", "JWT_SECRET_KEY": _TEST_SECRET,
                             "EMBEDDING_API_KEY": ""}), \
        patch.object(BaseSettings, "__init__", _offline_settings):
    from app.api.learning_context import router
    from app.common.exceptions import (
        BusinessException, business_exception_handler, global_excpetion_hadler,
        http_excpetion_hadler, validation_excpetion_hadler,
    )
    from app.common.holiday_config import HolidayPeriod, HOLIDAY_PERIODS, MAKEUP_WORKDAYS
    from app.config import settings
    from app.database import Base, get_db
    from app.models.coures import Course
    from app.models.schedule import Schedule
    from app.models.user import User
    from app.services import learning_context_service, mcp_client_service
    from app.utils.jwt import create_access_token


_DEFAULT_HEADERS = object()


class LearningContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        for field, value in (
            ("JWT_SECRET_KEY", _TEST_SECRET), ("JWT_ALGORITHM", "HS256"),
            ("SEMESTER_START_DATE", date(2026, 9, 7)), ("TOTAL_WEEKS", 20),
            ("EMBEDDING_API_KEY", SecretStr("offline-placeholder-not-a-real-key")),
            ("MCP_HOST", "127.0.0.1"), ("MCP_PORT", 8001), ("MCP_PATH", "/mcp"),
        ):
            self.patches.enter_context(patch.object(settings, field, value))
        self.patches.enter_context(patch.object(
            learning_context_service, "shanghai_today", return_value=date(2026, 10, 11),
        ))
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as db:
            aaa = User(username="aaa", name="学生甲", password="unused", role="student")
            bbb = User(username="bbb", name="学生乙", password="unused", role="student")
            admin = User(username="admin", name="管理员", password="unused", role="admin")
            course = Course(name="数据结构", code="DS101", description="算法与数据组织", status=1)
            unused = Course(name="离散数学", code="DM101", description=None, status=0)
            db.add_all([aaa, bbb, admin, course, unused])
            db.flush()
            self.aaa_id, self.bbb_id, self.admin_id = aaa.id, bbb.id, admin.id
            self.course_id, self.unused_id = course.id, unused.id
            db.add_all([
                Schedule(user_id=aaa.id, course_id=course.id, class_date=date(2026, 10, 14),
                         period="7-8节", teacher="李老师", location="A101", start_week=3, end_week=8),
                Schedule(user_id=bbb.id, course_id=course.id, class_date=date(2026, 10, 15),
                         period="1-2节", teacher="王老师", location="B202", start_week=1, end_week=20),
                Schedule(user_id=admin.id, course_id=course.id, class_date=date(2026, 10, 16),
                         period="3-4节", start_week=2, end_week=10),
            ])
            db.commit()
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        for kind, handler in (
            (BusinessException, business_exception_handler), (HTTPException, http_excpetion_hadler),
            (RequestValidationError, validation_excpetion_hadler), (Exception, global_excpetion_hadler),
        ):
            self.app.add_exception_handler(kind, handler)

        def dependency():
            with self.sessions() as db:
                yield db

        self.app.dependency_overrides[get_db] = dependency
        self.headers = self.auth(self.aaa_id)
        self.hit = {"items": [{"text": "实验课前提交预习报告。", "source": "数据结构实验教学规定.md",
                               "document_id": 17, "chunk_index": 0, "distance": 0.2}], "count": 1}
        self.tool = SimpleNamespace(name="search_course_docs", ainvoke=AsyncMock(return_value=json.dumps(self.hit, ensure_ascii=False)))
        self.actual_loader = mcp_client_service.get_schedule_tools
        self.loader = self.patches.enter_context(patch.object(
            mcp_client_service, "get_schedule_tools", AsyncMock(return_value=[self.tool]),
        ))

    def auth(self, identifier):
        return {"Authorization": "Bearer " + create_access_token(identifier, "unused")}

    def course_path(self, identifier=None, *, docs=False):
        return f"/api/learning-context/courses/{self.course_id if identifier is None else identifier}" + ("/docs" if docs else "")

    async def request(self, path="/api/learning-context", *, headers=_DEFAULT_HEADERS, params=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                                     base_url="http://offline") as client:
            return await client.get(path, headers=self.headers if headers is _DEFAULT_HEADERS else headers, params=params)

    async def success(self, *args, **kwargs):
        response = await self.request(*args, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["code"], 200, response.text)
        return response.json()["data"]

    async def test_missing_token_rejects_all_readonly_routes_before_mcp(self):
        for path in ("/api/learning-context", self.course_path(), self.course_path(docs=True)):
            with self.subTest(path=path):
                response = await self.request(path, headers={})
                self.assertEqual(response.status_code, 401)
        self.loader.assert_not_awaited()

    async def test_forged_token_is_rejected_before_mcp(self):
        response = await self.request(self.course_path(docs=True), headers={"Authorization": "Bearer forged"})
        self.assertEqual(response.status_code, 401)
        self.loader.assert_not_awaited()

    async def test_context_exposes_existing_courses_and_semester_dates(self):
        data = await self.success()
        self.assertEqual(data["courses"], [
            {"id": self.course_id, "name": "数据结构", "code": "DS101", "description": "算法与数据组织", "status": 1},
            {"id": self.unused_id, "name": "离散数学", "code": "DM101", "description": None, "status": 0},
        ])
        self.assertEqual(data["semester"], {"start_date": "2026-09-07", "end_date": "2027-01-24",
                         "total_weeks": 20, "current_week": 5, "today": "2026-10-11"})

    async def test_holidays_and_makeup_days_read_exact_backend_configuration(self):
        data = await self.success()
        self.assertEqual(data["holidays"], [{"name": period.name, "start_date": period.start_date.isoformat(),
                         "end_date": period.end_date.isoformat(), "note": period.note} for period in HOLIDAY_PERIODS])
        self.assertEqual(data["makeup_workdays"], [day.isoformat() for day in sorted(MAKEUP_WORKDAYS)])
        with patch.object(learning_context_service, "HOLIDAY_PERIODS", (
                HolidayPeriod("配置替身", date(2028, 1, 1), date(2028, 1, 2), "离线日历"),)), \
                patch.object(learning_context_service, "MAKEUP_WORKDAYS", frozenset({date(2028, 1, 8)})):
            changed = await self.success()
        self.assertEqual(changed["holidays"][0]["name"], "配置替身")
        self.assertEqual(changed["makeup_workdays"], ["2028-01-08"])

    async def test_semester_outside_range_reports_no_current_week(self):
        for today in (date(2026, 9, 6), date(2027, 1, 25)):
            with self.subTest(today=today), patch.object(learning_context_service, "shanghai_today", return_value=today):
                data = await self.success()
                self.assertIsNone(data["semester"]["current_week"])

    async def test_course_detail_contains_only_own_schedule_and_ignores_user_id(self):
        data = await self.success(self.course_path(), params={"user_id": self.bbb_id})
        self.assertEqual(len(data["schedules"]), 1)
        self.assertEqual(data["schedules"][0]["class_date"], "2026-10-14")
        self.assertEqual(data["schedules"][0]["weekday"], 3)
        self.assertEqual(data["schedules"][0]["teacher"], "李老师")
        self.assertEqual((data["start_week"], data["end_week"]), (3, 8))

    async def test_other_student_receives_own_schedule(self):
        data = await self.success(self.course_path(), headers=self.auth(self.bbb_id), params={"user_id": self.aaa_id})
        self.assertEqual([row["class_date"] for row in data["schedules"]], ["2026-10-15"])
        self.assertEqual((data["start_week"], data["end_week"]), (1, 20))

    async def test_admin_is_also_restricted_to_own_course_schedules(self):
        data = await self.success(self.course_path(), headers=self.auth(self.admin_id), params={"user_id": self.aaa_id})
        self.assertEqual([row["class_date"] for row in data["schedules"]], ["2026-10-16"])
        self.assertEqual((data["start_week"], data["end_week"]), (2, 10))

    async def test_course_without_own_schedule_returns_empty_and_null_range(self):
        data = await self.success(self.course_path(self.unused_id))
        self.assertEqual(data["schedules"], [])
        self.assertIsNone(data["start_week"])
        self.assertIsNone(data["end_week"])

    async def test_missing_course_is_404_on_detail_and_docs_without_mcp(self):
        for path in (self.course_path(9999), self.course_path(9999, docs=True)):
            with self.subTest(path=path):
                response = await self.request(path)
                self.assertEqual(response.json()["code"], 404)
                self.assertEqual(response.json()["message"], "课程不存在")
        self.loader.assert_not_awaited()

    async def test_context_and_detail_do_not_change_database_rows(self):
        with self.sessions() as db:
            before = [(row.id, row.user_id, row.course_id, row.class_date, row.update_time)
                      for row in db.query(Schedule).order_by(Schedule.id)]
        await self.success()
        await self.success(self.course_path())
        with self.sessions() as db:
            after = [(row.id, row.user_id, row.course_id, row.class_date, row.update_time)
                     for row in db.query(Schedule).order_by(Schedule.id)]
            self.assertEqual(db.query(Course).count(), 2)
        self.assertEqual(after, before)

    async def test_docs_pass_only_course_name_and_current_authorization(self):
        data = await self.success(self.course_path(docs=True), params={"query": "伪造问题", "user_id": self.bbb_id})
        self.assertEqual(data, self.hit)
        self.loader.assert_awaited_once_with(self.headers["Authorization"])
        self.tool.ainvoke.assert_awaited_once_with({"query": "数据结构"})

    async def test_docs_each_request_reloads_tools_with_its_own_jwt(self):
        bbb = self.auth(self.bbb_id)
        await self.success(self.course_path(docs=True))
        await self.success(self.course_path(docs=True), headers=bbb)
        self.assertEqual([call.args[0] for call in self.loader.await_args_list],
                         [self.headers["Authorization"], bbb["Authorization"]])

    async def test_docs_discovery_uses_streamable_http_with_current_authorization(self):
        self.loader.side_effect = self.actual_loader
        client = SimpleNamespace(get_tools=AsyncMock(return_value=[
            SimpleNamespace(name="get_my_schedules"), self.tool,
        ]))
        with patch.object(mcp_client_service, "MultiServerMCPClient", return_value=client) as factory:
            data = await self.success(self.course_path(docs=True))
        self.assertEqual(data, self.hit)
        factory.assert_called_once_with({"campus": {
            "transport": "streamable_http", "url": "http://127.0.0.1:8001/mcp",
            "headers": {"Authorization": self.headers["Authorization"]},
        }})
        client.get_tools.assert_awaited_once_with(server_name="campus")
        self.tool.ainvoke.assert_awaited_once_with({"query": "数据结构"})

    async def test_docs_normal_empty_result_is_success_not_failure(self):
        self.tool.ainvoke.return_value = json.dumps({"items": [], "count": 0})
        self.assertEqual(await self.success(self.course_path(docs=True)), {"items": [], "count": 0})

    async def test_missing_embedding_key_fails_before_mcp(self):
        with patch.object(settings, "EMBEDDING_API_KEY", SecretStr("")):
            response = await self.request(self.course_path(docs=True))
        self.assertEqual(response.json()["code"], 503)
        self.assertIn("检索失败", response.json()["message"])
        self.assertNotIn("没有检索到", response.json()["message"])
        self.loader.assert_not_awaited()

    async def test_mcp_discovery_failure_returns_safe_502(self):
        self.loader.side_effect = BusinessException("MCP 服务不可用，请确认已启动 mcp_server.py")
        response = await self.request(self.course_path(docs=True))
        self.assertEqual(response.json()["code"], 502)
        self.assertIn("检索失败", response.json()["message"])
        self.assertIsNone(response.json()["data"])

    async def test_missing_or_duplicate_search_tool_returns_502(self):
        for tools in ([], [self.tool, self.tool]):
            with self.subTest(tools=len(tools)):
                self.loader.return_value = tools
                response = await self.request(self.course_path(docs=True))
                self.assertEqual(response.json()["code"], 502)
                self.assertIn("检索失败", response.json()["message"])
        self.tool.ainvoke.assert_not_awaited()

    async def test_tool_runtime_failure_is_not_empty_and_hides_internal_detail(self):
        self.tool.ainvoke.side_effect = RuntimeError("internal-sql-and-secret")
        response = await self.request(self.course_path(docs=True))
        self.assertEqual(response.json()["code"], 502)
        self.assertIn("检索失败", response.json()["message"])
        self.assertNotIn("internal-sql-and-secret", response.text)
        self.assertIsNone(response.json()["data"])

    async def test_tool_error_preserves_existing_business_code_and_failure_wording(self):
        self.tool.ainvoke.return_value = json.dumps({"error": "检索失败：embedding 服务返回 HTTP 429", "code": 502})
        response = await self.request(self.course_path(docs=True))
        self.assertEqual(response.json()["code"], 502)
        self.assertIn("检索失败", response.json()["message"])
        self.assertIn("429", response.json()["message"])
        self.assertIsNone(response.json()["data"])

    async def test_source_less_or_malformed_tool_result_is_rejected(self):
        for data in ({"items": [{"text": "无来源"}], "count": 1}, {"items": [], "count": 1}, "not-json"):
            with self.subTest(data=data):
                self.tool.ainvoke.return_value = json.dumps(data)
                response = await self.request(self.course_path(docs=True))
                self.assertEqual(response.json()["code"], 502)
                self.assertIn("检索失败", response.json()["message"])

    async def test_readonly_database_failure_uses_existing_safe_business_envelope(self):
        with patch.object(learning_context_service, "_find_course", side_effect=SQLAlchemyError("internal-select")):
            response = await self.request(self.course_path())
        self.assertEqual(response.json()["code"], 503)
        self.assertNotIn("internal-select", response.text)

    def test_openapi_is_readonly_and_has_no_user_id_or_query_input(self):
        schema = self.app.openapi()
        for path, value in schema["paths"].items():
            self.assertEqual(set(value), {"get"})
            self.assertTrue(value["get"]["security"])
            self.assertFalse(any(parameter["name"] in {"user_id", "query"}
                                 for parameter in value["get"].get("parameters", [])))
        self.assertEqual(len(schema["paths"]), 3)


if __name__ == "__main__":
    unittest.main()
