"""Public-demo limits only; the original FastAPI entry point is unchanged.

Quota storage is separate from the business database and survives API restarts.
Run exactly one API worker: in-flight admission is local to that worker.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo


class DemoRejection(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


@dataclass(frozen=True)
class Identity:
    user_id: int
    role: str


@dataclass(frozen=True)
class Limits:
    ai_per_minute: int = 3
    ai_per_user_day: int = 20
    ai_global_day: int = 60
    ai_concurrency: int = 2
    ai_timeout: float = 90
    max_output_tokens: int = 700
    body_bytes: int = 32768
    upload_bytes: int = 2 * 1024 * 1024
    message_chars: int = 1000
    history_chars: int = 6000
    quota_path: str = "/app/demo_state/quota.sqlite3"

    @classmethod
    def from_environment(cls):
        fields = {
            "ai_per_minute": "DEMO_AI_PER_MINUTE",
            "ai_per_user_day": "DEMO_AI_PER_USER_DAY",
            "ai_global_day": "DEMO_AI_GLOBAL_DAY",
            "ai_concurrency": "DEMO_AI_CONCURRENCY",
            "max_output_tokens": "DEMO_MAX_OUTPUT_TOKENS",
        }
        defaults = cls()
        values = {field: int(os.environ.get(env, getattr(defaults, field)))
                  for field, env in fields.items()}
        values["ai_timeout"] = float(os.environ.get("DEMO_AI_TIMEOUT_SECONDS", 90))
        values["quota_path"] = os.environ.get("DEMO_QUOTA_PATH", defaults.quota_path)
        if any(value <= 0 for field, value in values.items() if field != "quota_path"):
            raise ValueError("Public demo limits must be positive")
        return cls(**values)


class QuotaStore:
    """Atomic, durable admissions; failed paid requests still consume an allowance."""

    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = str(path), clock
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS counters (
                category TEXT NOT NULL, subject TEXT NOT NULL, bucket TEXT NOT NULL,
                used INTEGER NOT NULL, expires_at REAL NOT NULL,
                PRIMARY KEY (category, subject, bucket))""")

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def consume(self, budgets):
        now = self.clock()
        minute = str(int(now // 60))
        day = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM counters WHERE expires_at < ?", (now,))
            entries = []
            for category, subject, period, maximum, message in budgets:
                bucket = minute if period == "minute" else day
                key = (category, str(subject), bucket)
                row = connection.execute(
                    "SELECT used FROM counters WHERE category=? AND subject=? AND bucket=?", key
                ).fetchone()
                if row and row[0] >= maximum:
                    raise DemoRejection(429, message)
                entries.append(key)
            for entry in entries:
                connection.execute("""INSERT INTO counters VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(category, subject, bucket)
                    DO UPDATE SET used = used + 1""", (*entry, now + 172800))


def capped_factory(factory, maximum):
    def build(config):
        model = factory(config)
        model.max_tokens = maximum
        return model
    return build


def capped_stream_factory(factory, maximum):
    async def build(config):
        model = await factory(config)
        model.max_tokens = maximum
        return model
    return build


async def json_response(send, code, message, *, data=None):
    payload = json.dumps({"code": code, "message": message, "data": data},
                         ensure_ascii=False).encode()
    headers = [(b"content-type", b"application/json; charset=utf-8"),
               (b"cache-control", b"no-store"), (b"content-length", str(len(payload)).encode())]
    if code == 429:
        headers.append((b"retry-after", b"60"))
    await send({"type": "http.response.start", "status": code, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


def is_ai_path(path):
    return path in {"/api/agent/chat", "/api/agent/chat/stream"} or (
        path.startswith("/api/learning-context/courses/") and path.endswith("/docs"))


def is_embedding_write(path, method):
    return method == "POST" and (path == "/api/documents/upload" or (
        path.startswith("/api/documents/") and path.endswith("/reindex")))


async def bounded_body(receive, maximum):
    data = bytearray()
    while True:
        event = await receive()
        if event["type"] == "http.disconnect":
            raise asyncio.CancelledError()
        if event["type"] != "http.request":
            continue
        data.extend(event.get("body", b""))
        if len(data) > maximum:
            raise DemoRejection(413, "演示请求过大，请缩短内容或减少上传文件")
        if not event.get("more_body", False):
            return bytes(data)


def validate_chat_body(body, limits):
    try:
        request = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise DemoRejection(400, "请求必须是合法 JSON") from None
    if not isinstance(request, dict):
        raise DemoRejection(400, "请求必须是 JSON 对象")
    message, history = request.get("message"), request.get("history", [])
    if not isinstance(message, str) or not message.strip() or len(message) > limits.message_chars:
        raise DemoRejection(400, f"演示提问须为1～{limits.message_chars}字")
    if not isinstance(history, list) or len(history) > 10:
        raise DemoRejection(400, "演示历史最多10条，请新建会话后重试")
    length = 0
    for item in history:
        if (not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}
                or not isinstance(item.get("content"), str)):
            raise DemoRejection(400, "历史仅允许用户与助手的问答文字")
        length += len(item["content"])
    if length > limits.history_chars:
        raise DemoRejection(400, f"演示历史最多{limits.history_chars}字，请新建会话后重试")


class PublicDemoApp:
    """Pure ASGI wrapper, preserving SSE/disconnect handling of the original app."""

    def __init__(self, app, *, authenticate, limits=None, store=None):
        self.app, self.authenticate = app, authenticate
        self.limits = limits or Limits.from_environment()
        self.store = store or QuotaStore(self.limits.quota_path)
        self.active_ai = 0

    async def consume(self, budgets):
        try:
            await asyncio.to_thread(self.store.consume, budgets)
        except DemoRejection:
            raise
        except (OSError, sqlite3.Error):
            # An inaccessible quota file must not silently disable cost limits.
            raise DemoRejection(503, "演示额度服务暂不可用，请稍后重试") from None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "").rstrip("/")
        if path == "/api/demo/health" and scope["method"] == "GET":
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"status":"ok","environment":"demo"}'})
            return
        if not path.startswith("/api/"):
            return await self.app(scope, receive, send)
        entered = False
        started, ended = False, False
        response_type = b""

        async def tracked_send(event):
            nonlocal started, ended, response_type
            if event["type"] == "http.response.start":
                started = True
                response_type = dict(event.get("headers", [])).get(b"content-type", b"")
            if event["type"] == "http.response.body" and not event.get("more_body", False):
                ended = True
            await send(event)

        try:
            if path == "/api/auth/register":
                raise DemoRejection(403, "公网演示不开放注册，请使用演示学生账号")
            headers = dict(scope.get("headers", []))
            peer = (scope.get("client") or ("unknown", 0))[0]
            peer_key = hashlib.sha256(str(peer).encode()).hexdigest()
            await self.consume([("requests", peer_key, "minute", 120, "演示访问过于频繁，请稍后重试")])
            identity = None
            if path in {"/api/auth/login", "/api/auth/token"}:
                await self.consume([("login", peer_key, "minute", 10, "登录尝试过于频繁，请稍后重试")])
            else:
                authorization = headers.get(b"authorization", b"").decode("latin-1")
                identity = await self.authenticate(authorization)
                if identity.role != "admin":
                    if (path.startswith("/api/user") and scope["method"] not in {"GET", "HEAD", "OPTIONS"}
                            or path == "/api/files/upload"):
                        raise DemoRejection(403, "演示学生账号不允许修改账号信息或上传通用文件")

            mutating = scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}
            paid = is_ai_path(path) or is_embedding_write(path, scope["method"])
            if mutating:
                maximum = self.limits.upload_bytes if path in {
                    "/api/files/upload", "/api/documents/upload"} else self.limits.body_bytes
                length = headers.get(b"content-length")
                if length is not None and (not length.isdigit() or int(length) > maximum):
                    raise DemoRejection(413, "演示请求过大，请缩短内容或减少上传文件")
                body = await asyncio.wait_for(bounded_body(receive, maximum), timeout=15)
                if path in {"/api/agent/chat", "/api/agent/chat/stream"}:
                    validate_chat_body(body, self.limits)
                replayed = False
                original_receive = receive

                async def replay_receive():
                    nonlocal replayed
                    if not replayed:
                        replayed = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await original_receive()

                receive = replay_receive
                if identity and not paid:
                    await self.consume([
                        ("writes", identity.user_id, "minute", 20, "演示操作过于频繁，请稍后重试"),
                        ("writes", identity.user_id, "day", 100, "该演示账号今日操作额度已用完"),
                    ])
            if paid:
                if self.active_ai >= self.limits.ai_concurrency:
                    raise DemoRejection(429, "演示 AI 正忙，请稍后重试")
                self.active_ai += 1
                entered = True
                await self.consume([
                    ("ai", identity.user_id, "minute", self.limits.ai_per_minute, "AI 提问过于频繁，请稍后重试"),
                    ("ai-user", identity.user_id, "day", self.limits.ai_per_user_day, "该演示账号今日 AI 额度已用完"),
                    ("ai-global", "all", "day", self.limits.ai_global_day, "公网演示今日 AI 总额度已用完"),
                ])
                await asyncio.wait_for(self.app(scope, receive, tracked_send), timeout=self.limits.ai_timeout)
            else:
                await self.app(scope, receive, tracked_send)
        except DemoRejection as error:
            if not started:
                await json_response(send, error.code, error.message)
        except asyncio.TimeoutError:
            if not started:
                await json_response(send, 504, "演示请求超时，请稍后重试")
            elif not ended and b"text/event-stream" in response_type:
                payload = json.dumps({"code": 504, "message": "演示 AI 请求超时，请稍后重试"}, ensure_ascii=False)
                await send({"type": "http.response.body",
                            "body": f"event: error\ndata: {payload}\n\n".encode(), "more_body": False})
        except Exception:
            # Starlette can re-raise after returning its sanitized 500 response.
            # Suppress the raw traceback in this public wrapper, never claim success.
            if not started:
                await json_response(send, 500, "演示服务暂不可用，请稍后重试")
            elif not ended and b"text/event-stream" in response_type:
                payload = json.dumps({"code": 500, "message": "演示服务暂不可用，请稍后重试"}, ensure_ascii=False)
                await send({"type": "http.response.body",
                            "body": f"event: error\ndata: {payload}\n\n".encode(), "more_body": False})
        finally:
            if entered:
                self.active_ai -= 1
