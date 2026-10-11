"""真实 HTTP 联调：只启动 MCP，复用 FastAPI，用业务接口造数据及清理。"""

import asyncio
from collections import Counter
from datetime import date, timedelta
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
import jwt
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


BACKEND = Path(__file__).resolve().parents[2]
PYTHON = BACKEND / ".venv/bin/python"
API = "http://127.0.0.1:8000"
MCP = "http://127.0.0.1:8001/mcp"
START_API = f'cd "{BACKEND}"\n.venv/bin/python -m uvicorn app.main:app --reload --port 8000'


class CheckFailed(Exception):
    pass


def check(condition, message):
    if not condition:
        raise CheckFailed(message)


def passed(message):
    print(f"[PASS] {message}", flush=True)


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def request(client, method, path, **kwargs):
    try:
        response = client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        raise CheckFailed(f"{method} {path} 请求失败（{type(exc).__name__}）") from None
    try:
        body = response.json()
    except ValueError:
        raise CheckFailed(f"{method} {path} 未返回 JSON，HTTP {response.status_code}") from None
    check(isinstance(body, dict), f"{method} {path} 返回格式错误")
    return response.status_code, body


def success(client, method, path, **kwargs):
    status, body = request(client, method, path, **kwargs)
    # 不输出完整响应，避免登录响应中的 JWT 进入终端或日志。
    check(status == 200 and body.get("code") == 200,
          f"{method} {path} 未成功，HTTP {status}，业务码 {body.get('code')}")
    return body.get("data")


def login(client, username, expected_role):
    password = os.environ.get(f"E2E_{username.upper()}_PASSWORD",
                              "admin" if username == "admin" else "123")
    data = success(client, "POST", "/api/auth/login",
                   json={"username": username, "password": password})
    check(isinstance(data, dict) and isinstance(data.get("token"), str),
          f"{username} 登录未返回 Token；请检查账号或 E2E_{username.upper()}_PASSWORD")
    user = data.get("user") or {}
    check(user.get("username") == username and user.get("role") == expected_role,
          f"{username} 账号身份或角色不符合要求")
    check(type(user.get("id")) is int, f"{username} 登录未返回用户 ID")
    return data["token"], user["id"]


def schedules(client, token, user_id=None):
    rows = []
    page = 1
    while True:
        params = {"page": page, "page_size": 100}
        if user_id is not None:
            params["user_id"] = user_id
        data = success(client, "GET", "/api/schedules", headers=headers(token), params=params)
        check(isinstance(data, dict) and isinstance(data.get("list"), list)
              and type(data.get("total")) is int, "课表分页返回格式错误")
        rows.extend(data["list"])
        if len(rows) >= data["total"]:
            return rows
        check(data["list"], "课表分页未返回剩余数据")
        page += 1


def mcp_items(client, token, params=None):
    data = success(client, "GET", "/api/mcp/my-schedule", headers=headers(token), params=params)
    check(isinstance(data, dict) and isinstance(data.get("items"), list)
          and type(data.get("count")) is int and data["count"] == len(data["items"]),
          "MCP 返回的 items/count 不一致")
    return data["items"]


def signature(row):
    return (row.get("date", row.get("class_date")), row.get("period"),
            row.get("course_id"), row.get("course_name"), row.get("location"))


async def probe_mcp():
    # MCP initialize + tools/list；这是真实 Streamable HTTP，不调用 Python service。
    async with Client(StreamableHttpTransport(MCP)) as client:
        tools = await client.list_tools()
        check(any(tool.name == "get_my_schedules" for tool in tools),
              "8001 MCP 未提供 get_my_schedules 工具")


def start_mcp(log):
    with socket.socket() as sock:
        sock.settimeout(1)
        check(sock.connect_ex(("127.0.0.1", 8001)) != 0,
              "8001 已被占用，请先自行关闭已有 MCP 后再运行；脚本不会停止其他进程")
    check(PYTHON.is_file(), f"缺少 {PYTHON}，请先安装项目虚拟环境依赖")
    env = os.environ.copy()
    env.update(MCP_HOST="127.0.0.1", MCP_PORT="8001", MCP_PATH="/mcp",
               PYTHONUNBUFFERED="1")
    return subprocess.Popen([str(PYTHON), "mcp_server.py"], cwd=BACKEND, env=env,
                            stdout=log, stderr=log, start_new_session=True)


def wait_mcp(process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        check(process.poll() is None, "脚本启动的 MCP 提前退出，请检查依赖和监听配置")
        try:
            asyncio.run(asyncio.wait_for(probe_mcp(), timeout=2))
            check(process.poll() is None, "脚本启动的 MCP 已退出")
            return
        except CheckFailed:
            raise
        except Exception:
            time.sleep(0.2)
    raise CheckFailed("MCP 在 30 秒内未完成 Streamable HTTP 握手")


def stop_mcp(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
    with socket.socket() as sock:
        sock.settimeout(1)
        check(sock.connect_ex(("127.0.0.1", 8001)) != 0,
              "MCP 进程退出后 8001 仍被占用，请检查是否有其他进程启动")
    passed("脚本启动的 MCP 已关闭，8001 已释放")


class Fixture:
    def __init__(self, client, admin_token, students):
        self.client = client
        self.admin_token = admin_token
        self.students = students
        self.marker = "E2E07-" + uuid.uuid4().hex
        self.course_id = None
        self.schedule_ids = set()
        self.attempted = False

    def create(self, first_date):
        self.attempted = True
        course = success(self.client, "POST", "/api/course", headers=headers(self.admin_token),
                         json={"name": self.marker, "code": self.marker,
                               "description": "第07节临时 HTTP 联调数据", "status": 1})
        check(isinstance(course, dict) and type(course.get("id")) is int,
              "创建临时课程未返回 ID")
        self.course_id = course["id"]
        rows = []
        for username, day, period in (("aaa", first_date, "E2E-A1"),
                                     ("aaa", first_date + timedelta(days=1), "E2E-A2"),
                                     ("bbb", first_date, "E2E-B1")):
            row = success(self.client, "POST", "/api/schedules", headers=headers(self.admin_token),
                          json={"user_id": self.students[username], "course_id": self.course_id,
                                "class_date": day.isoformat(), "period": period,
                                "location": self.marker})
            check(isinstance(row, dict) and type(row.get("id")) is int,
                  "新增临时课表未返回 ID")
            self.schedule_ids.add(row["id"])
            rows.append(row)
        return rows

    def cleanup(self):
        if not self.attempted:
            return
        # 查询唯一课程编码也能发现 POST 已提交、但响应在网络中丢失的记录。
        courses = success(self.client, "GET", "/api/course/list",
                          headers=headers(self.admin_token), params={"keywords": self.marker})
        owned = [row for row in courses if row.get("code") == self.marker]
        course_ids = {row["id"] for row in owned}
        if self.course_id is not None:
            course_ids.add(self.course_id)
        for user_id in self.students.values():
            for row in schedules(self.client, self.admin_token, user_id):
                if row["course_id"] in course_ids and row["location"] == self.marker:
                    self.schedule_ids.add(row["id"])
        failures = []
        for schedule_id in sorted(self.schedule_ids):
            try:
                success(self.client, "DELETE", f"/api/schedules/{schedule_id}",
                        headers=headers(self.admin_token))
            except CheckFailed as exc:
                failures.append(str(exc))
        if not failures:
            for course_id in sorted(course_ids):
                try:
                    success(self.client, "DELETE", f"/api/course/{course_id}",
                            headers=headers(self.admin_token))
                except CheckFailed as exc:
                    failures.append(str(exc))
        check(not failures, "清理失败：" + "; ".join(failures))
        remaining = success(self.client, "GET", "/api/course/list",
                            headers=headers(self.admin_token), params={"keywords": self.marker})
        check(not any(row.get("code") == self.marker for row in remaining), "临时课程仍存在")
        for user_id in self.students.values():
            check(not any(row["id"] in self.schedule_ids or row.get("location") == self.marker
                          for row in schedules(self.client, self.admin_token, user_id)),
                  "临时课表仍存在")
        passed("临时 3 条课表及 1 门课程已删除并通过回查")


def verify(client, admin_token, tokens, fixture, rows, miss_date):
    courses = success(client, "GET", "/api/course/list", headers=headers(admin_token),
                      params={"keywords": fixture.marker})
    names = {row["id"]: row["name"] for row in courses}
    aaa_rows, bbb_row = rows[:2], rows[2]
    expected = Counter(signature(row) for row in aaa_rows)
    actual = mcp_items(client, tokens["aaa"])
    check(Counter(signature(row) for row in actual if row["course_id"] == fixture.course_id)
          == expected, "aaa 的 MCP 结果未包含且仅包含自己的两条临时课表")
    check(all(row["course_name"] == names[fixture.course_id] for row in aaa_rows)
          and all(row["course_name"] == names[fixture.course_id]
                  for row in actual if row["course_id"] == fixture.course_id),
          "course_name 与课程业务接口返回的课程名不一致")
    check(Counter(map(signature, actual)) == Counter(map(signature, schedules(client, tokens["aaa"]))),
          "aaa 的 MCP 全量结果与本人业务接口课表不一致")
    passed("aaa 查询本人两条课表，course_name 与课程表一致")

    hit_date = aaa_rows[0]["class_date"]
    hit = mcp_items(client, tokens["aaa"], {"date": hit_date})
    check(all(row["date"] == hit_date for row in hit), "日期筛选包含其他日期")
    check(Counter(signature(row) for row in hit if row["course_id"] == fixture.course_id)
          == Counter([signature(aaa_rows[0])]), "日期命中结果错误")
    passed("?date= 命中，仅返回对应日期的课表")
    check(mcp_items(client, tokens["aaa"], {"date": miss_date}) == [], "不命中日期应返回空列表")
    passed("?date= 不命中，items=[]、count=0")

    status, body = request(client, "GET", "/api/mcp/my-schedule", headers=headers(tokens["aaa"]),
                           params={"date": "2026-13-99"})
    check(status == 200 and type(body.get("code")) is int and body["code"] >= 400
          and body.get("data") is None and "YYYY-MM-DD" in str(body.get("message", "")),
          "非法日期应返回业务错误且提示含 YYYY-MM-DD")
    passed("非法日期返回业务错误，提示包含 YYYY-MM-DD")

    forged = jwt.encode({"user_id": fixture.students["aaa"], "exp": int(time.time()) + 600},
                        "e2e-deliberately-wrong-signing-key", algorithm="HS256")
    for label, auth in (("无 Token", {}), ("伪造 Token", headers(forged))):
        status, body = request(client, "GET", "/api/mcp/my-schedule", headers=auth)
        check(status == 401 and body.get("code") == 401, f"{label} 应返回 HTTP/业务码 401")
        passed(f"{label} 返回 401")

    bbb = mcp_items(client, tokens["bbb"])
    check(Counter(signature(row) for row in bbb if row["course_id"] == fixture.course_id)
          == Counter([signature(bbb_row)]), "bbb 应仅看到自己的临时课表")
    check(not any(signature(row) in expected for row in bbb), "bbb 泄露 aaa 临时课表")
    check(Counter(map(signature, bbb)) == Counter(map(signature, schedules(client, tokens["bbb"]))),
          "bbb 的 MCP 结果与本人业务接口课表不一致")
    passed("bbb 仅看到自己的临时课表，看不到 aaa 的任何临时数据")


def interrupted(_signum, _frame):
    raise KeyboardInterrupt


def main():
    process = None
    fixture = None
    failures = []
    signal.signal(signal.SIGTERM, interrupted)
    with tempfile.TemporaryFile() as log, httpx.Client(base_url=API, trust_env=False, timeout=25) as client:
        try:
            try:
                response = client.get("/openapi.json", timeout=5)
            except httpx.RequestError:
                raise CheckFailed("FastAPI 8000 未就绪；请在另一个终端启动：\n" + START_API) from None
            check(response.status_code == 200
                  and "/api/mcp/my-schedule" in response.json().get("paths", {}),
                  "8000 未提供第07节接口，请检查已运行的 FastAPI 版本")
            passed("复用 FastAPI 8000，不启动新 FastAPI 进程")
            process = start_mcp(log)
            wait_mcp(process)
            passed("MCP 8001 已就绪，Streamable HTTP 握手及 tools/list 成功")
            admin_token, _ = login(client, "admin", "admin")
            aaa_token, aaa_id = login(client, "aaa", "student")
            bbb_token, bbb_id = login(client, "bbb", "student")
            check(aaa_id != bbb_id, "aaa/bbb 用户 ID 不应相同")
            tokens = {"aaa": aaa_token, "bbb": bbb_token}
            passed("admin、aaa、bbb 均通过真实 POST /api/auth/login 登录")
            baseline = mcp_items(client, aaa_token)
            occupied = {row["date"] for row in baseline}
            first_date = date(2026, 10, 10)
            missing = first_date + timedelta(days=2)
            while missing.isoformat() in occupied:
                missing += timedelta(days=1)
            fixture = Fixture(client, admin_token, {"aaa": aaa_id, "bbb": bbb_id})
            rows = fixture.create(first_date)
            passed("通过业务接口新增 1 门临时课程、aaa 两条课表、bbb 一条课表")
            verify(client, admin_token, tokens, fixture, rows, missing.isoformat())
        except KeyboardInterrupt:
            failures.append("运行被中断")
        except Exception as exc:
            failures.append(str(exc) if isinstance(exc, CheckFailed) else f"运行异常：{type(exc).__name__}")
        finally:
            try:
                if fixture is not None:
                    fixture.cleanup()
            except BaseException as exc:
                reason = str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__
                failures.append(f"测试数据清理未完成（标记 {fixture.marker}）：{reason}")
            finally:
                try:
                    stop_mcp(process)
                except BaseException as exc:
                    failures.append(f"关闭 MCP 失败：{type(exc).__name__}")
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}", file=sys.stderr)
        return 1
    print("[PASS] 第07节真实 HTTP 联调全部通过（退出码 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
