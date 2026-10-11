"""学习计划接口回归测试：只使用内存数据库，不读取项目 .env。"""

import asyncio
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic_settings import BaseSettings
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# 禁止配置加载真实 .env；应用模块得到独立测试数据库及 JWT 密钥。
_original_settings_init = BaseSettings.__init__


def _test_settings_init(self, *args, **kwargs):
    kwargs["_env_file"] = None
    _original_settings_init(self, *args, **kwargs)


with patch.dict(
    os.environ,
    {
        "DATABASE_URL": "sqlite://",
        "JWT_SECRET_KEY": "study-plan-tests-only-secret-key-never-use-in-production",
        "JWT_ALGORITHM": "HS256",
        "JWT_EXPIRE_HOURS": "1",
    },
), patch.object(BaseSettings, "__init__", _test_settings_init):
    from app.api.study_plan import router
    from app.common.exceptions import (
        BusinessException,
        business_exception_handler,
        http_excpetion_hadler,
        validation_excpetion_hadler,
    )
    from app.database import Base, get_db
    from app.models.study_plan import StudyPlan
    from app.models.user import User
    from app.utils.jwt import create_access_token


class StudyPlanApiTests(unittest.IsolatedAsyncioTestCase):
    """运行真实 JWT 依赖与 API，只替换数据库会话。"""

    def setUp(self):
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
            aaa = User(username="aaa", password="test-only", name="学生甲", role="student")
            bbb = User(username="bbb", password="test-only", name="学生乙", role="student")
            db.add_all([aaa, bbb])
            db.commit()
            self.aaa_id = aaa.id
            self.bbb_id = bbb.id

        self.tokens = {
            "aaa": create_access_token(self.aaa_id, "test-only"),
            "bbb": create_access_token(self.bbb_id, "test-only"),
        }
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        self.app.add_exception_handler(BusinessException, business_exception_handler)
        self.app.add_exception_handler(HTTPException, http_excpetion_hadler)
        self.app.add_exception_handler(RequestValidationError, validation_excpetion_hadler)

        def test_db():
            with self.sessions() as db:
                yield db

        self.app.dependency_overrides[get_db] = test_db

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    async def request(self, method, target="/api/study-plans", *, user="aaa", payload=None):
        """直接发送 ASGI HTTP 请求，无需服务器、网络或 httpx。"""
        parsed = urlsplit(target)
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        headers = [(b"host", b"testserver")]
        if payload is not None:
            headers.append((b"content-type", b"application/json"))
        if user is not None:
            headers.append((b"authorization", f"Bearer {self.tokens[user]}".encode("ascii")))
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
        start = next(message for message in messages if message["type"] == "http.response.start")
        response_body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return start["status"], json.loads(response_body)

    async def save(self, content, user="aaa"):
        status, response = await self.request("POST", user=user, payload={"content": content})
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 200, response)
        return response["data"]

    def count_plans(self):
        with self.sessions() as db:
            return db.query(StudyPlan).count()

    async def test_save_trims_whitespace_and_preserves_markdown(self):
        markdown = "## 本周复习\n- 高等数学：导数练习\n- 大学英语：Unit 3 单词"
        plan = await self.save(f" \n\t{markdown}\n ")
        self.assertEqual(plan["content"], markdown)
        self.assertTrue(plan["create_time"])
        self.assertNotIn("user_id", plan)
        with self.sessions() as db:
            stored = db.get(StudyPlan, plan["id"])
            self.assertEqual(stored.content, markdown)
            self.assertEqual(stored.user_id, self.aaa_id)

    async def test_pagination_is_newest_first_and_total_is_per_user(self):
        first = await self.save("第一条")
        await self.save("乙的计划", user="bbb")
        second = await self.save("第二条")
        third = await self.save("第三条")
        for page, expected in ((1, [third, second]), (2, [first]), (3, [])):
            with self.subTest(page=page):
                status, response = await self.request("GET", f"/api/study-plans?page={page}&page_size=2")
                self.assertEqual(status, 200)
                self.assertEqual(response["code"], 200)
                self.assertEqual(response["data"]["total"], 3)
                self.assertEqual(
                    [row["id"] for row in response["data"]["list"]],
                    [row["id"] for row in expected],
                )

    async def test_other_user_cannot_list_or_delete_my_plan(self):
        plan = await self.save("甲的私有计划")
        status, response = await self.request("GET", user="bbb")
        self.assertEqual(status, 200)
        self.assertEqual(response["data"], {"list": [], "total": 0})
        status, response = await self.request("DELETE", f"/api/study-plans/{plan['id']}", user="bbb")
        self.assertEqual(status, 200)
        self.assertNotEqual(response["code"], 200)
        self.assertEqual(response["message"], "学习计划不存在")
        with self.sessions() as db:
            stored = db.get(StudyPlan, plan["id"])
            self.assertIsNotNone(stored)
            self.assertEqual(stored.content, "甲的私有计划")
            self.assertEqual(stored.user_id, self.aaa_id)

    async def test_user_id_in_payload_or_query_cannot_forge_owner(self):
        status, response = await self.request(
            "POST",
            f"/api/study-plans?user_id={self.bbb_id}",
            payload={"content": "归甲所有", "user_id": self.bbb_id},
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 200)
        plan_id = response["data"]["id"]
        with self.sessions() as db:
            self.assertEqual(db.get(StudyPlan, plan_id).user_id, self.aaa_id)
        status, response = await self.request(
            "GET", f"/api/study-plans?user_id={self.aaa_id}", user="bbb"
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["data"], {"list": [], "total": 0})
        _, response = await self.request(
            "DELETE", f"/api/study-plans/{plan_id}?user_id={self.aaa_id}", user="bbb"
        )
        self.assertNotEqual(response["code"], 200)
        self.assertEqual(self.count_plans(), 1)

    async def test_all_methods_require_login(self):
        plan = await self.save("已有计划")
        for method, target, payload in (
            ("GET", "/api/study-plans", None),
            ("POST", "/api/study-plans", {"content": "不得保存"}),
            ("DELETE", f"/api/study-plans/{plan['id']}", None),
        ):
            with self.subTest(method=method):
                status, response = await self.request(method, target, user=None, payload=payload)
                self.assertEqual(status, 401)
                self.assertEqual(response["code"], 401)
        self.assertEqual(self.count_plans(), 1)

    async def test_invalid_jwt_and_disabled_user_are_rejected(self):
        self.tokens["invalid"] = "invalid.jwt.token"
        self.tokens["missing_user"] = create_access_token(999999, "missing-user")
        with self.sessions() as db:
            db.get(User, self.bbb_id).status = 0
            db.commit()
        for user in ("invalid", "missing_user", "bbb"):
            with self.subTest(user=user):
                status, response = await self.request("GET", user=user)
                self.assertEqual(status, 401)
                self.assertEqual(response["code"], 401)

    async def test_invalid_pagination_is_rejected(self):
        for query in (
            "page=0", "page=-1", "page=abc", "page_size=0",
            "page_size=-1", "page_size=101", "page_size=abc",
        ):
            with self.subTest(query=query):
                status, response = await self.request("GET", f"/api/study-plans?{query}")
                self.assertEqual(status, 422)
                self.assertEqual(response["code"], 422)
        self.assertEqual(self.count_plans(), 0)

    async def test_empty_and_whitespace_content_are_not_persisted(self):
        for content in ("", " \n\t ", "\u3000\u3000"):
            with self.subTest(content=repr(content)):
                status, response = await self.request("POST", payload={"content": content})
                self.assertEqual(status, 200)
                self.assertEqual(response["code"], 400)
                self.assertEqual(response["message"], "计划内容不能为空")
                self.assertEqual(self.count_plans(), 0)

    async def test_exact_length_boundary_and_overlong_content(self):
        plan = await self.save(" \n" + "学" * 2000 + "\n ")
        self.assertEqual(len(plan["content"]), 2000)
        status, response = await self.request("POST", payload={"content": "学" * 2001})
        self.assertEqual(status, 200)
        self.assertNotEqual(response["code"], 200)
        self.assertIn("2000", response["message"])
        self.assertEqual(self.count_plans(), 1)

    async def test_invalid_content_schema_is_not_persisted(self):
        for payload in ({}, {"content": None}, {"content": []}):
            with self.subTest(payload=payload):
                status, response = await self.request("POST", payload=payload)
                self.assertEqual(status, 422)
                self.assertEqual(response["code"], 422)
        self.assertEqual(self.count_plans(), 0)

    async def test_delete_my_plan_reduces_total_and_preserves_other_users(self):
        first = await self.save("删除这条")
        remaining = await self.save("保留这条")
        other = await self.save("乙的计划", user="bbb")
        status, response = await self.request("DELETE", f"/api/study-plans/{first['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 200)
        _, response = await self.request("GET")
        self.assertEqual(response["data"]["total"], 1)
        self.assertEqual([row["id"] for row in response["data"]["list"]], [remaining["id"]])
        with self.sessions() as db:
            self.assertIsNone(db.get(StudyPlan, first["id"]))
            self.assertIsNotNone(db.get(StudyPlan, other["id"]))
        _, response = await self.request("DELETE", f"/api/study-plans/{first['id']}")
        self.assertNotEqual(response["code"], 200)
        self.assertEqual(response["message"], "学习计划不存在")
        self.assertEqual(self.count_plans(), 2)


    async def test_update_my_plan_changes_content_in_place(self):
        plan = await self.save("原始计划")
        status, response = await self.request(
            "PUT", f"/api/study-plans/{plan['id']}", payload={"content": "修改后的计划"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 200)
        self.assertEqual(response["data"]["id"], plan["id"], "修改不应产生新记录")
        self.assertEqual(response["data"]["content"], "修改后的计划")
        self.assertEqual(self.count_plans(), 1)
        with self.sessions() as db:
            row = db.get(StudyPlan, plan["id"])
            self.assertEqual(row.content, "修改后的计划")
            self.assertEqual(row.user_id, self.aaa_id, "归属不应被改动")

    async def test_update_rejects_other_users_plan(self):
        plan = await self.save("甲的计划")
        status, response = await self.request(
            "PUT", f"/api/study-plans/{plan['id']}", payload={"content": "乙来篡改"}, user="bbb"
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 404, "他人的计划按不存在处理，不能泄露存在性")
        self.assertEqual(response["message"], "学习计划不存在")
        with self.sessions() as db:
            self.assertEqual(db.get(StudyPlan, plan["id"]).content, "甲的计划")

    async def test_update_missing_plan_and_invalid_payload(self):
        status, response = await self.request(
            "PUT", "/api/study-plans/999999", payload={"content": "随便"}
        )
        self.assertEqual(response["code"], 404)
        plan = await self.save("保留")
        for payload in ({}, {"content": None}, {"content": []}):
            with self.subTest(payload=payload):
                status, response = await self.request(
                    "PUT", f"/api/study-plans/{plan['id']}", payload=payload
                )
                self.assertEqual(status, 422)
                self.assertEqual(response["code"], 422)
        for content in ("", "   ", "\u3000"):
            with self.subTest(content=repr(content)):
                status, response = await self.request(
                    "PUT", f"/api/study-plans/{plan['id']}", payload={"content": content}
                )
                self.assertEqual(response["code"], 400)
                self.assertEqual(response["message"], "计划内容不能为空")
        self.assertEqual(self.count_plans(), 1)

    async def test_update_enforces_length_limit_without_truncating(self):
        plan = await self.save("短计划")
        status, response = await self.request(
            "PUT", f"/api/study-plans/{plan['id']}", payload={"content": "学" * 2001}
        )
        self.assertEqual(response["code"], 400)
        self.assertIn("2000", response["message"])
        with self.sessions() as db:
            self.assertEqual(db.get(StudyPlan, plan["id"]).content, "短计划", "超长请求不得落库")
        status, response = await self.request(
            "PUT", f"/api/study-plans/{plan['id']}", payload={"content": "学" * 2000}
        )
        self.assertEqual(response["code"], 200)
        self.assertEqual(len(response["data"]["content"]), 2000)

    async def test_update_requires_login(self):
        plan = await self.save("已有计划")
        status, response = await self.request(
            "PUT", f"/api/study-plans/{plan['id']}", payload={"content": "匿名修改"}, user=None
        )
        self.assertEqual(status, 401)
        self.assertEqual(response["code"], 401)
        with self.sessions() as db:
            self.assertEqual(db.get(StudyPlan, plan["id"]).content, "已有计划")


if __name__ == "__main__":
    unittest.main()
