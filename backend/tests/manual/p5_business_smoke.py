"""P5 真实业务错误回归：复用 FastAPI/MySQL，仅造删唯一标记的测试数据。

运行：.venv/bin/python tests/manual/p5_business_smoke.py
不会启动 FastAPI/MCP，不修改 .env；文档入库会向已配置的 embedding
服务发送本脚本自造的教务规定。失败与中断均按文档→计划→课表→课程→用户清理。
"""

import signal
import sys
import uuid

import httpx

from e2e_mcp import (
    API, START_API, CheckFailed, check, headers, interrupted, login, passed,
    request, schedules, success,
)


def page_rows(client, token, path, **filters):
    rows, page = [], 1
    while True:
        data = success(client, "GET", path, headers=headers(token),
                       params={"page": page, "page_size": 100, **filters})
        check(isinstance(data, dict) and isinstance(data.get("list"), list)
              and type(data.get("total")) is int, f"{path} 分页响应格式错误")
        rows.extend(data["list"])
        if len(rows) >= data["total"]:
            return rows
        check(data["list"], f"{path} 分页提前结束")
        page += 1


def expect_error(client, method, path, token, code, *messages, **kwargs):
    status, body = request(client, method, path, headers=headers(token), **kwargs)
    check(status == 200 and body.get("code") == code and body.get("data") is None,
          f"{method} {path} 应为 HTTP 200 / 业务码 {code}，实际 HTTP {status} / {body.get('code')}")
    message = body.get("message")
    check(isinstance(message, str), "业务错误未返回明确消息")
    for fragment in messages:
        check(fragment in message, f"业务错误消息应包含 {fragment}")
    for internal in (
        "users", "coruses", "schedules", "study_plans", "documents",
        "mysql://", "sqlite://", "SELECT ", "DELETE ", "UPDATE ",
    ):
        check(internal not in message, "业务错误泄露 SQL、表名或连接信息")
    passed(f"{method} {path} → 业务码 {code}：{message}")
    return body


class Fixture:
    def __init__(self, client, admin_token):
        self.client = client
        self.admin_token = admin_token
        self.marker = "P5-" + uuid.uuid4().hex
        self.username = self.marker + "-user"
        self.filename = self.marker + "-规定.txt"
        self.course_code = self.marker + "-main"
        self.unlinked_code = self.marker + "-free"
        self.plan_content = self.marker + " 学习计划：复习测试课程三十分钟。"
        self.user_id = None
        self.user_token = None
        self.course_id = None
        self.unlinked_id = None
        self.document_ids = set()
        self.plan_ids = set()
        self.schedule_ids = set()
        self.attempted = False

    def admin(self, method, path, **kwargs):
        return success(self.client, method, path, headers=headers(self.admin_token), **kwargs)

    def users(self):
        return [row for row in page_rows(
            self.client, self.admin_token, "/api/user/list", keywords=self.marker
        ) if row.get("username") == self.username]

    def courses(self):
        rows = self.admin("GET", "/api/course/list", params={"keywords": self.marker})
        check(isinstance(rows, list), "课程列表格式错误")
        return [row for row in rows if row.get("code") in {self.course_code, self.unlinked_code}]

    def main_course(self):
        rows = [row for row in self.courses() if row.get("code") == self.course_code]
        check(len(rows) == 1 and rows[0]["id"] == self.course_id, "临时课程不存在或不唯一")
        return rows[0]

    def documents(self):
        return [row for row in page_rows(self.client, self.admin_token, "/api/documents")
                if row.get("filename") == self.filename and row.get("uploader_id") == self.user_id]

    def plans(self):
        check(self.user_token is not None, "缺少临时用户的计划查询凭据")
        return [row for row in page_rows(self.client, self.user_token, "/api/study-plans")
                if row.get("content") == self.plan_content]

    def create(self):
        self.attempted = True
        user = self.admin("POST", "/api/user", json={
            "username": self.username, "password": "123", "name": self.marker,
            "role": "admin", "status": 1,
        })
        check(isinstance(user, dict) and type(user.get("id")) is int, "创建临时用户未返回 ID")
        self.user_id = user["id"]
        self.user_token, logged_id = login(self.client, self.username, "admin")
        check(logged_id == self.user_id, "临时用户登录身份不一致")
        course = self.admin("POST", "/api/course", json={
            "name": self.marker + "测试课程", "code": self.course_code,
            "description": "原始简介", "status": 1,
        })
        check(isinstance(course, dict) and type(course.get("id")) is int, "创建课程未返回 ID")
        self.course_id = course["id"]
        unlinked = self.admin("POST", "/api/course", json={
            "name": self.marker + "无课表课程", "code": self.unlinked_code,
            "description": "无关联删除测试", "status": 1,
        })
        check(isinstance(unlinked, dict) and type(unlinked.get("id")) is int, "创建无关联课程未返回 ID")
        self.unlinked_id = unlinked["id"]
        passed("业务接口新增唯一临时用户与两门测试课程")

    def add_document_and_become_student(self):
        text = (
            "P5 业务错误处理回归用合成规定。测试课程需在上课前十分钟到达教室，"
            "完成回归后删除这份临时资料。本规定不包含任何真实学生信息。"
        )
        rows = success(self.client, "POST", "/api/documents/upload",
                       headers=headers(self.user_token),
                       files=[("files", (self.filename, text.encode(), "text/plain"))])
        check(isinstance(rows, list) and len(rows) == 1, "单文档上传未返回一条记录")
        row = rows[0]
        check(type(row.get("id")) is int, "文档记录缺少 ID")
        self.document_ids.add(row["id"])
        check(row.get("uploader_id") == self.user_id and row.get("filename") == self.filename
              and row.get("status") == "indexed" and row.get("chunk_count", 0) >= 1,
              "真实文档未归属临时上传人或未成功入库")
        student = self.admin("PUT", f"/api/user/{self.user_id}", json={"role": "student"})
        check(student.get("role") == "student", "临时上传人未转为学生")
        self.user_token, logged_id = login(self.client, self.username, "student")
        check(logged_id == self.user_id, "转学生后登录身份错误")
        passed("合成资料真实入库后，上传人转为学生；保留上传归属")

    def add_schedules_and_plan(self):
        for period in ("P5-1", "P5-2"):
            row = self.admin("POST", "/api/schedules", json={
                "user_id": self.user_id, "course_id": self.course_id,
                "class_date": "2026-10-10", "period": period, "location": self.marker,
            })
            check(isinstance(row, dict) and type(row.get("id")) is int, "新增课表未返回 ID")
            self.schedule_ids.add(row["id"])
        row = success(self.client, "POST", "/api/study-plans", headers=headers(self.user_token),
                      json={"content": self.plan_content})
        check(isinstance(row, dict) and type(row.get("id")) is int, "新增计划未返回 ID")
        self.plan_ids.add(row["id"])
        passed("业务接口新增临时学生两条课表与一条学习计划")

    def cleanup(self):
        if not self.attempted:
            return
        failures = []

        def attempt(label, operation, fallback=None):
            try:
                return operation()
            except BaseException as error:
                reason = str(error) if isinstance(error, CheckFailed) else type(error).__name__
                failures.append(f"{label}：{reason}")
                return fallback

        # 唯一业务标记恢复 POST 已提交但响应丢失的记录。
        users = attempt("回查临时用户", self.users, [])
        if users:
            check(len(users) == 1, "唯一标记对应多名用户，拒绝扩大清理范围")
            self.user_id = users[0]["id"]
            if self.user_token is None:
                credentials = attempt(
                    "恢复临时用户登录", lambda: login(self.client, self.username, users[0]["role"])
                )
                if credentials is not None:
                    self.user_token = credentials[0]
        courses = attempt("回查临时课程", self.courses, [])
        course_ids = {row["id"] for row in courses}
        docs = attempt("回查临时文档", self.documents, []) if self.user_id is not None else []
        self.document_ids.update(row["id"] for row in docs)

        def delete_owned(path, token):
            status, body = request(self.client, "DELETE", path, headers=headers(token))
            check(status == 200 and body.get("code") in (200, 404),
                  f"清理 {path} 未成功，HTTP {status} / 业务码 {body.get('code')}")

        for document_id in sorted(self.document_ids):
            attempt("删除临时文档", lambda row_id=document_id:
                    delete_owned(f"/api/documents/{row_id}", self.admin_token))

        if users and self.user_token:
            plans = attempt("回查临时计划", self.plans, [])
            self.plan_ids.update(row["id"] for row in plans)
            for plan_id in sorted(self.plan_ids):
                attempt("删除临时计划", lambda row_id=plan_id:
                        delete_owned(f"/api/study-plans/{row_id}", self.user_token))

        if self.user_id is not None:
            rows = attempt("回查临时课表", lambda: schedules(
                self.client, self.admin_token, self.user_id
            ), [])
            self.schedule_ids.update(row["id"] for row in rows
                                     if row.get("location") == self.marker
                                     and row.get("course_id") in course_ids)
        for schedule_id in sorted(self.schedule_ids):
            attempt("删除临时课表", lambda row_id=schedule_id:
                    delete_owned(f"/api/schedules/{row_id}", self.admin_token))
        for course_id in sorted(course_ids):
            attempt("删除临时课程", lambda row_id=course_id:
                    delete_owned(f"/api/course/{row_id}", self.admin_token))
        for user in users:
            attempt("删除临时用户", lambda row_id=user["id"]:
                    delete_owned(f"/api/user/{row_id}", self.admin_token))

        remaining_users = attempt("确认用户已清理", self.users, [])
        remaining_courses = attempt("确认课程已清理", self.courses, [])
        remaining_docs = attempt("确认文档已清理", self.documents, []) if self.user_id is not None else []
        if remaining_users or remaining_courses or remaining_docs:
            failures.append("唯一标记的用户、课程或文档仍存在")
        if self.user_id is not None:
            remaining_schedules = attempt("确认课表已清理", lambda: schedules(
                self.client, self.admin_token, self.user_id
            ), [])
            if any(row.get("location") == self.marker for row in remaining_schedules):
                failures.append("唯一标记的课表仍存在")
        check(not failures, "临时数据清理未完成：" + "; ".join(failures))
        passed("临时文档、计划、课表、课程、用户已按顺序清理，并通过业务接口回查")


def verify(client, admin_token, admin_id, fixture):
    course_path = f"/api/course/{fixture.course_id}"
    user_path = f"/api/user/{fixture.user_id}"

    fixture.admin("PUT", course_path, json={"description": ""})
    check(fixture.main_course().get("description") == "", "清空简介后回查不是空串")
    passed("PUT description:'' 后 GET /api/course/list 回查为空串")
    fixture.admin("PUT", course_path, json={"description": "必须保留的简介"})
    fixture.admin("PUT", course_path, json={"name": fixture.marker + "只更新名称"})
    check(fixture.main_course().get("description") == "必须保留的简介", "只更新名称修改了简介")
    passed("只提交 name 时简介保持不变")
    expect_error(client, "PUT", course_path, admin_token, 400, "课程编码不能为空", json={"code": ""})
    check(fixture.main_course()["code"] == fixture.course_code, "编码清空失败却修改了原编码")
    passed("课程编码必填；空串报400，原编码保持不变")

    fixture.admin("DELETE", f"/api/course/{fixture.unlinked_id}")
    check(not any(row["id"] == fixture.unlinked_id for row in fixture.courses()), "无课表课程删除后仍在")
    expect_error(client, "DELETE", f"/api/course/{fixture.unlinked_id}", admin_token, 404, "课程不存在")
    passed("无课表课程删除成功；再次删除返回404业务错误")

    fixture.add_document_and_become_student()
    expect_error(client, "DELETE", user_path, admin_token, 409, "上传文档 1 份", "请先清理")
    check(len(fixture.users()) == 1 and len(fixture.documents()) == 1, "文档阻塞删除时用户或文档丢失")
    passed("真实 documents.uploader_id 单独阻止学生删除，记录全部保留")

    fixture.add_schedules_and_plan()
    expect_error(client, "DELETE", course_path, admin_token, 409, "已有 2 条课表安排", "先删除或调整")
    check(fixture.main_course()["id"] == fixture.course_id, "关联课程删除被拒绝后仍应存在")
    expect_error(client, "DELETE", user_path, admin_token, 409,
                 "课表 2 条", "学习计划 1 条", "上传文档 1 份", "请先清理")
    check(len(fixture.users()) == 1 and len(fixture.plans()) == 1
          and len(fixture.documents()) == 1, "关联删除被拒绝后数据未完整保留")
    check({row["id"] for row in schedules(client, admin_token, fixture.user_id)}
          == fixture.schedule_ids, "删除被拒绝后临时课表数量变化")
    passed("真实 MySQL 三类关联共同阻止删除，用户和关联数据全部保留")
    expect_error(client, "DELETE", f"/api/user/{admin_id}", admin_token, 403, "不能删除当前登录的账号")
    me = success(client, "GET", "/api/user/me", headers=headers(admin_token))
    check(me.get("id") == admin_id, "当前管理员账号被删除")

    for document_id in sorted(fixture.document_ids):
        fixture.admin("DELETE", f"/api/documents/{document_id}")
    check(fixture.documents() == [], "临时上传文档删除后仍在列表中")
    for schedule_id in sorted(fixture.schedule_ids):
        fixture.admin("DELETE", f"/api/schedules/{schedule_id}")
    check(schedules(client, admin_token, fixture.user_id) == [], "临时学生课表删除后仍存在")
    expect_error(client, "DELETE", user_path, admin_token, 409, "学习计划 1 条", "请先清理")
    fixture.admin("DELETE", course_path)
    expect_error(client, "DELETE", course_path, admin_token, 404, "课程不存在")
    expect_error(client, "PUT", course_path, admin_token, 404, "课程不存在", json={"name": "新名称"})
    passed("清理课表后课程可删除；删除或更新已删除课程均为404")

    for plan_id in sorted(fixture.plan_ids):
        success(client, "DELETE", f"/api/study-plans/{plan_id}", headers=headers(fixture.user_token))
    check(fixture.plans() == [], "临时计划删除后仍在列表中")
    fixture.admin("DELETE", user_path)
    expect_error(client, "DELETE", user_path, admin_token, 404, "用户不存在")
    expect_error(client, "PUT", user_path, admin_token, 404, "用户不存在", json={"name": "新名称"})
    check(fixture.users() == [], "无关联用户删除后仍存在")
    passed("文档、课表与学习计划清理完成后用户可删除；重复删除或更新404")


def main():
    fixture = None
    failures = []
    signal.signal(signal.SIGTERM, interrupted)
    with httpx.Client(base_url=API, trust_env=False, timeout=90) as client:
        try:
            try:
                response = client.get("/openapi.json", timeout=5)
            except httpx.RequestError:
                raise CheckFailed("FastAPI 8000 未运行，请先启动：\n" + START_API) from None
            check(response.status_code == 200, "FastAPI 8000 未就绪")
            paths = response.json().get("paths", {})
            check(all(path in paths for path in (
                "/api/course/{course_id}", "/api/user/{user_id}", "/api/documents/upload",
                "/api/schedules", "/api/study-plans",
            )), "已运行的 FastAPI 缺少本次验收接口")
            passed("复用已有 FastAPI 8000 与 MySQL；不启动 FastAPI/MCP")
            admin_token, admin_id = login(client, "admin", "admin")
            fixture = Fixture(client, admin_token)
            fixture.create()
            verify(client, admin_token, admin_id, fixture)
        except KeyboardInterrupt:
            failures.append("运行被中断")
        except Exception as error:
            failures.append(str(error) if isinstance(error, CheckFailed)
                            else f"运行异常：{type(error).__name__}")
        finally:
            if fixture is not None:
                try:
                    fixture.cleanup()
                except BaseException as error:
                    reason = str(error) if isinstance(error, CheckFailed) else type(error).__name__
                    failures.append(f"清理失败（唯一标记 {fixture.marker}）：{reason}")
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}", file=sys.stderr)
        return 1
    print("[PASS] P5 真实 FastAPI/MySQL 业务错误回归全部通过（退出码 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
