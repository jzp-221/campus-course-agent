"""P7 真实 Agent SSE 三问；只读现有演示资料，不上传/重建/删除资产。

在 backend 运行 .venv/bin/python tests/manual/p7_quality_smoke.py。
复用已有 FastAPI 8000，仅启动本脚本 MCP 8001，退出时自动关闭。
需要已配置 LLM/embedding Key 和已重新入库的演示文档17/18/19。
"""

from pathlib import Path
import re
import signal
import sys
import tempfile
import time

import httpx

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from e2e_mcp import (  # noqa: E402
    API, START_API, CheckFailed, check, headers, interrupted, login, passed,
    start_mcp, stop_mcp, success, wait_mcp,
)
from sse_contract import SSEContract  # noqa: E402

PROBES = [
    ("实验课前需要预习吗？", 17, "数据结构实验教学规定.md", "preview"),
    ("考试可以带计算器吗？", 18, "高等数学学习与考核说明.md", "calculator"),
    ("英语作文可以交打印稿吗？", 19, "大学英语学习要求.md", "handwriting"),
]


def reject_item(text, item):
    """匹配否定句，容忍 Markdown 强调及模型自然措辞。"""
    text = re.sub(r"[*_`]", "", text)
    negative = r"(?:不允许|不能|不可以|禁止|不得|不接受|不准)"
    return bool(re.search(negative + r"[^\n。；！？]{0,30}" + item, text)
                or re.search(item + r"[^\n。；！？]{0,20}" + negative, text))


def assert_answer(answer, filename, kind):
    check(filename in answer or Path(filename).stem in answer,
          f"回答未引用正确来源：{filename}")
    if kind == "preview":
        check("预习报告" in answer and re.search(r"必须|需要|要完成|应完成|须完成", answer)
              and not re.search(r"(?:不需要|无需|不用)[^\n。；！？]{0,12}(?:预习|预习报告)", answer),
              "预习回答与原文不一致")
    elif kind == "calculator":
        check(reject_item(answer, "计算器"), "未明确说明考试禁止携带计算器")
    else:
        check("手写" in answer and reject_item(answer, "打印稿"),
              "未明确说明英语作文必须手写、不接受打印稿")


def stream_question(client, token, question):
    contract = SSEContract()
    pending = []
    started = time.perf_counter()
    first_token = last_token = finished = None
    with client.stream("POST", "/api/agent/chat/stream",
                       headers={**headers(token), "Accept": "text/event-stream"},
                       json={"message": question, "history": []}) as response:
        check(response.status_code == 200
              and response.headers.get("content-type", "").startswith("text/event-stream"),
              "流开始前被拒绝，请检查后端配置/登录状态")
        for line in response.iter_lines():
            if line:
                pending.append(line)
                continue
            before = len(contract.events)
            contract.frame(pending)
            pending = []
            if len(contract.events) == before:
                continue
            name, _ = contract.events[-1]
            elapsed = time.perf_counter() - started
            if name == "token":
                first_token = elapsed if first_token is None else first_token
                last_token = elapsed
            if name in {"done", "error"}:
                finished = elapsed
                break
    check(not pending, "SSE 最后一帧不完整")
    contract.finish()
    check(contract.terminal == "done", "资料问答 SSE 未以 done 正常结束")
    check(contract.calls and all(call["name"] == "search_course_docs" for call in contract.calls),
          "资料问答必须实际检索，且不能调用写入工具")
    check(first_token is not None, "未收到 SSE token")
    answer = contract.events[-1][1]["answer"]
    print("最终回答：\n" + answer, flush=True)
    print("实际工具：" + repr(contract.calls), flush=True)
    print(f"[SSE 时间轴] 首token +{first_token:.3f}s；末token +{last_token:.3f}s；"
          f"done +{finished:.3f}s；首token→done {finished - first_token:.3f}s", flush=True)
    return answer


def main():
    if not settings.LLM_API_KEY.get_secret_value().strip() or not settings.EMBEDDING_API_KEY.get_secret_value().strip():
        print("[FAIL] 请配置 LLM_API_KEY 与 EMBEDDING_API_KEY；未启动 MCP、未调用模型", file=sys.stderr)
        return 1
    signal.signal(signal.SIGTERM, interrupted)
    process = None
    failures = []
    timeout = (settings.LLM_TIMEOUT_SECONDS + 10) * settings.AGENT_MAX_ITERATIONS + 30
    with tempfile.TemporaryFile() as log, httpx.Client(base_url=API, trust_env=False, timeout=timeout) as client:
        try:
            try:
                response = client.get("/openapi.json", timeout=5)
            except httpx.RequestError:
                raise CheckFailed("FastAPI 未运行，请先启动：\n" + START_API) from None
            check(response.status_code == 200
                  and "/api/agent/chat/stream" in response.json().get("paths", {}),
                  "已有 FastAPI 未提供 SSE 接口，请先重载后端")
            admin, _ = login(client, "admin", "admin")
            token, _ = login(client, "aaa", "student")
            for _, document_id, filename, _ in PROBES:
                document = success(client, "GET", f"/api/documents/{document_id}/preview",
                                   headers=headers(admin))
                check(isinstance(document, dict) and document.get("status") == "indexed"
                      and document.get("filename") == filename,
                      f"演示资产{document_id}未就绪或文件名变化；脚本不会重新上传或删除")
            process = start_mcp(log)
            wait_mcp(process)
            for question, _, filename, kind in PROBES:
                print("\n=== " + question + " ===", flush=True)
                answer = stream_question(client, token, question)
                assert_answer(answer, filename, kind)
                passed("来源与答案均符合原文：" + filename)
        except KeyboardInterrupt:
            failures.append("运行被中断")
        except (CheckFailed, AssertionError) as error:
            failures.append(str(error))
        except Exception as error:
            failures.append("P7 资料问答冒烟异常：" + type(error).__name__)
        finally:
            try:
                stop_mcp(process)
            except BaseException as error:
                failures.append("MCP 关闭失败：" + type(error).__name__)
    for failure in failures:
        print("[FAIL] " + failure, file=sys.stderr)
    if not failures:
        passed("P7 SSE三问：实际检索、正确来源与原文一致，演示资产未改动（退出码0）")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
