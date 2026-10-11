"""周课表接口离线回归：真实 JWT + 内存 SQLite，无 Key 或网络依赖。"""

from contextlib import ExitStack
from datetime import date, timedelta
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic_settings import BaseSettings
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


_settings_init = BaseSettings.__init__
_TEST_SECRET = "week-schedule-offline-tests-only-do-not-use-in-production"


def _offline_settings(self, *args, **kwargs):
    kwargs["_env_file"] = None
    _settings_init(self, *args, **kwargs)


with patch.dict(os.environ, {"DATABASE_URL": "sqlite://", "JWT_SECRET_KEY": _TEST_SECRET}), \
        patch.object(BaseSettings, "__init__", _offline_settings):
    from app.api.schedule import router
    from app.common.exceptions import (
        BusinessException,
        business_exception_handler,
        global_excpetion_hadler,
        http_excpetion_hadler,
        validation_excpetion_hadler,
    )
    from app.common.schedule_config import SCHEDULE_PERIODS
    from app.config import settings
    from app.database import Base, get_db
    from app.models.coures import Course
    from app.models.schedule import Schedule
    from app.models.user import User
    from app.services import week_schedule_service
    from app.utils.jwt import create_access_token


_DEFAULT_HEADERS = object()


class WeekScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(settings, "JWT_SECRET_KEY", _TEST_SECRET))
        self.patches.enter_context(patch.object(settings, "JWT_ALGORITHM", "HS256"))
        self.patches.enter_context(patch.object(settings, "SEMESTER_START_DATE", date(2026, 9, 7)))
        self.patches.enter_context(patch.object(settings, "TOTAL_WEEKS", 20))
        self.patches.enter_context(
            patch.object(week_schedule_service, "shanghai_today", return_value=date(2026, 10, 10))
        )
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        event.listen(
            self.engine,
            "connect",
            lambda connection, record: connection.execute("PRAGMA foreign_keys=ON"),
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        with self.sessions() as db:
            aaa = User(username="aaa", name="学生甲", password="unused", role="student")
            bbb = User(username="bbb", name="学生乙", password="unused", role="student")
            admin = User(username="admin", name="管理员", password="unused", role="admin")
            courses = [Course(name="数据结构", code="DS101"), Course(name="高等数学", code="MATH101")]
            db.add_all([aaa, bbb, admin, *courses])
            db.flush()
            self.aaa_id, self.bbb_id, self.admin_id = aaa.id, bbb.id, admin.id
            self.course_ids = [course.id for course in courses]
            db.commit()
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        self.app.add_exception_handler(BusinessException, business_exception_handler)
        self.app.add_exception_handler(HTTPException, http_excpetion_hadler)
        self.app.add_exception_handler(RequestValidationError, validation_excpetion_hadler)
        self.app.add_exception_handler(Exception, global_excpetion_hadler)

        def dependency():
            with self.sessions() as db:
                yield db

        self.app.dependency_overrides[get_db] = dependency
        self.headers = self.auth(self.aaa_id)

    def auth(self, user_id):
        return {"Authorization": "Bearer " + create_access_token(user_id, "unused")}

    def add_schedule(self, class_date="2026-10-08", **kwargs):
        values = {
            "user_id": self.aaa_id,
            "course_id": self.course_ids[0],
            "class_date": date.fromisoformat(class_date),
            "period": "1-2节",
            "location": "教学楼 A101",
            "teacher": "李老师",
        }
        values.update(kwargs)
        with self.sessions() as db:
            row = Schedule(**values)
            db.add(row)
            db.flush()
            identifier = row.id
            db.commit()
        return identifier

    async def request(self, path="/api/schedules/week", *, params=None, headers=_DEFAULT_HEADERS):
        headers = self.headers if headers is _DEFAULT_HEADERS else headers
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
            base_url="http://offline",
        ) as client:
            return await client.get(path, params=params, headers=headers)

    async def week(self, week=None, **kwargs):
        params = kwargs.pop("params", {})
        if week is not None:
            params["week"] = str(week)
        response = await self.request(params=params, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["code"], 200, result)
        return result["data"]

    @staticmethod
    def courses(data):
        return [course for day in data["days"] for course in day["courses"]]

    async def test_empty_week_preserves_seven_days_and_four_periods(self):
        data = await self.week(1)
        self.assertEqual((data["user_id"], data["username"], data["name"]), (self.aaa_id, "aaa", "学生甲"))
        self.assertEqual(data["periods"], ["1-2节", "3-4节", "5-6节", "7-8节"])
        self.assertEqual(len(data["days"]), 7)
        self.assertEqual([day["weekday"] for day in data["days"]], list(range(1, 8)))
        self.assertEqual([len(day["courses"]) for day in data["days"]], [0] * 7)
        self.assertEqual((data["start_date"], data["end_date"]), ("2026-09-07", "2026-09-13"))
        self.assertEqual((data["semester_start_date"], data["semester_end_date"]), ("2026-09-07", "2027-01-24"))
        self.assertFalse(data["has_holidays"])
        self.assertEqual(data["holiday_notice"], "")

    async def test_course_fields_include_joined_name_teacher_range_and_actual_slot(self):
        identifier = self.add_schedule(start_week=3, end_week=8)
        data = await self.week(5)
        course = data["days"][3]["courses"][0]
        self.assertEqual(
            course,
            {"id": identifier, "user_id": self.aaa_id, "period": "1-2节",
             "course_id": self.course_ids[0], "course_name": "数据结构", "teacher": "李老师",
             "location": "教学楼 A101", "start_week": 3, "end_week": 8},
        )

    async def test_range_start_and_end_are_inclusive(self):
        self.add_schedule(start_week=5, end_week=5)
        self.assertEqual(len(self.courses(await self.week(5))), 1)

    async def test_range_excludes_future_and_ended_courses(self):
        self.add_schedule(start_week=6, end_week=20)
        self.add_schedule(period="3-4节", start_week=1, end_week=4)
        self.assertEqual(self.courses(await self.week(5)), [])

    async def test_class_date_must_also_belong_to_requested_week(self):
        self.add_schedule("2026-09-30", start_week=1, end_week=20)
        self.add_schedule("2026-10-12", start_week=1, end_week=20)
        self.assertEqual(self.courses(await self.week(5)), [])

    async def test_old_record_defaults_remain_visible(self):
        self.add_schedule(teacher=None, location=None)
        course = self.courses(await self.week(5))[0]
        self.assertEqual((course["start_week"], course["end_week"]), (1, 20))
        self.assertIsNone(course["teacher"])
        self.assertIsNone(course["location"])

    async def test_first_and_twentieth_weeks_can_both_return_courses(self):
        self.add_schedule("2026-09-07")
        self.add_schedule("2027-01-22")
        first, last = await self.week(1), await self.week(20)
        self.assertEqual(len(self.courses(first)), 1)
        self.assertEqual(len(self.courses(last)), 1)
        self.assertEqual((last["start_date"], last["end_date"]), ("2027-01-18", "2027-01-24"))

    async def test_week_two_sunday_makeup_returns_actual_sunday_course(self):
        self.add_schedule("2026-09-20")
        self.add_schedule("2026-09-19")
        data = await self.week(2)
        self.assertFalse(data["days"][5]["is_class_day"])
        self.assertEqual(data["days"][5]["courses"], [])
        self.assertTrue(data["days"][6]["is_class_day"])
        self.assertEqual(len(data["days"][6]["courses"]), 1)
        self.assertEqual(data["days"][6]["date"], "2026-09-20")

    async def test_week_five_only_thursday_friday_and_makeup_saturday_return_courses(self):
        for offset in range(7):
            self.add_schedule((date(2026, 10, 5) + timedelta(days=offset)).isoformat())
        data = await self.week(5)
        self.assertEqual([len(day["courses"]) for day in data["days"]], [0, 0, 0, 1, 1, 1, 0])
        self.assertEqual([day["is_holiday"] for day in data["days"]], [True, True, True, False, False, False, False])
        self.assertEqual([day["holiday_name"] for day in data["days"][:3]], ["国庆节"] * 3)
        self.assertTrue(data["days"][5]["is_class_day"])
        self.assertFalse(data["days"][6]["is_class_day"])
        self.assertTrue(data["has_holidays"])
        self.assertIn("10/1-10/7 国庆节放假", data["holiday_notice"])

    async def test_regular_weekend_courses_do_not_enter_grid(self):
        self.add_schedule("2026-10-17")
        self.add_schedule("2026-10-18")
        self.assertEqual(self.courses(await self.week(6)), [])

    async def test_mid_autumn_weekday_and_new_year_weekday_are_excluded(self):
        self.add_schedule("2026-09-25")
        self.add_schedule("2027-01-01")
        for week, name in ((3, "中秋节"), (17, "元旦")):
            data = await self.week(week)
            self.assertEqual(self.courses(data), [])
            self.assertTrue(data["days"][4]["is_holiday"])
            self.assertEqual(data["days"][4]["holiday_name"], name)
            self.assertIn(name, data["holiday_notice"])

    async def test_course_order_uses_standard_period_order(self):
        for period in reversed(SCHEDULE_PERIODS):
            self.add_schedule(period=period)
        data = await self.week(5)
        self.assertEqual([course["period"] for course in data["days"][3]["courses"]], list(SCHEDULE_PERIODS))

    async def test_student_isolation_cannot_be_overridden_by_user_id_query(self):
        aaa_row = self.add_schedule()
        bbb_row = self.add_schedule(user_id=self.bbb_id, course_id=self.course_ids[1])
        aaa = await self.week(5, params={"user_id": str(self.bbb_id)})
        bbb = await self.week(5, params={"user_id": str(self.aaa_id)}, headers=self.auth(self.bbb_id))
        self.assertEqual([course["id"] for course in self.courses(aaa)], [aaa_row])
        self.assertEqual([course["id"] for course in self.courses(bbb)], [bbb_row])
        self.assertTrue(all(course["user_id"] == self.aaa_id for course in self.courses(aaa)))
        self.assertTrue(all(course["user_id"] == self.bbb_id for course in self.courses(bbb)))
        self.assertEqual((aaa["user_id"], aaa["username"]), (self.aaa_id, "aaa"))
        self.assertEqual((bbb["user_id"], bbb["username"]), (self.bbb_id, "bbb"))

    async def test_student_ignores_invalid_and_nonexistent_user_id_query(self):
        own_row = self.add_schedule()
        for value in ("missing", "999999", "0", "", "-1"):
            with self.subTest(user_id=value):
                data = await self.week(5, params={"user_id": value})
                self.assertEqual(data["user_id"], self.aaa_id)
                self.assertEqual([row["id"] for row in self.courses(data)], [own_row])

    async def test_invalid_week_values_are_business_400_instead_of_422_or_500(self):
        for value in ("0", "21", "-1", "1.5", "abc", "", "true", "2026-10-10", "9" * 5000):
            with self.subTest(value=value[:20]):
                response = await self.request(params={"week": value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["code"], 400)
                self.assertIn("周次", response.json()["message"])

    async def test_missing_token_is_rejected_with_http_401(self):
        response = await self.request(params={"week": "5"}, headers={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], 401)

    async def test_forged_token_is_rejected_with_http_401(self):
        response = await self.request(headers={"Authorization": "Bearer forged-token"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], 401)

    async def test_admin_without_user_id_returns_own_week_instead_of_all_students(self):
        self.add_schedule()
        self.add_schedule(user_id=self.bbb_id)
        own_row = self.add_schedule(user_id=self.admin_id)
        data = await self.week(5, headers=self.auth(self.admin_id))
        self.assertEqual((data["user_id"], data["username"], data["name"]), (self.admin_id, "admin", "管理员"))
        self.assertEqual([row["id"] for row in self.courses(data)], [own_row])

    async def test_admin_can_select_student_and_receives_target_identity(self):
        aaa_row = self.add_schedule()
        bbb_row = self.add_schedule(user_id=self.bbb_id, course_id=self.course_ids[1])
        self.add_schedule(user_id=self.admin_id)
        for identifier, username, name, expected in (
            (self.aaa_id, "aaa", "学生甲", aaa_row),
            (self.bbb_id, "bbb", "学生乙", bbb_row),
        ):
            with self.subTest(username=username):
                data = await self.week(5, params={"user_id": str(identifier)}, headers=self.auth(self.admin_id))
                self.assertEqual((data["user_id"], data["username"], data["name"]), (identifier, username, name))
                self.assertEqual([row["id"] for row in self.courses(data)], [expected])
                self.assertTrue(all(row["user_id"] == identifier for row in self.courses(data)))

    async def test_admin_nonexistent_student_returns_business_404(self):
        response = await self.request(params={"week": "5", "user_id": "999999"}, headers=self.auth(self.admin_id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], 404)
        self.assertEqual(response.json()["message"], "学生不存在")

    async def test_admin_explicit_non_student_target_returns_business_404(self):
        response = await self.request(params={"week": "5", "user_id": str(self.admin_id)}, headers=self.auth(self.admin_id))
        self.assertEqual(response.json()["code"], 404)
        self.assertEqual(response.json()["message"], "学生不存在")

    async def test_admin_invalid_student_id_returns_clear_business_400(self):
        for value in ("missing", "0", "-1", "1.5", "", "9" * 5000):
            with self.subTest(user_id=value[:20]):
                response = await self.request(params={"week": "5", "user_id": value}, headers=self.auth(self.admin_id))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["code"], 400)
                self.assertIn("学生 ID", response.json()["message"])

    async def test_admin_view_keeps_holiday_makeup_and_week_range_filters(self):
        self.add_schedule("2026-10-05")
        self.add_schedule("2026-10-08", start_week=6, end_week=20)
        self.add_schedule("2026-10-09", start_week=1, end_week=4)
        makeup = self.add_schedule("2026-10-10", start_week=5, end_week=5)
        self.add_schedule("2026-10-11")
        self.add_schedule("2026-10-10", user_id=self.bbb_id)
        data = await self.week(5, params={"user_id": str(self.aaa_id)}, headers=self.auth(self.admin_id))
        self.assertEqual([row["id"] for row in self.courses(data)], [makeup])
        self.assertTrue(data["has_holidays"])
        self.assertTrue(data["days"][5]["is_class_day"])

    async def test_disabled_student_is_rejected_before_query(self):
        with self.sessions() as db:
            db.query(User).filter(User.id == self.aaa_id).update({"status": 0})
            db.commit()
        response = await self.request(params={"week": "5"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], 401)

    async def test_current_week_today_flags_and_default_week_use_injected_shanghai_date(self):
        self.add_schedule("2026-10-10")
        data = await self.week()
        self.assertEqual((data["week"], data["current_week"], data["today"]), (5, 5, "2026-10-10"))
        self.assertEqual(data["total_weeks"], 20)
        self.assertTrue(data["is_current_week"])
        self.assertEqual([day["weekday"] for day in data["days"] if day["is_today"]], [6])
        self.assertEqual(len(data["days"][5]["courses"]), 1)
        self.assertFalse((await self.week(2))["is_current_week"])

    async def test_before_semester_default_clamps_to_first_week_and_current_is_null(self):
        with patch.object(week_schedule_service, "shanghai_today", return_value=date(2026, 9, 6)):
            data = await self.week()
        self.assertEqual(data["week"], 1)
        self.assertIsNone(data["current_week"])
        self.assertFalse(data["is_current_week"])
        self.assertFalse(any(day["is_today"] for day in data["days"]))

    async def test_after_semester_default_clamps_to_last_week_and_current_is_null(self):
        with patch.object(week_schedule_service, "shanghai_today", return_value=date(2027, 1, 25)):
            data = await self.week()
        self.assertEqual(data["week"], 20)
        self.assertIsNone(data["current_week"])
        self.assertFalse(data["is_current_week"])

    async def test_same_cell_conflict_is_409_and_does_not_silently_stack_or_overwrite(self):
        self.add_schedule()
        self.add_schedule(course_id=self.course_ids[1])
        response = await self.request(params={"week": "5"})
        self.assertEqual(response.json()["code"], 409)
        self.assertIn("2026-10-08 1-2节", response.json()["message"])
        self.assertIn("冲突", response.json()["message"])
        with self.sessions() as db:
            self.assertEqual(db.query(Schedule).count(), 2)

    async def test_nonstandard_or_missing_period_is_explicit_business_400(self):
        identifier = self.add_schedule(period="9-10节")
        for period in ("9-10节", None):
            with self.sessions() as db:
                db.query(Schedule).filter(Schedule.id == identifier).update({"period": period})
                db.commit()
            response = await self.request(params={"week": "5"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["code"], 400)
            self.assertIn("四档标准节次", response.json()["message"])

    async def test_ineligible_rows_do_not_cause_spurious_conflicts_or_period_errors(self):
        self.add_schedule()
        self.add_schedule(start_week=6)
        self.add_schedule("2026-10-05", period="unsupported")
        self.assertEqual(len(self.courses(await self.week(5))), 1)

    async def test_existing_list_endpoint_keeps_its_unfiltered_actual_date_behavior(self):
        self.add_schedule("2026-10-05")
        response = await self.request("/api/schedules", params={"page": "1", "page_size": "100"})
        self.assertEqual(response.json()["code"], 200)
        self.assertEqual(response.json()["data"]["total"], 1)
        self.assertEqual(response.json()["data"]["list"][0]["class_date"], "2026-10-05")

    async def test_reading_twenty_weeks_does_not_change_any_schedule(self):
        self.add_schedule()
        with self.sessions() as db:
            row = db.query(Schedule).one()
            before = {column.name: getattr(row, column.name) for column in Schedule.__table__.columns}
        for week in range(1, 21):
            await self.week(week)
        with self.sessions() as db:
            row = db.query(Schedule).one()
            after = {column.name: getattr(row, column.name) for column in Schedule.__table__.columns}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
