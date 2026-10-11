"""P11 真实文档管理验收：仅通过 HTTP 写入，临时文档最终自动清理。

从 frontend 运行：
  ../backend/.venv/bin/python tests/manual/document_management_smoke.py

复用已经运行的 FastAPI，不启动/停止服务，不调用 DeepSeek。
入库和检索会使用 backend/.env 配置的真实 SiliconFlow embedding。
不要与其它管理员写文档操作并行执行，以便精确核对回滚基线。
"""

from datetime import datetime
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import uuid
from zoneinfo import ZoneInfo

import httpx


FRONTEND = Path(__file__).resolve().parents[2]
BACKEND = FRONTEND.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app import models  # noqa: E402,F401 — 注册只读表映射，不调用 create_all。
from app.config import DOCUMENT_DIR  # noqa: E402
from app.database import Base, SessionLocal  # noqa: E402
from app.services import rag_service  # noqa: E402


DEMO_IDS = {17, 18, 19, 20}
PROTECTED_TABLES = ("users", "coruses", "schedules", "study_plans")


class CheckFailed(Exception):
    pass


def check(condition, message):
    if not condition:
        raise CheckFailed(message)


def timestamp():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def digest(value):
    serialized = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def database_snapshot():
    """只保存哈希与行数，用户密码等原始列值不进入日志/记录。"""
    result = {}
    with SessionLocal() as db:
        for name in (*PROTECTED_TABLES, "documents"):
            table = Base.metadata.tables[name]
            rows = [dict(row) for row in db.execute(table.select().order_by(table.c.id)).mappings()]
            result[name] = {"count": len(rows), "digest": digest(rows)}
    result["env_digest"] = hashlib.sha256((BACKEND / ".env").read_bytes()).hexdigest()
    return result


def private_files():
    directory = Path(DOCUMENT_DIR)
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*") if path.is_file()
    }


class DocumentSmoke:
    def __init__(self, client, record):
        self.client = client
        self.record = record
        self.marker = "P11DOC-" + uuid.uuid4().hex
        self.ids = set()
        self.deleted_ids = set()
        self.token = None
        self.headers = {}
        self.baseline = None
        self.baseline_files = None
        self.baseline_vectors = None
        self.texts = {
            self.marker + "-资料备份规定.md": (
                "# 文档管理验收资料 A\n\n## 验收规则\n"
                f"{self.marker} 资料备份演练须在十九分钟内完成，并使用青绿色封面。"
            ),
            self.marker + "-展板提交规定.md": (
                "# 文档管理验收资料 B\n\n## 提交规则\n"
                f"{self.marker} 展板提交演练须由指导老师签字，编号为紫竹四十七。"
            ),
        }

    def request(self, method, path, *, record=False, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise CheckFailed(f"{method} {path} 请求失败（{type(exc).__name__}）") from None
        try:
            body = response.json()
        except ValueError:
            raise CheckFailed(f"{method} {path} 返回非 JSON，HTTP {response.status_code}") from None
        check(isinstance(body, dict), f"{method} {path} 返回格式错误")
        if record:
            # 登录调用永不启用 record，避免 JWT/凭据落入验收记录。
            self.record["operations"].append({
                "time": timestamp(), "method": method, "path": path,
                "http_status": response.status_code, "response": body,
            })
        return response.status_code, body

    def success(self, method, path, *, record=False, **kwargs):
        status, body = self.request(method, path, record=record, **kwargs)
        check(status == 200 and body.get("code") == 200,
              f"{method} {path} 未成功，HTTP {status}，业务码 {body.get('code')}："
              + str(body.get("message", "请求失败")))
        return body.get("data")

    def login(self):
        data = self.success("POST", "/api/auth/login", json={
            "username": "admin",
            "password": os.environ.get("E2E_ADMIN_PASSWORD", "admin"),
        })
        check(isinstance(data, dict) and isinstance(data.get("token"), str), "管理员登录缺少 Token")
        user = data.get("user") or {}
        check(user.get("role") == "admin" and user.get("username") == "admin", "登录身份不是管理员")
        self.token = data["token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def documents(self):
        rows, page = [], 1
        while True:
            data = self.success("GET", "/api/documents", headers=self.headers,
                                params={"page": page, "page_size": 100})
            check(isinstance(data, dict) and isinstance(data.get("list"), list)
                  and type(data.get("total")) is int, "文档分页结构错误")
            rows.extend(data["list"])
            if len(rows) >= data["total"]:
                return rows
            check(data["list"], "文档分页提前结束")
            page += 1

    def demo_state(self):
        rows = {row["id"]: row for row in self.documents() if row["id"] in DEMO_IDS}
        check(set(rows) == DEMO_IDS, "演示文档 17/18/19/20 不完整，停止验收")
        for document_id, row in rows.items():
            check(row["status"] == "indexed" and row["chunk_count"] == 7,
                  f"演示文档 {document_id} 必须已入库且有 7 个分块")
            check(rag_service.vector_document_count(document_id) == 7,
                  f"演示文档 {document_id} 向量数不是 7")
        return [rows[document_id] for document_id in sorted(rows)]

    def start(self):
        self.login()
        self.record["marker"] = self.marker
        self.record["demo_before"] = self.demo_state()
        self.baseline = database_snapshot()
        self.baseline_files = private_files()
        self.baseline_vectors = rag_service.vector_count()
        self.record["baseline"] = {
            "table_counts": {name: item["count"] for name, item in self.baseline.items()
                             if isinstance(item, dict)},
            "private_file_count": len(self.baseline_files),
            "vector_count": self.baseline_vectors,
        }
        print("[PASS] 已只读记录数据库、原文、向量及 4 份演示文档基线", flush=True)

    def upload(self):
        # 一个 HTTP 请求中重复同一字段名 files，绝不逐文件循环调用接口。
        parts = [("files", (name, text.encode("utf-8"), "text/markdown"))
                 for name, text in self.texts.items()]
        data = self.success("POST", "/api/documents/upload", record=True,
                            headers=self.headers, files=parts)
        self.record["operations"][-1]["multipart_fields"] = ["files", "files"]
        self.record["operations"][-1]["filenames"] = list(self.texts)
        self.record["operations"][-1]["upload_request_count"] = 1
        check(isinstance(data, list) and len(data) == 2, "批量上传未返回两份文档")
        self.ids.update(row["id"] for row in data if type(row.get("id")) is int)
        check({row["filename"] for row in data} == set(self.texts), "上传文件名不匹配")
        for row in data:
            check(type(row.get("id")) is int and row["id"] not in DEMO_IDS
                  and row["status"] == "indexed" and row["chunk_count"] > 0,
                  "上传记录不是新建且已入库的测试文档")
            check(rag_service.vector_document_count(row["id"]) == row["chunk_count"],
                  "文档分块数与向量数不一致")
            preview = self.success("GET", f"/api/documents/{row['id']}/preview", record=True,
                                   headers=self.headers)
            check(preview.get("extracted_text") == self.texts[row["filename"]],
                  "预览抽取文本与测试文档内容不一致")
        check(rag_service.vector_count() == self.baseline_vectors + sum(row["chunk_count"] for row in data),
              "批量上传后总向量增长不匹配分块数")
        print("[PASS] 两份 md 共用一次 files+files 请求，已入库且预览内容一致", flush=True)
        return data

    def reindex(self, row):
        before = rag_service.vector_count()
        data = self.success("POST", f"/api/documents/{row['id']}/reindex", record=True,
                            headers=self.headers)
        check(data["id"] == row["id"] and data["status"] == "indexed"
              and data["chunk_count"] == row["chunk_count"], "重新入库状态或分块数异常")
        check(rag_service.vector_count() == before
              and rag_service.vector_document_count(row["id"]) == row["chunk_count"],
              "重新入库产生重复向量")
        self.record["reindex_vector_count"] = {"before": before, "after": before}
        print("[PASS] 测试文档重新入库成功，向量总数未增长", flush=True)

    def verify_bad_batch(self):
        before_rows = self.documents()
        before_ids = {row["id"] for row in before_rows}
        before_files = private_files()
        before_vectors = rag_service.vector_count()
        bad_filename = self.marker + "-不支持图片.png"
        good_filename = self.marker + "-整批回滚测试.md"
        status, body = self.request(
            "POST", "/api/documents/upload", record=True, headers=self.headers,
            files=[
                ("files", (good_filename, b"## P11 rollback\nValid document before unsupported PNG.", "text/markdown")),
                ("files", (bad_filename, b"\x89PNG\r\n\x1a\nP11 unsupported file", "image/png")),
            ],
        )
        operation = self.record["operations"][-1]
        operation["multipart_fields"] = ["files", "files"]
        operation["filenames"] = [good_filename, bad_filename]
        message = body.get("message", "")
        check(status == 200 and body.get("code") == 400, "md + png 批量上传未返回业务码 400")
        check(bad_filename in message and "仅支持" in message
              and all(extension in message for extension in (".txt", ".md", ".pdf", ".docx"))
              and "整批已回滚" in message, "坏批次错误未明确指出文件名、支持格式及整批回滚")
        check({row["id"] for row in self.documents()} == before_ids, "坏批次留下数据库记录")
        check(private_files() == before_files, "坏批次留下原文文件或改变已有原文")
        check(rag_service.vector_count() == before_vectors, "坏批次留下孤儿向量")
        self.record["batch_rollback"] = {
            "document_ids_before": sorted(before_ids), "document_ids_after": sorted(before_ids),
            "private_files_unchanged": True, "vector_count_before": before_vectors,
            "vector_count_after": before_vectors,
        }
        print("[PASS] 正常 md + 不支持 png 整批回滚，记录/原文/向量均不变", flush=True)
        print(message, flush=True)

    def delete(self, document_id, *, record=False):
        check(document_id in self.ids and document_id not in DEMO_IDS, "拒绝删除非本次测试文档")
        row = next((row for row in self.documents() if row["id"] == document_id), None)
        if row is None:
            check(rag_service.vector_document_count(document_id) == 0, "缺失测试记录仍有孤儿向量")
            self.deleted_ids.add(document_id)
            return
        before = rag_service.vector_count()
        before_doc = rag_service.vector_document_count(document_id)
        self.success("DELETE", f"/api/documents/{document_id}", record=record, headers=self.headers)
        self.deleted_ids.add(document_id)
        after = rag_service.vector_count()
        check(rag_service.vector_document_count(document_id) == 0, "删除后文档向量未清空")
        check(before - after == before_doc == row["chunk_count"], "删除后向量下降数量与该文档分块数不符")
        if record:
            self.record["operations"][-1]["vector_count"] = {
                "before": before, "after": after, "removed": before_doc, "document_after": 0,
            }

    def verify_deletion(self, rows):
        for row in rows:
            # 与被索引章节正文相同的查询，确保删除前确实能命中测试文档。
            query = self.texts[row["filename"]].split("\n\n", 1)[1]
            before_search = rag_service.search_course_docs(query)
            check(any(item["document_id"] == row["id"] for item in before_search["items"]),
                  "删除前无法检索到测试文档，不能完成检索清理验收")
            self.delete(row["id"], record=True)
            after_search = rag_service.search_course_docs(query)
            check(not any(item["document_id"] == row["id"] or item["source"] == row["filename"]
                          for item in after_search["items"]), "删除后检索仍返回测试文档")
            self.record["retrieval_checks"].append({
                "document_id": row["id"], "source": row["filename"],
                "found_before_delete": True, "found_after_delete": False,
                "matches_before": [
                    {"document_id": item["document_id"], "source": item["source"], "distance": item["distance"]}
                    for item in before_search["items"] if item["document_id"] == row["id"]
                ],
            })
        print("[PASS] 两份测试文档删除后对应向量归零，检索不再返回其片段", flush=True)

    def cleanup(self):
        if not self.token:
            return
        # POST 若已提交而响应丢失，只按本轮唯一文件名标记恢复并清理。
        rows = self.documents()
        self.ids.update(row["id"] for row in rows if row.get("filename", "").startswith(self.marker + "-"))
        errors = []
        for document_id in sorted(self.ids - self.deleted_ids):
            try:
                self.delete(document_id)
            except Exception as exc:
                errors.append(str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__)
        check(not errors, "本次测试文档清理失败：" + "；".join(errors))
        check(not any(row.get("filename", "").startswith(self.marker + "-") for row in self.documents()),
              "本次测试文档未清理完")
        for document_id in self.ids:
            check(rag_service.vector_document_count(document_id) == 0, "本次测试文档残留向量")
        if self.baseline is not None:
            check(database_snapshot() == self.baseline, "数据库基线或 .env 被改变")
            check(private_files() == self.baseline_files, "原文文件基线被改变")
            check(rag_service.vector_count() == self.baseline_vectors, "向量总数未恢复基线")
            demo_after = self.demo_state()
            check(demo_after == self.record["demo_before"], "4 份演示文档元信息发生变化")
            self.record["demo_after"] = demo_after
            self.record["cleanup"] = {
                "test_document_ids": sorted(self.ids), "all_deleted": True,
                "database_unchanged": True, "env_unchanged": True,
                "private_files_unchanged": True, "vector_count": self.baseline_vectors,
                "demo_documents_unchanged": True,
            }
            print("[PASS] 仅清理本轮测试文档，数据库/原文/向量/.env 全部恢复基线", flush=True)


def interrupted(_signum, _frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="已运行的 FastAPI 地址")
    parser.add_argument("--output", type=Path, default=FRONTEND / "docs/P11-interface-records.json")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted)
    record = {"started_at": timestamp(), "api": args.api, "operations": [], "retrieval_checks": [], "errors": []}
    with httpx.Client(base_url=args.api, trust_env=False, timeout=120) as client:
        smoke = DocumentSmoke(client, record)
        try:
            smoke.start()
            rows = smoke.upload()
            smoke.reindex(rows[0])
            smoke.verify_bad_batch()
            smoke.verify_deletion(rows)
        except (Exception, KeyboardInterrupt) as exc:
            message = str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__
            record["errors"].append(message)
            print("[FAIL] " + message, file=sys.stderr, flush=True)
        finally:
            try:
                smoke.cleanup()
            except (Exception, KeyboardInterrupt) as exc:
                message = str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__
                record["errors"].append("cleanup: " + message)
                print("[FAIL] 清理：" + message, file=sys.stderr, flush=True)
    record["completed_at"] = timestamp()
    record["exit_code"] = 1 if record["errors"] else 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"验收记录：{args.output}", flush=True)
    return record["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
