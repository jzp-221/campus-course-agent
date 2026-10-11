"""P13 HTTP + real SQLite foreign keys; no network, dotenv or real credentials."""

from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from tests import test_agent  # initialise offline settings before app imports
from app.api.assignment import router as assignment_router
from app.api.plan_task import router as task_router
from app.api.course import router as course_router
from app.api.user import router as user_router
from app.api.study_plan import router as text_plan_router
from app.common.exceptions import (BusinessException, business_exception_handler,
    http_excpetion_hadler, validation_excpetion_hadler, global_excpetion_hadler)
from app.database import Base, get_db
from app.models.assignment import Assignment
from app.models.plan_task import PlanTask
from app.models.coures import Course
from app.models.user import User
from app.models.study_plan import StudyPlan
from app.models.schedule import Schedule
from app.utils.jwt import create_access_token

NOW = datetime(2026, 10, 11, 12)


class LearningItemsApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, autoflush=False)
        self.addCleanup(self.engine.dispose)
        with self.sessions() as db:
            users = [User(username=name, name=name, password="offline", role="admin" if name == "admin" else "student")
                     for name in ("aaa", "bbb", "admin")]
            courses = [Course(name="数据结构", code="DS"), Course(name="高等数学", code="MATH")]
            db.add_all(users + courses)
            db.commit()
            self.users = {row.username: row.id for row in users}
            self.courses = [row.id for row in courses]
        self.tokens = {name: create_access_token(user_id, "offline") for name, user_id in self.users.items()}
        self.app = FastAPI()
        for router in (assignment_router, task_router, course_router, user_router, text_plan_router):
            self.app.include_router(router, prefix="/api")
        for error, handler in ((BusinessException, business_exception_handler),
                               (HTTPException, http_excpetion_hadler),
                               (RequestValidationError, validation_excpetion_hadler),
                               (Exception, global_excpetion_hadler)):
            self.app.add_exception_handler(error, handler)
        def db_dependency():
            with self.sessions() as db:
                yield db
        self.app.dependency_overrides[get_db] = db_dependency

    async def call(self, method, path, *, user="aaa", payload=None):
        headers = {} if user is None else {"Authorization": "Bearer " + self.tokens[user]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                                     base_url="http://offline") as client:
            result = await client.request(method, path, headers=headers, json=payload)
        self.assertEqual(result.status_code, 401 if user is None else 200, result.text)
        return result.json()

    async def create(self, resource="assignments", *, user="aaa", **fields):
        payload = {"title": "第3章课后习题"}
        if resource == "assignments":
            payload.update(course_id=self.courses[0], type="assignment", due_at="2026-10-12T18:00:00")
        payload.update(fields)
        result = await self.call("POST", f"/api/{resource}", user=user, payload=payload)
        self.assertEqual(result["code"], 200, result)
        return result["data"]

    async def test_assignment_defaults_owner_course_name_and_orm_timestamps(self):
        row = await self.create(title="  实验报告二 \n", user_id=self.users["bbb"])
        self.assertEqual((row['title'], row['user_id'], row['status'], row['course_name']),
                         ("实验报告二", self.users['aaa'], "pending", "数据结构"))
        self.assertIsNotNone(row['create_time'])
        self.assertIsNotNone(row['update_time'])
        with self.sessions() as db:
            stored = db.get(Assignment, row['id'])
            self.assertIsNone(stored.due_at.tzinfo)

    async def test_task_optional_fields_defaults_and_counter_cannot_be_forged(self):
        row = await self.create("plan-tasks", defer_count=99, user_id=self.users['bbb'])
        self.assertEqual((row['user_id'], row['course_id'], row['due_at'], row['defer_count']),
                         (self.users['aaa'], None, None, 0))

    async def test_both_resources_trim_titles_and_enforce_200_character_boundary(self):
        for resource in ("assignments", "plan-tasks"):
            row = await self.create(resource, title="  " + "学" * 200 + "  ")
            self.assertEqual(len(row['title']), 200)
            for title in ("", " \t\n ", "学" * 201, None, 123):
                result = await self.call("PUT", f"/api/{resource}/{row['id']}", payload={"title": title})
                self.assertEqual(result['code'], 400, result)

    async def test_invalid_assignment_type_is_400_on_create_and_update(self):
        row = await self.create()
        for value in ("quiz", "", None, True, []):
            payload = {"title":"测试", "type":value, "course_id":self.courses[0], "due_at":"2026-10-12T18:00:00"}
            for method, path in (("POST", "/api/assignments"), ("PUT", f"/api/assignments/{row['id']}")):
                result = await self.call(method, path, payload=payload)
                self.assertEqual(result['code'], 400, result)

    async def test_invalid_status_is_400_for_both_create_and_update(self):
        for resource in ("assignments", "plan-tasks"):
            row = await self.create(resource)
            for value in ("completed", "", None, {}, 1):
                result = await self.call("PUT", f"/api/{resource}/{row['id']}", payload={"status":value})
                self.assertEqual(result['code'], 400, result)
            result = await self.call("POST", f"/api/{resource}", payload={
                "title":"测试", "course_id":self.courses[0], "type":"assignment",
                "due_at":"2026-10-12T18:00:00", "status":"bad"})
            self.assertEqual(result['code'], 400)

    async def test_assignment_missing_required_fields_returns_400(self):
        valid = {"title":"测试", "course_id":self.courses[0], "type":"assignment", "due_at":"2026-10-12T18:00:00"}
        for field in valid:
            payload = {key:value for key,value in valid.items() if key != field}
            result = await self.call("POST", "/api/assignments", payload=payload)
            self.assertEqual(result['code'], 400, result)
        result = await self.call("POST", "/api/plan-tasks", payload={})
        self.assertEqual(result['code'], 400)
        from app.schemas.assignment import AssignmentCreateRequest
        from app.schemas.plan_task import PlanTaskCreateRequest
        self.assertEqual(set(AssignmentCreateRequest.model_json_schema()['required']),set(valid))
        self.assertEqual(PlanTaskCreateRequest.model_json_schema()['required'],['title'])

    async def test_invalid_due_at_is_400_and_not_persisted(self):
        for resource in ("assignments", "plan-tasks"):
            row = await self.create(resource)
            for value in ("2026-13-99T12:00:00", "2026-02-30T12:00:00", "bad", "2026-10-12", 12345, []) :
                result = await self.call("PUT", f"/api/{resource}/{row['id']}", payload={"due_at":value})
                self.assertEqual(result['code'], 400, result)
        result = await self.call("PUT", "/api/assignments/1", payload={"due_at":None})
        self.assertEqual(result['code'], 400)

    async def test_due_at_is_not_converted_when_iso_offset_is_supplied(self):
        for value in ("2026-10-12T18:00:00", "2026-10-12T18:00:00+08:00", "2026-10-12T18:00:00Z"):
            row = await self.create(due_at=value)
            self.assertEqual(row['due_at'], "2026-10-12T18:00:00")

    async def test_nonexistent_course_returns_404_for_create_and_update(self):
        for resource in ("assignments", "plan-tasks"):
            row = await self.create(resource)
            for method, path in (("POST", f"/api/{resource}"), ("PUT", f"/api/{resource}/{row['id']}")):
                result = await self.call(method, path, payload={"title":"测试", "type":"assignment",
                    "course_id":999999, "due_at":"2026-10-12T18:00:00"})
                self.assertEqual(result['code'], 404)
                self.assertEqual(result['message'], "课程不存在")

    async def test_invalid_course_id_and_overlong_note_are_400(self):
        row = await self.create()
        for value in (0, -1, True, "1", None):
            result = await self.call("PUT", f"/api/assignments/{row['id']}", payload={"course_id":value})
            self.assertEqual(result['code'], 400)
        for note in ("备" * 501, 123):
            result = await self.call("PUT", f"/api/assignments/{row['id']}", payload={"note":note})
            self.assertEqual(result['code'], 400)

    async def test_partial_update_preserves_omitted_fields(self):
        for resource in ("assignments", "plan-tasks"):
            row = await self.create(resource, course_id=self.courses[0], due_at="2026-10-12T18:00:00")
            result = await self.call("PUT", f"/api/{resource}/{row['id']}", payload={"title":"新标题"})
            self.assertEqual(result['code'], 200)
            self.assertEqual((result['data']['due_at'], result['data']['course_id']),
                             (row['due_at'], row['course_id']))

    async def test_task_null_clears_course_and_due_at_but_assignment_note_can_be_cleared(self):
        row = await self.create("plan-tasks", course_id=self.courses[0], due_at="2026-10-12T18:00:00")
        result = await self.call("PUT", f"/api/plan-tasks/{row['id']}", payload={"course_id":None, "due_at":None})
        self.assertEqual((result['data']['course_id'], result['data']['course_name'], result['data']['due_at']), (None,None,None))
        row = await self.create(note="备注")
        result = await self.call("PUT", f"/api/assignments/{row['id']}", payload={"note":None})
        self.assertIsNone(result['data']['note'])

    async def test_other_student_and_admin_cannot_update_delete_or_defer(self):
        for resource in ("assignments", "plan-tasks"):
            row = await self.create(resource)
            actions = [("PUT", {"title":"越权"}), ("DELETE", None)]
            if resource == "plan-tasks":
                actions.append(("POST", {"days":2}))
            for user in ("bbb", "admin"):
                for method, payload in actions:
                    suffix = "/defer" if method == "POST" else ""
                    result = await self.call(method, f"/api/{resource}/{row['id']}{suffix}", user=user, payload=payload)
                    self.assertEqual(result['code'], 404, result)
            with self.sessions() as db:
                model = Assignment if resource == "assignments" else PlanTask
                self.assertEqual(db.get(model, row['id']).title, row['title'])

    async def test_every_role_lists_only_own_records_and_ignores_query_owner(self):
        for resource in ("assignments", "plan-tasks"):
            for name in self.users:
                await self.create(resource, user=name, title=name)
            for name in self.users:
                result = await self.call("GET", f"/api/{resource}?user_id={self.users['aaa']}", user=name)
                self.assertEqual(result['data']['total'], 1)
                self.assertEqual(result['data']['list'][0]['user_id'], self.users[name])

    async def test_own_record_can_be_completed_and_deleted(self):
        for resource in ("assignments", "plan-tasks"):
            row = await self.create(resource)
            result = await self.call("PUT", f"/api/{resource}/{row['id']}", payload={"status":"done"})
            self.assertEqual(result['data']['status'], "done")
            self.assertEqual((await self.call("DELETE", f"/api/{resource}/{row['id']}"))['code'], 200)
            self.assertEqual((await self.call("DELETE", f"/api/{resource}/{row['id']}"))['code'], 404)

    async def test_all_new_endpoints_require_login(self):
        for resource in ("assignments", "plan-tasks"):
            for method, suffix, payload in (("GET","",None), ("POST","",{"title":"测试"}),
                                            ("PUT","/1",{}), ("DELETE","/1",None)):
                result = await self.call(method, f"/api/{resource}{suffix}", user=None, payload=payload)
                self.assertEqual(result['code'], 401)
        self.assertEqual((await self.call("POST", "/api/plan-tasks/1/defer", user=None))['code'], 401)

    async def test_assignment_pagination_and_combined_filters(self):
        await self.create(type="exam", status="done", course_id=self.courses[1])
        for day in (12,13,14):
            await self.create(due_at=f"2026-10-{day}T18:00:00")
        await self.create(user="bbb")
        result = await self.call("GET", f"/api/assignments?status=pending&type=assignment&course_id={self.courses[0]}&page=2&page_size=2")
        self.assertEqual(result['data']['total'], 3)
        self.assertEqual(len(result['data']['list']), 1)
        self.assertEqual(result['data']['list'][0]['due_at'], "2026-10-14T18:00:00")

    async def test_days_filter_excludes_overdue_includes_exact_boundary(self):
        for hours in (-1,0,48,49):
            await self.create(due_at=(NOW+timedelta(hours=hours)).isoformat())
        with patch("app.services.assignment_service.local_now", return_value=NOW):
            result = await self.call("GET", "/api/assignments?days=2")
        self.assertEqual(result['data']['total'], 2)

    async def test_task_pagination_status_filter(self):
        await self.create("plan-tasks", status="done")
        for title in ("一","二","三"):
            await self.create("plan-tasks", title=title)
        result = await self.call("GET", "/api/plan-tasks?status=pending&page=2&page_size=2")
        self.assertEqual((result['data']['total'], len(result['data']['list'])), (3,1))
        self.assertEqual(result['data']['list'][0]['title'], "一")

    async def test_invalid_filters_and_pagination_are_business_400(self):
        for resource in ("assignments", "plan-tasks"):
            for query in ("status=bad", "page=0", "page=abc", "page_size=101", "page_size=-1"):
                result = await self.call("GET", f"/api/{resource}?{query}")
                self.assertEqual(result['code'], 400)
        for query in ("days=0", "days=-1", "days=abc", "type=bad", "course_id=abc"):
            self.assertEqual((await self.call("GET", f"/api/assignments?{query}"))['code'], 400)
        self.assertEqual((await self.call("GET", "/api/assignments?course_id=99999"))['code'],404)

    async def test_defer_default_and_explicit_days_accumulate(self):
        row = await self.create("plan-tasks", due_at=NOW.isoformat())
        first = await self.call("POST", f"/api/plan-tasks/{row['id']}/defer")
        self.assertEqual((first['data']['due_at'], first['data']['defer_count']), ((NOW+timedelta(days=1)).isoformat(),1))
        second = await self.call("POST", f"/api/plan-tasks/{row['id']}/defer", payload={"days":30})
        self.assertEqual((second['data']['due_at'], second['data']['defer_count']), ((NOW+timedelta(days=31)).isoformat(),2))

    async def test_defer_without_deadline_starts_from_local_now(self):
        row = await self.create("plan-tasks")
        with patch("app.services.plan_task_service.local_now", return_value=NOW):
            result = await self.call("POST", f"/api/plan-tasks/{row['id']}/defer", payload={})
        self.assertEqual(result['data']['due_at'], (NOW+timedelta(days=1)).isoformat())

    async def test_defer_invalid_days_and_overflow_do_not_change_row(self):
        row = await self.create("plan-tasks", due_at="9999-12-31T18:00:00")
        for days in (0,31,-1,True,1.5,None,"bad"):
            result = await self.call("POST", f"/api/plan-tasks/{row['id']}/defer", payload={"days":days})
            self.assertEqual(result['code'],400)
        self.assertEqual((await self.call("POST", f"/api/plan-tasks/{row['id']}/defer"))['code'],400)
        with self.sessions() as db:
            self.assertEqual(db.get(PlanTask,row['id']).defer_count,0)

    async def test_defer_counter_uses_atomic_sql_expression_and_owner_where_clause(self):
        row = await self.create("plan-tasks", due_at=NOW.isoformat())
        statements = []
        def capture(connection, cursor, statement, params, context, multiple):
            if statement.lstrip().upper().startswith("UPDATE PLAN_TASKS"):
                statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", capture)
        result = await self.call("POST", f"/api/plan-tasks/{row['id']}/defer")
        self.assertEqual(result['code'],200)
        self.assertEqual(len(statements),1)
        self.assertIn("defer_count=(plan_tasks.defer_count +", statements[0])
        self.assertIn("plan_tasks.user_id =", statements[0])

    async def test_course_assignment_reference_returns_409_and_preserves_rows(self):
        await self.create()
        result = await self.call("DELETE", f"/api/course/{self.courses[0]}", user="admin")
        self.assertEqual(result['code'],409)
        self.assertIn("1 条作业/考试",result['message'])
        with self.sessions() as db:
            self.assertIsNotNone(db.get(Course,self.courses[0]))
            self.assertEqual(db.query(Assignment).count(),1)

    async def test_course_task_reference_returns_409_and_preserves_rows(self):
        await self.create("plan-tasks", course_id=self.courses[0])
        result = await self.call("DELETE", f"/api/course/{self.courses[0]}", user="admin")
        self.assertEqual(result['code'],409)
        self.assertIn("计划任务",result['message'])

    async def test_course_with_both_new_references_lists_both_blockers(self):
        await self.create()
        await self.create("plan-tasks", course_id=self.courses[0])
        result = await self.call("DELETE", f"/api/course/{self.courses[0]}", user="admin")
        self.assertEqual(result['code'],409)
        self.assertIn("作业/考试", result['message'])
        self.assertIn("计划任务", result['message'])
        with self.sessions() as db:
            db.add(Schedule(user_id=self.users['aaa'], course_id=self.courses[0], class_date=NOW.date()))
            db.commit()
        result = await self.call("DELETE", f"/api/course/{self.courses[0]}", user="admin")
        self.assertEqual(result['code'],409)
        for label in ("课表安排", "作业/考试", "计划任务"):
            self.assertIn(label,result['message'])

    async def test_user_assignment_reference_returns_409(self):
        await self.create()
        result = await self.call("DELETE", f"/api/user/{self.users['aaa']}", user="admin")
        self.assertEqual(result['code'],409)
        self.assertIn("作业/考试 1 条", result['message'])

    async def test_user_task_reference_returns_409(self):
        await self.create("plan-tasks")
        result = await self.call("DELETE", f"/api/user/{self.users['aaa']}", user="admin")
        self.assertEqual(result['code'],409)
        self.assertIn("计划任务 1 条", result['message'])
        with self.sessions() as db:
            self.assertIsNotNone(db.get(User,self.users['aaa']))

    async def test_new_dependencies_can_be_cleared_then_parent_deleted(self):
        assignment = await self.create()
        task = await self.create("plan-tasks", course_id=self.courses[0])
        await self.call("DELETE", f"/api/assignments/{assignment['id']}")
        await self.call("DELETE", f"/api/plan-tasks/{task['id']}")
        self.assertEqual((await self.call("DELETE", f"/api/course/{self.courses[0]}", user="admin"))['code'],200)
        self.assertEqual((await self.call("DELETE", f"/api/user/{self.users['aaa']}", user="admin"))['code'],200)

    async def test_text_study_plans_work_independently_of_tasks(self):
        task = await self.create("plan-tasks")
        result = await self.call("POST", "/api/study-plans", payload={"content":"原有文本计划"})
        self.assertEqual(result['code'],200)
        self.assertEqual((await self.call("GET", "/api/study-plans"))['data']['total'],1)
        await self.call("DELETE", f"/api/plan-tasks/{task['id']}")
        with self.sessions() as db:
            self.assertEqual(db.query(StudyPlan).one().content,"原有文本计划")

    async def test_database_failure_for_all_new_mutations_is_sanitized_and_rolls_back(self):
        assignment = await self.create()
        task = await self.create("plan-tasks")
        for method, path, payload in (
            ("POST","/api/assignments",{"title":"失败","type":"exam","course_id":self.courses[0],"due_at":NOW.isoformat()}),
            ("PUT",f"/api/assignments/{assignment['id']}",{"title":"失败"}),
            ("DELETE",f"/api/assignments/{assignment['id']}",None),
            ("POST","/api/plan-tasks",{"title":"失败"}),
            ("PUT",f"/api/plan-tasks/{task['id']}",{"title":"失败"}),
            ("DELETE",f"/api/plan-tasks/{task['id']}",None),
            ("POST",f"/api/plan-tasks/{task['id']}/defer",None),
        ):
            error = OperationalError("PRIVATE-SQL",{}, RuntimeError("PRIVATE-CONNECTION"))
            with patch.object(Session,"commit",side_effect=error):
                result = await self.call(method,path,payload=payload)
            self.assertEqual(result['code'],503,result)
            self.assertNotIn("PRIVATE",result['message'])
        with self.sessions() as db:
            self.assertEqual(db.query(Assignment).count(),1)
            self.assertEqual(db.query(PlanTask).count(),1)
            self.assertEqual(db.get(PlanTask,task['id']).defer_count,0)

    async def test_fk_race_is_business_409_and_no_success(self):
        row = await self.create()
        error = IntegrityError("PRIVATE",{}, RuntimeError("PRIVATE"))
        with patch.object(Session,"commit",side_effect=error):
            result = await self.call("DELETE", f"/api/assignments/{row['id']}")
        self.assertEqual(result['code'],409)
        self.assertNotIn("PRIVATE",result['message'])


if __name__ == "__main__":
    unittest.main()
