"""P12 真实 SSE 三问：只读课程知识库，不修改演示资产或配置。

在 backend 运行：
    .venv/bin/python tests/manual/p12_quality_smoke.py
    .venv/bin/python tests/manual/p12_quality_smoke.py --output /tmp/p12-sse-report.json

沿用 P7 的 SSEContract 和 MCP 启停逻辑：复用已运行的 FastAPI 8000，
8001 必须空闲，只关闭自己启动的 MCP。SSE 仅提供工具名/参数，不包含
工具结果正文，因此再经真实 MCP 复查本轮实际 query 的片段及来源，
报告明确区分实际 SSE 事件与复查片段，不冒充捕获了 SSE 工具输出。
"""

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import re
import signal
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

import httpx

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from e2e_mcp import (  # noqa: E402
    API, START_API, CheckFailed, check, headers, interrupted, login, passed,
    start_mcp, stop_mcp, success, wait_mcp,
)
from p7_quality_smoke import reject_item  # noqa: E402
from rag_smoke import assert_search_result, mcp_search  # noqa: E402
from sse_contract import SSEContract  # noqa: E402


PROBES = [
    ("作业晚交了会怎么样？", 18, "高等数学学习与考核说明.md", "late_homework"),
    ("写作文可以用电脑打字打印出来交吗？", 19, "大学英语学习要求.md", "handwriting"),
    ("学校食堂几点开门？", None, None, "no_hits"),
]
DEMO_DOCUMENTS = {
    17: "数据结构实验教学规定.md",
    18: "高等数学学习与考核说明.md",
    19: "大学英语学习要求.md",
    20: "学生课程事务办理指南.md",
}


def timestamp():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def plain_text(text):
    return re.sub(r"\s+", "", re.sub(r"[*_`]", "", text))


def assert_answer(answer, filename, kind):
    """核对正面答案与来源；不把提到关键词但否认事实的回答当通过。"""
    if kind == "no_hits":
        check("知识库中没有检索到相关内容" in answer,
              "食堂问题未明确答出知识库中没有检索到相关内容")
        check(not any(name in answer or Path(name).stem in answer
                      for name in DEMO_DOCUMENTS.values()),
              "食堂无命中却引用了课程文档")
        return
    check(filename in answer or Path(filename).stem in answer,
          f"回答未引用正确来源：{filename}")
    text = plain_text(answer)
    if kind == "late_homework":
        check(bool(re.search(r"(?:扣|扣除)(?:两|2)分", text))
              and not bool(re.search(r"(?:不扣|不扣除|不会扣)(?:两|2)分", text)),
              "晚交作业回答未明确说明扣 2 分")
    elif kind == "handwriting":
        check(bool(re.search(r"(?:必须|须|要求|应)[^。；！？\n]{0,12}手写", text))
              and reject_item(answer, "(?:打印稿|打印|电脑打字)"),
              "英语作文回答未明确说明必须手写、不能打印提交")
        check(not bool(re.search(r"(?:不需要|无需|不用)[^。；！？\n]{0,12}手写", text)),
              "英语作文回答错误地否定手写要求")


def stream_question(client, token, question):
    """沿用 P7 帧解析/时序校验，并保留实际调用及完整回答作报告。"""
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
    if contract.terminal == "error":
        error = contract.events[-1][1]
        raise CheckFailed(f"资料问答 SSE error：业务码 {error['code']}，{error['message']}")
    check(contract.terminal == "done", "资料问答 SSE 未以 done 正常结束")
    check(contract.calls and all(call["name"] == "search_course_docs"
                                for call in contract.calls),
          "资料问答必须实际检索，且不能调用写入工具")
    check(all(call["args"]["query"] == question.strip() for call in contract.calls),
          "检索 query 必须使用原问题，不能用模型改写后的问题冒充原探针通过")
    check(first_token is not None, "未收到 SSE token")
    answer = contract.events[-1][1]["answer"]
    print("最终回答：\n" + answer, flush=True)
    print("本轮 SSE 实际工具：" + json.dumps(contract.calls, ensure_ascii=False), flush=True)
    print(f"[SSE 时间轴] 首token +{first_token:.3f}s；末token +{last_token:.3f}s；"
          f"done +{finished:.3f}s；首token→done {finished - first_token:.3f}s", flush=True)
    return {
        "question": question, "answer": answer, "tool_calls": contract.calls,
        "sse_events": [{"event": name, "data": data} for name, data in contract.events],
        "timing_seconds": {
            "first_token": first_token, "last_token": last_token, "done": finished,
            "first_token_to_done": finished - first_token,
        },
    }


def verify_sources(token, result, document_id, filename, kind):
    """SSE 参数可观测，正文不可观测：用实际 query 复查真实 HTTP MCP。"""
    checks = []
    for call in result["tool_calls"]:
        data = asyncio.run(mcp_search(token, call["args"]["query"]))
        assert_search_result(data)
        checks.append({"query": call["args"]["query"], **data})
    result["mcp_source_rechecks"] = checks
    result["source_evidence_method"] = "真实 MCP 复查 SSE 已记录的实际 query；SSE 不含工具正文"
    snippets = [item for data in checks for item in data["items"]]
    sources = sorted({item["source"] for item in snippets})
    result["sources"] = sources
    print("实际 query 的 MCP 复查来源：" + (" / ".join(sources) if sources else "无命中"), flush=True)
    if kind == "no_hits":
        check(not snippets, "食堂问题的实际查询仍检索到了无关片段")
    else:
        wanted = [item for item in snippets
                  if item.get("document_id") == document_id and item.get("source") == filename]
        fact = "扣2分" if kind == "late_homework" else "不接受打印稿"
        check(any(fact in plain_text(item["text"]) for item in wanted),
              f"实际 query 未召回正确来源的答案事实：{filename} / {fact}")
        for item in wanted:
            print(f"  [来源证据] {item['source']}，块 {item['chunk_index']}，"
                  f"距离 {item['distance']:.6f}\n{item['text']}", flush=True)
    assert_answer(result["answer"], filename, kind)
    result["passed"] = True


def demo_snapshot(client, admin_token):
    documents = []
    for document_id, filename in DEMO_DOCUMENTS.items():
        row = success(client, "GET", f"/api/documents/{document_id}/preview",
                      headers=headers(admin_token))
        check(isinstance(row, dict) and row.get("filename") == filename
              and row.get("status") == "indexed" and row.get("chunk_count") == 7,
              f"演示资产 {document_id} 未就绪；脚本不会上传、重建或删除")
        documents.append(row)
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="可选：保存无 JWT/Key 的 JSON 验收记录")
    args = parser.parse_args()
    if (not settings.LLM_API_KEY.get_secret_value().strip()
            or not settings.EMBEDDING_API_KEY.get_secret_value().strip()):
        print("[FAIL] 请配置 LLM_API_KEY 与 EMBEDDING_API_KEY；未启动 MCP、未调用模型", file=sys.stderr)
        return 1
    signal.signal(signal.SIGTERM, interrupted)
    process = None
    failures = []
    baseline = None
    admin_token = None
    report = {"started_at": timestamp(), "probes": [], "errors": failures}
    timeout = (settings.LLM_TIMEOUT_SECONDS + 10) * settings.AGENT_MAX_ITERATIONS + 30
    with tempfile.TemporaryFile() as log, httpx.Client(base_url=API, trust_env=False,
                                                      timeout=timeout) as client:
        try:
            try:
                response = client.get("/openapi.json", timeout=5)
            except httpx.RequestError:
                raise CheckFailed("FastAPI 未运行，请先启动：\n" + START_API) from None
            check(response.status_code == 200
                  and "/api/agent/chat/stream" in response.json().get("paths", {}),
                  "已有 FastAPI 未提供 SSE 接口，请先重载后端")
            admin_token, _ = login(client, "admin", "admin")
            token, _ = login(client, "aaa", "student")
            baseline = demo_snapshot(client, admin_token)
            process = start_mcp(log)
            wait_mcp(process)
            for question, document_id, filename, kind in PROBES:
                print("\n=== " + question + " ===", flush=True)
                result = stream_question(client, token, question)
                report["probes"].append(result)
                verify_sources(token, result, document_id, filename, kind)
                passed("SSE 回答与实际查询来源均正确：" + (filename or "食堂无命中"))
        except KeyboardInterrupt:
            failures.append("运行被中断")
        except (CheckFailed, AssertionError) as error:
            failures.append(str(error))
        except Exception as error:
            failures.append("P12 资料问答冒烟异常：" + type(error).__name__)
        finally:
            try:
                if baseline is not None:
                    check(demo_snapshot(client, admin_token) == baseline,
                          "4 份演示文档的正文或元信息发生变化")
                    report["demo_documents_unchanged"] = True
            except (Exception, KeyboardInterrupt) as error:
                failures.append("演示资产核对失败：" + (str(error) if isinstance(error, CheckFailed)
                                                   else type(error).__name__))
            finally:
                try:
                    stop_mcp(process)
                except BaseException as error:
                    failures.append("MCP 关闭失败：" + type(error).__name__)
    report["completed_at"] = timestamp()
    report["exit_code"] = 1 if failures else 0
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"验收记录：{args.output}", flush=True)
    for failure in failures:
        print("[FAIL] " + failure, file=sys.stderr)
    if not failures:
        passed("P12 SSE三问：晚交扣2分 / 作文必须手写 / 食堂无命中，正确来源且资产未改动（退出码0）")
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
