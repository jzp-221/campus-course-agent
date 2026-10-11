"""P5 mutation failures: real foreign keys, safe SQL errors and document compensation."""

from datetime import date
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import uuid

from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from pydantic_settings import BaseSettings
from sqlalchemy import create_engine, delete, event
from sqlalchemy.exc import IntegrityError, OperationalError
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
    from app.api.schedule import router as schedule_router
    from app.api.study_plan import router as plan_router
    from app.common.exceptions import BusinessException, business_exception_handler
    from app.database import Base, get_db
    from app.dependencies.auth import get_current_admin, get_current_user
    from app.models.coures import Course
    from app.models.document import Document
    from app.models.schedule import Schedule
    from app.models.study_plan import StudyPlan
    from app.models.user import User
    from app.schemas.schedule import ScheduleCreateRequest, ScheduleUpdateRequest
    from app.schemas.study_plan import StudyPlanCreateRequest
    from app.services import document_service, rag_vector_service, schedule_service, study_plan_service


def database_error(error_type=OperationalError):
    return error_type(
        "PRIVATE SQL FROM schedules documents", {"password": "PRIVATE PASSWORD"},
        Exception("PRIVATE CONNECTION mysql://internal-host/private-db"),
    )


class MutationErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "documents"
        self.directory.mkdir()
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        with self.sessions() as db:
            admin = User(username="admin", password="offline", name="管理员", role="admin")
            aaa = User(username="aaa", password="offline", name="学生甲", role="student")
            bbb = User(username="bbb", password="offline", name="学生乙", role="student")
            course = Course(name="测试课程", code="OFFLINE-P5")
            db.add_all([admin, aaa, bbb, course])
            db.flush()
            self.admin_id, self.aaa_id, self.bbb_id, self.course_id = admin.id, aaa.id, bbb.id, course.id
            schedule = Schedule(
                user_id=aaa.id, course_id=course.id, class_date=date(2026, 10, 10),
                period="1-2节", location="A101",
            )
            plan = StudyPlan(user_id=aaa.id, content="已有计划")
            db.add_all([schedule, plan])
            db.commit()
            self.schedule_id, self.plan_id = schedule.id, plan.id

        self.vectors = {}
        for modification in (
            patch.object(document_service, "DOCUMENT_DIR", self.directory),
            patch.object(rag_vector_service, "CHROMA_DIR", Path(self.temp.name) / "vectors"),
            patch.object(document_service.rag_service, "index_document", self.index),
            patch.object(document_service.rag_service, "delete_document", self.remove_vectors),
        ):
            modification.start()
            self.addCleanup(modification.stop)

    def index(self, document_id, filename, chunks):
        self.vectors[document_id] = chunks

    def remove_vectors(self, document_id):
        self.vectors.pop(document_id, None)

    def assert_business(self, operation, code, text=None):
        with self.assertRaises(BusinessException) as raised:
            operation()
        error = raised.exception
        self.assertEqual(error.code, code)
        if text:
            self.assertIn(text, error.message)
        for private in ("PRIVATE", "schedules", "documents", "mysql://", "internal-host", "password"):
            self.assertNotIn(private, error.message)
        return error

    def make_document(self):
        path = self.directory / f"{uuid.uuid4().hex}.txt"
        path.write_text("课程参考资料", encoding="utf-8")
        with self.sessions() as db:
            row = Document(
                filename="资料.txt", storage_path=str(path), extracted_text="课程参考资料",
                uploader_id=self.admin_id, status="indexed", chunk_count=1,
            )
            db.add(row)
            db.commit()
            document_id = row.id
        self.vectors[document_id] = ["旧分块"]
        return document_id, path

    def test_schedule_missing_update_and_delete_are_404(self):
        for operation in (
            lambda db: schedule_service.update_schedule(db, 9999, ScheduleUpdateRequest(location="新教室")),
            lambda db: schedule_service.delete_schedule(db, 9999),
        ):
            with self.subTest(operation=operation), self.sessions() as db:
                self.assert_business(lambda: operation(db), 404, "课表不存在")

    def test_schedule_invalid_references_have_domain_codes(self):
        for fields, code, message in (
            ({"user_id": 9999}, 404, "用户不存在"),
            ({"course_id": 9999}, 404, "课程不存在"),
            ({"user_id": self.admin_id}, 400, "只能给学生安排课表"),
        ):
            with self.subTest(fields=fields), self.sessions() as db:
                self.assert_business(
                    lambda: schedule_service.update_schedule(db, self.schedule_id, ScheduleUpdateRequest(**fields)),
                    code, message,
                )
        with self.sessions() as db:
            self.assertEqual(db.get(Schedule, self.schedule_id).user_id, self.aaa_id)

    def test_schedule_foreign_key_race_is_409_and_transaction_is_rolled_back(self):
        with self.sessions() as db:
            # Reference validation succeeds; the parent disappears before the INSERT.
            def remove_course_before_flush(session, _, __):
                session.execute(delete(Course).where(Course.id == race_course_id))

            race_course = Course(name="并发测试课程", code="RACE-COURSE")
            db.add(race_course)
            db.commit()
            race_course_id = race_course.id
            event.listen(db, "before_flush", remove_course_before_flush, once=True)
            data = ScheduleCreateRequest(
                user_id=self.aaa_id, course_id=race_course_id, class_date=date(2026, 10, 10),
                period="3-4节", location="A102",
            )
            self.assert_business(lambda: schedule_service.create_schedule(db, data), 409, "关联")
            self.assertIsNotNone(db.get(Course, race_course_id))
            self.assertEqual(db.query(Schedule).count(), 1)

    def test_schedule_update_and_delete_integrity_failure_keep_records(self):
        for operation in (
            lambda db: schedule_service.update_schedule(db, self.schedule_id, ScheduleUpdateRequest(location="新教室")),
            lambda db: schedule_service.delete_schedule(db, self.schedule_id),
        ):
            with self.subTest(operation=operation), self.sessions() as db:
                with patch.object(db, "commit", side_effect=database_error(IntegrityError)):
                    self.assert_business(lambda: operation(db), 409, "关联")
                row = db.get(Schedule, self.schedule_id)
                self.assertIsNotNone(row)
                self.assertEqual(row.location, "A101")

    def test_schedule_connection_failure_during_lookup_is_503(self):
        with self.sessions() as db, patch.object(db, "query", side_effect=database_error()):
            self.assert_business(lambda: schedule_service.delete_schedule(db, self.schedule_id), 503, "数据库暂时不可用")

    def test_schedule_connection_failure_during_commit_is_503(self):
        with self.sessions() as db:
            with patch.object(db, "commit", side_effect=database_error()):
                self.assert_business(
                    lambda: schedule_service.update_schedule(db, self.schedule_id, ScheduleUpdateRequest(location="新教室")),
                    503, "操作结果未确认",
                )
            self.assertEqual(db.get(Schedule, self.schedule_id).location, "A101")

    def test_plan_missing_or_another_owner_delete_is_404(self):
        with self.sessions() as db:
            bbb = db.get(User, self.bbb_id)
            for plan_id in (9999, self.plan_id):
                with self.subTest(plan_id=plan_id):
                    self.assert_business(lambda: study_plan_service.delete_study_plan(db, bbb, plan_id), 404, "学习计划不存在")
            self.assertIsNotNone(db.get(StudyPlan, self.plan_id))

    def test_plan_foreign_key_failure_is_409_without_inserting(self):
        with self.sessions() as db:
            deleted_user = User(id=9999, username="deleted", password="offline", role="student", name="已删除")
            self.assert_business(
                lambda: study_plan_service.save_plan(db, deleted_user, StudyPlanCreateRequest(content="不能保存")),
                409, "关联",
            )
            self.assertEqual(db.query(StudyPlan).count(), 1)

    def test_plan_delete_connection_failure_is_503_and_keeps_record(self):
        with self.sessions() as db:
            aaa = db.get(User, self.aaa_id)
            with patch.object(db, "commit", side_effect=database_error()):
                self.assert_business(lambda: study_plan_service.delete_study_plan(db, aaa, self.plan_id), 503)
            self.assertIsNotNone(db.get(StudyPlan, self.plan_id))

    def test_document_upload_sql_constraint_failure_cleans_every_store(self):
        with self.sessions() as db:
            admin = db.get(User, self.admin_id)
            with patch.object(db, "commit", side_effect=database_error(IntegrityError)):
                error = self.assert_business(
                    lambda: document_service.upload_documents(db, admin, [UploadFile(filename="资料.txt", file=BytesIO(b"valid text"))]),
                    409, "整批已回滚",
                )
            self.assertIn("资料.txt", error.message)
            self.assertEqual(db.query(Document).count(), 0)
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertEqual(self.vectors, {})

    def test_document_upload_sql_connection_failure_does_not_claim_confirmed_rollback(self):
        with self.sessions() as db:
            admin = db.get(User, self.admin_id)
            with patch.object(db, "commit", side_effect=database_error()):
                error = self.assert_business(
                    lambda: document_service.upload_documents(db, admin, [UploadFile(filename="资料.txt", file=BytesIO(b"valid text"))]),
                    503, "回查数据库记录",
                )
            self.assertNotIn("整批已回滚", error.message)
            self.assertEqual(db.query(Document).count(), 0)
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertEqual(self.vectors, {})

    def test_document_upload_rollback_failure_still_compensates_files_and_vectors(self):
        with self.sessions() as db:
            admin = db.get(User, self.admin_id)
            with patch.object(db, "commit", side_effect=database_error(IntegrityError)), patch.object(
                db, "rollback", side_effect=database_error(),
            ):
                error = self.assert_business(
                    lambda: document_service.upload_documents(db, admin, [UploadFile(filename="资料.txt", file=BytesIO(b"valid text"))]),
                    409, "数据库回滚未确认",
                )
            self.assertNotIn("整批已回滚", error.message)
            self.assertIn("补偿清理未完成", error.message)
            db.rollback()
            self.assertEqual(db.query(Document).count(), 0)
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertEqual(self.vectors, {})

    def test_document_reindex_and_delete_sql_lookup_failure_is_503(self):
        document_id, path = self.make_document()
        for operation in (document_service.reindex_document, document_service.delete_document):
            with self.subTest(operation=operation), self.sessions() as db:
                with patch.object(db, "get", side_effect=database_error()):
                    self.assert_business(lambda: operation(db, document_id), 503)
            self.assertTrue(path.exists())
            self.assertIn(document_id, self.vectors)

    def test_document_reindex_compensation_commit_failure_is_503(self):
        document_id, path = self.make_document()
        with self.sessions() as db:
            # First SQL commit succeeds. Index fails; even persisting failed status loses DB.
            real_commit = db.commit
            count = 0

            def commit():
                nonlocal count
                count += 1
                if count > 1:
                    raise database_error()
                real_commit()

            with patch.object(db, "commit", side_effect=commit), patch.object(
                document_service.rag_service, "index_document", side_effect=BusinessException("入库失败", code=502),
            ):
                self.assert_business(lambda: document_service.reindex_document(db, document_id), 503)
            self.assertIsNotNone(db.get(Document, document_id))
        self.assertTrue(path.exists())
        self.assertNotIn(document_id, self.vectors)

    def test_document_delete_sql_commit_failure_remains_business_error_and_retryable(self):
        document_id, path = self.make_document()
        with self.sessions() as db:
            real_commit = db.commit
            attempts = 0

            def commit():
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise database_error()
                real_commit()

            with patch.object(db, "commit", side_effect=commit):
                self.assert_business(lambda: document_service.delete_document(db, document_id), 503, "记录保留供重试")
            self.assertEqual(db.get(Document, document_id).status, "failed")
            document_service.delete_document(db, document_id)
            self.assertIsNone(db.get(Document, document_id))
        self.assertFalse(path.exists())
        self.assertNotIn(document_id, self.vectors)

    def test_document_delete_compensation_lookup_failure_is_503(self):
        document_id, _ = self.make_document()
        with self.sessions() as db:
            original_get = db.get
            calls = 0

            def get(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls > 1:
                    raise database_error()
                return original_get(*args, **kwargs)

            with patch.object(db, "get", side_effect=get), patch.object(
                document_service.rag_service, "delete_document", side_effect=BusinessException("向量删除失败", code=502),
            ):
                self.assert_business(lambda: document_service.delete_document(db, document_id), 503)

    def test_document_delete_lost_commit_acknowledgement_does_not_turn_into_404_or_success(self):
        document_id, path = self.make_document()
        with self.sessions() as db:
            real_commit = db.commit

            def committed_then_disconnected():
                real_commit()
                raise database_error()

            with patch.object(db, "commit", side_effect=committed_then_disconnected):
                self.assert_business(
                    lambda: document_service.delete_document(db, document_id), 503, "删除结果未确认",
                )
            self.assertIsNone(db.get(Document, document_id))
        self.assertFalse(path.exists())
        self.assertNotIn(document_id, self.vectors)

    def test_http_mutations_return_business_codes_instead_of_sql_http_500(self):
        app = FastAPI()
        app.include_router(schedule_router, prefix="/api")
        app.include_router(plan_router, prefix="/api")
        app.add_exception_handler(BusinessException, business_exception_handler)
        app.dependency_overrides[get_current_admin] = lambda: User(id=self.admin_id, role="admin")
        app.dependency_overrides[get_current_user] = lambda: User(id=self.aaa_id, role="student")

        def offline_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = offline_db
        with TestClient(app) as client:
            for method, url, json in (
                ("PUT", f"/api/schedules/{self.schedule_id}", {"location": "修改失败"}),
                ("DELETE", f"/api/schedules/{self.schedule_id}", None),
                ("DELETE", f"/api/study-plans/{self.plan_id}", None),
            ):
                with self.subTest(method=method, url=url), patch.object(
                    Session, "commit", side_effect=database_error(),
                ):
                    response = client.request(method, url, json=json)
                    self.assertEqual(response.status_code, 200)
                    result = response.json()
                    self.assertEqual(result["code"], 503)
                    self.assertIn("数据库暂时不可用", result["message"])
                    self.assertNotIn("PRIVATE", result["message"])
                    self.assertIsNone(result["data"])


if __name__ == "__main__":
    unittest.main()
