"""真实周冒烟的文本输出回归；不发 HTTP，不读取应用配置或密钥。"""

from contextlib import redirect_stdout
from datetime import timedelta
from io import StringIO
import unittest

from tests.manual import week_schedule_smoke as smoke


def week_data(week):
    monday = smoke.SEMESTER_START + timedelta(days=(week - 1) * 7)
    days = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        name = smoke.holiday_name(day)
        days.append({
            "date": day.isoformat(), "is_holiday": bool(name),
            "holiday_name": name or None, "is_class_day": smoke.expected_class_day(day),
            "courses": [],
        })
    return {
        "week": week, "start_date": monday.isoformat(),
        "end_date": (monday + timedelta(days=6)).isoformat(),
        "days": days, "periods": smoke.PERIODS, "holiday_notice": "",
    }


class WeekScheduleSmokeOutputTests(unittest.TestCase):
    def render(self, data, **kwargs):
        output = StringIO()
        with redirect_stdout(output):
            smoke.print_grid(data, "aaa", **kwargs)
        return output.getvalue()

    def test_holiday_columns_use_the_actual_name(self):
        for week, name, other in ((3, "中秋节", "国庆"), (5, "国庆节", "中秋"), (17, "元旦", "国庆")):
            with self.subTest(week=week):
                rendered = self.render(week_data(week))
                self.assertIn(name, rendered)
                self.assertNotIn(other, rendered)

    def test_course_id_makeup_and_normal_weekend_are_visible(self):
        data = week_data(5)
        data["days"][5]["courses"] = [{
            "period": "1-2节", "course_name": "数据结构", "course_id": 2,
        }]
        rendered = self.render(data, show_course_ids=True)
        self.assertIn("周六 10/10 调休", rendered)
        self.assertIn("周日 10/11 周末", rendered)
        self.assertIn("数据结构[ID=2]", rendered)
        self.assertEqual(len([line for line in rendered.splitlines() if line.startswith("| ")]), 6)


class WeekScheduleDifferenceTests(unittest.TestCase):
    @staticmethod
    def course(course_id, period="1-2节", name="数据结构"):
        return {"course_id": course_id, "period": period, "course_name": name}

    def summary(self, aaa, bbb):
        return smoke.weekly_difference_summary({"aaa": {6: aaa}, "bbb": {6: bbb}})[0]

    def test_identical_occupied_cells_have_zero_difference(self):
        aaa, bbb = week_data(6), week_data(6)
        aaa["days"][0]["courses"] = [self.course(2)]
        bbb["days"][0]["courses"] = [self.course(2)]
        result = self.summary(aaa, bbb)
        self.assertEqual((result["aaa_count"], result["bbb_count"]), (1, 1))
        self.assertEqual((result["same_cells"], result["different_cells"]), (1, 0))
        self.assertEqual(result["different_ratio"], 0)

    def test_only_one_student_has_a_cell_it_is_different(self):
        aaa, bbb = week_data(6), week_data(6)
        aaa["days"][0]["courses"] = [self.course(2)]
        bbb["days"][3]["courses"] = [self.course(2)]
        result = self.summary(aaa, bbb)
        self.assertEqual((result["same_cells"], result["different_cells"]), (0, 2))
        self.assertEqual(result["different_ratio"], 1)

    def test_different_courses_in_same_cell_count_as_one_difference(self):
        aaa, bbb = week_data(6), week_data(6)
        aaa["days"][0]["courses"] = [self.course(2)]
        bbb["days"][0]["courses"] = [self.course(3)]
        result = self.summary(aaa, bbb)
        self.assertEqual((result["compared_cells"], result["different_cells"]), (1, 1))
        self.assertEqual(result["same_cells"], 0)

    def test_empty_cells_are_not_counted_and_empty_week_does_not_pass(self):
        result = self.summary(week_data(6), week_data(6))
        self.assertEqual(result["compared_cells"], 0)
        self.assertEqual(result["same_cells"], 0)
        self.assertEqual(result["different_ratio"], 0)

    def test_exact_eighty_percent_boundary(self):
        aaa, bbb = week_data(6), week_data(6)
        for day in aaa["days"][:5]:
            day["courses"] = [self.course(2)]
        bbb["days"][0]["courses"] = [self.course(2)]
        result = self.summary(aaa, bbb)
        self.assertEqual((result["same_cells"], result["different_cells"]), (1, 4))
        self.assertEqual(result["different_ratio"], .8)

    def test_all_twenty_weeks_are_summarized_in_date_order(self):
        weeks = list(range(20, 0, -1))
        weekly = {name: {week: week_data(week) for week in weeks} for name in ("aaa", "bbb")}
        result = smoke.weekly_difference_summary(weekly)
        self.assertEqual([row["week"] for row in result], list(range(1, 21)))
        self.assertEqual(result[0]["start_date"], "2026-09-07")
        self.assertEqual(result[-1]["end_date"], "2027-01-24")

    def test_difference_table_and_comparison_grid_include_dynamic_counts(self):
        aaa, bbb = week_data(6), week_data(6)
        aaa["days"][0]["courses"] = [self.course(2)]
        bbb["days"][3]["courses"] = [self.course(3, "3-4节", "Web 应用开发")]
        output = StringIO()
        with redirect_stdout(output):
            smoke.print_difference_summary([self.summary(aaa, bbb)])
            smoke.print_comparison_grid(aaa, bbb)
        text = output.getvalue()
        self.assertIn("| 6 | 1 | 1 | 0 | 2 | 100.0% |", text)
        self.assertIn("aaa / bbb 第 6 周同格对照", text)
        self.assertIn("| 周一 10/12 | 1-2节 | 数据结构[ID=2] | — |", text)
        self.assertIn("| 周四 10/15 | 3-4节 | — | Web 应用开发[ID=3] |", text)

    def test_fixed_course_slot_validator_rejects_old_two_slot_alias(self):
        rows = {
            1: {"class_date": "2026-10-14", "period": "7-8节", "course_id": 2},
            2: {"class_date": "2026-10-15", "period": "5-6节", "course_id": 2},
        }
        with self.assertRaisesRegex(smoke.CheckFailed, "固定星期/节次"):
            smoke.verify_fixed_slots(rows, "aaa")


if __name__ == "__main__":
    unittest.main()
