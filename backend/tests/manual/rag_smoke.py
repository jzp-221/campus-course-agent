"""P4 真实资料入库 / Streamable HTTP MCP / Agent 验证，临时文档自动清理。

复用已运行的 FastAPI 8000；只启动、停止自己的 MCP 8001，不修改 .env。
完整模式需要 LLM_API_KEY 与 EMBEDDING_API_KEY；--missing-key-only 仅需 LLM Key。
故意缺 Key 的验证通过子进程环境覆盖，绝不改配置文件或复用其它 MCP 进程。
"""

import argparse
import asyncio
from contextlib import contextmanager
from io import BytesIO
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import uuid

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pypdf import PdfWriter

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.common.exceptions import BusinessException
from app.config import DOCUMENT_DIR, settings
from e2e_mcp import (
    API, MCP, START_API, CheckFailed, check, headers, interrupted, login,
    passed, request, start_mcp, stop_mcp, success, wait_mcp,
)


# The shared demo knowledge base also contains attendance/leave rules. Use a
# distinct fictional practicum so zero hits after deletion still tests cleanup,
# rather than depending on unrelated existing course rules missing the threshold.
HIT_QUERY = "月球岩芯封存实训中，氦气检漏失败三次后，应在几个工作日内提交复检申请？"
MISS_QUERY = "课程知识库对于南极海底火山在2187年喷发后的鲸鱼迁徙航线有什么规定？"


def document_rows(client, token):
    rows, page = [], 1
    while True:
        data = success(client, "GET", "/api/documents", headers=headers(token),
                       params={"page": page, "page_size": 100})
        check(isinstance(data, dict) and isinstance(data.get("list"), list)
              and type(data.get("total")) is int, "文档分页响应格式错误")
        rows.extend(data["list"])
        if len(rows) >= data["total"]:
            return rows
        check(data["list"], "文档分页提前结束")
        page += 1


def disk_files():
    directory = Path(DOCUMENT_DIR)
    return {path.relative_to(directory) for path in directory.rglob("*") if path.is_file()}


def vector_count():
    from app.services.rag_service import vector_count as count

    return count()


def vector_document_count(document_id):
    from app.services.rag_service import vector_document_count as count

    return count(document_id)


async def mcp_search(token, query):
    transport = StreamableHttpTransport(MCP, headers=headers(token) if token else {})
    async with Client(transport) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        check("search_course_docs" in tools, "MCP 未提供 search_course_docs")
        schema = tools["search_course_docs"].inputSchema
        check(set(schema.get("properties", {})) == {"query"},
              "检索工具签名暴露了 query 之外的参数")
        check(schema.get("additionalProperties") is False,
              "检索工具未明确禁止额外参数")
        result = await client.call_tool("search_course_docs", {"query": query},
                                       raise_on_error=False)
        data = result.structured_content
        if not isinstance(data, dict):
            for block in result.content:
                try:
                    candidate = json.loads(getattr(block, "text", ""))
                except ValueError:
                    continue
                if isinstance(candidate, dict):
                    data = candidate
                    break
        check(isinstance(data, dict), "MCP 检索结果不是 JSON 对象")
        return data


def assert_search_result(data):
    check("error" not in data, "正常检索返回了检索失败，不能当作无命中")
    check(set(data) == {"items", "count"} and isinstance(data["items"], list)
          and type(data["count"]) is int and data["count"] == len(data["items"])
          and 0 <= data["count"] <= 3, "MCP 检索 items/count 格式不正确")
    for item in data["items"]:
        check(isinstance(item, dict) and isinstance(item.get("text"), str)
              and item["text"].strip() and isinstance(item.get("source"), str)
              and item["source"].strip(), "检索结果出现无正文或无来源片段")


def agent_answer(token, query):
    # Exercise FastAPI authentication/response handling as well as the real
    # DeepSeek -> Streamable-HTTP MCP -> embedding/Chroma chain.
    with httpx.Client(base_url=API, trust_env=False, timeout=90) as client:
        status, body = request(client, "POST", "/api/agent/chat", headers=headers(token),
                               json={"message": query, "history": []})
    if body.get("code") != 200:
        raise BusinessException(body.get("message", "助手调用失败"), code=body.get("code", status))
    result = body.get("data")
    check(isinstance(result, dict) and isinstance(result.get("answer"), str)
          and result["answer"].strip() and isinstance(result.get("tool_calls"), list),
          "Agent 未返回有效回答和工具执行记录")
    calls = result["tool_calls"]
    check(any(call.get("name") == "search_course_docs" for call in calls),
          "资料问题没有实际调用 search_course_docs")
    check(all(call.get("name") == "search_course_docs"
              and isinstance(call.get("args"), dict)
              and set(call["args"]) == {"query"} for call in calls),
          "资料冒烟中执行了不相关工具或额外参数")
    print("助手回答：\n" + result["answer"], flush=True)
    return result["answer"]


@contextmanager
def missing_embedding_environment():
    """只影响 start_mcp 派生的子进程；空字符串显式覆盖该子进程的 .env。"""
    previous = os.environ.get("EMBEDDING_API_KEY")
    os.environ["EMBEDDING_API_KEY"] = ""
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("EMBEDDING_API_KEY", None)
        else:
            os.environ["EMBEDDING_API_KEY"] = previous


def verify_missing_key(token):
    data = asyncio.run(mcp_search(token, HIT_QUERY))
    check(isinstance(data.get("error"), str) and "检索失败" in data["error"]
          and "EMBEDDING_API_KEY" in data["error"] and "items" not in data,
          "缺少 embedding Key 未明确失败，或伪装成了空检索结果")
    try:
        answer = agent_answer(token, "请查课程资料并说明：" + HIT_QUERY)
    except BusinessException as exc:
        # 失败响应是助手接口的业务错误；不得伪装成成功的空结果。
        check("当前无法查询资料" in exc.message and "检索失败" in exc.message
              and "没有检索到" not in exc.message, "Agent 未保留无法查询资料的失败语义")
        print("助手业务错误：当前无法查询资料（检索失败：未配置 EMBEDDING_API_KEY）", flush=True)
    else:
        check("无法查询资料" in answer and "没有检索到" not in answer,
              "Agent 把缺 Key 错误说成了知识库无相关内容")
    passed("缺 Key 的真实 MCP 明确检索失败；助手明确无法查询资料，并非没有相关内容")


class DocumentFixture:
    def __init__(self, client, admin_token):
        self.client = client
        self.token = admin_token
        self.marker = "P4DOC-" + uuid.uuid4().hex
        self.filename = self.marker + "-实训课程规则.txt"
        self.bad_filename = self.marker + "-扫描版.pdf"
        self.batch_filename = self.marker + "-批量回滚规则.txt"
        self.ids = set()
        self.deleted_ids = set()
        self.text = (
            f"课程知识库真实验收文档（{self.marker}）。\n"
            "本资料为虚构测试规定，不描述真实课程或科学操作。\n"
            "月球岩芯封存实训中，氦气检漏失败达到三次时，必须在五个工作日内提交复检申请，"
            "导师批准后安排复检。逾期未申请者不安排复检。\n"
            "本资料仅规定该虚构实训的复检申请期限。"
        )

    def upload(self):
        data = success(self.client, "POST", "/api/documents/upload", headers=headers(self.token),
                       files=[("files", (self.filename, self.text.encode("utf-8"), "text/plain"))])
        check(isinstance(data, list) and len(data) == 1 and type(data[0].get("id")) is int,
              "上传文档未返回单条 DocumentResponse")
        row = data[0]
        self.ids.add(row["id"])
        check(row.get("filename") == self.filename and row.get("status") == "indexed"
              and type(row.get("chunk_count")) is int and row["chunk_count"] >= 1,
              "文档上传成功但未完成入库")
        preview = success(self.client, "GET", f"/api/documents/{row['id']}/preview",
                          headers=headers(self.token))
        check(isinstance(preview, dict) and preview.get("extracted_text") == self.text,
              "文档预览与抽取文本不一致")
        return row["id"]

    def verify_hit(self, token, document_id):
        data = asyncio.run(mcp_search(token, HIT_QUERY))
        assert_search_result(data)
        matches = [item for item in data["items"] if item.get("document_id") == document_id]
        check(matches and all(item["source"] == self.filename for item in matches),
              "MCP 未检索到本次刚上传的文档，或来源文件名不一致")
        check(any("五个工作日" in item["text"] for item in matches),
              "命中文档片段未包含原文规定")

    def delete(self, document_id):
        success(self.client, "DELETE", f"/api/documents/{document_id}", headers=headers(self.token))
        self.deleted_ids.add(document_id)
        check(vector_document_count(document_id) == 0, "文档删除后仍有该文档的孤儿向量")

    def verify_batch_rollback(self):
        before_ids = {row["id"] for row in document_rows(self.client, self.token)}
        before_files = disk_files()
        before_vectors = vector_count()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_metadata({"/Title": self.marker + "-scanned"})
        scan = BytesIO()
        writer.write(scan)
        status, body = request(
            self.client, "POST", "/api/documents/upload", headers=headers(self.token),
            files=[
                ("files", (self.batch_filename, self.text.encode("utf-8"), "text/plain")),
                ("files", (self.bad_filename, scan.getvalue(), "application/pdf")),
            ],
        )
        check(status == 200 and body.get("code") != 200, "含扫描 PDF 的批量上传未失败")
        message = body.get("message", "")
        check(self.bad_filename in message and "扫描版" in message and "回滚" in message,
              "批量失败未明确指出坏文件、扫描版原因及回滚结果")
        after_rows = document_rows(self.client, self.token)
        check(not any(self.marker in row.get("filename", "") for row in after_rows),
              "批量失败后保留了本次文档记录")
        check({row["id"] for row in after_rows} == before_ids,
              "批量回滚记录基线发生变化；请确保验证时没有并行管理员写入")
        check(disk_files() == before_files, "批量回滚后私有原文目录存在新增残留")
        check(vector_count() == before_vectors, "批量回滚后向量数存在新增残留")
        passed("valid TXT + 扫描 PDF 整批失败：文件名和原因明确，SQL/磁盘/向量均无残留")

    def cleanup(self):
        # 文件名中的唯一标记可恢复 POST 已提交但响应丢失的记录，不删除其它文档。
        for row in document_rows(self.client, self.token):
            if self.marker in row.get("filename", ""):
                self.ids.add(row["id"])
        failures = []
        for document_id in sorted(self.ids - self.deleted_ids):
            try:
                self.delete(document_id)
            except Exception as exc:
                failures.append(str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__)
        remaining = document_rows(self.client, self.token)
        check(not any(self.marker in row.get("filename", "") for row in remaining),
              "临时文档记录未清理完")
        for document_id in self.ids:
            check(vector_document_count(document_id) == 0, "临时文档向量未清理完")
        # 只读核对原文，不打印内容或路径；随机标记避免把既有文件当成测试残留。
        for relative in disk_files():
            check(self.marker.encode() not in (Path(DOCUMENT_DIR) / relative).read_bytes(),
                  "私有原文目录仍包含本次唯一标记的文件")
        check(not failures, "临时文档清理失败：" + "; ".join(failures))
        passed("本次临时文档及对应向量、原文均已清理；未删除既有资料")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--missing-key-only", action="store_true",
                        help="只用独立缺 embedding Key 的 MCP 验证失败语义")
    args = parser.parse_args()
    if not settings.LLM_API_KEY.get_secret_value().strip():
        print("[FAIL] 未配置 LLM_API_KEY，请自行填入 backend/.env", file=sys.stderr)
        return 1
    if not args.missing_key_only and not settings.EMBEDDING_API_KEY.get_secret_value().strip():
        print("[FAIL] 未配置 EMBEDDING_API_KEY，请自行填入 backend/.env；"
              "可先用 --missing-key-only 验证失败路径", file=sys.stderr)
        return 1

    process, fixture, failures = None, None, []
    signal.signal(signal.SIGTERM, interrupted)
    with tempfile.TemporaryFile() as log, httpx.Client(base_url=API, trust_env=False, timeout=90) as client:
        try:
            try:
                response = client.get("/openapi.json", timeout=5)
            except httpx.RequestError:
                raise CheckFailed("FastAPI 未运行，请先启动：\n" + START_API) from None
            check(response.status_code == 200, "FastAPI 8000 未就绪")
            check(args.missing_key_only or "/api/documents/upload" in response.json().get("paths", {}),
                  "FastAPI 未加载文档管理接口，请重启已有 8000 服务")
            admin_token, _ = login(client, "admin", "admin")
            aaa_token, _ = login(client, "aaa", "student")
            if not args.missing_key_only:
                process = start_mcp(log)
                wait_mcp(process)
                before_upload = asyncio.run(mcp_search(aaa_token, HIT_QUERY))
                assert_search_result(before_upload)
                check(before_upload["count"] == 0,
                      "临时探针上传前已命中共享库；请使用与既有资料不重叠的测试主题")
                passed("临时探针上传前无命中；共享库中的既有演示资料保持原样")
                fixture = DocumentFixture(client, admin_token)
                document_id = fixture.upload()
                fixture.verify_hit(aaa_token, document_id)
                passed("先启动的真实 MCP 无需重启，即可命中 FastAPI 新入库文档和来源")
                check(vector_document_count(document_id) >= 1, "实际 Chroma 未写入文档分块")
                answer = agent_answer(aaa_token, "请查课程资料并引用来源：" + HIT_QUERY)
                check(fixture.filename in answer and any(word in answer for word in
                      ("五个工作日", "5个工作日", "5 个工作日")),
                      "助手回答未依据规定或未引用本次真实来源文件名")
                passed("资料问题真实调用 MCP；回答依据五个工作日规定并引用来源文件名")

                missing = asyncio.run(mcp_search(aaa_token, MISS_QUERY))
                assert_search_result(missing)
                check(missing["count"] == 0,
                      "无关问题命中了既有知识库或本次文档；请使用隔离测试资料，脚本不会放宽阈值")
                answer = agent_answer(aaa_token, "请只依据课程知识库：" + MISS_QUERY)
                check("没有检索到" in answer and fixture.filename not in answer,
                      "正常空检索未明确没有检索到，或错误引用了测试文档")
                passed("不相关主题正常空检索；助手明确没有检索到且不引用无关文档")
                unauthenticated = asyncio.run(mcp_search(None, HIT_QUERY))
                check(unauthenticated.get("code") == 401
                      and "检索失败" in unauthenticated.get("error", ""),
                      "未登录调用资料检索未被拒绝")
                passed("未登录不能通过真实 MCP 检索")

                stop_mcp(process)
                process = None
                process = start_mcp(log)
                wait_mcp(process)
                fixture.verify_hit(aaa_token, document_id)
                passed("重启自己的 MCP 后仍命中文档，持久化目录共享验证通过")
                fixture.delete(document_id)
                deleted = asyncio.run(mcp_search(aaa_token, HIT_QUERY))
                assert_search_result(deleted)
                check(not any(item.get("document_id") == document_id for item in deleted["items"]),
                      "删除后 MCP 仍返回已删除文档")
                check(deleted["count"] == 0,
                      "删除本次文档后查询命中了既有文档；请使用隔离测试资料验证空结果")
                passed("删除文档后 MCP 检索为空，真实 Chroma 中该文档向量数为 0")
                fixture.verify_batch_rollback()
                stop_mcp(process)
                process = None

            with missing_embedding_environment():
                process = start_mcp(log)
            wait_mcp(process)
            verify_missing_key(aaa_token)
        except KeyboardInterrupt:
            failures.append("运行被中断")
        except CheckFailed as exc:
            failures.append(str(exc))
        except BusinessException as exc:
            failures.append(f"真实验收发生业务错误（业务码 {exc.code}）；请检查配置和服务状态")
        except Exception as exc:
            failures.append(f"真实验收异常：{type(exc).__name__}")
        finally:
            try:
                if fixture is not None:
                    fixture.cleanup()
            except BaseException as exc:
                reason = str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__
                failures.append(f"临时文档清理失败（标记 {fixture.marker}）：{reason}")
            finally:
                try:
                    stop_mcp(process)
                except BaseException as exc:
                    failures.append(f"MCP 关闭失败：{type(exc).__name__}")
    if failures:
        for failure in failures:
            print("[FAIL] " + failure, file=sys.stderr)
        return 1
    passed("P4 RAG 真实冒烟全部通过（退出码 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
