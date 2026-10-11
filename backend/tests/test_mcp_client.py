"""FastAPI → MCP Client 回归：模拟 MCP 运输，JWT 与 API 使用内存数据库。"""

import asyncio
import json
import os
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic_settings import BaseSettings
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


_TEST_SECRET = "mcp-client-tests-only-secret-never-use-in-production"
_original_settings_init = BaseSettings.__init__


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
    from app.api import api
    from app.common.exceptions import (
        BusinessException,
        business_exception_handler,
        http_excpetion_hadler,
        validation_excpetion_hadler,
    )
    from app.config import settings
    from app.database import Base, get_db
    from app.models.user import User
    from app.services import mcp_client_service
    from app.utils.jwt import create_access_token, decode_access_token
    from app.utils.password import hash_password


_SCHEDULE_DATA = {
    "items": [
        {
            "date": "2026-10-10",
            "period": "3-4节",
            "course_id": 2,
            "course_name": "数据结构",
            "location": "教学楼 B305",
        }
    ],
    "count": 1,
}


def _content_blocks(data):
    return [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]


class MCPClientServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        for field, value in (
            ("MCP_HOST", "127.0.0.1"), ("MCP_PORT", 8001), ("MCP_PATH", "/mcp")
        ):
            self.patches.enter_context(patch.object(settings, field, value))
        self.tool = SimpleNamespace(
            name="get_my_schedules", ainvoke=AsyncMock(return_value=_content_blocks(_SCHEDULE_DATA))
        )
        self.client = SimpleNamespace(get_tools=AsyncMock(return_value=[self.tool]))
        self.factory = self.patches.enter_context(
            patch.object(mcp_client_service, "MultiServerMCPClient", return_value=self.client)
        )

    def tearDown(self):
        self.patches.close()

    async def test_creates_streamable_http_client_with_only_authorization(self):
        authorization = "Bearer aaa.jwt.token"
        result = await mcp_client_service.call_get_my_schedules(authorization)
        self.assertEqual(result, _SCHEDULE_DATA)
        self.factory.assert_called_once_with(
            {
                "campus": {
                    "transport": "streamable_http",
                    "url": "http://127.0.0.1:8001/mcp",
                    "headers": {"Authorization": authorization},
                }
            }
        )
        self.client.get_tools.assert_awaited_once_with(server_name="campus")
        self.tool.ainvoke.assert_awaited_once_with({})

    async def test_date_is_the_only_tool_argument(self):
        result = await mcp_client_service.call_get_my_schedules(
            "Bearer aaa.jwt.token", "2026-10-10"
        )
        self.assertEqual(result, _SCHEDULE_DATA)
        self.tool.ainvoke.assert_awaited_once_with({"date": "2026-10-10"})

    async def test_different_requests_do_not_reuse_another_users_headers(self):
        for authorization in ("Bearer aaa.jwt.token", "Bearer bbb.jwt.token"):
            await mcp_client_service.call_get_my_schedules(authorization)
        self.assertEqual(self.factory.call_count, 2)
        headers = [call.args[0]["campus"]["headers"] for call in self.factory.call_args_list]
        self.assertEqual(
            headers, [{"Authorization": "Bearer aaa.jwt.token"}, {"Authorization": "Bearer bbb.jwt.token"}]
        )
        self.assertIsNot(
            self.factory.call_args_list[0].args[0], self.factory.call_args_list[1].args[0]
        )

    async def test_connection_failure_is_a_business_error_without_credentials(self):
        sensitive = "SELECT password; mysql://demo:secret@db/campus"
        self.client.get_tools.side_effect = ConnectionError(sensitive)
        with self.assertRaises(BusinessException) as raised:
            await mcp_client_service.call_get_my_schedules("Bearer aaa.jwt.token")
        self.assertNotIn(sensitive, raised.exception.message)
        self.assertIn("MCP", raised.exception.message)
        self.tool.ainvoke.assert_not_awaited()

    async def test_tool_call_failure_is_a_business_error(self):
        self.tool.ainvoke.side_effect = RuntimeError("private upstream error")
        with self.assertRaises(BusinessException) as raised:
            await mcp_client_service.call_get_my_schedules("Bearer aaa.jwt.token")
        self.assertNotIn("private upstream error", raised.exception.message)

    async def test_client_construction_failure_is_a_business_error(self):
        self.factory.side_effect = RuntimeError("private client configuration")
        with self.assertRaises(BusinessException) as raised:
            await mcp_client_service.call_get_my_schedules("Bearer aaa.jwt.token")
        self.assertNotIn("private client configuration", raised.exception.message)

    async def test_connection_and_tool_timeouts_are_business_errors(self):
        async def never_complete(*args, **kwargs):
            await asyncio.Event().wait()

        for stage in (self.client.get_tools, self.tool.ainvoke):
            with self.subTest(stage=stage), patch.object(mcp_client_service, "_CALL_TIMEOUT", 0.01):
                stage.side_effect = never_complete
                try:
                    with self.assertRaises(BusinessException):
                        await mcp_client_service.call_get_my_schedules("Bearer aaa.jwt.token")
                finally:
                    stage.side_effect = None

    async def test_json_text_string_and_text_block_after_non_text_are_supported(self):
        for result in (
            json.dumps(_SCHEDULE_DATA),
            [{"type": "image", "data": "test-only"}, *_content_blocks(_SCHEDULE_DATA)],
        ):
            with self.subTest(result=result):
                self.tool.ainvoke.return_value = result
                self.assertEqual(
                    await mcp_client_service.call_get_my_schedules("Bearer aaa.jwt.token"),
                    _SCHEDULE_DATA,
                )

    async def test_missing_tool_is_a_business_error(self):
        self.client.get_tools.return_value = [SimpleNamespace(name="some_other_tool")]
        with self.assertRaises(BusinessException) as raised:
            await mcp_client_service.call_get_my_schedules("Bearer aaa.jwt.token")
        self.assertIn("get_my_schedules", raised.exception.message)
        self.tool.ainvoke.assert_not_awaited()

    async def test_empty_non_json_and_wrong_shape_results_fail_safely(self):
        for result in (
            None,
            [],
            [{"type": "text", "text": "private invalid JSON"}],
            _content_blocks([]),
            _content_blocks({"unexpected": "private payload"}),
            _content_blocks({"items": "wrong", "count": 1}),
            _content_blocks({"items": [], "count": "wrong"}),
            _content_blocks({"items": [], "count": True}),
            _content_blocks({"items": [], "count": 2}),
        ):
            with self.subTest(result=result):
                self.tool.ainvoke.return_value = result
                with self.assertRaises(BusinessException) as raised:
                    await mcp_client_service.call_get_my_schedules("Bearer aaa.jwt.token")
                self.assertNotIn("private", raised.exception.message)

    async def test_mcp_tool_error_becomes_a_business_error(self):
        self.tool.ainvoke.return_value = _content_blocks(
            {"error": "日期格式错误，请使用 YYYY-MM-DD", "items": [], "count": 0}
        )
        with self.assertRaises(BusinessException) as raised:
            await mcp_client_service.call_get_my_schedules("Bearer aaa.jwt.token", "bad-date")
        self.assertIn("日期格式错误", raised.exception.message)


_DEFAULT_AUTHORIZATION = object()


class MCPClientApiTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.password_hash = hash_password("123")

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
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        with self.sessions() as db:
            users = [
                User(username="aaa", password=self.password_hash, name="学生甲", role="student"),
                User(username="bbb", password=self.password_hash, name="学生乙", role="student"),
            ]
            db.add_all(users)
            db.commit()
            self.aaa_id, self.bbb_id = [user.id for user in users]
        self.tokens = {
            "aaa": create_access_token(self.aaa_id, self.password_hash),
            "bbb": create_access_token(self.bbb_id, self.password_hash),
        }
        self.app = FastAPI()
        self.app.include_router(api)
        self.app.add_exception_handler(BusinessException, business_exception_handler)
        self.app.add_exception_handler(HTTPException, http_excpetion_hadler)
        self.app.add_exception_handler(RequestValidationError, validation_excpetion_hadler)

        def test_db():
            with self.sessions() as db:
                yield db

        self.app.dependency_overrides[get_db] = test_db
        self.call_mcp = self.patches.enter_context(
            patch.object(
                mcp_client_service,
                "call_get_my_schedules",
                new=AsyncMock(return_value=_SCHEDULE_DATA),
            )
        )

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.patches.close()
        self.engine.dispose()

    async def request(
        self, method="GET", target="/api/mcp/my-schedule", *,
        authorization=_DEFAULT_AUTHORIZATION, payload=None, form=None,
    ):
        parsed = urlsplit(target)
        if authorization is _DEFAULT_AUTHORIZATION:
            authorization = f"Bearer {self.tokens['aaa']}"
        body = b""
        headers = [(b"host", b"testserver")]
        if authorization is not None:
            headers.append((b"authorization", authorization.encode("ascii")))
        if form is not None:
            body = urlencode(form).encode("ascii")
            headers.append((b"content-type", b"application/x-www-form-urlencoded"))
        elif payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers.append((b"content-type", b"application/json"))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": parsed.path,
            "raw_path": parsed.path.encode("ascii"),
            "query_string": parsed.query.encode("ascii"),
            "root_path": "",
            "headers": headers,
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
        }
        messages = []
        request_received = False

        async def receive():
            nonlocal request_received
            if not request_received:
                request_received = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        await self.app(scope, receive, send)
        start = next(item for item in messages if item["type"] == "http.response.start")
        response_body = b"".join(
            item.get("body", b"") for item in messages if item["type"] == "http.response.body"
        )
        return start["status"], json.loads(response_body)

    async def test_unauthenticated_or_invalid_jwt_requests_never_call_mcp(self):
        for authorization in (None, "Bearer invalid.jwt.token", "Basic invalid"):
            with self.subTest(authorization=authorization):
                status, response = await self.request(authorization=authorization)
                self.assertEqual(status, 401)
                self.assertEqual(response["code"], 401)
        self.call_mcp.assert_not_awaited()

    async def test_authenticated_request_forwards_original_header_and_date(self):
        authorization = f"bearer {self.tokens['aaa']}"
        status, response = await self.request(
            target="/api/mcp/my-schedule?date=2026-10-10", authorization=authorization
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 200)
        self.assertEqual(response["data"], _SCHEDULE_DATA)
        self.call_mcp.assert_awaited_once_with(authorization, "2026-10-10")

    async def test_user_id_query_cannot_replace_authorization(self):
        status, response = await self.request(
            target=f"/api/mcp/my-schedule?user_id={self.aaa_id}",
            authorization=f"Bearer {self.tokens['bbb']}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 200)
        self.call_mcp.assert_awaited_once_with(f"Bearer {self.tokens['bbb']}", None)

    async def test_mcp_unavailable_is_returned_as_existing_business_error_envelope(self):
        self.call_mcp.side_effect = BusinessException(message="MCP 服务不可用，请稍后重试")
        status, response = await self.request()
        self.assertEqual(status, 200)
        self.assertNotEqual(response["code"], 200)
        self.assertIn("MCP", response["message"])
        self.assertIsNone(response["data"])

    def test_openapi_uses_correct_route_and_oauth_form_token_endpoint(self):
        schema = self.app.openapi()
        self.assertIn("/api/mcp/my-schedule", schema["paths"])
        parameters = schema["paths"]["/api/mcp/my-schedule"]["get"]["parameters"]
        self.assertEqual([parameter["name"] for parameter in parameters], ["date"])
        self.assertTrue(schema["paths"]["/api/mcp/my-schedule"]["get"]["security"])
        security = schema["components"]["securitySchemes"]["OAuth2PasswordBearer"]
        self.assertEqual(security["flows"]["password"]["tokenUrl"], "/api/auth/token")
        form = schema["paths"]["/api/auth/token"]["post"]["requestBody"]["content"]
        self.assertIn("application/x-www-form-urlencoded", form)

    async def test_swagger_form_token_succeeds_and_can_call_mcp_route(self):
        status, response = await self.request(
            "POST", "/api/auth/token", authorization=None,
            form={"username": "aaa", "password": "123", "grant_type": "password"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(set(response), {"access_token", "token_type"})
        self.assertEqual(response["token_type"], "bearer")
        self.assertEqual(decode_access_token(response["access_token"])["user_id"], self.aaa_id)
        authorization = f"Bearer {response['access_token']}"
        status, response = await self.request(authorization=authorization)
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 200)
        self.call_mcp.assert_awaited_once_with(authorization, None)

    async def test_swagger_wrong_password_returns_oauth_error_with_http_400(self):
        status, response = await self.request(
            "POST", "/api/auth/token", authorization=None,
            form={"username": "aaa", "password": "wrong"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["error"], "invalid_grant")
        self.assertTrue(response["error_description"])
        self.assertNotIn("data", response)
        self.call_mcp.assert_not_awaited()

    async def test_swagger_disabled_account_returns_oauth_error(self):
        with self.sessions() as db:
            db.get(User, self.bbb_id).status = 0
            db.commit()
        status, response = await self.request(
            "POST", "/api/auth/token", authorization=None,
            form={"username": "bbb", "password": "123"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["error"], "invalid_grant")

    async def test_existing_json_login_response_is_preserved(self):
        status, response = await self.request(
            "POST", "/api/auth/login", authorization=None,
            payload={"username": "aaa", "password": "123"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 200)
        self.assertIn("token", response["data"])
        self.assertEqual(response["data"]["user"]["username"], "aaa")
        self.assertEqual(decode_access_token(response["data"]["token"])["user_id"], self.aaa_id)


if __name__ == "__main__":
    unittest.main()
