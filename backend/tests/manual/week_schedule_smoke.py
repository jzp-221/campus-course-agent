"""只读验证真实周课表 HTTP 接口；不启动服务，不创建或修改演示数据。

在 backend 运行：.venv/bin/python tests/manual/week_schedule_smoke.py
复用已运行的 FastAPI 8000；不依赖 MCP、模型或 embedding Key。
默认演示学生密码为 123，管理员账号/密码为 admin / admin。
可用 E2E_AAA_PASSWORD / E2E_BBB_PASSWORD / E2E_ADMIN_USERNAME /
E2E_ADMIN_PASSWORD 覆盖；密码与 JWT 不进入输出。
"""

from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys
import unicodedata

import httpx


BACKEND = Path(__file__).resolve().parents[2]
API = "http://127.0.0.1:8000"
START_API = (
    f'cd "{BACKEND}"\n'
    ".venv/bin/python -m uvicorn app.main:app --reload --port 8000"
)
SEMESTER_START = date(2026, 9, 7)
TOTAL_WEEKS = 20
SEMESTER_END = SEMESTER_START + timedelta(days=TOTAL_WEEKS * 7 - 1)
PERIODS = ["1-2节", "3-4节", "5-6节", "7-8节"]
HOLIDAYS = (
    (date(2026, 9, 25), date(2026, 9, 27), "中秋节"),
    (date(2026, 10, 1), date(2026, 10, 7), "国庆节"),
    # 2027 年正式安排未发布；按本任务确认的惯例推定日期验收。
    (date(2027, 1, 1), date(2027, 1, 3), "元旦"),
)
MAKEUP_DAYS = {date(2026, 9, 20), date(2026, 10, 10)}
DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class CheckFailed(Exception):
    pass


def check(condition, message):
    if not condition:
        raise CheckFailed(message)


def passed(message):
    print("[PASS] " + message, flush=True)


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def request(client, method, path, **kwargs):
    try:
        response = client.request(method, path, **kwargs)
    except httpx.RequestError as error:
        raise CheckFailed(
            f"{method} {path} 请求失败（{type(error).__name__}）"
        ) from None
    try:
        body = response.json()
    except ValueError:
        raise CheckFailed(
            f"{method} {path} 未返回 JSON，HTTP {response.status_code}"
        ) from None
    check(isinstance(body, dict), f"{method} {path} 返回格式错误")
    return response.status_code, body


def success(client, method, path, **kwargs):
    status, body = request(client, method, path, **kwargs)
    # 不输出完整响应；登录响应包含 JWT。
    check(
        status == 200 and body.get("code") == 200,
        f"{method} {path} 未成功，HTTP {status}，业务码 {body.get('code')}",
    )
    return body.get("data")


def login(client, username, expected_role="student"):
    password_key = "E2E_ADMIN_PASSWORD" if expected_role == "admin" else f"E2E_{username.upper()}_PASSWORD"
    password = os.environ.get(password_key, "admin" if expected_role == "admin" else "123")
    data = success(
        client,
        "POST",
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    check(
        isinstance(data, dict) and isinstance(data.get("token"), str),
        f"{username} 登录未返回 Token，请检查账号密码",
    )
    user = data.get("user") or {}
    check(
        user.get("username") == username
        and user.get("role") == expected_role
        and type(user.get("id")) is int,
        f"{username} 必须是有效的演示{expected_role}账号",
    )
    return data["token"], user["id"]


def list_schedules(client, token, user_id, *, admin=False):
    rows = {}
    page = 1
    while True:
        data = success(
            client,
            "GET",
            "/api/schedules",
            headers=headers(token),
            params={"page": page, "page_size": 100, **({"user_id": user_id} if admin else {})},
        )
        check(
            isinstance(data, dict)
            and isinstance(data.get("list"), list)
            and type(data.get("total")) is int,
            "现有课表列表分页格式错误",
        )
        for row in data["list"]:
            check(
                isinstance(row, dict)
                and type(row.get("id")) is int
                and row.get("user_id") == user_id
                and row["id"] not in rows,
                "现有列表含他人课表或重复记录",
            )
            rows[row["id"]] = row
        if len(rows) == data["total"]:
            return rows
        check(
            data["list"] and len(rows) < data["total"],
            "现有课表分页总数不一致",
        )
        page += 1


def holiday_name(day):
    for start, end, name in HOLIDAYS:
        if start <= day <= end:
            return name
    return ""


def expected_class_day(day):
    return not holiday_name(day) and (day in MAKEUP_DAYS or day.weekday() < 5)


def parse_date(value, context):
    check(isinstance(value, str), context + " 日期必须是字符串")
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise CheckFailed(context + " 日期不是有效的 YYYY-MM-DD") from None
    check(value == day.isoformat(), context + " 日期格式不是 YYYY-MM-DD")
    return day


def course_ids(data):
    return {course["id"] for day in data["days"] for course in day["courses"]}


def occupied_cells(data):
    """只计有课格：空格不作为“相同”，否则稀疏课表会虚高相似率。"""
    return {
        (day["date"], course["period"]): course["course_id"]
        for day in data["days"] for course in day["courses"]
    }


def weekly_difference_summary(weekly):
    """独立从真实周接口计算；不用种子脚本的实现或数据库配置。"""
    result = []
    for week in sorted(weekly["aaa"]):
        aaa = occupied_cells(weekly["aaa"][week])
        bbb = occupied_cells(weekly["bbb"][week])
        union = set(aaa) | set(bbb)
        same = sum(cell in aaa and cell in bbb and aaa[cell] == bbb[cell]
                   for cell in union)
        different = len(union) - same
        result.append({
            "week": week,
            "start_date": weekly["aaa"][week]["start_date"],
            "end_date": weekly["aaa"][week]["end_date"],
            "aaa_count": len(aaa), "bbb_count": len(bbb),
            "same_cells": same, "different_cells": different,
            "compared_cells": len(union),
            "different_ratio": different / len(union) if union else 0.0,
        })
    return result


def print_difference_summary(summary):
    print("\n两人课表差异（仅比较至少一人有课的日期/节次格；同格同 course_id 才算相同）：", flush=True)
    print("| 周次 | aaa 课节数 | bbb 课节数 | 相同格 | 不同格 | 差异率 |", flush=True)
    print("| --- | --- | --- | --- | --- | --- |", flush=True)
    for row in summary:
        print(f"| {row['week']} | {row['aaa_count']} | {row['bbb_count']} | "
              f"{row['same_cells']} | {row['different_cells']} | "
              f"{row['different_ratio']:.1%} |", flush=True)


def verify_fixed_slots(schedule_rows, username):
    slots = {}
    workdays_by_week = {}
    for row in schedule_rows.values():
        day = parse_date(row["class_date"], username)
        slot = (day.weekday(), row["period"])
        course_id = row["course_id"]
        check(course_id not in slots or slots[course_id] == slot,
              f"{username} course_id={course_id} 未保持固定星期/节次")
        slots[course_id] = slot
        if day.weekday() < 5:
            week = (day - SEMESTER_START).days // 7 + 1
            workdays_by_week.setdefault(week, set()).add(day)
    if username == "bbb":
        check(3 <= len(slots) <= 5, "bbb 应仅选择 3~5 门唯一课程用于稀疏隔离演示")
        check(all(len(days) <= 3 for days in workdays_by_week.values()),
              "bbb 每周工作日最多三天有课，应与 aaa 形成明显对比")
    return slots


def verify_week(data, week, user_id, schedule_rows, *, username=None, allow_empty=False):
    context = f"第 {week} 周"
    check(isinstance(data, dict), context + " data 格式错误")
    check(data.get("user_id") == user_id, context + " 顶层 user_id 与被查询账号不一致")
    if username is not None:
        check(data.get("username") == username, context + " 顶层 username 与被查询账号不一致")
    check(isinstance(data.get("name"), str) and data["name"], context + " 缺少被查询账号的名称标识")
    monday = SEMESTER_START + timedelta(days=(week - 1) * 7)
    sunday = monday + timedelta(days=6)
    check(
        data.get("week") == week
        and data.get("start_date") == monday.isoformat()
        and data.get("end_date") == sunday.isoformat(),
        context + " 周次或日期区间错误",
    )
    check(
        data.get("total_weeks") == TOTAL_WEEKS
        and data.get("semester_start_date") == SEMESTER_START.isoformat()
        and data.get("semester_end_date") == SEMESTER_END.isoformat(),
        context + " 学期配置错误",
    )
    check(data.get("periods") == PERIODS, context + " 必须保留四档标准节次")
    today = parse_date(data.get("today"), context)
    expected_current = (
        (today - SEMESTER_START).days // 7 + 1
        if SEMESTER_START <= today <= SEMESTER_END
        else None
    )
    check(
        data.get("current_week") == expected_current
        and type(data.get("is_current_week")) is bool
        and data["is_current_week"] == (week == expected_current),
        context + " 本周标记错误",
    )
    days = data.get("days")
    check(isinstance(days, list) and len(days) == 7, context + " 必须返回完整七天")
    seen_ids = set()
    seen_cells = set()
    included_holidays = set()
    for offset, day_data in enumerate(days):
        day = monday + timedelta(days=offset)
        label = f"{context} {day.isoformat()}"
        name = holiday_name(day)
        if name:
            included_holidays.add(name)
        check(isinstance(day_data, dict), label + " 日期数据格式错误")
        check(
            day_data.get("date") == day.isoformat()
            and day_data.get("weekday") == offset + 1,
            label + " 日期或星期位置错误",
        )
        for field, expected in (
            ("is_class_day", bool(expected_class_day(day))),
            ("is_today", day == today),
            ("is_holiday", bool(name)),
        ):
            check(
                type(day_data.get(field)) is bool and day_data[field] == expected,
                label + f" {field} 错误",
            )
        check(
            day_data.get("holiday_name") == name
            if name
            else day_data.get("holiday_name") in ("", None),
            label + " 假期名称错误",
        )
        courses = day_data.get("courses")
        check(isinstance(courses, list), label + " 课程必须是列表")
        if not expected_class_day(day):
            check(not courses, label + " 节假日或普通周末不应有课")
        for course in courses:
            check(isinstance(course, dict), label + " 课程格式错误")
            required = {
                "id", "user_id", "period", "course_id", "course_name",
                "teacher", "location", "start_week", "end_week",
            }
            check(required.issubset(course), label + " 课程字段不完整")
            check(
                type(course["id"]) is int
                and type(course["course_id"]) is int
                and course["user_id"] == user_id,
                label + " 课程身份字段错误或发生越权",
            )
            check(course["period"] in PERIODS, label + " 课程节次不在四档中")
            check(
                type(course["start_week"]) is int
                and type(course["end_week"]) is int
                and 1 <= course["start_week"] <= week <= course["end_week"] <= TOTAL_WEEKS,
                label + " 返回了周次范围之外的课程",
            )
            check(
                isinstance(course["teacher"], str) and course["teacher"].strip(),
                label + " 演示课程缺少教师",
            )
            cell = (day.isoformat(), course["period"])
            check(
                course["id"] not in seen_ids and cell not in seen_cells,
                label + " 同一格出现多门课或重复课表记录",
            )
            seen_ids.add(course["id"])
            seen_cells.add(cell)
            baseline = schedule_rows.get(course["id"])
            check(baseline is not None, label + " 返回不属于当前学生列表的记录")
            check(
                baseline.get("class_date") == day.isoformat()
                and all(
                    baseline.get(field) == course[field]
                    for field in ("user_id", "period", "course_id", "course_name", "location")
                ),
                label + " 课程名称、日期或内容与现有联表列表不一致",
            )
    if not allow_empty:
        check(seen_ids, context + " 应有演示课程")
    expected_ids = {
        row_id for row_id, row in schedule_rows.items()
        if monday.isoformat() <= row.get("class_date", "") <= sunday.isoformat()
        and row.get("start_week", 1) <= week <= row.get("end_week", TOTAL_WEEKS)
        and expected_class_day(parse_date(row["class_date"], context))
    }
    check(seen_ids == expected_ids, context + " 遗漏或多返回了演示课程")
    check(
        type(data.get("has_holidays")) is bool
        and data["has_holidays"] == bool(included_holidays),
        context + " 假期标记错误",
    )
    check(isinstance(data.get("holiday_notice"), str), context + " 缺少假期说明字符串")
    check(
        all(name in data["holiday_notice"] for name in included_holidays),
        context + " 假期提示缺少假期名称",
    )
    if week in (4, 5):
        check("10/1-10/7" in data["holiday_notice"], context + " 必须解释跨周国庆假期")
    if week == 17:
        check("推定" in data["holiday_notice"], "第 17 周元旦提示必须注明推定")
    return seen_ids


def verify_errors(client, token):
    for week in ("0", "21", "abc", "1.5"):
        status, body = request(
            client, "GET", "/api/schedules/week",
            headers=headers(token), params={"week": week},
        )
        check(
            status in (200, 400) and body.get("code") == 400,
            f"week={week} 必须返回明确业务码 400，而非 HTTP {status}/业务码 {body.get('code')}",
        )
        check(isinstance(body.get("message"), str) and body["message"], "周次错误必须有提示")
    for auth in ({}, headers("forged-token")):
        status, body = request(
            client, "GET", "/api/schedules/week", headers=auth, params={"week": 5}
        )
        check(
            status == 401 and body.get("code") == 401,
            "未登录或伪造 Token 必须被周课表接口以 401 拒绝",
        )
    passed("周次 0/21/abc/1.5 均为业务 400；未登录与伪造 Token 均为 401")


def display_width(text):
    return sum(
        0 if unicodedata.combining(character)
        else 2 if unicodedata.east_asian_width(character) in "WF" else 1
        for character in text
    )


def print_grid(data, username, *, show_course_ids=False):
    rows = []
    labels = []
    for offset, day in enumerate(data["days"]):
        parsed = date.fromisoformat(day["date"])
        hint = day["holiday_name"] if day["is_holiday"] else "调休" if parsed in MAKEUP_DAYS else "周末" if not day["is_class_day"] else ""
        labels.append(f"{DAY_NAMES[offset]} {parsed.month}/{parsed.day}" + (f" {hint}" if hint else ""))
    rows.append(["节次", *labels])
    for period in data["periods"]:
        rows.append([
            period,
            *(next((course["course_name"] + (f"[ID={course['course_id']}]" if show_course_ids else "")
                    for course in day["courses"] if course["period"] == period), "—")
              for day in data["days"]),
        ])
    period_width = max(display_width(row[0]) for row in rows)
    column_width = max(display_width(cell) for row in rows for cell in row[1:])
    print(f"\n{username} 第 {data['week']} 周：{data['start_date']} ~ {data['end_date']}", flush=True)
    print("假期提示：" + data["holiday_notice"], flush=True)
    for index, row in enumerate(rows):
        padded = [
            cell + " " * ((period_width if offset == 0 else column_width) - display_width(cell))
            for offset, cell in enumerate(row)
        ]
        print("| " + " | ".join(padded) + " |", flush=True)
        if index == 0:
            print("| " + " | ".join(["-" * period_width] + ["-" * column_width] * 7) + " |", flush=True)


def print_comparison_grid(aaa, bbb):
    check(aaa["week"] == bbb["week"] and aaa["start_date"] == bbb["start_date"],
          "并排网格必须比较同一周")
    print(f"\naaa / bbb 第 {aaa['week']} 周同格对照："
          f"{aaa['start_date']} ~ {aaa['end_date']}（显示 course_id）", flush=True)
    print("| 日期 | 节次 | aaa | bbb |", flush=True)
    print("| --- | --- | --- | --- |", flush=True)
    for offset, day in enumerate(aaa["days"]):
        other = bbb["days"][offset]
        check(day["date"] == other["date"], "并排网格日期未对齐")
        parsed = date.fromisoformat(day["date"])
        for period in aaa["periods"]:
            cells = []
            for current in (day, other):
                course = next((course for course in current["courses"]
                               if course["period"] == period), None)
                cells.append(f"{course['course_name']}[ID={course['course_id']}]"
                             if course else "—")
            print(f"| {DAY_NAMES[offset]} {parsed.month}/{parsed.day} | {period} | "
                  f"{cells[0]} | {cells[1]} |", flush=True)


def verify_course_names(client, admin_token, schedule_rows):
    courses = success(client, "GET", "/api/course/list", headers=headers(admin_token))
    check(isinstance(courses, list), "课程列表格式错误")
    names = {}
    for course in courses:
        check(isinstance(course, dict) and type(course.get("id")) is int
              and isinstance(course.get("name"), str), "课程身份或名称格式错误")
        names.setdefault(course["name"], []).append(course["id"])
    duplicate_names = {name: ids for name, ids in names.items() if len(ids) > 1}
    check(not duplicate_names, "课程库仍有重名课程：" + json.dumps(duplicate_names, ensure_ascii=False))
    check(len(names.get("数据结构", [])) == 1, "课程库必须保留且仅保留一条数据结构课程")
    canonical_id = names["数据结构"][0]
    for username, rows in schedule_rows.items():
        references = {row["course_id"] for row in rows.values() if row.get("course_name") == "数据结构"}
        check(references <= {canonical_id}, f"{username} 数据结构课表必须只指向同一个课程 ID")
    passed(f"课程名称无重复；数据结构引用只允许 course_id={canonical_id}")


def verify_admin(client, admin_token, admin_id, admin_name, students, weekly, student_rows):
    aaa_id = students["aaa"][1]
    admin_aaa_ids = set()
    for week in range(1, TOTAL_WEEKS + 1):
        data = success(client, "GET", "/api/schedules/week", headers=headers(admin_token),
                       params={"week": week, "user_id": aaa_id})
        admin_aaa_ids.update(verify_week(data, week, aaa_id, student_rows["aaa"], username="aaa"))
        check(data == weekly["aaa"][week], f"管理员查 aaa 第 {week} 周与 aaa 本人视图不一致")
    check(admin_aaa_ids == set(student_rows["aaa"]), "管理员查 aaa 20 周未返回全部课表")
    passed(f"管理员逐周查询 aaa 的 1~20 周：与 aaa 本人视图完全一致，共 {len(admin_aaa_ids)} 条")

    own_rows = list_schedules(client, admin_token, admin_id, admin=True)
    own_week = success(client, "GET", "/api/schedules/week", headers=headers(admin_token), params={"week": 6})
    verify_week(own_week, 6, admin_id, own_rows, username=admin_name, allow_empty=True)
    check(all(course["user_id"] == admin_id for day in own_week["days"] for course in day["courses"]),
          "管理员未传 user_id 时不得默认查询所有学生")
    passed("管理员不传 user_id 只查自己，并返回本人身份标识")

    users, page = [], 1
    while True:
        data = success(client, "GET", "/api/user/list", headers=headers(admin_token),
                       params={"page": page, "page_size": 100})
        check(isinstance(data, dict) and isinstance(data.get("list"), list)
              and type(data.get("total")) is int, "用户列表分页响应格式错误")
        users.extend(data["list"])
        if len(users) == data["total"]:
            break
        check(data["list"] and len(users) < data["total"], "用户列表分页总数不一致")
        page += 1
    unknown_id = max(row["id"] for row in users) + 1
    status, body = request(client, "GET", "/api/schedules/week", headers=headers(admin_token),
                           params={"week": 6, "user_id": unknown_id})
    check(status in (200, 404) and body.get("code") == 404 and body.get("data") is None,
          "管理员查询不存在账号必须返回业务 404")
    check(isinstance(body.get("message"), str) and body["message"], "账号不存在应有明确提示")
    passed("管理员查询不存在的学生返回业务 404")

    data = success(client, "GET", "/api/schedules/week", headers=headers(admin_token),
                   params={"week": 6, "user_id": aaa_id})
    print("\n管理员查 aaa 第 6 周响应片段（不含 JWT）：", flush=True)
    print(json.dumps({
        "user_id": data["user_id"], "username": data["username"],
        "week": data["week"], "start_date": data["start_date"], "end_date": data["end_date"],
        "days": [day for day in data["days"]
                 if any(course["course_name"] == "数据结构" for course in day["courses"])],
    }, ensure_ascii=False, indent=2), flush=True)


def main():
    try:
        with httpx.Client(base_url=API, trust_env=False, timeout=15) as client:
            try:
                response = client.get("/openapi.json", timeout=5)
            except httpx.RequestError:
                raise CheckFailed("FastAPI 未运行，请先启动：\n" + START_API) from None
            check(response.status_code == 200, "已有 8000 服务未提供 FastAPI OpenAPI")
            check(
                "/api/schedules/week" in response.json().get("paths", {}),
                "已有 FastAPI 尚未提供周课表接口，请先重载后端",
            )
            students = {name: login(client, name) for name in ("aaa", "bbb")}
            admin_name = os.environ.get("E2E_ADMIN_USERNAME", "admin")
            admin_token, admin_id = login(client, admin_name, "admin")
            check(students["aaa"][1] != students["bbb"][1], "两名演示学生 ID 必须不同")
            weekly = {}
            all_ids = {}
            student_rows = {}
            for name, (token, user_id) in students.items():
                schedule_rows = list_schedules(client, token, user_id)
                student_rows[name] = schedule_rows
                check(schedule_rows, f"{name} 演示课表应非空；脚本不会重造数据")
                verify_fixed_slots(schedule_rows, name)
                weekly[name] = {}
                all_ids[name] = set()
                for week in range(1, TOTAL_WEEKS + 1):
                    data = success(
                        client, "GET", "/api/schedules/week",
                        headers=headers(token), params={"week": week},
                    )
                    all_ids[name].update(verify_week(data, week, user_id, schedule_rows, username=name))
                    weekly[name][week] = data
                check(all_ids[name] == set(schedule_rows), f"{name} 20 周返回记录与列表不一致")
                sunday = weekly[name][2]["days"][6]
                saturday = weekly[name][5]["days"][5]
                check(sunday["date"] == "2026-09-20" and sunday["courses"],
                      f"{name} 9/20 周日调休应有专属课程")
                check(saturday["date"] == "2026-10-10" and saturday["courses"],
                      f"{name} 10/10 周六调休应有专属课程")
                passed(f"{name}：20 周均有课，共 {len(schedule_rows)} 条；"
                       "7 天×4 档、范围、固定课程格、名称、假期和调休均正确")
            check(len(student_rows["bbb"]) < len(student_rows["aaa"]),
                  "bbb 课表必须明显少于 aaa，不能再复用同一网格")
            summary = weekly_difference_summary(weekly)
            for row in summary:
                check(row["bbb_count"] < row["aaa_count"],
                      f"第 {row['week']} 周 bbb 应比 aaa 稀疏")
                check(row["different_ratio"] >= .8,
                      f"第 {row['week']} 周有课格差异不足 80%：{row['different_ratio']:.1%}")
            print_difference_summary(summary)
            passed("20 周有课格差异均 ≥80%；bbb 每周工作日最多三天、仅 3~5 门唯一课程")
            check(all_ids["aaa"].isdisjoint(all_ids["bbb"]), "两名学生的课表 ID 发生交叉")
            for name, other in (("aaa", "bbb"), ("bbb", "aaa")):
                token, user_id = students[name]
                for week in range(1, TOTAL_WEEKS + 1):
                    attempted = success(
                        client, "GET", "/api/schedules/week",
                        headers=headers(token),
                        params={"week": week, "user_id": students[other][1]},
                    )
                    check(course_ids(attempted) == course_ids(weekly[name][week]),
                          f"{name} 注入 user_id 后周课表发生越权或丢失")
                    check(all(course["user_id"] == user_id for day in attempted["days"] for course in day["courses"]),
                          f"{name} 注入 user_id 后看到了别人的课表")
                    check(attempted.get("user_id") == user_id and attempted.get("username") == name,
                          f"{name} 注入 user_id 后响应身份标识发生越权")
            passed("aaa / bbb 课表完全隔离；20 周逐一注入他人 user_id 仍无法越权")
            verify_course_names(client, admin_token, student_rows)
            verify_admin(client, admin_token, admin_id, admin_name, students, weekly, student_rows)
            verify_errors(client, students["aaa"][0])
            print_grid(weekly["aaa"][3], "aaa", show_course_ids=True)
            print_grid(weekly["aaa"][6], "aaa", show_course_ids=True)
            print_grid(weekly["aaa"][5], "aaa")
            print_comparison_grid(weekly["aaa"][6], weekly["bbb"][6])
        passed("周课表真实只读冒烟通过（退出码 0）；未启动服务、未修改演示数据")
        return 0
    except KeyboardInterrupt:
        print("[FAIL] 运行被中断", file=sys.stderr)
    except CheckFailed as error:
        print("[FAIL] " + str(error), file=sys.stderr)
    except Exception as error:
        # 不打印带 URL/响应/请求头的堆栈，避免认证信息进入日志。
        print("[FAIL] 周课表冒烟异常：" + type(error).__name__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
