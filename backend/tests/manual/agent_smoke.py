"""真实 DeepSeek + ReAct + Streamable HTTP MCP 冒烟，临时数据自动清理。"""

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import signal
import sys
import tempfile

import httpx

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.common.exceptions import BusinessException
from app.config import settings
from app.services.agent_service import run_agent, shanghai_today
from e2e_mcp import (
    API, START_API, CheckFailed, Fixture, check, interrupted, login,
    passed, start_mcp, stop_mcp, wait_mcp,
)


def main():
    # 先检查 Key；缺失时，不登录、不造数据、不启动或接触 MCP。
    if not settings.LLM_API_KEY.get_secret_value().strip():
        print("[FAIL] 未配置 LLM_API_KEY，请自行填入 backend/.env；脚本不会调用 MCP", file=sys.stderr)
        return 1
    process = None
    fixture = None
    failures = []
    signal.signal(signal.SIGTERM, interrupted)
    with tempfile.TemporaryFile() as log, httpx.Client(base_url=API, trust_env=False, timeout=25) as client:
        try:
            try:
                response = client.get("/openapi.json", timeout=5)
            except httpx.RequestError:
                raise CheckFailed("FastAPI 未运行，请先启动：\n" + START_API) from None
            check(response.status_code == 200, "FastAPI 8000 未就绪")
            process = start_mcp(log)
            wait_mcp(process)
            passed("真实 MCP Streamable HTTP 已就绪")
            admin_token, _ = login(client, "admin", "admin")
            aaa_token, aaa_id = login(client, "aaa", "student")
            _, bbb_id = login(client, "bbb", "student")
            today = shanghai_today()
            tomorrow = today + timedelta(days=1)
            fixture = Fixture(client, admin_token, {"aaa": aaa_id, "bbb": bbb_id})
            fixture.create(tomorrow)
            # 使用新进程读取的 .env 配置直接调用 Agent 服务。Agent 的工具仍只走 HTTP MCP。
            # 这样填写 .env 后可立即实测，不依赖另一个 FastAPI 进程是否已重新加载 LLM Key。
            result = asyncio.run(run_agent("Bearer " + aaa_token, "我明天有什么课？",
                                          today_provider=lambda: today))
            print("最终回答：", flush=True)
            print(result["answer"], flush=True)
            print("本轮实际执行的工具调用：", flush=True)
            print(json.dumps(result["tool_calls"], ensure_ascii=False, indent=2), flush=True)
            check(result["tool_calls"], "模型没有实际调用工具，不能算通过")
            for call in result["tool_calls"]:
                check(call["name"] == "get_my_schedules", "出现未授权的工具")
                check(set(call["args"]) <= {"date"}, "工具参数出现 user_id 或其他多余参数")
            check(any(call["args"].get("date") == tomorrow.isoformat()
                      for call in result["tool_calls"]), "未使用系统给出的明天日期查询课表")
            check(fixture.marker in result["answer"], "最终回答未包含明天的真实临时课程名")
            passed("DeepSeek 实际调用 get_my_schedules；日期正确、无 user_id，回答包含真实课程")
        except KeyboardInterrupt:
            failures.append("运行被中断")
        except (CheckFailed, BusinessException) as exc:
            failures.append(str(exc))
        except Exception as exc:
            failures.append(f"冒烟异常：{type(exc).__name__}")
        finally:
            try:
                if fixture is not None:
                    fixture.cleanup()
            except BaseException as exc:
                reason = str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__
                failures.append(f"临时数据清理失败（标记 {fixture.marker}）：{reason}")
            finally:
                try:
                    stop_mcp(process)
                except BaseException as exc:
                    failures.append(f"MCP 关闭失败：{type(exc).__name__}")
    if failures:
        for failure in failures:
            print("[FAIL] " + failure, file=sys.stderr)
        return 1
    passed("Agent 真实冒烟全部通过（退出码 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
