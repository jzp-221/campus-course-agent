"""排课写入边界：真实 HTTP/JWT + 内存 SQLite，不访问真实库或模型。"""

from contextlib import ExitStack
from datetime import date
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests import test_agent  # Initialise settings without reading dotenv.
from app.api.schedule import router
from app.common.exceptions import (
    BusinessException, business_exception_handler, global_excpetion_hadler,
    http_excpetion_hadler, validation_excpetion_hadler,
)
from app.common.schedule_config import SCHEDULE_PERIODS
from app.config import settings
from app.database import Base, get_db
from app.models.coures import Course
from app.models.schedule import Schedule
from app.models.user import User
from app.services import schedule_service
from app.utils.jwt import create_access_token


class ScheduleWriteValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(patch.object(settings, "JWT_SECRET_KEY", "schedule-offline-test-key-only-not-for-production"))
        patches.enter_context(patch.object(settings, "SEMESTER_START_DATE", date(2026, 9, 7)))
        patches.enter_context(patch.object(settings, "TOTAL_WEEKS", 20))
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, autoflush=False)
        with self.sessions() as db:
            users = [User(username=name, name=name, password="offline", role="admin" if name == "admin" else "student")
                     for name in ("aaa", "bbb", "admin")]
            courses = [Course(name="数据结构", code="DS101"), Course(name="高等数学", code="MATH101")]
            db.add_all(users + courses)
            db.commit()
            self.users = {user.username: user.id for user in users}
            self.courses = [course.id for course in courses]
            self.tokens = {user.username: create_access_token(user.id, user.password) for user in users}
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        for error, handler in (
            (BusinessException, business_exception_handler), (HTTPException, http_excpetion_hadler),
            (RequestValidationError, validation_excpetion_hadler), (Exception, global_excpetion_hadler),
        ):
            self.app.add_exception_handler(error, handler)

        def dependency():
            with self.sessions() as db:
                yield db

        self.app.dependency_overrides[get_db] = dependency

    async def call(self, method, path="/api/schedules", *, payload=None, user="admin", params=None):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False), base_url="http://offline",
        ) as client:
            response = await client.request(method, path, json=payload, params=params,
                headers={"Authorization": "Bearer " + self.tokens[user]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def payload(self, **fields):
        data = {"user_id": self.users["aaa"], "course_id": self.courses[0],
                "class_date": "2026-10-14", "period": "1-2节", "location": "A101"}
        data.update(fields)
        return data

    async def create(self, **fields):
        result = await self.call("POST", payload=self.payload(**fields))
        self.assertEqual(result["code"], 200, result)
        return result["data"]

    def rows(self):
        with self.sessions() as db:
            return [{"id": row.id, "user_id": row.user_id, "class_date": row.class_date,
                     "period": row.period, "course_id": row.course_id, "teacher": row.teacher,
                     "start_week": row.start_week, "end_week": row.end_week}
                    for row in db.query(Schedule).order_by(Schedule.id).all()]

    async def test_create_same_student_same_day_same_period_is_409_without_inserting(self):
        row = await self.create()
        result = await self.call("POST", payload=self.payload(course_id=self.courses[1]))
        self.assertEqual(result["code"], 409)
        self.assertIn("已有课表安排", result["message"])
        self.assertEqual([item["id"] for item in self.rows()], [row["id"]])
        week = await self.call("GET", "/api/schedules/week", user="aaa", params={"week": 6})
        self.assertEqual(week["code"], 200)

    async def test_update_period_into_occupied_slot_is_409_and_preserves_row(self):
        await self.create()
        second = await self.create(period="3-4节")
        before = self.rows()
        result = await self.call("PUT", f"/api/schedules/{second['id']}", payload={"period": "1-2节"})
        self.assertEqual(result["code"], 409)
        self.assertEqual(self.rows(), before)

    async def test_update_date_into_occupied_slot_is_409(self):
        await self.create()
        second = await self.create(class_date="2026-10-15")
        result = await self.call("PUT", f"/api/schedules/{second['id']}", payload={"class_date": "2026-10-14"})
        self.assertEqual(result["code"], 409)
        self.assertEqual(self.rows()[1]["class_date"], date(2026, 10, 15))

    async def test_update_student_into_occupied_slot_is_409(self):
        await self.create()
        second = await self.create(user_id=self.users["bbb"])
        result = await self.call("PUT", f"/api/schedules/{second['id']}", payload={"user_id": self.users["aaa"]})
        self.assertEqual(result["code"], 409)
        self.assertEqual(self.rows()[1]["user_id"], self.users["bbb"])

    async def test_updating_own_existing_slot_excludes_itself(self):
        row = await self.create()
        result = await self.call("PUT", f"/api/schedules/{row['id']}", payload={"period": "1-2节", "location": "B202"})
        self.assertEqual(result["code"], 200)
        self.assertEqual(result["data"]["location"], "B202")

    async def test_different_students_dates_and_periods_can_share_course(self):
        await self.create()
        await self.create(user_id=self.users["bbb"])
        await self.create(class_date="2026-10-15")
        await self.create(period="3-4节")
        self.assertEqual(len(self.rows()), 4)

    async def test_all_four_standard_periods_are_accepted(self):
        for period in SCHEDULE_PERIODS:
            self.assertEqual((await self.create(period=period))["period"], period)

    async def test_invalid_periods_are_400_for_create_and_update(self):
        row = await self.create()
        for period in ("9-10节", "随便一节", "", "  ", 1, True, []):
            for method, path, payload in (
                ("POST", "/api/schedules", self.payload(period=period)),
                ("PUT", f"/api/schedules/{row['id']}", {"period": period}),
            ):
                with self.subTest(method=method, period=period):
                    result = await self.call(method, path, payload=payload)
                    self.assertEqual(result["code"], 400, result)
        self.assertEqual(len(self.rows()), 1)

    async def test_old_create_payload_retains_defaults_and_list_fields(self):
        row = await self.create()
        self.assertEqual((row["start_week"], row["end_week"], row["teacher"]), (1, 20, None))
        result = await self.call("GET", user="aaa")
        self.assertEqual(result["data"]["list"][0], row)

    async def test_teacher_and_week_range_survive_create_list_and_week_view(self):
        row = await self.create(start_week=3, end_week=8, teacher="  李老师  ")
        self.assertEqual((row["start_week"], row["end_week"], row["teacher"]), (3, 8, "李老师"))
        result = await self.call("GET", user="aaa")
        self.assertEqual(result["data"]["list"][0]["teacher"], "李老师")
        week = await self.call("GET", "/api/schedules/week", user="aaa", params={"week": 6})
        courses = [course for day in week["data"]["days"] for course in day["courses"]]
        self.assertEqual((courses[0]["start_week"], courses[0]["end_week"], courses[0]["teacher"]), (3, 8, "李老师"))

    async def test_partial_update_retains_teacher_and_week_range_when_omitted(self):
        row = await self.create(start_week=3, end_week=8, teacher="李老师")
        result = await self.call("PUT", f"/api/schedules/{row['id']}", payload={"location": "B202"})
        self.assertEqual((result["data"]["start_week"], result["data"]["end_week"], result["data"]["teacher"]), (3, 8, "李老师"))

    async def test_teacher_can_be_updated_and_explicitly_cleared(self):
        row = await self.create(teacher="李老师")
        for teacher in ("王老师", None):
            result = await self.call("PUT", f"/api/schedules/{row['id']}", payload={"teacher": teacher})
            self.assertEqual(result["code"], 200)
            self.assertEqual(result["data"]["teacher"], teacher)

    async def test_teacher_length_limit_is_400_without_updating(self):
        row = await self.create(teacher="李老师")
        result = await self.call("PUT", f"/api/schedules/{row['id']}", payload={"teacher": "师" * 101})
        self.assertEqual(result["code"], 400)
        self.assertEqual(self.rows()[0]["teacher"], "李老师")

    async def test_week_boundaries_one_and_twenty_are_accepted(self):
        row = await self.create(start_week=1, end_week=20)
        result = await self.call("PUT", f"/api/schedules/{row['id']}", payload={"start_week": 20, "end_week": 20})
        self.assertEqual(result["code"], 200)
        self.assertEqual((result["data"]["start_week"], result["data"]["end_week"]), (20, 20))

    async def test_invalid_weeks_are_business_400_on_create_and_update(self):
        row = await self.create()
        for field in ("start_week", "end_week"):
            for value in (0, 21, -1, True, 1.5, "bad", None):
                for method, path, payload in (
                    ("POST", "/api/schedules", self.payload(**{field: value})),
                    ("PUT", f"/api/schedules/{row['id']}", {field: value}),
                ):
                    with self.subTest(field=field, value=value, method=method):
                        result = await self.call(method, path, payload=payload)
                        self.assertEqual(result["code"], 400, result)
        self.assertEqual(len(self.rows()), 1)

    async def test_range_order_is_checked_against_unchanged_update_fields(self):
        row = await self.create(start_week=3, end_week=8)
        for payload in ({"start_week": 9}, {"end_week": 2}):
            result = await self.call("PUT", f"/api/schedules/{row['id']}", payload=payload)
            self.assertEqual(result["code"], 400)
            self.assertIn("起始周次", result["message"])
        result = await self.call("POST", payload=self.payload(start_week=9, end_week=3))
        self.assertEqual(result["code"], 400)
        self.assertEqual((self.rows()[0]["start_week"], self.rows()[0]["end_week"]), (3, 8))

    async def test_student_cannot_write_and_user_filter_stays_own_only(self):
        await self.create()
        await self.create(user_id=self.users["bbb"])
        rejected = await self.call("POST", payload=self.payload(period="3-4节"), user="aaa")
        self.assertEqual(rejected["code"], 403)
        result = await self.call("GET", user="aaa", params={"user_id": self.users["bbb"]})
        self.assertEqual(result["data"]["total"], 1)
        self.assertEqual(result["data"]["list"][0]["user_id"], self.users["aaa"])

    async def test_mcp_schedule_dictionary_contract_is_unchanged(self):
        await self.create(start_week=3, end_week=8, teacher="李老师")
        with self.sessions() as db:
            rows = schedule_service.list_schedules_as_dict(db, self.users["aaa"], "2026-10-14")
        self.assertEqual(set(rows[0]), {"date", "period", "course_id", "course_name", "location"})


if __name__ == "__main__":
    unittest.main()
