"""只读诊断真实检索质量；失败返回非零，不修改数据、配置或准入阈值。

在 backend 目录运行：
    .venv/bin/python tests/manual/rag_quality_diagnose.py
    .venv/bin/python tests/manual/rag_quality_diagnose.py --top 8 --dump

正常检索仍走生产 search_course_docs（最多三条、固定准入阈值）。另行读取
八条原始向量候选，仅用于解释答案排名与距离，绝不当作正常命中。
--dump 可保存不含文档正文的距离报告；不会写数据库或向量库。
"""

import argparse
import json
import math
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.common.exceptions import BusinessException  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.services import embedding_service, rag_service, rag_vector_service  # noqa: E402

# 保留 P7 的七个有答案问题，并加入 P12 的口语问法与无关问题。
# 正常命中必须同时含答案事实及正确来源；原始候选只用于标定。
PROBES = [
    ("实验课前需要预习吗？", "预习报告"),
    ("实验报告最晚什么时候交？", "第 3 天"),
    ("数学作业迟交扣几分？", "扣 2 分"),
    ("考试可以带计算器吗？", "计算器"),
    ("期中考试占多少分？", "期中考试"),
    ("英语作文可以交打印稿吗？", "手写"),
    ("请假超过三天由谁审批？", "教务处"),
    ("作业晚交了会怎么样？", "扣 2 分"),
    ("写作文可以用电脑打字打印出来交吗？", "手写"),
    ("学校食堂几点开门？", None),
    ("图书馆周末开放吗？", None),
    ("奖学金怎么申请？", None),
    ("考研报名时间是什么时候？", None),
    ("宿舍几点熄灯？", None),
    ("校园卡丢了怎么办？", None),
    ("今天天气怎么样？", None),
]
EXPECTED_SOURCES = [
    "数据结构实验教学规定.md", "数据结构实验教学规定.md",
    "高等数学学习与考核说明.md", "高等数学学习与考核说明.md",
    "高等数学学习与考核说明.md", "大学英语学习要求.md",
    "学生课程事务办理指南.md", "高等数学学习与考核说明.md",
    "大学英语学习要求.md", None, None, None, None, None, None, None,
]
# 某些关键词也会出现在复习建议或发布信息中；确认片段包含实际规定。
ANSWER_FACTS = {
    "预习报告": ("必须完成预习报告",),
    "计算器": ("不允许携带任何形式的计算器",),
    "期中考试": ("20%",),
    "手写": ("不接受打印稿",),
    "教务处": ("超过 3 天",),
}


def indexed_documents():
    with SessionLocal() as db:
        return {row.id: row.filename for row in db.query(Document)
                .filter(Document.status == "indexed").all()}


def contains_answer(hit, keyword, source):
    return bool(keyword and keyword in hit["text"] and hit["source"] == source
                and all(fact in hit["text"] for fact in ANSWER_FACTS.get(keyword, ())))


def raw_candidates(question, documents):
    """读取距离，不修改 MAX_COSINE_DISTANCE 或生产返回数量。"""
    embeddings = embedding_service.embed_texts([question])
    result = rag_vector_service.vector_operation(
        "query", embeddings=embeddings, document_ids=list(documents),
        candidate_count=8, embedding_identity=rag_vector_service.embedding_identity(),
    )
    candidates = result["items"]
    for hit in candidates:
        distance = hit["distance"]
        if (type(distance) not in (int, float) or not math.isfinite(distance)
                or not -1e-6 <= distance <= 2.000001
                or documents.get(hit["document_id"]) != hit["source"]
                or not isinstance(hit["text"], str) or not hit["text"].strip()):
            raise ValueError("invalid vector candidate")
    return candidates


def diagnose(top):
    documents = indexed_documents()
    if not documents:
        print("[FAIL] 知识库里没有已入库文档，请先完成入库", file=sys.stderr)
        return 1, {}
    print(f"已入库文档 {len(documents)} 份：")
    for document_id, name in sorted(documents.items()):
        print(f"  id={document_id:<4} {name}")

    threshold = rag_service.MAX_COSINE_DISTANCE
    print(f"\n当前准入阈值 MAX_COSINE_DISTANCE = {threshold}")
    print(f"生产候选数={settings.RAG_CANDIDATE_COUNT}；正常最多返回3条；"
          f"诊断读取8条原始候选、展示前{top}条")
    reports = []
    for (question, keyword), source in zip(PROBES, EXPECTED_SOURCES, strict=True):
        # 生产检索必须自行通过；原始候选只作标定，不能替代本项判定。
        hits = rag_service.search_course_docs(question)["items"]
        matched = [hit for hit in hits if contains_answer(hit, keyword, source)]
        passed = bool(matched) if keyword is not None else not hits
        candidates = raw_candidates(question, documents)
        answers = [(rank, hit) for rank, hit in enumerate(candidates, 1)
                   if contains_answer(hit, keyword, source)]
        nearest_answer = min(answers, key=lambda item: item[1]["distance"]) if answers else None
        verdict = "命中正确来源且含答案" if keyword is not None else "无关问题正确地无命中"
        print(f"\n{'✅' if passed else '❌'} {question}："
              f"{'通过' if passed else '失败'}，正常命中{len(hits)}条"
              + (f"，{verdict}" if passed else ""))
        for rank, hit in enumerate(candidates[:top], 1):
            marker = "★含答案" if contains_answer(hit, keyword, source) else "       "
            admission = "准入" if hit["distance"] <= threshold else "拦截"
            print(f"  #{rank} {hit['distance']:.4f} [{admission}] {marker} "
                  f"[{hit['source']}] {hit['text'][:60].replace(chr(10), ' ')}")
        answer_distance = nearest_answer[1]["distance"] if nearest_answer else None
        if keyword is not None:
            print("  正确答案块：" + (
                f"第{nearest_answer[0]}位，距离={answer_distance:.6f}"
                if nearest_answer else "未进入8条候选，需检查分块/召回；不能靠放宽阈值通过"))
        reports.append({
            "question": question, "keyword": keyword, "expected_source": source,
            "passed": passed, "normal_hit_count": len(hits),
            "answer_rank": nearest_answer[0] if nearest_answer else None,
            "answer_distance": answer_distance,
            "nearest_distance": min(hit["distance"] for hit in candidates) if candidates else None,
            "candidates": [{key: hit[key] for key in
                            ("document_id", "source", "chunk_index", "distance")}
                           for hit in candidates],
        })

    answer_reports = [row for row in reports if row["keyword"] is not None]
    unrelated_reports = [row for row in reports if row["keyword"] is None]
    answer_distances = [row["answer_distance"] for row in answer_reports]
    maximum_answer = (max(answer_distances) if answer_distances
                      and all(distance is not None for distance in answer_distances) else None)
    unrelated_distances = [row["nearest_distance"] for row in unrelated_reports
                           if row["nearest_distance"] is not None]
    unrelated_nearest = min(unrelated_distances) if unrelated_distances else None
    answer_passed = sum(row["passed"] for row in answer_reports)
    unrelated_passed = sum(row["passed"] for row in unrelated_reports)
    passed_count = sum(row["passed"] for row in reports)
    print(f"\n有答案问题：{answer_passed}/{len(answer_reports)} 命中正确来源且含答案")
    print(f"无关问题：{unrelated_passed}/{len(unrelated_reports)} 无命中")
    print(f"诊断验收：{passed_count}/{len(reports)} 通过")
    print(f"{len(answer_reports)}个有答案问题的正确答案块最大距离："
          + (f"{maximum_answer:.6f}" if maximum_answer is not None else "不完整（存在未召回的答案块）"))
    print(f"{len(unrelated_reports)}个无关问题的最近距离："
          + (f"{unrelated_nearest:.6f}" if unrelated_nearest is not None else "无候选"))
    if (maximum_answer is not None and unrelated_nearest is not None
            and maximum_answer < threshold < unrelated_nearest):
        print(f"标定依据：{maximum_answer:.6f} < {threshold:.2f} < {unrelated_nearest:.6f}；"
              "当前阈值位于答案块与无关问题之间；embedding 或文档集变化后须重新标定")
    else:
        print("标定尚未形成严格间隔；报告实测距离，脚本不会修改阈值")
    return (0 if passed_count == len(reports) else 1), {
        "threshold": threshold, "production_candidate_count": settings.RAG_CANDIDATE_COUNT,
        "passed": passed_count, "total": len(PROBES),
        "answer_passed": answer_passed, "answer_total": len(answer_reports),
        "unrelated_passed": unrelated_passed, "unrelated_total": len(unrelated_reports),
        "maximum_answer_distance": maximum_answer,
        "unrelated_nearest_distance": unrelated_nearest, "probes": reports,
    }


def main():
    parser = argparse.ArgumentParser(description="RAG 检索质量诊断（只读）")
    parser.add_argument("--top", type=int, default=8, choices=range(1, 9),
                        help="展示的原始候选条数（1到8，实际诊断固定读取8条）")
    parser.add_argument("--dump", action="store_true", help="额外保存不含文档正文的 JSON 距离报告")
    arguments = parser.parse_args()
    try:
        code, report = diagnose(arguments.top)
    except BusinessException as error:
        print(f"[FAIL] 诊断失败（业务码{error.code}）：{error.message}", file=sys.stderr)
        return 1
    except Exception as error:
        # 不回显第三方响应、连接串、认证头或其他内部异常细节。
        print(f"[FAIL] 诊断失败：{type(error).__name__}", file=sys.stderr)
        return 1
    if arguments.dump:
        destination = BACKEND / "rag_quality_report.json"
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"距离报告已保存到 {destination}")
    return code


if __name__ == "__main__":
    sys.exit(main())
