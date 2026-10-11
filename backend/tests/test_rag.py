"""Offline provider, real on-disk Chroma, fresh workers and rollback primitives."""

import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import subprocess
import sys
from unittest.mock import Mock, patch
import uuid

import httpx
from pydantic import SecretStr

# Disable dotenv before importing services, as in the existing offline suite.
from tests import test_agent  # noqa: F401
from app.common.exceptions import BusinessException
from app.services import embedding_service, rag_service, rag_vector_service


def config(key=True):
    return SimpleNamespace(EMBEDDING_API_KEY=SecretStr(uuid.uuid4().hex if key else ""),
                           EMBEDDING_BASE_URL="https://offline.invalid/v1",
                           EMBEDDING_MODEL="offline-embedding")


class EmbeddingTests(unittest.TestCase):
    def factory(self, handler):
        return lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    def test_missing_key_does_not_touch_http(self):
        factory = Mock()
        with self.assertRaises(BusinessException) as caught:
            embedding_service.embed_texts(["课程"], config=config(False), client_factory=factory)
        self.assertEqual(caught.exception.code, 503)
        self.assertIn("检索失败", caught.exception.message)
        factory.assert_not_called()

    def test_batches_and_reorders_vectors(self):
        sizes = []
        def handler(request):
            data = json.loads(request.content)
            sizes.append(len(data["input"]))
            self.assertEqual(data["encoding_format"], "float")
            self.assertEqual(data["model"], "offline-embedding")
            return httpx.Response(200, json={"data": [
                {"index": index, "embedding": [float(start), 1.0]}
                for index, start in reversed(list(enumerate(data["input"])))
            ]})
        result = embedding_service.embed_texts([str(i) for i in range(35)], config=config(), client_factory=self.factory(handler))
        self.assertEqual(sizes, [32, 3])
        self.assertEqual(result, [[float(i), 1.0] for i in range(35)])

    def test_provider_error_is_failure_without_response_body(self):
        def handler(request):
            return httpx.Response(401, json={"error": "private-provider-response"})
        with self.assertRaises(BusinessException) as caught:
            embedding_service.embed_texts(["课程"], config=config(), client_factory=self.factory(handler))
        self.assertEqual(caught.exception.code, 502)
        self.assertIn("检索失败", caught.exception.message)
        self.assertNotIn("private-provider-response", caught.exception.message)

    def test_timeout_is_not_empty_result(self):
        def handler(request):
            raise httpx.ReadTimeout("private-header", request=request)
        with self.assertRaises(BusinessException) as caught:
            embedding_service.embed_texts(["课程"], config=config(), client_factory=self.factory(handler))
        self.assertIn("超时", caught.exception.message)
        self.assertNotIn("private-header", caught.exception.message)

    def test_malformed_vectors_are_rejected(self):
        cases = [[], [{"index": 0, "embedding": []}], [{"index": 0, "embedding": [0.0]}],
                 [{"index": 1, "embedding": [1.0]}], [{"index": False, "embedding": [1.0]}],
                 [{"index": 0, "embedding": [True]}], [{"index": 0, "embedding": ["1"]}],
                 [{"index": 0, "embedding": [math.nan]}]]
        for data in cases:
            with self.subTest(data=data):
                # json.dumps permits NaN so the response parser must reject it.
                handler = lambda request: httpx.Response(200, content=json.dumps({"data": data}))
                with self.assertRaises(BusinessException):
                    embedding_service.embed_texts(["课程"], config=config(), client_factory=self.factory(handler))

    def test_dimension_mismatch_and_duplicate_indexes_rejected(self):
        for data in [[{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0, 2.0]}],
                     [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [2.0]}]]:
            with self.assertRaises(BusinessException):
                embedding_service.embed_texts(["a", "b"], config=config(),
                    client_factory=self.factory(lambda request: httpx.Response(200, json={"data": data})))


class RagRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.hit = {"text": "出勤率不得低于80%", "source": "规定.txt", "document_id": 7,
                    "chunk_index": 0, "distance": 0.1}
        self.patches = [patch.object(embedding_service, "embed_texts", return_value=[[1.0, 0.0]]),
                        patch.object(rag_service, "_indexed_documents", return_value={7: "规定.txt"}),
                        patch.object(rag_vector_service, "vector_operation", return_value={"items": [self.hit]})]
        self.embed, self.documents, self.vector = [item.start() for item in self.patches]
        for item in self.patches:
            self.addCleanup(item.stop)

    def test_found_snippet_has_original_source_and_top_three(self):
        result = rag_service.search_course_docs("出勤规定")
        self.assertEqual(result, {"items": [self.hit], "count": 1})
        self.assertEqual(self.vector.call_args.kwargs["document_ids"], [7])
        self.assertEqual(self.vector.call_args.kwargs["candidate_count"], 8)

    def test_wider_pool_still_returns_only_three_admitted_hits(self):
        self.vector.return_value = {"items": [
            {**self.hit, "chunk_index": index, "distance": 0.1 + index * 0.01}
            for index in range(8)
        ]}
        result = rag_service.search_course_docs("出勤规定")
        self.assertEqual(result["count"], 3)
        self.assertEqual([hit["chunk_index"] for hit in result["items"]], [0, 1, 2])

    def test_later_candidates_survive_withdrawn_document_filter(self):
        self.documents.side_effect = [{7: "规定.txt", 8: "补充.txt"}, {8: "补充.txt"}]
        self.vector.return_value = {"items": [
            {**self.hit, "chunk_index": index} for index in range(3)
        ] + [{**self.hit, "document_id": 8, "source": "补充.txt", "chunk_index": 0}]}
        result = rag_service.search_course_docs("出勤规定")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["document_id"], 8)

    def test_low_similarity_is_normal_empty_and_threshold_fixed(self):
        self.assertEqual(rag_service.MAX_COSINE_DISTANCE, 0.40)
        self.hit["distance"] = 0.41
        self.assertEqual(rag_service.search_course_docs("不相关"), {"items": [], "count": 0})

    def test_boundary_is_included(self):
        self.hit["distance"] = 0.40
        self.assertEqual(rag_service.search_course_docs("相关")["count"], 1)

    def test_distance_above_calibrated_boundary_is_excluded(self):
        self.hit["distance"] = 0.4000001
        self.assertEqual(rag_service.search_course_docs("略超阈值"), {"items": [], "count": 0})

    def test_recorded_calibration_samples_pass_through_production_filter(self):
        """Recorded P12 distances test filtering, not real embedding recall.

        Provider and Chroma are mocked; the live diagnosis script separately
        verifies these questions against the current model and course documents.
        """
        math_source = "高等数学学习与考核说明.md"
        english_source = "大学英语学习要求.md"
        self.documents.return_value = {18: math_source, 19: english_source}
        samples = [
            ("作业晚交了会怎么样？", 0.3814, True, 18, math_source, "迟交作业扣 2 分。"),
            ("写作文可以用电脑打字打印出来交吗？", 0.3883, True, 19, english_source, "作文必须手写。"),
            ("学校食堂几点开门？", 0.4162, False, 18, math_source, "数学考核说明。"),
            ("图书馆周末开放吗？", 0.4164, False, 18, math_source, "数学考核说明。"),
            ("奖学金怎么申请？", 0.4277, False, 18, math_source, "数学考核说明。"),
            ("考研报名时间是什么时候？", 0.4593, False, 18, math_source, "数学考核说明。"),
            ("宿舍几点熄灯？", 0.4786, False, 18, math_source, "数学考核说明。"),
            ("校园卡丢了怎么办？", 0.4838, False, 18, math_source, "数学考核说明。"),
            ("今天天气怎么样？", 0.5674, False, 18, math_source, "数学考核说明。"),
        ]
        for query, distance, admitted, document_id, source, text in samples:
            with self.subTest(query=query, recorded_distance=distance, admitted=admitted):
                self.embed.reset_mock()
                hit = {"document_id": document_id, "source": source, "text": text,
                       "chunk_index": 0, "distance": distance}
                self.vector.return_value = {"items": [hit]}
                result = rag_service.search_course_docs(query)
                self.embed.assert_called_once_with([query])
                self.assertEqual(result, {"items": [hit] if admitted else [],
                                          "count": 1 if admitted else 0})

    def test_only_floating_point_roundoff_is_normalized(self):
        self.hit["distance"] = -1e-7
        self.assertEqual(rag_service.search_course_docs("同一个原文")["items"][0]["distance"], 0.0)
        self.hit["distance"] = -0.1
        with self.assertRaises(BusinessException):
            rag_service.search_course_docs("资料")

    def test_missing_key_cannot_bypass_on_empty_database(self):
        self.documents.return_value = {}
        self.embed.side_effect = BusinessException("检索失败：未配置 EMBEDDING_API_KEY", code=503)
        with self.assertRaises(BusinessException) as caught:
            rag_service.search_course_docs("资料")
        self.assertEqual(caught.exception.code, 503)
        self.documents.assert_not_called()
        self.vector.assert_not_called()

    def test_chroma_failure_is_not_no_hit(self):
        self.vector.side_effect = BusinessException("检索失败：Chroma 读取异常", code=502)
        with self.assertRaises(BusinessException):
            rag_service.search_course_docs("资料")

    def test_no_committed_ids_passes_no_ids_to_chroma(self):
        self.documents.return_value = {}
        self.vector.return_value = {"items": []}
        self.assertEqual(rag_service.search_course_docs("资料")["count"], 0)
        self.assertEqual(self.vector.call_args.kwargs["document_ids"], [])

    def test_reindex_withdrawn_after_snapshot_does_not_leak_partial_result(self):
        self.documents.side_effect = [{7: "规定.txt"}, {}]
        self.assertEqual(rag_service.search_course_docs("资料"), {"items": [], "count": 0})

    def test_source_and_metadata_validation(self):
        for change in [{"source": ""}, {"source": "其它.txt"}, {"document_id": 8},
                       {"text": " "}, {"distance": math.nan}, {"chunk_index": -1}]:
            with self.subTest(change=change):
                self.vector.return_value = {"items": [{**self.hit, **change}]}
                with self.assertRaises(BusinessException) as caught:
                    rag_service.search_course_docs("资料")
                self.assertIn("检索失败", caught.exception.message)

    def test_invalid_query_cannot_call_provider(self):
        for query in ["", " ", None, "课" * 2001]:
            with self.assertRaises(BusinessException):
                rag_service.search_course_docs(query)
        self.embed.assert_not_called()

    def test_delete_does_not_need_embedding(self):
        rag_service.delete_document(7)
        self.embed.assert_not_called()
        self.vector.assert_called_once_with("delete", document_id=7)

    def test_index_embeds_documents_only_once(self):
        rag_service.index_document(7, "规定.txt", ["文档正文"])
        self.embed.assert_called_once_with(["文档正文"])
        self.assertEqual(self.vector.call_args.args, ("index",))

    def test_index_embeds_content_and_keeps_source_prefix_in_stored_chunks(self):
        chunks = ["规定.md · 作文要求\n## 作文要求\n必须手写，不接受打印稿。",
                  "规定.md · 正文\n课程原文介绍"]
        rag_service.index_document(7, "规定.md", chunks)
        self.embed.assert_called_once_with([
            "## 作文要求\n必须手写，不接受打印稿。", "课程原文介绍",
        ])
        self.assertEqual(self.vector.call_args.kwargs["chunks"], chunks)
        self.assertEqual(self.vector.call_args.kwargs["filename"], "规定.md")

    def test_continuation_embedding_retains_chapter_context(self):
        chunks = ["规定.md · 作文要求\n续段原文"]
        rag_service.index_document(7, "规定.md", chunks)
        self.embed.assert_called_once_with(["## 作文要求\n续段原文"])


class DurableChromaTests(unittest.TestCase):
    """Real Chroma I/O in new interpreter processes; no network/provider/key."""

    def test_query_reads_eight_candidates_and_supports_six(self):
        with TemporaryDirectory(prefix="campus-rag-candidates-") as directory:
            identity = "offline-candidate-pool"
            rag_vector_service.vector_operation(
                "index", directory=directory, embedding_identity=identity,
                document_id=7, filename="规则.txt", chunks=[f"规则{index}" for index in range(9)],
                embeddings=[[1.0, index * 0.1] for index in range(9)],
            )
            for candidate_count in (6, 8):
                result = rag_vector_service.vector_operation(
                    "query", directory=directory, embedding_identity=identity,
                    document_ids=[7], embeddings=[[1.0, 0.0]], candidate_count=candidate_count,
                )
                self.assertEqual(len(result["items"]), candidate_count)
                self.assertTrue(all(item["source"] == "规则.txt" for item in result["items"]))

    def test_fresh_process_visibility_restart_filter_delete_and_persistence(self):
        with TemporaryDirectory(prefix="campus-rag-offline-") as directory:
            identity = "offline-fixed-embedding-identity"
            def operation(name, **payload):
                return rag_vector_service.vector_operation(name, directory=directory, **payload)
            self.assertEqual(operation("query", embedding_identity=identity,
                             embeddings=[[1.0, 0.0, 0.0]], document_ids=[7])["items"], [])
            operation("index", embedding_identity=identity, document_id=7,
                      filename="离线规定.txt", chunks=["课程出勤规定", "作业规定"],
                      embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
            # Each call runs in a new process: changes visible immediately and
            # still visible after the previous Chroma/MCP process has exited.
            result = operation("query", embedding_identity=identity,
                               embeddings=[[1.0, 0.0, 0.0]], document_ids=[7])
            self.assertEqual(result["items"][0]["source"], "离线规定.txt")
            self.assertAlmostEqual(result["items"][0]["distance"], 0)
            self.assertEqual(operation("count", document_id=7)["count"], 2)
            self.assertEqual(operation("query", embedding_identity=identity,
                              embeddings=[[1.0, 0.0, 0.0]], document_ids=[8])["items"], [])
            # Reindex replaces, rather than appends, old chunks.
            operation("index", embedding_identity=identity, document_id=7,
                      filename="离线规定.txt", chunks=["更新课程规定"], embeddings=[[1.0, 0.0, 0.0]])
            self.assertEqual(operation("count", document_id=7)["count"], 1)
            operation("delete", document_id=7)
            self.assertEqual(operation("count")["count"], 0)
            self.assertEqual(operation("query", embedding_identity=identity,
                             embeddings=[[1.0, 0.0, 0.0]], document_ids=[7])["items"], [])
            self.assertTrue((Path(directory) / "chroma.sqlite3").exists())
            self.assertEqual((Path(directory) / ".gitignore").read_text(), "*\n")

    def test_mismatched_model_is_failure_instead_of_empty_search(self):
        with TemporaryDirectory(prefix="campus-rag-offline-") as directory:
            rag_vector_service.vector_operation("index", directory=directory, embedding_identity="one", document_id=7,
                filename="规则.txt", chunks=["规则"], embeddings=[[1.0, 0.0]])
            with self.assertRaises(BusinessException) as caught:
                rag_vector_service.vector_operation("query", directory=directory, embedding_identity="two",
                    embeddings=[[1.0, 0.0]], document_ids=[7])
            self.assertIn("检索失败", caught.exception.message)

    def test_two_parent_processes_serialize_writes_to_shared_directory(self):
        with TemporaryDirectory(prefix="campus-rag-offline-") as directory:
            program = (
                "import sys; from app.services.rag_vector_service import vector_operation; "
                "vector_operation('index', directory=sys.argv[1], document_id=int(sys.argv[2]), "
                "embedding_identity='concurrency-offline', filename='并发规则.txt', "
                "chunks=['并发规则'], embeddings=[[1.0,0.0]])"
            )
            environment = {**os.environ, "DATABASE_URL": "sqlite://", "JWT_SECRET_KEY": uuid.uuid4().hex,
                           "LLM_API_KEY": "", "EMBEDDING_API_KEY": ""}
            processes = [subprocess.Popen([sys.executable, "-c", program, directory, str(index)],
                            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=rag_vector_service.BASE_DIR) for index in (71, 72)]
            try:
                for process in processes:
                    process.communicate(timeout=20)
                    self.assertEqual(process.returncode, 0, "concurrent vector write failed")
                self.assertEqual(rag_vector_service.vector_operation("count", directory=directory)["count"], 2)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    def test_custom_directory_existing_ignore_is_protected(self):
        with TemporaryDirectory(prefix="campus-rag-offline-") as directory:
            ignore = Path(directory) / ".gitignore"
            ignore.write_text("!source-files\n", encoding="utf-8")
            rag_vector_service._prepare_directory(directory)
            self.assertEqual(ignore.read_text(), "!source-files\n*\n")


if __name__ == "__main__":
    unittest.main()
