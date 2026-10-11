"""验收诊断本身的判定与退出码；所有数据、embedding、向量 I/O 均为离线替身。"""

from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch

from tests.manual import rag_quality_diagnose as diagnostic


def answer_hit(index):
    _, keyword = diagnostic.PROBES[index]
    return {
        "text": keyword + " " + " ".join(diagnostic.ANSWER_FACTS.get(keyword, ())),
        "source": diagnostic.EXPECTED_SOURCES[index],
        "document_id": 17 + index, "chunk_index": 0,
        "distance": {7: 0.3814, 8: 0.3883}.get(index, 0.28 + index * 0.01),
    }


class RagQualityDiagnosticTests(unittest.TestCase):
    def diagnose(self, wrong_index=None, unrelated_wrong_index=None, unrelated_distances=None):
        threshold = diagnostic.rag_service.MAX_COSINE_DISTANCE
        hits = {question: ([answer_hit(index)] if keyword is not None else [])
                for index, (question, keyword) in enumerate(diagnostic.PROBES)}
        distances = iter(unrelated_distances or (0.4162, 0.4164, 0.4277, 0.4593,
                                                  0.4786, 0.4838, 0.5674))
        raw = {question: ([answer_hit(index)] if keyword is not None else [{
            "text": "完全无关的课程资料", "source": "大学英语学习要求.md",
            "document_id": 19, "chunk_index": 3, "distance": next(distances),
        }]) for index, (question, keyword) in enumerate(diagnostic.PROBES)}
        if wrong_index is not None:
            hits[diagnostic.PROBES[wrong_index][0]] = []
        if unrelated_wrong_index is not None:
            question = diagnostic.PROBES[unrelated_wrong_index][0]
            hits[question] = raw[question]
        output = io.StringIO()
        with patch.object(diagnostic, "indexed_documents", return_value={17: "演示资料.md"}), \
                patch.object(diagnostic.rag_service, "search_course_docs",
                             side_effect=lambda question: {"items": hits[question]}), \
                patch.object(diagnostic, "raw_candidates", side_effect=lambda question, _: raw[question]), \
                redirect_stdout(output):
            result = diagnostic.diagnose(8)
        self.output = output.getvalue()
        self.assertEqual(diagnostic.rag_service.MAX_COSINE_DISTANCE, threshold)
        return result

    def test_all_production_probes_pass_and_calibration_reports_actual_answer_distance(self):
        code, report = self.diagnose()
        self.assertEqual(code, 0)
        self.assertEqual(report["passed"], 16)
        self.assertEqual(report["total"], 16)
        self.assertEqual((report["answer_passed"], report["answer_total"]), (9, 9))
        self.assertEqual((report["unrelated_passed"], report["unrelated_total"]), (7, 7))
        self.assertAlmostEqual(report["maximum_answer_distance"], 0.3883)
        self.assertEqual(report["unrelated_nearest_distance"], 0.4162)
        self.assertIn("有答案问题：9/9", self.output)
        self.assertIn("无关问题：7/7", self.output)
        self.assertIn("诊断验收：16/16", self.output)

    def test_answer_only_in_raw_candidates_cannot_make_production_miss_pass(self):
        code, report = self.diagnose(wrong_index=5)
        self.assertEqual(code, 1)
        self.assertEqual(report["passed"], 15)
        self.assertFalse(report["probes"][5]["passed"])
        self.assertIsNotNone(report["probes"][5]["answer_distance"])

    def test_new_colloquial_answer_requires_production_hit_even_when_raw_answer_is_first(self):
        for question_index in (7, 8):
            with self.subTest(question=diagnostic.PROBES[question_index][0]):
                code, report = self.diagnose(wrong_index=question_index)
                self.assertEqual(code, 1)
                self.assertEqual(report["answer_passed"], 8)
                self.assertEqual(report["probes"][question_index]["answer_rank"], 1)
                self.assertFalse(report["probes"][question_index]["passed"])

    def test_any_unrelated_question_returning_a_hit_fails_the_diagnostic(self):
        for question_index in range(9, 16):
            with self.subTest(question=diagnostic.PROBES[question_index][0]):
                code, report = self.diagnose(unrelated_wrong_index=question_index)
                self.assertEqual(code, 1)
                self.assertEqual(report["passed"], 15)
                self.assertEqual(report["unrelated_passed"], 6)
                self.assertFalse(report["probes"][question_index]["passed"])

    def test_calibration_uses_nearest_of_all_unrelated_questions_not_only_canteen(self):
        code, report = self.diagnose(unrelated_distances=(0.52, 0.42, 0.45, 0.46,
                                                        0.47, 0.48, 0.41))
        self.assertEqual(code, 0)
        self.assertEqual(report["unrelated_nearest_distance"], 0.41)

    def test_probes_contain_nine_answers_and_seven_distinct_unrelated_questions(self):
        self.assertEqual(len(diagnostic.PROBES), 16)
        self.assertEqual(len(set(question for question, _ in diagnostic.PROBES)), 16)
        self.assertEqual(sum(keyword is not None for _, keyword in diagnostic.PROBES), 9)
        self.assertEqual(sum(keyword is None for _, keyword in diagnostic.PROBES), 7)
        self.assertEqual(diagnostic.EXPECTED_SOURCES[7], "高等数学学习与考核说明.md")
        self.assertEqual(diagnostic.EXPECTED_SOURCES[8], "大学英语学习要求.md")

    def test_reference_keyword_without_actual_answer_or_correct_source_is_rejected(self):
        self.assertFalse(diagnostic.contains_answer(
            {"text": "期中考试复习重点约占卷面30%", "source": "高等数学学习与考核说明.md"},
            "期中考试", "高等数学学习与考核说明.md"))
        self.assertFalse(diagnostic.contains_answer(
            {"text": "期中考试20%", "source": "另一门课.md"},
            "期中考试", "高等数学学习与考核说明.md"))


if __name__ == "__main__":
    unittest.main()
