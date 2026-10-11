"""P8 周次与日历离线测试，无网络、数据库连接或配置文件修改。"""

from datetime import date, timedelta
import unittest
from unittest.mock import patch

from app.common.exceptions import BusinessException
from app.common.holiday_config import HOLIDAY_PERIODS, MAKEUP_WORKDAYS
from app.config import Settings
from app.utils.semester import date_to_week, is_class_day, week_date_range, week_holiday_info


class SemesterCalendarTests(unittest.TestCase):
    def assert_business_error(self, operation, expected):
        with self.assertRaises(BusinessException) as raised:
            operation()
        self.assertEqual(raised.exception.code, 400)
        self.assertIn(expected, raised.exception.message)

    def test_settings_defaults_match_decision(self):
        config = Settings(
            _env_file=None,
            DATABASE_URL="sqlite://",
            JWT_SECRET_KEY="offline-test",
        )
        self.assertEqual(config.SEMESTER_START_DATE, date(2026, 9, 7))
        self.assertEqual(config.TOTAL_WEEKS, 20)

    def test_week_one_is_monday_to_sunday(self):
        self.assertEqual(week_date_range(1), (date(2026, 9, 7), date(2026, 9, 13)))

    def test_week_twenty_is_last_full_week(self):
        self.assertEqual(week_date_range(20), (date(2027, 1, 18), date(2027, 1, 24)))

    def test_week_out_of_range_returns_clear_business_error(self):
        for week in (0, -1, 21, 99):
            with self.subTest(week=week):
                self.assert_business_error(lambda: week_date_range(week), "1 至 20")

    def test_week_requires_integer_and_rejects_bool(self):
        for week in (True, False, 1.5, "1", None):
            with self.subTest(week=week):
                self.assert_business_error(lambda: week_date_range(week), "整数")

    def test_date_to_week_includes_first_and_last_dates(self):
        self.assertEqual(date_to_week(date(2026, 9, 7)), 1)
        self.assertEqual(date_to_week("2026-09-13"), 1)
        self.assertEqual(date_to_week("2027-01-18"), 20)
        self.assertEqual(date_to_week("2027-01-24"), 20)

    def test_week_change_happens_on_monday(self):
        self.assertEqual(date_to_week("2026-09-13"), 1)
        self.assertEqual(date_to_week("2026-09-14"), 2)

    def test_all_twenty_weeks_round_trip(self):
        for week in range(1, 21):
            monday, sunday = week_date_range(week)
            for offset in range(7):
                self.assertEqual(date_to_week(monday + timedelta(days=offset)), week)
            self.assertEqual(sunday.weekday(), 6)

    def test_dates_outside_semester_return_clear_business_error(self):
        for value in ("2026-09-06", "2027-01-25"):
            with self.subTest(value=value):
                self.assert_business_error(lambda: date_to_week(value), "学期范围")

    def test_invalid_date_format_is_business_error(self):
        for value in ("2026-13-99", "2026-9-7", "20260907", "invalid", None):
            with self.subTest(value=value):
                self.assert_business_error(lambda: date_to_week(value), "YYYY-MM-DD")

    def test_config_can_be_injected_without_changing_environment(self):
        options = {"semester_start_date": "2025-09-01", "total_weeks": 2}
        self.assertEqual(week_date_range(2, **options), (date(2025, 9, 8), date(2025, 9, 14)))
        self.assertEqual(date_to_week("2025-09-14", **options), 2)
        self.assert_business_error(lambda: week_date_range(3, **options), "1 至 2")

    def test_invalid_semester_configuration_is_business_error(self):
        self.assert_business_error(
            lambda: week_date_range(1, semester_start_date="2026-09-08"), "周一"
        )
        for total in (0, -1, True, 1.5):
            with self.subTest(total=total):
                self.assert_business_error(lambda: week_date_range(1, total_weeks=total), "正整数")

    def test_default_configuration_is_read_from_settings(self):
        with patch("app.utils.semester.settings.SEMESTER_START_DATE", date(2025, 9, 1)):
            with patch("app.utils.semester.settings.TOTAL_WEEKS", 2):
                self.assertEqual(week_date_range(1)[0], date(2025, 9, 1))
                self.assertEqual(date_to_week("2025-09-14"), 2)

    def test_configured_holiday_ranges_are_exact(self):
        self.assertEqual(
            [(item.name, item.start_date, item.end_date) for item in HOLIDAY_PERIODS],
            [
                ("中秋节", date(2026, 9, 25), date(2026, 9, 27)),
                ("国庆节", date(2026, 10, 1), date(2026, 10, 7)),
                ("元旦", date(2027, 1, 1), date(2027, 1, 3)),
            ],
        )
        self.assertIn("尚未发布", HOLIDAY_PERIODS[2].note)
        self.assertIn("推定", HOLIDAY_PERIODS[2].note)

    def test_every_holiday_date_is_skipped(self):
        for period in HOLIDAY_PERIODS:
            day = period.start_date
            while day <= period.end_date:
                with self.subTest(day=day):
                    self.assertFalse(is_class_day(day))
                day += timedelta(days=1)

    def test_makeup_sunday_and_saturday_are_class_days(self):
        self.assertEqual(MAKEUP_WORKDAYS, {date(2026, 9, 20), date(2026, 10, 10)})
        self.assertTrue(is_class_day("2026-09-20"))
        self.assertTrue(is_class_day("2026-10-10"))

    def test_regular_weekdays_and_weekends(self):
        for day in range(7, 12):
            self.assertTrue(is_class_day(date(2026, 9, day)))
        self.assertFalse(is_class_day("2026-09-12"))
        self.assertFalse(is_class_day("2026-09-13"))
        self.assertTrue(is_class_day("2026-09-28"))
        self.assertTrue(is_class_day("2026-10-08"))
        self.assertTrue(is_class_day("2027-01-04"))

    def test_invalid_class_date_is_business_error(self):
        self.assert_business_error(lambda: is_class_day("2026-02-30"), "YYYY-MM-DD")

    def test_week_without_holidays_has_no_notice(self):
        self.assertEqual(
            week_holiday_info(1),
            {"has_holidays": False, "holidays": [], "holiday_notice": ""},
        )

    def test_mid_autumn_notice_explains_week_three(self):
        info = week_holiday_info(3)
        self.assertTrue(info["has_holidays"])
        self.assertEqual(info["holidays"][0]["name"], "中秋节")
        self.assertIn("9/25-9/27", info["holiday_notice"])

    def test_national_holiday_spans_weeks_four_and_five(self):
        four, five = week_holiday_info(4), week_holiday_info(5)
        self.assertTrue(four["has_holidays"])
        self.assertTrue(five["has_holidays"])
        self.assertEqual(four["holidays"][0]["week_start_date"], "2026-10-01")
        self.assertEqual(four["holidays"][0]["week_end_date"], "2026-10-04")
        self.assertEqual(five["holidays"][0]["week_start_date"], "2026-10-05")
        self.assertEqual(five["holidays"][0]["week_end_date"], "2026-10-07")
        self.assertIn("10/1-10/7 国庆节放假", four["holiday_notice"])
        self.assertEqual(four["holiday_notice"], five["holiday_notice"])

    def test_new_year_notice_is_marked_as_estimated(self):
        info = week_holiday_info(17)
        self.assertTrue(info["has_holidays"])
        self.assertEqual(info["holidays"][0]["name"], "元旦")
        self.assertIn("尚未发布", info["holiday_notice"])
        self.assertIn("推定", info["holiday_notice"])

    def test_holiday_info_rejects_week_out_of_range(self):
        self.assert_business_error(lambda: week_holiday_info(21), "1 至 20")


if __name__ == "__main__":
    unittest.main()
