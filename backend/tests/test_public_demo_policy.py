"""Offline deployment guard tests; no original app, local database or provider keys."""

import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

SCAFFOLD = Path(__file__).resolve().parents[2]
if str(SCAFFOLD) not in sys.path:
    sys.path.insert(0, str(SCAFFOLD))
from deploy.demo.configure import generate_config
from deploy.demo.policy import (
    DemoRejection, Identity, Limits, PublicDemoApp, QuotaStore,
    capped_factory, capped_stream_factory, validate_chat_body,
)


class DemoQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "quota.sqlite3"
        self.now = datetime(2026, 10, 11, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        self.store = QuotaStore(self.path, clock=lambda: self.now)

    def test_quota_survives_store_restart(self):
        budget = [("ai", 2, "day", 1, "limit")]
        self.store.consume(budget)
        with self.assertRaises(DemoRejection):
            QuotaStore(self.path, clock=lambda: self.now).consume(budget)

    def test_daily_quota_resets_at_shanghai_midnight(self):
        budget = [("ai", 2, "day", 1, "limit")]
        self.store.consume(budget)
        self.now += 120
        self.store.consume(budget)

    def test_global_daily_quota_cannot_be_bypassed_by_switching_users(self):
        self.store.consume([("user", 2, "day", 10, "user"), ("global", "all", "day", 1, "global")])
        with self.assertRaises(DemoRejection) as result:
            self.store.consume([("user", 3, "day", 10, "user"), ("global", "all", "day", 1, "global")])
        self.assertEqual(result.exception.message, "global")
        with self.store.connect() as connection:
            self.assertIsNone(connection.execute("SELECT used FROM counters WHERE subject='3'").fetchone())

    def test_minute_and_daily_budgets_are_independent(self):
        self.now -= 3600  # Keep these three minute buckets within one Shanghai day.
        budget = [("minute", 2, "minute", 1, "minute"), ("daily", 2, "day", 2, "day")]
        self.store.consume(budget)
        with self.assertRaises(DemoRejection):
            self.store.consume(budget)
        self.now += 61
        self.store.consume(budget)
        self.now += 61
        with self.assertRaises(DemoRejection) as result:
            self.store.consume(budget)
        self.assertEqual(result.exception.message, "day")


class DemoConfigurationTests(unittest.TestCase):
    def test_generator_only_produces_new_isolated_placeholders_and_strong_distinct_secrets(self):
        values = dict(line.split("=", 1) for line in generate_config("demo.example.com").splitlines()
                      if line and not line.startswith("#"))
        self.assertIn("@db:3306/campus_demo", values["DATABASE_URL"])
        self.assertEqual(values["LLM_API_KEY"], "")
        self.assertEqual(values["EMBEDDING_API_KEY"], "")
        self.assertEqual(values["DEMO_CORS_ORIGINS"], "https://demo.example.com")
        secrets = [values[key] for key in ("DEMO_DB_PASSWORD", "DEMO_DB_ROOT_PASSWORD",
                   "JWT_SECRET_KEY", "DEMO_ADMIN_PASSWORD", "DEMO_STUDENT_PASSWORD")]
        self.assertEqual(len(set(secrets)), len(secrets))
        self.assertTrue(all(len(value) >= 20 for value in secrets))

    def test_config_generator_rejects_env_injection_and_urls_with_secrets(self):
        for site in ("demo.example.com\nKEY=value", "demo.example.com#comment", "http://user:secret@host",
                     "$(whoami)", "demo.example.com/path", "https://demo.example.com?key=value"):
            with self.subTest(site=site), self.assertRaises(ValueError):
                generate_config(site)

    def test_unbounded_or_zero_limits_are_rejected(self):
        with patch.dict("os.environ", {"DEMO_AI_GLOBAL_DAY": "0"}, clear=True), self.assertRaises(ValueError):
            Limits.from_environment()

    def test_chat_message_and_total_history_are_bounded_before_model(self):
        for data in ({"message": "x" * 1001}, {"message": "ok", "history": [{"role": "user", "content": "x" * 6001}]},
                     {"message": "ok", "history": [{"role": "tool", "content": "bad"}]},
                     {"message": "ok", "history": [{"role": "user", "content": "x"}] * 11}):
            with self.subTest(data_type=list(data)), self.assertRaises(DemoRejection):
                validate_chat_body(json.dumps(data).encode(), Limits())


class DemoPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = QuotaStore(Path(self.temporary.name) / "quota.sqlite3")
        self.identity = Identity(2, "student")
        self.authenticate = AsyncMock(side_effect=lambda _: self.identity)
        self.calls = []

        async def downstream(scope, receive, send):
            self.calls.append(scope["path"])
            if scope["method"] == "POST":
                self.body = (await receive())["body"]
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        self.downstream = downstream
        self.wrapper = PublicDemoApp(downstream, authenticate=self.authenticate, store=self.store)

    async def request(self, path="/api/agent/chat", *, method="POST", body=None, headers=None, wrapper=None):
        encoded = json.dumps(body or {"message": "我明天有什么课？"}).encode()
        events = []
        incoming = [{"type": "http.request", "body": encoded, "more_body": False}]

        async def receive():
            if incoming:
                return incoming.pop(0)
            await asyncio.Event().wait()

        async def send(event):
            events.append(event)

        scope = {"type": "http", "method": method, "path": path, "client": ("192.0.2.10", 80),
                 "headers": headers if headers is not None else [(b"authorization", b"Bearer fixture")]}
        await (wrapper or self.wrapper)(scope, receive, send)
        return events

    def status(self, events):
        return next(event["status"] for event in events if event["type"] == "http.response.start")

    async def test_health_has_no_auth_or_database_call(self):
        events = await self.request("/api/demo/health", method="GET")
        self.assertEqual(self.status(events), 200)
        self.assertEqual(json.loads(events[-1]["body"])["status"], "ok")
        self.authenticate.assert_not_awaited()
        self.assertEqual(self.calls, [])

    async def test_invalid_identity_is_rejected_before_paid_app(self):
        self.authenticate.side_effect = DemoRejection(401, "invalid")
        events = await self.request()
        self.assertEqual(self.status(events), 401)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.wrapper.active_ai, 0)

    async def test_registration_is_disabled_including_trailing_slash(self):
        events = await self.request("/api/auth/register/")
        self.assertEqual(self.status(events), 403)
        self.assertEqual(self.calls, [])

    async def test_shared_student_cannot_change_password_profile_or_upload_files(self):
        for path, method in (("/api/user/password", "PUT"), ("/api/user/me", "PUT"),
                             ("/api/files/upload", "POST")):
            with self.subTest(path=path):
                self.assertEqual(self.status(await self.request(path, method=method)), 403)
        self.assertEqual(self.calls, [])

    async def test_student_business_pages_and_admin_profile_keep_original_handlers(self):
        self.assertEqual(self.status(await self.request("/api/assignments")), 200)
        self.identity = Identity(1, "admin")
        self.assertEqual(self.status(await self.request("/api/user/me", method="PUT")), 200)
        self.assertEqual(self.calls, ["/api/assignments", "/api/user/me"])

    async def test_login_and_oauth_share_the_same_minute_budget(self):
        for index in range(10):
            path = "/api/auth/login" if index % 2 else "/api/auth/token"
            self.assertEqual(self.status(await self.request(path)), 200)
        self.assertEqual(self.status(await self.request("/api/auth/login")), 429)
        self.authenticate.assert_not_awaited()

    async def test_learning_context_embedding_is_in_same_paid_daily_budget(self):
        limits = Limits(ai_per_user_day=1)
        wrapper = PublicDemoApp(self.downstream, authenticate=self.authenticate, limits=limits, store=self.store)
        self.assertEqual(self.status(await self.request(wrapper=wrapper)), 200)
        events = await self.request("/api/learning-context/courses/2/docs", method="GET", wrapper=wrapper)
        self.assertEqual(self.status(events), 429)
        self.assertEqual(len(self.calls), 1)

    async def test_admin_embedding_upload_and_reindex_also_consume_paid_allowance(self):
        self.identity = Identity(1, "admin")
        wrapper = PublicDemoApp(self.downstream, authenticate=self.authenticate,
                                limits=Limits(ai_per_user_day=1), store=self.store)
        self.assertEqual(self.status(await self.request("/api/documents/upload", wrapper=wrapper)), 200)
        self.assertEqual(self.status(await self.request("/api/documents/1/reindex", wrapper=wrapper)), 429)
        self.assertEqual(len(self.calls), 1)

    async def test_overlong_message_body_and_history_do_not_call_model(self):
        self.assertEqual(self.status(await self.request(body={"message": "x" * 1001})), 400)
        self.assertEqual(self.status(await self.request(headers=[(b"content-length", b"40000")])), 413)
        self.assertEqual(self.status(await self.request(body={"message": "ok", "history": [
            {"role": "user", "content": "x" * 7000}]})), 400)
        self.assertEqual(self.calls, [])

    async def test_request_body_is_replayed_unchanged_to_original_handler(self):
        data = {"message": "查一下", "history": [{"role": "assistant", "content": "上一轮"}]}
        self.assertEqual(self.status(await self.request(body=data)), 200)
        self.assertEqual(json.loads(self.body), data)

    async def test_quota_storage_error_fails_closed(self):
        with patch.object(self.store, "consume", side_effect=sqlite3.OperationalError("private path")):
            events = await self.request()
        self.assertEqual(self.status(events), 503)
        self.assertNotIn(b"private path", events[-1]["body"])
        self.assertEqual(self.calls, [])

    async def test_concurrency_rejects_excess_then_cancellation_releases_slot(self):
        started, closed = asyncio.Event(), asyncio.Event()

        async def blocked(scope, receive, send):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        wrapper = PublicDemoApp(blocked, authenticate=self.authenticate, limits=Limits(ai_concurrency=1), store=self.store)
        request = asyncio.create_task(self.request(wrapper=wrapper))
        await asyncio.wait_for(started.wait(), 2)
        self.assertEqual(wrapper.active_ai, 1)
        self.assertEqual(self.status(await self.request(wrapper=wrapper)), 429)
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request
        self.assertTrue(closed.is_set())
        self.assertEqual(wrapper.active_ai, 0)

    async def test_sse_timeout_cancels_downstream_and_emits_stable_error_frame(self):
        closed = asyncio.Event()

        async def streaming(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream; charset=utf-8")]})
            await send({"type": "http.response.body", "body": b'event: status\ndata: {"stage":"thinking"}\n\n',
                        "more_body": True})
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        wrapper = PublicDemoApp(streaming, authenticate=self.authenticate, limits=Limits(ai_timeout=0.03), store=self.store)
        events = await self.request("/api/agent/chat/stream", wrapper=wrapper)
        self.assertEqual(self.status(events), 200)
        self.assertIn(b"event: error", events[-1]["body"])
        self.assertIn(b'"code": 504', events[-1]["body"])
        self.assertFalse(events[-1].get("more_body", False))
        self.assertTrue(closed.is_set())
        self.assertEqual(wrapper.active_ai, 0)

    async def test_stream_frames_pass_through_without_batching(self):
        progress = []

        async def streaming(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            for value in range(3):
                await send({"type": "http.response.body", "body": str(value).encode(), "more_body": True})
                progress.append(value)
                await asyncio.sleep(0)
            await send({"type": "http.response.body", "body": b"done"})

        wrapper = PublicDemoApp(streaming, authenticate=self.authenticate, store=self.store)
        events = await self.request("/api/agent/chat/stream", wrapper=wrapper)
        self.assertEqual([event["body"] for event in events if event["type"] == "http.response.body"],
                         [b"0", b"1", b"2", b"done"])
        self.assertEqual(progress, [0, 1, 2])

    async def test_unhandled_error_does_not_expose_connection_or_sql(self):
        async def broken(scope, receive, send):
            raise RuntimeError("SQL SELECT secret FROM private_database")
        wrapper = PublicDemoApp(broken, authenticate=self.authenticate, store=self.store)
        events = await self.request(wrapper=wrapper)
        self.assertEqual(self.status(events), 500)
        self.assertNotIn(b"private_database", events[-1]["body"])
        self.assertEqual(wrapper.active_ai, 0)

    async def test_both_real_model_factories_apply_output_limit(self):
        sync = capped_factory(lambda _: SimpleNamespace(max_tokens=None), 700)
        self.assertEqual(sync(None).max_tokens, 700)
        async_factory = AsyncMock(return_value=SimpleNamespace(max_tokens=None))
        async_model = await capped_stream_factory(async_factory, 700)(None)
        self.assertEqual(async_model.max_tokens, 700)
