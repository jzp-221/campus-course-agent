"""Course mutation API, real foreign keys and sanitized database failures."""

from datetime import date
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from tests import test_agent  # disable dotenv before app imports
from app.api.course import router
from app.common.db_errors import database_business_exception
from app.common.exceptions import (BusinessException, business_exception_handler,
    http_excpetion_hadler, validation_excpetion_hadler, global_excpetion_hadler)
from app import database
from app.database import Base, get_db
from app.models.coures import Course
from app.models.schedule import Schedule
from app.models.user import User
from app.utils.jwt import create_access_token


def test_app():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.add_exception_handler(BusinessException, business_exception_handler)
    app.add_exception_handler(HTTPException, http_excpetion_hadler)
    app.add_exception_handler(RequestValidationError, validation_excpetion_hadler)
    app.add_exception_handler(Exception, global_excpetion_hadler)
    return app


class CourseBusinessErrors(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(self.engine, "connect", lambda connection, record: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.addCleanup(self.engine.dispose)
        with self.sessions() as db:
            admin = User(username="admin", name="管理员", password="unused-password-hash", role="admin", status=1)
            student = User(username="student", name="学生", password="unused-password-hash", role="student", status=1)
            course = Course(name="原课程", code="COURSE01", description="原简介", status=1)
            db.add_all([admin, student, course])
            db.commit()
            self.admin_id, self.student_id, self.course_id = admin.id, student.id, course.id
        self.app = test_app()
        def dependency():
            with self.sessions() as db:
                yield db
        self.app.dependency_overrides[get_db] = dependency
        self.headers = {"Authorization": "Bearer " + create_access_token(self.admin_id, "unused-password-hash")}

    async def call(self, method, path, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False), base_url="http://offline") as client:
            response = await client.request(method, path, headers=self.headers, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def listing(self):
        result = await self.call("GET", "/api/course/list")
        self.assertEqual(result["code"], 200)
        return result["data"]

    async def test_delete_without_schedule_succeeds(self):
        result = await self.call("DELETE", f"/api/course/{self.course_id}")
        self.assertEqual(result["code"], 200)
        self.assertEqual(await self.listing(), [])

    async def test_delete_referenced_course_returns_count_and_preserves_all_rows(self):
        with self.sessions() as db:
            db.add_all([Schedule(user_id=self.student_id, course_id=self.course_id, class_date=date(2026, 10, 10)),
                        Schedule(user_id=self.student_id, course_id=self.course_id, class_date=date(2026, 10, 11))])
            db.commit()
        result = await self.call("DELETE", f"/api/course/{self.course_id}")
        self.assertEqual(result["code"], 409)
        self.assertIn("2 条课表安排", result["message"])
        self.assertIn("先删除或调整", result["message"])
        self.assertEqual(len(await self.listing()), 1)
        with self.sessions() as db:
            self.assertEqual(db.query(Schedule).count(), 2)

    async def test_delete_missing_course_is_404_business_error(self):
        result = await self.call("DELETE", "/api/course/99999")
        self.assertEqual(result["code"], 404)
        self.assertEqual(result["message"], "课程不存在")

    async def test_empty_description_survives_put_and_get(self):
        result = await self.call("PUT", f"/api/course/{self.course_id}", json={"description": ""})
        self.assertEqual(result["code"], 200)
        self.assertEqual(result["data"]["description"], "")
        self.assertEqual((await self.listing())[0]["description"], "")

    async def test_name_only_leaves_description_and_code_unchanged(self):
        result = await self.call("PUT", f"/api/course/{self.course_id}", json={"name": "新课程"})
        self.assertEqual(result["code"], 200)
        row = (await self.listing())[0]
        self.assertEqual((row["name"], row["description"], row["code"]), ("新课程", "原简介", "COURSE01"))

    async def test_empty_or_blank_required_code_explicitly_fails_without_other_changes(self):
        for value in ("", "   ", "\t"):
            result = await self.call("PUT", f"/api/course/{self.course_id}", json={"code": value, "description": ""})
            self.assertEqual(result["code"], 400)
            self.assertEqual(result["message"], "课程编码不能为空")
            row = (await self.listing())[0]
            self.assertEqual((row["code"], row["description"]), ("COURSE01", "原简介"))

    async def test_whitespace_description_is_an_empty_string(self):
        result = await self.call("PUT", f"/api/course/{self.course_id}", json={"description": " \t "})
        self.assertEqual(result["code"], 200)
        self.assertEqual((await self.listing())[0]["description"], "")

    async def test_null_is_existing_no_change_semantics(self):
        result = await self.call("PUT", f"/api/course/{self.course_id}", json={"description": None, "code": None})
        self.assertEqual(result["code"], 200)
        self.assertEqual((await self.listing())[0]["description"], "原简介")

    async def test_duplicate_code_update_is_409(self):
        with self.sessions() as db:
            db.add(Course(name="第二门", code="SECOND", description="第二门简介"))
            db.commit()
        result = await self.call("PUT", f"/api/course/{self.course_id}", json={"code": " SECOND "})
        self.assertEqual(result["code"], 409)
        self.assertEqual(result["message"], "课程编码已存在")

    async def test_duplicate_name_create_with_different_code_is_409_without_inserting(self):
        for name in ("原课程", " 原课程 \t"):
            with self.subTest(name=name):
                result = await self.call("POST", "/api/course", json={"name": name, "code": "DIFFERENT", "description": "重复新增"})
                self.assertEqual(result["code"], 409)
                self.assertEqual(result["message"], "课程名称已存在")
                rows = await self.listing()
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]["name"], rows[0]["code"], rows[0]["description"]), ("原课程", "COURSE01", "原简介"))

    async def test_duplicate_name_update_after_trim_is_409_and_preserves_all_fields(self):
        with self.sessions() as db:
            db.add(Course(name="第二门", code="SECOND", description="第二门简介"))
            db.commit()
            before = {column.name: getattr(db.get(Course, self.course_id), column.name) for column in Course.__table__.columns}
        for name in ("第二门", " 第二门 \t"):
            with self.subTest(name=name):
                result = await self.call("PUT", f"/api/course/{self.course_id}", json={"name": name, "code": "ALTERED", "description": "改变", "status": 0})
                self.assertEqual(result["code"], 409)
                self.assertEqual(result["message"], "课程名称已存在")
                with self.sessions() as db:
                    after = {column.name: getattr(db.get(Course, self.course_id), column.name) for column in Course.__table__.columns}
                self.assertEqual(after, before)

    async def test_own_name_update_after_trim_is_allowed(self):
        result = await self.call("PUT", f"/api/course/{self.course_id}", json={"name": " 原课程 ", "description": "更新简介"})
        self.assertEqual(result["code"], 200)
        row = (await self.listing())[0]
        self.assertEqual((row["name"], row["description"], row["code"]), ("原课程", "更新简介", "COURSE01"))

    async def test_distinct_fifty_character_chinese_name_can_be_created_and_updated(self):
        name = "中" * 49 + "课"
        result = await self.call("POST", "/api/course", json={"name": name, "code": "LONGNAME"})
        self.assertEqual(result["code"], 200)
        self.assertEqual(result["data"]["name"], name)
        changed = "文" * 49 + "课"
        result = await self.call("PUT", f"/api/course/{result['data']['id']}", json={"name": changed})
        self.assertEqual(result["code"], 200)
        self.assertEqual(result["data"]["name"], changed)
        self.assertEqual(Course.__table__.c.name.type.length, 50)

    async def test_update_missing_is_404(self):
        result = await self.call("PUT", "/api/course/99999", json={"name": "不存在"})
        self.assertEqual(result["code"], 404)

    async def test_create_empty_code_and_name_are_400(self):
        for values, message in [({"name": "课程", "code": ""}, "课程编码不能为空"),
                                ({"name": " ", "code": "NEWCODE"}, "课程名称不能为空")]:
            result = await self.call("POST", "/api/course", json=values)
            self.assertEqual(result["code"], 400)
            self.assertEqual(result["message"], message)

    async def test_constraint_race_is_409_not_database_500_and_rolls_back(self):
        error = IntegrityError("private-sql-statement", {"private": "parameter"}, RuntimeError("private-connection"))
        for method, payload in [("DELETE", {}), ("PUT", {"json": {"description": "changed"}})]:
            with patch.object(Session, "commit", side_effect=error):
                result = await self.call(method, f"/api/course/{self.course_id}", **payload)
            self.assertEqual(result["code"], 409)
            self.assertFalse(any(value in result["message"] for value in ("private", "sql", "connection")))
            row = (await self.listing())[0]
            self.assertEqual(row["description"], "原简介")

    async def test_database_outage_update_is_503_and_not_applied(self):
        error = OperationalError("secret-sql", {}, RuntimeError("mysql://private-address"))
        with patch.object(Session, "commit", side_effect=error):
            result = await self.call("PUT", f"/api/course/{self.course_id}", json={"name": "失败名称"})
        self.assertEqual(result["code"], 503)
        self.assertNotIn("mysql", result["message"])
        self.assertEqual((await self.listing())[0]["name"], "原课程")

    async def test_auth_database_failure_before_service_is_business_503(self):
        self.app.dependency_overrides.clear()
        broken = Mock()
        broken.query.side_effect = OperationalError("secret-query", {}, RuntimeError("mysql://secret-address"))
        with patch.object(database, "SessionLocal", return_value=broken):
            result = await self.call("DELETE", f"/api/course/{self.course_id}")
        self.assertEqual(result["code"], 503)
        self.assertNotIn("secret", result["message"])
        broken.rollback.assert_called_once()
        broken.close.assert_called_once()


class DatabaseBoundaryTests(unittest.TestCase):
    def test_classifier_no_sql_parameters_or_connection_data(self):
        for error_type, code in [(IntegrityError, 409), (DataError, 400), (OperationalError, 503)]:
            error = error_type("private-query", {"private": "value"}, RuntimeError("private-connection"))
            result = database_business_exception(error)
            self.assertEqual(result.code, code)
            self.assertNotIn("private", result.message)

    def test_database_session_close_failure_is_sanitized(self):
        broken = Mock()
        broken.close.side_effect = OperationalError("private-query", {}, RuntimeError("private-connection"))
        with patch.object(database, "SessionLocal", return_value=broken):
            dependency = database.get_db()
            self.assertIs(next(dependency), broken)
            with self.assertRaises(BusinessException) as caught:
                next(dependency)
        self.assertEqual(caught.exception.code, 503)
        self.assertNotIn("private", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
