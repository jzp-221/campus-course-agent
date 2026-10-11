"""P3 真实 HTTP / MCP 验收；仅重复保存的攻击模型使用可控替身。

复用 8000，启动并关闭本脚本的 8001。所有临时写入通过业务接口或 MCP，
归属的数值核对只读数据库；JWT 和模型 Key 不进入输出或进程参数。
"""

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import signal
import sys
import tempfile
import time
import uuid

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.common.exceptions import BusinessException
from app.config import settings
from app.database import SessionLocal
from app.models.study_plan import StudyPlan
from app.services.agent_service import run_agent, shanghai_today
from e2e_mcp import (
    API, MCP, START_API, CheckFailed, Fixture, check, headers, interrupted,
    login, passed, start_mcp, stop_mcp, success, wait_mcp,
)
from sse_contract import SSEContract


def plans(client, token):
    rows, page = [], 1
    while True:
        data = success(client, "GET", "/api/study-plans", headers=headers(token),
                       params={"page": page, "page_size": 100})
        check(isinstance(data, dict) and isinstance(data.get("list"), list)
              and type(data.get("total")) is int, "学习计划分页响应格式错误")
        rows.extend(data["list"])
        if len(rows) >= data["total"]:
            return rows
        check(data["list"], "学习计划分页提前结束")
        page += 1


class PlanFixture:
    def __init__(self, client, tokens, user_ids):
        self.client, self.tokens, self.user_ids = client, tokens, user_ids
        self.marker = "P3PLAN-" + uuid.uuid4().hex
        self.before = {name: {row["id"] for row in plans(client, token)}
                       for name, token in tokens.items()}
        self.contents = set()
        self.ids = {name: set() for name in tokens}

    def remember_calls(self, result):
        for call in result.get("tool_calls", []):
            if call.get("name") == "save_study_plan":
                content = call.get("args", {}).get("content")
                if isinstance(content, str):
                    self.contents.add(content.strip())

    def new_rows(self, name):
        return [row for row in plans(self.client, self.tokens[name])
                if row["id"] not in self.before[name]]

    def owns(self, row):
        content = row.get("content", "")
        return self.marker in content or content.strip() in self.contents

    def assert_owner(self, row, name):
        # 先由业务接口证实当前学生可读，另一学生不可读。
        check(any(item["id"] == row["id"] for item in plans(self.client, self.tokens[name])),
              "当前登录学生未能通过业务接口读到自己的计划")
        for other, token in self.tokens.items():
            if other != name:
                check(not any(item["id"] == row["id"] for item in plans(self.client, token)),
                      "其他学生能读到当前学生的计划")
        # 响应既有 schema 不暴露 user_id；这里只读归属，绝不裸 SQL 造数。
        with SessionLocal() as db:
            actual = db.query(StudyPlan.user_id).filter(StudyPlan.id == row["id"]).scalar()
        check(actual == self.user_ids[name], "学习计划数据库 user_id 不属于当前登录学生")
        self.ids[name].add(row["id"])

    def cleanup(self):
        failures = []
        for name, token in self.tokens.items():
            try:
                for row in self.new_rows(name):
                    if self.owns(row):
                        self.ids[name].add(row["id"])
                for plan_id in sorted(self.ids[name]):
                    success(self.client, "DELETE", f"/api/study-plans/{plan_id}",
                            headers=headers(token))
                check(not any(self.owns(row) or row["id"] in self.ids[name]
                              for row in self.new_rows(name)), "临时学习计划未清理完")
            except Exception as exc:
                failures.append(str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__)
        check(not failures, "学习计划清理失败：" + "; ".join(failures))
        passed("临时学习计划已通过本人 DELETE 接口删除并回查")


class AttackModel(BaseChatModel):
    """真实 Agent 图中的确定性攻击模型，工具 I/O 仍为真实 MCP HTTP。"""

    arguments: list[dict]
    calls: int = 0
    _bound: list = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self):
        return "p3-manual-attack-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self._bound = [tool.name for tool in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        args = self.arguments[min(self.calls, len(self.arguments) - 1)]
        self.calls += 1
        answer = AIMessage(content="", tool_calls=[{
            "name": "save_study_plan", "args": args,
            "id": uuid.uuid4().hex, "type": "tool_call",
        }])
        return ChatResult(generations=[ChatGeneration(message=answer)])


async def require_guard_failure(token, model, expected_code):
    try:
        await run_agent("Bearer " + token, "帮我生成学习计划并保存。",
                        model_factory=lambda config: model)
    except BusinessException as exc:
        check(exc.code == expected_code, f"防护业务码应为 {expected_code}，实际为 {exc.code}")
        if expected_code == 409:
            check("最多成功保存一次" in exc.message, "重复保存错误提示不明确")
        return
    raise CheckFailed("攻击工具调用未被 Agent 代码防护拒绝")


async def malicious_mcp(token, content, other_id):
    transport = StreamableHttpTransport(MCP, headers=headers(token))
    async with Client(transport) as client:
        available = {tool.name: tool for tool in await client.list_tools()}
        check("save_study_plan" in available, "MCP 未提供 save_study_plan")
        schema = available["save_study_plan"].inputSchema
        check(set(schema.get("properties", {})) == {"content"},
              "保存工具签名暴露了正文之外的参数")
        result = await client.call_tool("save_study_plan",
                                       {"content": content, "user_id": other_id},
                                       raise_on_error=False)
        structured = result.structured_content or {}
        errors = [structured.get("error")]
        for block in result.content:
            try:
                payload = json.loads(getattr(block, "text", ""))
            except ValueError:
                continue
            if isinstance(payload, dict):
                errors.append(payload.get("error"))
        check(result.is_error or any(errors), "MCP 未拒绝模型可控 user_id")


def agent_http(client, token, message, history=None):
    result = success(client, "POST", "/api/agent/chat", headers=headers(token),
                     json={"message": message, "history": history or []})
    check(isinstance(result, dict) and isinstance(result.get("answer"), str)
          and result["answer"].strip() and isinstance(result.get("tool_calls"), list),
          "Agent 回答或工具执行记录格式错误")
    for call in result["tool_calls"]:
        name = call.get("name")
        allowed = {"date"} if name == "get_my_schedules" else {"content"}
        check(name in {"get_my_schedules", "save_study_plan"}
              and isinstance(call.get("args"), dict) and set(call["args"]) <= allowed,
              "Agent 调用了未授权工具或模型可控身份参数")
    print("最终回答：\n" + result["answer"], flush=True)
    print("实际工具：" + json.dumps(result["tool_calls"], ensure_ascii=False), flush=True)
    return result


def timed_stream(client, token, marker):
    contract = SSEContract()
    timeline, frame = [], []
    started = time.perf_counter()
    message = ("我明天有什么课？请查询真实课表后，结合课程详细说明明天的学习安排。"
               "回答至少250字，按早晨、课前、课后、晚间分段，给出具体准备方法和复习建议。")
    with client.stream("POST", "/api/agent/chat/stream", headers=headers(token),
                       json={"message": message, "history": []}) as response:
        check(response.status_code == 200
              and response.headers.get("content-type", "").startswith("text/event-stream"),
              "流开始前被拒绝，请检查已运行后端的 Key 与登录配置")
        for line in response.iter_lines():
            if line:
                frame.append(line)
                continue
            before = len(contract.events)
            contract.frame(frame)
            frame = []
            if len(contract.events) != before:
                elapsed = time.perf_counter() - started
                name, data = contract.events[-1]
                timeline.append((name, elapsed))
                print(f"[SSE +{elapsed:.3f}s] event: {name}", flush=True)
                print("data: " + json.dumps(data, ensure_ascii=False), flush=True)
                if name in {"done", "error"}:
                    break
    check(not frame, "SSE 最后一帧不完整")
    contract.finish()
    check(contract.terminal == "done", "真流式请求未正常完成")
    check(any(call["name"] == "get_my_schedules"
              and call["args"].get("date") == (shanghai_today() + timedelta(days=1)).isoformat()
              for call in contract.calls), "真流式未实际查询系统给定的明天课表")
    check(marker in contract.events[-1][1]["answer"], "真流式回答未包含真实临时课程")
    tokens = [at for name, at in timeline if name == "token"]
    finished = next(at for name, at in timeline if name == "done")
    check(len(tokens) >= 2, "未收到多个原生 token 帧，不能证明渐进推送")
    span = finished - tokens[0]
    check(span >= 0.1, f"首 token 到 done 仅 {span:.3f}s，仍疑似批量推送")
    passed(f"真流式时间轴：首 token +{tokens[0]:.3f}s；末 token +{tokens[-1]:.3f}s；"
           f"done +{finished:.3f}s；首 token→done {span:.3f}s；共 {len(tokens)} 个 token 帧")
    return contract


def main():
    if not settings.LLM_API_KEY.get_secret_value().strip():
        print("[FAIL] 未配置 LLM_API_KEY；未启动 MCP、未调用模型", file=sys.stderr)
        return 1
    signal.signal(signal.SIGTERM, interrupted)
    process = schedule_fixture = plan_fixture = None
    failures = []
    timeout = (settings.LLM_TIMEOUT_SECONDS + 10) * settings.AGENT_MAX_ITERATIONS + 30
    with tempfile.TemporaryFile() as log, httpx.Client(
            base_url=API, trust_env=False, timeout=timeout) as client:
        try:
            try:
                response = client.get("/openapi.json", timeout=5)
            except httpx.RequestError:
                raise CheckFailed("FastAPI 未运行，请先启动：\n" + START_API) from None
            check(response.status_code == 200
                  and "/api/agent/chat/stream" in response.json().get("paths", {}),
                  "已有 FastAPI 尚未提供 Agent SSE 接口，请先重载后端")
            process = start_mcp(log)
            wait_mcp(process)
            admin, _ = login(client, "admin", "admin")
            aaa, aaa_id = login(client, "aaa", "student")
            bbb, bbb_id = login(client, "bbb", "student")
            tokens = {"aaa": aaa, "bbb": bbb}
            users = {"aaa": aaa_id, "bbb": bbb_id}
            plan_fixture = PlanFixture(client, tokens, users)
            schedule_fixture = Fixture(client, admin, users)
            schedule_fixture.create(shanghai_today() + timedelta(days=1))
            passed("复用 8000，真实登录三账号，临时课表通过业务接口创建")

            planning = agent_http(client, aaa, "帮我规划一下明天的学习安排，请结合我的明天课表。")
            plan_fixture.remember_calls(planning)
            check(not any(call["name"] == "save_study_plan" for call in planning["tool_calls"]),
                  "仅要求规划时仍调用了保存工具")
            check(not plan_fixture.new_rows("aaa"), "仅规划时数据库新增了学习计划")
            passed("真实模型只规划时未调用保存工具，数据库未新增计划")

            saving = agent_http(
                client, aaa, "帮我生成一份学习计划并保存，请结合我明天的真实课程。"
                "正文不超过1200字，必须原样包含验证标记：" + plan_fixture.marker)
            plan_fixture.remember_calls(saving)
            saves = [call for call in saving["tool_calls"] if call["name"] == "save_study_plan"]
            check(len(saves) == 1, "明确要求保存应成功调用且只调用一次保存工具")
            created = plan_fixture.new_rows("aaa")
            check(len(created) == 1 and plan_fixture.marker in created[0]["content"],
                  "GET /api/study-plans 未回查到唯一标记计划")
            plan_fixture.assert_owner(created[0], "aaa")
            passed(f"真实模型保存成功，GET 回查 id={created[0]['id']}，user_id 属于 aaa；bbb 不可见")

            baseline = {name: {row["id"] for row in plans(client, token)}
                        for name, token in tokens.items()}
            malicious = plan_fixture.marker + " MCP 非法身份参数验证"
            asyncio.run(malicious_mcp(aaa, malicious, bbb_id))
            model = AttackModel(arguments=[{"content": malicious, "user_id": bbb_id}])
            asyncio.run(require_guard_failure(aaa, model, 400))
            check(all({row["id"] for row in plans(client, token)} == baseline[name]
                      for name, token in tokens.items()), "非法身份参数导致了学习计划落库")
            passed("真实 MCP 与 Agent 两层拒绝 user_id，aaa/bbb 均无新增计划")

            duplicate_content = plan_fixture.marker + " 重复保存验证：课前预习，课后复习，晚间总结。"
            plan_fixture.contents.add(duplicate_content)
            model = AttackModel(arguments=[{"content": duplicate_content}, {"content": duplicate_content}])
            asyncio.run(require_guard_failure(aaa, model, 409))
            duplicate_rows = [row for row in plans(client, aaa) if row["id"] not in baseline["aaa"]]
            check(model.calls == 2 and len(duplicate_rows) == 1
                  and duplicate_rows[0]["content"] == duplicate_content,
                  "重复保存防护未在第二次执行前阻止，或落库数量不是一条")
            plan_fixture.assert_owner(duplicate_rows[0], "aaa")
            passed("可控模型同轮两次保存：首次真实 MCP 落库，第二次 409，数据库仅新增一条")

            timed_stream(client, aaa, schedule_fixture.marker)
        except KeyboardInterrupt:
            failures.append("运行被中断")
        except (CheckFailed, AssertionError, BusinessException) as exc:
            failures.append(str(exc))
        except Exception as exc:
            failures.append("P3 冒烟异常：" + type(exc).__name__)
        finally:
            try:
                if plan_fixture is not None:
                    plan_fixture.cleanup()
            except BaseException as exc:
                reason = str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__
                failures.append(f"临时计划清理失败（标记 {plan_fixture.marker}）：{reason}")
            try:
                if schedule_fixture is not None:
                    schedule_fixture.cleanup()
            except BaseException as exc:
                reason = str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__
                failures.append(f"临时课表清理失败（标记 {schedule_fixture.marker}）：{reason}")
            finally:
                try:
                    stop_mcp(process)
                except BaseException as exc:
                    failures.append("MCP 关闭失败：" + type(exc).__name__)
    for failure in failures:
        print("[FAIL] " + failure, file=sys.stderr)
    if not failures:
        passed("P3 学习计划、越权/重复保存及真流式时间轴全部通过（退出码 0）")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
