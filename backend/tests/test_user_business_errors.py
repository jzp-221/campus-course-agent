"""P5 用户写操作：真实 SQLite 外键与 HTTP 业务响应，不读真实 .env。"""

from datetime import date
import os
import unittest
from unittest.mock import patch
import uuid

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic_settings import BaseSettings
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


_settings_init = BaseSettings.__init__


def _offline_settings(self, *args, **kwargs):
    kwargs["_env_file"] = None
    _settings_init(self, *args, **kwargs)


with patch.dict(os.environ, {
    "DATABASE_URL": "sqlite://", "JWT_SECRET_KEY": uuid.uuid4().hex,
    "LLM_API_KEY": "", "EMBEDDING_API_KEY": "",
}), patch.object(BaseSettings, "__init__", _offline_settings):
    from app.api.user import router
    from app.common.exceptions import (
        BusinessException, business_exception_handler, global_excpetion_hadler,
        http_excpetion_hadler, validation_excpetion_hadler,
    )
    from app.database import Base, get_db
    from app.models.coures import Course
    from app.models.document import Document
    from app.models.schedule import Schedule
    from app.models.study_plan import StudyPlan
    from app.models.user import User
    from app.services import user_service
    from app.utils.jwt import create_access_token


class UserBusinessErrorsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        with self.sessions() as db:
            admin = User(username="admin", password="offline", name="管理员", role="admin")
            student = User(username="student", password="offline", name="学生", role="student")
            other = User(username="other", password="offline", name="另一学生", role="student")
            course = Course(name="离线测试课程", code="OFFLINE-USER")
            db.add_all([admin, student, other, course])
            db.commit()
            self.admin_id, self.student_id, self.other_id = admin.id, student.id, other.id
            self.course_id = course.id
            self.tokens = {
                "admin": create_access_token(admin.id, admin.password),
                "student": create_access_token(student.id, student.password),
            }

        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        for error, handler in (
            (BusinessException, business_exception_handler),
            (HTTPException, http_excpetion_hadler),
            (RequestValidationError, validation_excpetion_hadler),
            (Exception, global_excpetion_hadler),
        ):
            self.app.add_exception_handler(error, handler)

        def test_db():
            with self.sessions() as db:
                yield db

        self.app.dependency_overrides[get_db] = test_db

    async def request(self, method, path, *, user="admin", payload=None):
        headers = {} if user is None else {"Authorization": f"Bearer {self.tokens[user]}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
            base_url="http://offline",
        ) as client:
            response = await client.request(method, path, headers=headers, json=payload)
        return response.status_code, response.json()

    async def delete(self, user_id=None, *, user="admin"):
        return await self.request(
            "DELETE", f"/api/user/{self.student_id if user_id is None else user_id}", user=user
        )

    def add_references(self, *, schedules=0, plans=0, documents=0, user_id=None):
        owner = self.student_id if user_id is None else user_id
        with self.sessions() as db:
            db.add_all([
                Schedule(user_id=owner, course_id=self.course_id, class_date=date(2026, 10, 10))
                for _ in range(schedules)
            ] + [
                StudyPlan(user_id=owner, content=f"学习计划 {index}")
                for index in range(plans)
            ] + [
                Document(
                    uploader_id=owner, filename=f"规定 {index}.txt", storage_path=f"offline-{index}",
                    extracted_text="教务规定", chunk_count=1, status="indexed",
                )
                for index in range(documents)
            ])
            db.commit()

    def assert_user_exists(self, user_id=None):
        with self.sessions() as db:
            self.assertIsNotNone(db.get(User, self.student_id if user_id is None else user_id))

    def reference_counts(self):
        with self.sessions() as db:
            return tuple(db.query(model).count() for model in (Schedule, StudyPlan, Document))

    async def test_delete_unreferenced_student_succeeds(self):
        status, response = await self.delete()
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 200)
        with self.sessions() as db:
            self.assertIsNone(db.get(User, self.student_id))
        self.assert_user_exists(self.admin_id)
        self.assert_user_exists(self.other_id)

    async def test_schedule_reference_blocks_delete_with_count(self):
        self.add_references(schedules=2)
        status, response = await self.delete()
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 409)
        self.assertIn("课表 2 条", response["message"])
        self.assertIn("请先清理", response["message"])
        self.assert_user_exists()
        self.assertEqual(self.reference_counts(), (2, 0, 0))

    async def test_study_plan_reference_blocks_delete_with_count(self):
        self.add_references(plans=2)
        _, response = await self.delete()
        self.assertEqual(response["code"], 409)
        self.assertIn("学习计划 2 条", response["message"])
        self.assert_user_exists()
        self.assertEqual(self.reference_counts(), (0, 2, 0))

    async def test_uploaded_document_reference_blocks_delete_with_count(self):
        self.add_references(documents=2)
        _, response = await self.delete()
        self.assertEqual(response["code"], 409)
        self.assertIn("上传文档 2 份", response["message"])
        self.assert_user_exists()
        self.assertEqual(self.reference_counts(), (0, 0, 2))

    async def test_combined_references_all_reported_and_no_data_removed(self):
        self.add_references(schedules=2, plans=3, documents=1)
        _, response = await self.delete()
        self.assertEqual(response["code"], 409)
        for fragment in ("课表 2 条", "学习计划 3 条", "上传文档 1 份"):
            self.assertIn(fragment, response["message"])
        self.assert_user_exists()
        self.assertEqual(self.reference_counts(), (2, 3, 1))

    async def test_references_owned_by_other_user_do_not_block(self):
        self.add_references(schedules=1, plans=1, documents=1, user_id=self.other_id)
        _, response = await self.delete()
        self.assertEqual(response["code"], 200)
        self.assertEqual(self.reference_counts(), (1, 1, 1))
        self.assert_user_exists(self.other_id)

    async def test_delete_nonexistent_user_returns_404_business_error(self):
        status, response = await self.delete(99999)
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 404)
        self.assertEqual(response["message"], "用户不存在")
        self.assert_user_exists()

    async def test_delete_current_admin_returns_403_and_keeps_account(self):
        status, response = await self.delete(self.admin_id)
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 403)
        self.assertEqual(response["message"], "不能删除当前登录的账号")
        self.assert_user_exists(self.admin_id)

    async def test_student_and_unauthenticated_request_rejected(self):
        status, response = await self.delete(user="student")
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 403)
        status, response = await self.delete(user=None)
        self.assertEqual(status, 401)
        self.assertEqual(response["code"], 401)
        self.assert_user_exists()

    async def test_foreign_key_race_is_business_conflict_not_http_500(self):
        # 在关联检查后插入引用，实际开启的外键使 DELETE commit 抛 IntegrityError。
        # 这验证 fallback 仍拒绝写入，未靠吞异常返回成功。
        real_delete = Session.delete

        def late_reference(db, instance):
            if isinstance(instance, User) and instance.id == self.student_id:
                db.add(Schedule(
                    user_id=self.student_id, course_id=self.course_id,
                    class_date=date(2026, 10, 10),
                ))
                db.flush()
            return real_delete(db, instance)

        with patch.object(Session, "delete", late_reference):
            status, response = await self.delete()
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 409)
        self.assertIn("关联", response["message"])
        self.assertNotIn("DELETE", response["message"])
        self.assertNotIn("FOREIGN KEY", response["message"])
        self.assertNotIn("users", response["message"])
        self.assert_user_exists()
        self.assertEqual(self.reference_counts(), (0, 0, 0))

    async def test_nonexistent_user_update_is_business_404(self):
        status, response = await self.request("PUT", "/api/user/99999", payload={"name": "新名称"})
        self.assertEqual(status, 200)
        self.assertEqual(response["code"], 404)
        self.assertEqual(response["message"], "用户不存在")

    async def test_all_user_mutation_database_failures_are_sanitized_and_rolled_back(self):
        error = OperationalError(
            "UPDATE users SET password='PRIVATE-PASSWORD'", {},
            Exception("mysql://private-user:PRIVATE-PASSWORD@db/users private SQL"),
        )
        operations = (
            ("POST", "/api/user", {"username": "new-user", "password": "old", "name": "新增学生"}),
            ("PUT", f"/api/user/{self.student_id}", {"name": "应回滚"}),
            ("PUT", "/api/user/me", {"name": "应回滚"}),
            ("PUT", "/api/user/password", {"old_password": "old", "new_password": "new"}),
            ("DELETE", f"/api/user/{self.student_id}", None),
        )
        for method, path, payload in operations:
            with self.subTest(method=method, path=path):
                with patch.object(Session, "commit", side_effect=error), \
                     patch.object(user_service, "verify_password", return_value=True), \
                     patch.object(user_service, "hash_password", return_value="offline-hash"):
                    status, response = await self.request(method, path, payload=payload)
                self.assertEqual(status, 200)
                self.assertEqual(response["code"], 503)
                self.assertIn("数据库暂时不可用", response["message"])
                serialized = str(response)
                for secret in ("UPDATE", "users", "PRIVATE-PASSWORD", "mysql://", "private SQL"):
                    self.assertNotIn(secret, serialized)
                with self.sessions() as db:
                    self.assertEqual(db.get(User, self.admin_id).name, "管理员")
                    self.assertEqual(db.get(User, self.admin_id).password, "offline")
                    self.assertEqual(db.get(User, self.student_id).name, "学生")
                    self.assertIsNone(db.query(User).filter(User.username == "new-user").first())


if __name__ == "__main__":
    unittest.main()
